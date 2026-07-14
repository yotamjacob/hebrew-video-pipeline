/**
 * Share button visibility + behavior.
 *
 * Native (Capacitor): the button always shows once a result exists.
 * Web: shows only where the Web Share API can share FILES (navigator.canShare)
 *      and hands navigator.share a File built from the finished video.
 * Web without the capability: stays hidden.
 */
const { test, expect } = require('@playwright/test');
const { mockAllApis, selectFile, bootApp } = require('./helpers');

async function burnToBanner(page) {
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  await page.click('#runBtn');   // burn
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await page.waitForSelector('#burnSuccessBanner', { state: 'visible', timeout: 15_000 });
}

test('native: share button appears after burn', async ({ page }) => {
  await page.addInitScript(() => {
    window.Capacitor = { isNativePlatform: () => true, Plugins: {} };
  });
  await bootApp(page);
  await burnToBanner(page);
  await expect(page.locator('#burnShareBtn')).toBeVisible({ timeout: 5_000 });
});

test('web with Web Share files support: button appears and shares a File', async ({ page }) => {
  await page.addInitScript(() => {
    navigator.canShare = d => !!(d && d.files && d.files.length);
    navigator.share = async d => {
      window.__shared = {
        files: (d.files || []).length,
        name: d.files && d.files[0] && d.files[0].name,
        type: d.files && d.files[0] && d.files[0].type,
      };
    };
  });
  await bootApp(page);
  await burnToBanner(page);
  const btn = page.locator('#burnShareBtn');
  await expect(btn).toBeVisible({ timeout: 5_000 });
  // Wait for the warm-up to settle so the tap is the instant path.
  await expect(btn).not.toHaveClass(/share-loading/, { timeout: 8_000 });
  await btn.click();
  const shared = await page.evaluate(() => window.__shared);
  expect(shared.files).toBe(1);
  expect(shared.type).toBe('video/mp4');
});

test('web without Web Share files support: button stays hidden', async ({ page }) => {
  await bootApp(page);   // headless Chromium has no navigator.canShare
  await burnToBanner(page);
  await expect(page.locator('#burnShareBtn')).toBeHidden();
});
