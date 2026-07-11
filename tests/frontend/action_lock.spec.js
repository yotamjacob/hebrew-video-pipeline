const { test, expect } = require('@playwright/test');
const { API_BASE, runFullUpload, bootApp } = require('./helpers');

// Fulfill a route only after `ms`, so the operation stays in flight long enough
// for assertions about the locked UI.
function delayedFulfill(page, urlPattern, body, ms = 1500) {
  return page.route(urlPattern, async route => {
    await new Promise(r => setTimeout(r, ms));
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('burn locks hooks and the caption editor until done', async ({ page }) => {
  await runFullUpload(page);
  // Slow burn poll keeps the burn in flight
  await page.unroute(`${API_BASE}/burn_poll/**`);
  await delayedFulfill(page, `${API_BASE}/burn_poll/**`, { output_key: 'mock-output-key_out.mp4' });

  await page.click('#runBtn');           // burnMode → doBurn
  await page.click('#confirmOk');        // confirm modal

  // In flight: hook generation disabled, caption editor + options greyed out
  await expect(page.locator('#captionEditorCard')).toHaveClass(/action-locked/, { timeout: 5_000 });
  await expect(page.locator('#optionsCard')).toHaveClass(/action-locked/);
  await expect(page.locator('#generateHookBtn')).toBeDisabled();

  // Done: everything usable again
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/, { timeout: 15_000 });
  await expect(page.locator('#optionsCard')).not.toHaveClass(/action-locked/);
  await expect(page.locator('#generateHookBtn')).toBeEnabled();
});

test('hook generation greys out every other section but keeps its own card live', async ({ page }) => {
  await runFullUpload(page);
  await page.unroute(`${API_BASE}/generate-hook-poll/**`);
  await delayedFulfill(page, `${API_BASE}/generate-hook-poll/**`,
    { hooks: [{ text: 'hook', rationale: 'r' }] });

  await page.click('#generateHookBtn');

  // In flight: run/burn disabled, all other sections greyed out…
  await expect(page.locator('#runBtn')).toBeDisabled();
  await expect(page.locator('#captionEditorCard')).toHaveClass(/action-locked/);
  await expect(page.locator('#optionsCard')).toHaveClass(/action-locked/);
  // …but the hook card itself stays interactive
  await expect(page.locator('#hookCard')).not.toHaveClass(/action-locked/);

  await expect(page.locator('#runBtn')).toBeEnabled({ timeout: 10_000 });
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/);
});

test('burn completion reveals schedule + success banner immediately (download is browser-native)', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#runBtn');
  await page.click('#confirmOk');

  // Burn done: the device download is handed straight to the browser (an
  // attachment anchor that streams to disk), so it no longer gates the UI.
  // The Schedule button AND the success banner both appear right away…
  await expect(page.locator('#openScheduleBtn')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#openScheduleBtn')).toBeEnabled();
  await expect(page.locator('#burnSuccessBanner')).toBeVisible({ timeout: 10_000 });
  // …and the pipeline unlocks (no waiting on a blob fetch to settle).
  await expect(page.locator('#runBtn')).toBeEnabled({ timeout: 10_000 });
  await expect(page.locator('#optionsCard')).not.toHaveClass(/action-locked/);
});

test('finished burn downloads via a hidden iframe (no top-level navigation / unload prompt)', async ({ page }) => {
  // Regression: the API is cross-origin, so a plain <a>/location download would
  // be treated as a top-level navigation and fire the beforeunload "leave page?"
  // prompt (cancelling the download). It must go through a hidden iframe instead.
  await runFullUpload(page);
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect(page.locator('#burnSuccessBanner')).toBeVisible({ timeout: 10_000 });
  const src = await page.locator('#_dlFrame').getAttribute('src');
  expect(src, 'download must use the hidden iframe').toContain('/download/mock-output-key.mp4');
  expect(src, 'download must carry an auth token').toContain('token=');
  // The page never navigated away - the app is still here.
  await expect(page.locator('#burnSuccessBanner')).toBeVisible();
});

test('lock releases after a failed operation', async ({ page }) => {
  await runFullUpload(page, { burnStatus: 500 });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect(page.locator('#burnError')).toBeVisible();
  await expect(page.locator('#runBtn')).toBeEnabled();
  await expect(page.locator('#generateHookBtn')).toBeEnabled();
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/);
});
