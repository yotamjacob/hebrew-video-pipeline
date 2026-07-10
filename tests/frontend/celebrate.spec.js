const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp, DEFAULT_CAPTIONS } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('edit-ready fires the celebration toast with the caption count', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await expect(page.locator('#celebrateToast')).toHaveClass(/show/);
  await expect(page.locator('#celebrateToastText')).toContainText(String(DEFAULT_CAPTIONS.length));
  // Toast escapes the in-flow stacking context (reparented to <body>) so it
  // isn't trapped behind the sticky topbar.
  const parent = await page.locator('#celebrateToast').evaluate(el => el.parentElement.tagName);
  expect(parent).toBe('BODY');
});

test('export complete reveals + animates the success banner', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await expect(page.locator('#burnSuccessBanner')).toBeVisible({ timeout: 15000 });
});

test('celebration still appears under prefers-reduced-motion', async ({ page }) => {
  // The global reduced-motion rule neutralises the keyframes; the toast should
  // still appear (instantly, no motion).
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await expect(page.locator('#celebrateToast')).toHaveClass(/show/);
});
