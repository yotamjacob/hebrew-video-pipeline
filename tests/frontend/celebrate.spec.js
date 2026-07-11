const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp, DEFAULT_CAPTIONS } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('edit-ready does NOT show a captions-ready toast', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await expect(page.locator('#captionEditorCard')).toBeVisible();
  // The "N captions ready" toast was removed - it must not appear at edit-ready.
  await page.waitForTimeout(300);
  await expect(page.locator('#celebrateToast')).not.toHaveClass(/show/);
});

test('export complete reveals + animates the success banner (no seconds-trimmed stat)', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await expect(page.locator('#burnSuccessBanner')).toBeVisible({ timeout: 15000 });
  // The finished-video banner (with Download) is scrolled into view.
  await page.waitForTimeout(700);
  await expect(page.locator('#burnDownloadBtn')).toBeInViewport();
  // The "seconds trimmed" payoff stat was removed - it stays hidden.
  await expect(page.locator('#burnSuccessStat')).toBeHidden();
});
