/**
 * Native upload path selection (size-gated hybrid).
 *
 * Small native files: the background stream uploader (@capgo, survives
 * minimize - but its retry starts the WHOLE file over).
 * Large native files: the chunked resumable JS uploader, reading the picked
 * file through the WebView local-file scheme - a wifi blip costs one 1MB
 * chunk, not the entire upload. The keep-open note (normally hidden on
 * native) shows for this path. If the local read fails, fall back to the
 * stream uploader.
 *
 * app.js is a classic top-level script: nativeUploadDesc/selectedFile are
 * assignable and window.__NATIVE_CHUNKED_MIN is a test seam.
 */
const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, selectFile, bootApp } = require('./helpers');

const LOCAL_FILE_BYTES = 3 * 1024 * 1024;   // 3 chunks on the chunked path

/** Boot with a Capacitor shim whose Uploader records stream-upload attempts. */
async function bootNative(page, { withUploader = true } = {}) {
  await page.addInitScript(({ withUploader }) => {
    window.__streamUploads = [];
    const Plugins = {};
    if (withUploader) {
      Plugins.Uploader = {
        addListener: (name, cb) => { window.__upCb = cb; return { remove() {} }; },
        startUpload: (opts) => {
          window.__streamUploads.push(opts.serverUrl);
          setTimeout(() => window.__upCb && window.__upCb(
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
  }, { withUploader });
  await bootApp(page);
}

/** Serve the WebView local-file scheme with real bytes (or an error). */
async function mockLocalFile(page, { status = 200 } = {}) {
  await page.route('**/__native_file__', r =>
    status === 200
      ? r.fulfill({ status: 200, headers: { 'Content-Type': 'video/mp4' },
                    body: Buffer.alloc(LOCAL_FILE_BYTES, 3) })
      : r.fulfill({ status, body: '' }));
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

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { window.__NATIVE_CHUNKED_MIN = 1024 * 1024; });   // 1 MB gate
});

test('large native file uploads via resumable chunks, not the stream uploader', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  await mockLocalFile(page);
  const chunkPosts = countChunkPosts(page);
  await primeNativePick(page, 200 * 1024 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  expect(chunkPosts.length).toBe(3);                          // 3 MB / 1 MB chunks
  const streams = await page.evaluate(() => window.__streamUploads);
  expect(streams).toHaveLength(0);                            // stream uploader untouched
});

test('large native chunked path shows the keep-open note', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  // Local file that stalls long enough to observe the in-flight UI.
  await page.route('**/__native_file__', async r => {
    await new Promise(res => setTimeout(res, 300));
    await r.fulfill({ status: 200, headers: { 'Content-Type': 'video/mp4' },
                      body: Buffer.alloc(LOCAL_FILE_BYTES, 3) });
  });
  await primeNativePick(page, 200 * 1024 * 1024);
  await page.click('#runBtn');
  await expect(page.locator('#uploadNote')).toBeVisible({ timeout: 10_000 });
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
});

test('small native file keeps the background stream uploader', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  await mockLocalFile(page);
  const chunkPosts = countChunkPosts(page);
  await primeNativePick(page, 512 * 1024);                    // under the 1 MB gate
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  const streams = await page.evaluate(() => window.__streamUploads);
  expect(streams).toHaveLength(1);
  expect(streams[0]).toContain('/upload_stream');
  expect(chunkPosts.length).toBe(0);
});

test('unreadable local file falls back to the stream uploader', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  await mockLocalFile(page, { status: 404 });                 // provider refuses the read
  await primeNativePick(page, 200 * 1024 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  const streams = await page.evaluate(() => window.__streamUploads);
  expect(streams).toHaveLength(1);                            // graceful fallback
});
