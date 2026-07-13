const { test, expect } = require('@playwright/test');
const { runFullUpload, DEFAULT_HOOKS, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('generate hook button is present and clickable after captions load', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnHook');   // the hook editor lives in a tab now
  const btn = page.locator('#generateHookBtn');
  await expect(btn).toBeVisible();
  await expect(btn).toBeEnabled();
});

test('clicking generate hook calls the API and shows options', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');

  // Wait for hook options to render - each option is a div with id hookOption{n}
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });
  const options = page.locator('#hookOptions > div[id^="hookOption"]');
  const count = await options.count();
  expect(count).toBeGreaterThan(0);
});

test('hook API sends captions_json and video_key', async ({ page }) => {
  let capturedBody = null;

  await runFullUpload(page);

  // Intercept AFTER runFullUpload so this handler runs first (LIFO), captures body, then falls back to mock
  await page.route(`${require('./helpers').API_BASE}/generate-hook/`, async (route, request) => {
    try { capturedBody = JSON.parse(request.postData() || '{}'); } catch {}
    await route.fallback();
  });

  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });

  expect(capturedBody).not.toBeNull();
  expect(capturedBody).toHaveProperty('captions_json');
  expect(capturedBody).toHaveProperty('video_key', 'mock-video-key_cut.mp4');
});

test('hook generation 500 error shows error message in hook section', async ({ page }) => {
  // Process succeeds but hook returns 500
  await runFullUpload(page, { hookStatus: 500 });
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');

  const errEl = page.locator('#hookError');
  await expect(errEl).toBeVisible({ timeout: 6_000 });
  const errText = await errEl.textContent();
  expect(errText.toLowerCase()).toMatch(/failed|error|500/);
});

test('hook error does not crash the rest of the page', async ({ page }) => {
  await runFullUpload(page, { hookStatus: 500 });
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.locator('#hookError').waitFor({ state: 'visible', timeout: 6_000 });

  // Caption editor should still be intact (switch back to the captions tab)
  await expect(page.locator('#captionEditorCard')).toBeVisible();
  await page.click('#tabBtnCaptions');
  await expect(page.locator('.caption-input').first()).toBeVisible();
});

test('hook generate button re-enables after error', async ({ page }) => {
  await runFullUpload(page, { hookStatus: 500 });
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.locator('#hookError').waitFor({ state: 'visible', timeout: 6_000 });

  // Button should be re-enabled so user can retry
  await expect(page.locator('#generateHookBtn')).toBeEnabled({ timeout: 3_000 });
});

test('poll endpoint receives the hook call_id', async ({ page }) => {
  const pollUrls = [];
  page.on('request', req => {
    if (req.url().includes('/generate-hook-poll/')) pollUrls.push(req.url());
  });

  await runFullUpload(page);
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });

  expect(pollUrls.length).toBeGreaterThan(0);
  expect(pollUrls[0]).toContain('mock-hook-call-id');
});

test('burn sends the exact hook lines the preview wrapped (WYSIWYG)', async ({ page }) => {
  // The burn must reproduce the preview's wrapping verbatim (not re-wrap in
  // libass), so the frontend sends the canvas-computed lines in hook.lines.
  const { API_BASE } = require('./helpers');
  let burnBody = null;
  await runFullUpload(page);
  await page.route(/\/burn\/?(\?|$)/, async (route, request) => {
    try { burnBody = JSON.parse(request.postData() || '{}'); } catch {}
    await route.fallback();   // hand off to the mock from runFullUpload
  });

  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });
  await page.click('#hookOption0');

  await page.click('#runBtn');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();

  await expect.poll(() => (burnBody && burnBody.hook ? 'y' : 'n'), { timeout: 10_000 }).toBe('y');
  expect(Array.isArray(burnBody.hook.lines)).toBe(true);
  expect(burnBody.hook.lines.length).toBeGreaterThan(0);
  // The lines partition the hook text - same words, same order (just wrapped).
  const words = s => s.split(/\s+/).filter(Boolean).join(' ');
  expect(words(burnBody.hook.lines.join(' '))).toBe(words(burnBody.hook.text));
});
