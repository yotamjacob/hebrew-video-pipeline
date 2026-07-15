/**
 * Preview player recovery - the spinner must never hang forever.
 *
 * Field report: on a stalled mobile connection the preview download promise
 * never settled, and the player awaited it UNBOUNDED - eternal "Preview
 * loading" spinner after the processing checklist had already finished.
 *
 * Covered:
 *  - a stalled prefetch falls back to live STREAMING after the bounded wait
 *  - a source that errors twice surfaces a tappable retry instead of spinning
 */
const { test, expect } = require('@playwright/test');
const fs   = require('fs');
const path = require('path');
const { API_BASE, mockAllApis, selectFile, bootApp } = require('./helpers');

const MP4 = path.join(__dirname, 'fixtures/portrait_1080x1920.mp4');

test('stalled preview prefetch falls back to streaming (spinner clears)', async ({ page }) => {
  await page.addInitScript(() => { window.__PREVIEW_BLOB_WAIT_MS = 1500; window.__PREVIEW_READY_WAIT_MS = 1500; });
  await bootApp(page);
  await mockAllApis(page);
  // First download request (the prefetch) stalls FOREVER; later requests (the
  // streaming <video> src) serve the real fixture.
  const buf = fs.readFileSync(MP4);
  await page.route(`${API_BASE}/download/**`, async (route, request) => {
    // JS prefetch fetches stall FOREVER (incl. the app's internal retry); only
    // the <video> element's own streaming request is served - so the test
    // proves the player escapes to streaming. The media element is the one
    // sending an OPEN-ENDED range (bytes=0-); the JS prefetch sends bounded
    // ranges and the single-stream fallback sends none.
    if ((request.headers()['range'] || '') !== 'bytes=0-') return;   // stall
    await route.fulfill({ status: 200,
      headers: { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
                 'Content-Length': String(buf.length) }, body: buf });
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  // The player must give up on the stalled blob and stream instead.
  await page.waitForFunction(() => {
    const v = document.getElementById('cutVideo');
    return v && v.src && !v.src.startsWith('blob:');
  }, { timeout: 15_000 });
  await expect(page.locator('#playerLoading')).toBeHidden({ timeout: 15_000 });
  const dims = await page.evaluate(() => {
    const v = document.getElementById('cutVideo');
    return { w: v.videoWidth, h: v.videoHeight };
  });
  expect(dims.h).toBeGreaterThan(0);   // metadata arrived via streaming
});

test('a source that keeps failing shows a tappable retry, not an eternal spinner', async ({ page }) => {
  await page.addInitScript(() => { window.__PREVIEW_BLOB_WAIT_MS = 500; window.__PREVIEW_READY_WAIT_MS = 500; });
  await bootApp(page);
  await mockAllApis(page);
  // Every download yields undecodable garbage - blob AND streaming both fail.
  await page.route(`${API_BASE}/download/**`, r =>
    r.fulfill({ status: 200, headers: { 'Content-Type': 'video/mp4' },
                body: Buffer.alloc(64 * 1024, 0x55) }));
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 20_000 });
  const loading = page.locator('#playerLoading');
  await expect(loading).toContainText(/retry|לניסיון חוזר/i, { timeout: 20_000 });
  // Tapping retry re-attempts; the source is still garbage, so the cycle
  // must re-arm and offer retry again (briefly flashing the spinner in
  // between) - the point is it can never dead-end.
  await loading.click({ force: true });   // decorative animations make strict stability flaky
  await expect(loading).toContainText(/retry|לניסיון חוזר/i, { timeout: 20_000 });
});
