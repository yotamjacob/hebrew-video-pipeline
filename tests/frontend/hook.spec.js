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

test('an AI-overloaded failure (529) shows the friendly message, not a raw 500', async ({ page }) => {
  // Workers re-raise Anthropic API errors as plain "ai_busy:<code>" (the raw
  // SDK exceptions aren't picklable across Modal and surfaced as "Could not
  // deserialize remote exception..."). The poll body must reach the user as
  // the human i18n message.
  await runFullUpload(page);
  await page.route(new RegExp('/generate-hook-poll/'), r =>
    r.fulfill({ status: 500, contentType: 'application/json',
                body: '{"error":"ai_busy:529"}' }));
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  const err = page.locator('#hookError');
  await expect(err).toBeVisible({ timeout: 15_000 });
  await expect(err).toContainText(/עמוס כרגע|overloaded/i);
  await expect(err).not.toContainText(/500|deserialize|ai_busy/);
});

test('a network blip on the hook spawn retries and succeeds (no Failed to fetch)', async ({ page }) => {
  await runFullUpload(page);
  // First two attempts die at the network layer (what a phone backgrounding /
  // wifi blip looks like); the third goes through to the mock.
  let attempts = 0;
  await page.route(`${require('./helpers').API_BASE}/generate-hook/`, async route => {
    attempts++;
    if (attempts <= 2) return route.abort('connectionfailed');
    return route.fallback();
  });
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 15_000 });
  expect(attempts).toBe(3);
  await expect(page.locator('#hookError')).toBeHidden();
});

test('a dead network on the hook spawn shows a human message, not Failed to fetch', async ({ page }) => {
  await runFullUpload(page);
  await page.route(`${require('./helpers').API_BASE}/generate-hook/`, r => r.abort('connectionfailed'));
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  const errEl = page.locator('#hookError');
  await expect(errEl).toBeVisible({ timeout: 20_000 });
  const txt = await errEl.textContent();
  expect(txt).not.toMatch(/failed to fetch/i);
  expect(txt).toMatch(/connection|network|החיבור|רשת/i);
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

test('hook text fields stay readable when options render in a hidden tab', async ({ page }) => {
  const { DEFAULT_HOOKS } = require('./helpers');
  await runFullUpload(page);
  // Render the options while the CAPTIONS tab is active (exactly what the
  // background auto-generation does) - the hidden fields measure scrollHeight
  // 0 and used to collapse into thin, text-hiding strips.
  await page.evaluate(hooks => renderHookOptions(hooks), DEFAULT_HOOKS);
  await page.click('#tabBtnHook');
  const heights = await page.$$eval('.hook-text-input', els =>
    els.map(el => ({ h: el.clientHeight, hasText: !!el.value.trim() })));
  expect(heights.length).toBeGreaterThan(0);
  for (const { h, hasText } of heights) {
    expect(hasText).toBe(true);
    expect(h).toBeGreaterThan(28);   // at least one full text line visible
  }
});

test('hook option text is editable and the edit reaches the burn payload', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });
  // Wiring test (edit -> payload): semantic events, not pointer clicks - see
  // the note in the WYSIWYG test below (mobile scroll-anchoring interplay).
  await page.locator('#hookOption0').dispatchEvent('click');

  const ta = page.locator('#hookText0');
  await expect(page.locator('#hookOption0 .hook-edit-pencil')).toBeVisible();   // edit affordance
  await ta.evaluate(el => {
    el.value = 'טקסט חדש שערכתי';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(ta).toHaveValue('טקסט חדש שערכתי');

  const payload = await page.evaluate(() => _hookPreviewPayload());
  expect(payload.text).toBe('טקסט חדש שערכתי');
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
  // dispatchEvent, not pointer clicks: this asserts SELECTION -> BURN PAYLOAD
  // wiring. Pointer physics on these elements is covered by the tests above;
  // here Playwright's pre-click scroll fights a scroll-anchoring interaction
  // with the docked player on mobile emulation (element verified hittable at
  // rest via elementFromPoint sampling; real churn ruled out).
  await page.locator('#hookOption0').dispatchEvent('click');
  await page.locator('#runBtn').dispatchEvent('click');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();

  await expect.poll(() => (burnBody && burnBody.hook ? 'y' : 'n'), { timeout: 10_000 }).toBe('y');
  expect(Array.isArray(burnBody.hook.lines)).toBe(true);
  expect(burnBody.hook.lines.length).toBeGreaterThan(0);
  // The lines partition the hook text - same words, same order (just wrapped).
  const words = s => s.split(/\s+/).filter(Boolean).join(' ');
  expect(words(burnBody.hook.lines.join(' '))).toBe(words(burnBody.hook.text));
});

test('hook generation is budgeted: 3 uses per video, label counts down, disables at 0', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnHook');
  const btn = page.locator('#generateHookBtn');
  await expect(btn).toContainText('3');   // fresh editor → (3 left)
  for (let i = 0; i < 3; i++) {
    await btn.click();
    const ok = page.locator('#confirmOk');   // regen confirm from the 2nd run on
    if (await ok.isVisible().catch(() => false)) await ok.click();
    await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });
    await expect(btn).toContainText(String(2 - i));
  }
  await expect(btn).toBeDisabled();
});
