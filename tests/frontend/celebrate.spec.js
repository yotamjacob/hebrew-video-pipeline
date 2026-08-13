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

test('a sticky toast (duration: 0) has no timer and dismisses via the small X', async ({ page }) => {
  await bootApp(page);
  await page.evaluate(() => celebrateToast('הסרטון ירד ומוכן לצפייה', { duration: 0 }));
  const toast = page.locator('#celebrateToast');
  await expect(toast).toHaveClass(/show/);
  // Outlives any normal auto-hide window (default is 2.6s).
  await page.waitForTimeout(3200);
  await expect(toast).toHaveClass(/show/);
  const close = page.locator('#celebrateToastClose');
  await expect(close).toBeVisible();
  await close.click();
  await expect(toast).not.toHaveClass(/show/);
  // A later timed toast hides the X again and auto-hides as before.
  await page.evaluate(() => celebrateToast('quick', { duration: 500 }));
  await expect(page.locator('#celebrateToastClose')).toBeHidden();
  await expect(toast).not.toHaveClass(/show/, { timeout: 3000 });
});
