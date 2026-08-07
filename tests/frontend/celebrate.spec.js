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

test('a toast action link is actually clickable (pointer-events restored with .show)', async ({ page }) => {
  await bootApp(page);
  await page.evaluate(() => {
    window.__actionClicked = false;
    celebrateToast('saved', { duration: 60000, action: { label: 'open-me', onClick: () => { window.__actionClicked = true; } } });
  });
  const act = page.locator('#celebrateToastAction');
  await expect(act).toBeVisible();
  await act.click();
  expect(await page.evaluate(() => window.__actionClicked)).toBe(true);
  // The action click also dismisses the toast.
  await expect(page.locator('#celebrateToast')).not.toHaveClass(/show/);
});
