const { test, expect } = require('@playwright/test');
const { API_BASE, runFullUpload } = require('./helpers');

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

test('burn locks hooks and the caption editor until done', async ({ page }) => {
  await runFullUpload(page);
  // Slow burn poll keeps the burn in flight
  await page.unroute(`${API_BASE}/burn_poll/**`);
  await delayedFulfill(page, `${API_BASE}/burn_poll/**`, { output_key: 'mock-output-key_out.mp4' });

  await page.click('#runBtn');           // burnMode → doBurn
  await page.click('#confirmOk');        // confirm modal

  // In flight: hook generation disabled, caption editor greyed out
  await expect(page.locator('#captionEditorCard')).toHaveClass(/action-locked/, { timeout: 5_000 });
  await expect(page.locator('#generateHookBtn')).toBeDisabled();

  // Done: everything usable again
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/, { timeout: 15_000 });
  await expect(page.locator('#generateHookBtn')).toBeEnabled();
});

test('hook generation locks action buttons but keeps caption editing available', async ({ page }) => {
  await runFullUpload(page);
  await page.unroute(`${API_BASE}/generate-hook-poll/**`);
  await delayedFulfill(page, `${API_BASE}/generate-hook-poll/**`,
    { hooks: [{ text: 'hook', rationale: 'r' }] });

  await page.click('#generateHookBtn');

  // In flight: run/burn disabled…
  await expect(page.locator('#runBtn')).toBeDisabled();
  // …but the caption editor card is NOT greyed out (buttons-only lock)
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/);
  await expect(page.locator('.caption-input').first()).toBeEnabled();

  await expect(page.locator('#runBtn')).toBeEnabled({ timeout: 10_000 });
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
