/**
 * Native uploads always use Android's foreground-service stream uploader.
 *
 * This is the path that survives minimize/lock and owns the persistent upload
 * notification. Web uploads remain resumable and chunked.
 *
 * app.js is a classic top-level script: nativeUploadDesc/selectedFile are
 * assignable test seams.
 */
const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, selectFile, bootApp } = require('./helpers');

/** Boot with a Capacitor shim whose Uploader records stream-upload attempts.
 * fireCompleted=false simulates a webview that was FROZEN while the native
 * uploader finished - the 'completed' event is lost and never reaches JS. */
async function bootNative(page, { withUploader = true, fireCompleted = true } = {}) {
  await page.addInitScript(({ withUploader, fireCompleted }) => {
    window.__streamUploads = [];
    const Plugins = {};
    if (withUploader) {
      Plugins.Uploader = {
        addListener: (name, cb) => { window.__upCb = cb; return { remove() {} }; },
        startUpload: (opts) => {
          window.__streamUploads.push(opts);
          if (fireCompleted) setTimeout(() => window.__upCb && window.__upCb(
            { name: 'completed', payload: { statusCode: 200 } }), 50);
          return Promise.resolve({ id: 'up1' });
        },
      };
    }
    window.Capacitor = {
      isNativePlatform: () => true,
      Plugins,
      convertFileSrc: p => '/__native_file__',
    };
  }, { withUploader, fireCompleted });
  await bootApp(page);
}

/** Select a file (enables run), then override the native pick descriptor. */
async function primeNativePick(page, sizeBytes) {
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await page.evaluate(size => {
    nativeUploadDesc = { path: '/fake/video.mp4', name: 'big.mp4', size, mimeType: 'video/mp4' };
  }, sizeBytes);
}

function countChunkPosts(page) {
  const keys = [];
  page.on('request', req => {
    // Real upload chunks only - the uplink PROBE also POSTs here (4 concurrent
    // streams, index 9999) to measure throughput for the time estimate.
    if (req.method() === 'POST' && req.url().includes('/upload_chunk/')
        && req.headers()['x-upload-index'] !== '9999')
      keys.push(req.headers()['x-upload-index']);
  });
  return keys;
}

test('large native file uses the notification-owning background uploader', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  const chunkPosts = countChunkPosts(page);
  await primeNativePick(page, 200 * 1024 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  const streams = await page.evaluate(() => window.__streamUploads);
  expect(streams).toHaveLength(1);
  expect(streams[0].serverUrl).toContain('/upload_stream');
  expect(streams[0].method).toBe('PUT');
  expect(streams[0].uploadType).toBe('binary');
  expect(streams[0].notificationTitle).toBeTruthy();
  expect(chunkPosts.length).toBe(0);
});

test('small native file also uses the background stream uploader', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  const chunkPosts = countChunkPosts(page);
  await primeNativePick(page, 512 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  const streams = await page.evaluate(() => window.__streamUploads);
  expect(streams).toHaveLength(1);
  expect(streams[0].serverUrl).toContain('/upload_stream');
  expect(chunkPosts.length).toBe(0);
});

test("a lost 'completed' event recovers via the pending check on foreground return", async ({ page }) => {
  // The stuck-at-5% bug: the webview froze mid-upload, the native uploader
  // finished, the server spawned AND processed (deleting the chunks at
  // reassembly), and the 'completed' event never reached JS. upload_check
  // reports 0 bytes forever - only /process_pending's call_id (the done-marker
  // outlives the chunks) can prove the upload landed. The visibilitychange
  // reconcile must settle on it and carry the flow to the editor.
  await bootNative(page, { fireCompleted: false });
  await mockAllApis(page);                                    // process_pending → call_id
  await page.route(/\/upload_check\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"bytes":0}' }));
  await primeNativePick(page, 512 * 1024);
  await page.click('#runBtn');
  // Simulate the tap-on-notification thaw: page becomes visible again.
  await page.waitForTimeout(500);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
});
