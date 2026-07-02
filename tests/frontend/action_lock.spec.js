const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, runFullUpload, DEFAULT_CAPTIONS } = require('./helpers');

const MOCK_CUTS = [
  { index: 0, start: 2.0, end: 3.2, duration: 1.2, before: 'שלום', after: 'עולם' },
];

// Fulfill a route only after `ms`, so the operation stays in flight long enough
// for assertions about the locked UI.
function delayedFulfill(page, urlPattern, body, ms = 1500) {
  return page.route(urlPattern, async route => {
    await new Promise(r => setTimeout(r, ms));
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('cut re-render locks burn, hooks and editor until done', async ({ page }) => {
  await page.route(/\/rerender\/?$/, r =>
    r.fulfill({ status: 202, contentType: 'application/json',
                body: JSON.stringify({ call_id: 'mock-rerender-id' }) }));
  await delayedFulfill(page, /\/rerender_poll\//,
    { captions: DEFAULT_CAPTIONS, video_key: 'new-key_cut.mp4' });

  await runFullUpload(page, { cuts: MOCK_CUTS });
  await page.locator('.cut-check').first().check();
  await page.click('#applyCutsBtn');

  // In flight: burn/run + hook generation disabled, caption editor greyed out
  await expect(page.locator('#runBtn')).toBeDisabled();
  await expect(page.locator('#generateHookBtn')).toBeDisabled();
  await expect(page.locator('#captionEditorCard')).toHaveClass(/action-locked/);

  // Done: everything usable again
  await expect(page.locator('#runBtn')).toBeEnabled({ timeout: 10_000 });
  await expect(page.locator('#generateHookBtn')).toBeEnabled();
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/);
});

test('burn locks the cuts card so restore cannot interrupt it', async ({ page }) => {
  await runFullUpload(page, { cuts: MOCK_CUTS });
  // Slow burn poll keeps the burn in flight
  await page.unroute(`${API_BASE}/burn_poll/**`);
  await delayedFulfill(page, `${API_BASE}/burn_poll/**`, { output_key: 'mock-output-key_out.mp4' });

  await page.click('#runBtn');           // burnMode → doBurn
  await page.click('#confirmOk');        // confirm modal

  await expect(page.locator('#cutsCard')).toHaveClass(/action-locked/, { timeout: 5_000 });
  await expect(page.locator('#applyCutsBtn')).toBeDisabled();

  await expect(page.locator('#cutsCard')).not.toHaveClass(/action-locked/, { timeout: 15_000 });
});

test('hook generation locks action buttons but keeps caption editing available', async ({ page }) => {
  await runFullUpload(page, { cuts: MOCK_CUTS });
  await page.unroute(`${API_BASE}/generate-hook-poll/**`);
  await delayedFulfill(page, `${API_BASE}/generate-hook-poll/**`,
    { hooks: [{ text: 'hook', rationale: 'r' }] });

  await page.click('#generateHookBtn');

  // In flight: run/burn + cut apply disabled…
  await expect(page.locator('#runBtn')).toBeDisabled();
  await expect(page.locator('#applyCutsBtn')).toBeDisabled();
  // …but the caption editor card is NOT greyed out (buttons-only lock)
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/);
  await expect(page.locator('.caption-input').first()).toBeEnabled();

  await expect(page.locator('#runBtn')).toBeEnabled({ timeout: 10_000 });
});

test('lock releases after a failed operation', async ({ page }) => {
  await page.route(/\/rerender\/?$/, r =>
    r.fulfill({ status: 500, contentType: 'application/json',
                body: JSON.stringify({ error: 'boom' }) }));
  await runFullUpload(page, { cuts: MOCK_CUTS });
  await page.locator('.cut-check').first().check();
  await page.click('#applyCutsBtn');
  await expect(page.locator('#cutsError')).toBeVisible();
  await expect(page.locator('#runBtn')).toBeEnabled();
  await expect(page.locator('#generateHookBtn')).toBeEnabled();
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/);
});
