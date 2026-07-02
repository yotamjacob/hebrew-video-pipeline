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

test('schedule card is usable while the device download is in flight', async ({ page }) => {
  await runFullUpload(page);
  // Keep the post-burn device download in flight
  await page.unroute(`${API_BASE}/download/**`);
  await page.route(`${API_BASE}/download/**`, async route => {
    await new Promise(r => setTimeout(r, 3000));
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'video/mp4' },
      body: Buffer.alloc(512, 0),
    });
  });

  await page.click('#runBtn');
  await page.click('#confirmOk');

  // Burn done, download still running: schedule card visible AND unlocked
  await expect(page.locator('#scheduleCard')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#scheduleCard')).not.toHaveClass(/action-locked/);
  // …while burn stays held and the other sections stay greyed until it settles
  await expect(page.locator('#runBtn')).toBeDisabled();
  await expect(page.locator('#optionsCard')).toHaveClass(/action-locked/);
  await expect(page.locator('#runBtn')).toBeEnabled({ timeout: 15_000 });
  await expect(page.locator('#optionsCard')).not.toHaveClass(/action-locked/);
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
