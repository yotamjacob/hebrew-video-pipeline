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
  // The button must be READY immediately - plain label, enabled, no spinner
  // phase (it used to sit in a loading state for the whole warm-up download).
  await expect(btn).toBeEnabled();
  await expect(btn).not.toContainText('...');
  await btn.click();
  const shared = await page.evaluate(() => window.__shared);
  expect(shared.files).toBe(1);
  expect(shared.type).toBe('video/mp4');
});

test('share warm-up fetches the video in parallel byte ranges', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  const TOTAL = 1024 * 1024;                 // 1 MB mock file
  const buf = Buffer.alloc(TOTAL, 7);
  const rangeReqs = [];
  await page.addInitScript(() => {
    window.__RANGE_CHUNK = 256 * 1024;       // → 4 ranges over the 1 MB file
    navigator.canShare = d => !!(d && d.files && d.files.length);
    navigator.share = async d => { window.__shared = { size: d.files[0].size }; };
  });
  await bootApp(page);
  await mockAllApis(page);
  // Range-aware download mock (registered after mockAllApis → takes priority).
  await page.route(`${API_BASE}/download/**`, (route, request) => {
    const hdr = request.headers()['range'] || '';
    const m = /bytes=(\d+)-(\d+)/.exec(hdr);
    if (!m) {
      return route.fulfill({ status: 200, headers: { 'Content-Type': 'video/mp4' }, body: buf });
    }
    rangeReqs.push(hdr);
    const start = +m[1], end = Math.min(+m[2], TOTAL - 1);
    return route.fulfill({
      status: 206,
      headers: {
        'Content-Type': 'video/mp4',
        'Accept-Ranges': 'bytes',
        'Content-Range': `bytes ${start}-${end}/${TOTAL}`,
        // The real backend exposes these via CORS (app_modal CORS list); the
        // frontend can't read Content-Range without it.
        'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
      },
      body: buf.slice(start, end + 1),
    });
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  await page.click('#runBtn');   // burn
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await page.waitForSelector('#burnSuccessBanner', { state: 'visible', timeout: 15_000 });

  // The warm-up must split the file into multiple concurrent range requests.
  await expect.poll(() => rangeReqs.length, { timeout: 8_000 }).toBeGreaterThanOrEqual(4);

  // And the shared File must reassemble to the exact total size.
  await page.click('#burnShareBtn');
  await expect.poll(() => page.evaluate(() => window.__shared && window.__shared.size),
                    { timeout: 8_000 }).toBe(TOTAL);
});

test('web without Web Share files support: button stays hidden', async ({ page }) => {
  await bootApp(page);   // headless Chromium has no navigator.canShare
  await burnToBanner(page);
  await expect(page.locator('#burnShareBtn')).toBeHidden();
});
