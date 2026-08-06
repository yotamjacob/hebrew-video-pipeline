// R2 direct upload fast path: presigned parallel part PUTs + fallback
const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp, mockAllApis, selectFile } = require('./helpers');

const R2_HOST = 'https://fake-r2.test';

// Register the R2 routes AFTER mockAllApis (its default is 503-off; the
// last-registered route wins) to simulate a live R2 config. Part PUTs are
// cross-origin, so the mock must answer the preflight AND expose the ETag -
// exactly what the real bucket CORS must do.
async function enableR2(page, { parts = 3, partSize = 512 * 1024, seen } = {}) {
  await page.route(new RegExp(`${API_BASE.replace(/[/.]/g, '\\$&')}/upload_r2/init/`), async (route, request) => {
    seen && seen.push({ kind: 'init', body: request.postDataJSON() });
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        upload_id: 'mp-1', part_size: partSize,
        urls: Array.from({ length: parts }, (_, i) => `${R2_HOST}/part/${i}`),
      }),
    });
  });
  await page.route(new RegExp(`${API_BASE.replace(/[/.]/g, '\\$&')}/upload_r2/complete/`), (route, request) => {
    seen && seen.push({ kind: 'complete', body: request.postDataJSON() });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.route(new RegExp(`${API_BASE.replace(/[/.]/g, '\\$&')}/upload_r2/abort/`), (route, request) => {
    seen && seen.push({ kind: 'abort', body: request.postDataJSON() });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.route(new RegExp(R2_HOST.replace(/[/.]/g, '\\$&')), (route, request) => {
    const cors = {
      'access-control-allow-origin': '*',
      'access-control-allow-methods': 'PUT',
      'access-control-allow-headers': '*',
      'access-control-expose-headers': 'ETag',
    };
    if (request.method() === 'OPTIONS')
      return route.fulfill({ status: 204, headers: cors });
    const n = request.url().split('/').pop();
    seen && seen.push({ kind: 'put', part: Number(n) });
    return route.fulfill({ status: 200, headers: { ...cors, 'ETag': `"etag-${n}"` }, body: '' });
  });
}

test('upload takes the R2 path: parts PUT in parallel, complete carries ordered etags, no chunk POSTs', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  const seen = [];
  await enableR2(page, { parts: 3, partSize: 512 * 1024, seen });
  const chunkPosts = [];
  page.on('request', req => {
    if (req.method() === 'POST' && req.url().includes('/upload_chunk/')
        && req.headers()['x-upload-index'] !== '9999') chunkPosts.push(req.url());
  });

  await selectFile(page, { sizeMB: 1.5 });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  const puts = seen.filter(s => s.kind === 'put').map(s => s.part).sort();
  expect(puts).toEqual([0, 1, 2]);
  const complete = seen.find(s => s.kind === 'complete');
  expect(complete.body.upload_id).toBe('mp-1');
  expect(complete.body.etags).toEqual(['"etag-0"', '"etag-1"', '"etag-2"']);
  expect(complete.body.key).toBe(seen.find(s => s.kind === 'init').body.key);
  expect(chunkPosts).toEqual([]);   // fast path used - no Modal chunk POSTs
  expect(await page.evaluate(() => window.__lastUploadStats && window.__lastUploadStats.path)).toBe('r2');
});

test('init 503 (no R2 configured) falls back to the chunked path', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);   // default /upload_r2/* is 503 - nothing to enable
  const chunkPosts = [];
  page.on('request', req => {
    if (req.method() === 'POST' && req.url().includes('/upload_chunk/')
        && req.headers()['x-upload-index'] !== '9999') chunkPosts.push(req.url());
  });

  await selectFile(page, { sizeMB: 1.5 });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  expect(chunkPosts.length).toBeGreaterThan(0);   // slow path carried the bytes
});

test('part PUTs that die (bucket CORS unset) fall back to chunked and abort the multipart', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  const seen = [];
  await enableR2(page, { parts: 3, partSize: 512 * 1024, seen });
  // Kill every part PUT at the network layer - what an unset bucket CORS
  // policy looks like from JS. Last-registered route wins.
  await page.route(new RegExp(R2_HOST.replace(/[/.]/g, '\\$&')), (route, request) => {
    if (request.method() === 'OPTIONS')
      return route.fulfill({ status: 204, headers: {
        'access-control-allow-origin': '*', 'access-control-allow-methods': 'PUT',
        'access-control-allow-headers': '*' } });
    return route.abort();
  });
  const chunkPosts = [];
  page.on('request', req => {
    if (req.method() === 'POST' && req.url().includes('/upload_chunk/')
        && req.headers()['x-upload-index'] !== '9999') chunkPosts.push(req.url());
  });

  await selectFile(page, { sizeMB: 1.5 });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 20_000 });

  expect(chunkPosts.length).toBeGreaterThan(0);           // upload still landed
  expect(seen.some(s => s.kind === 'abort')).toBe(true);  // multipart handed back
});

test('a resumed R2 upload skips parts that already have etags', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  const seen = [];
  await enableR2(page, { parts: 3, partSize: 512 * 1024, seen });

  // Seed resume state for the exact file selectFile() creates. The upload key
  // must match what _resumeUploadKey derives, so seed BOTH stores the way a
  // prior interrupted run would have left them.
  await selectFile(page, { sizeMB: 1.5 });
  await page.evaluate(({ r2Host }) => {
    const sig = `hebpipe_up_test.mp4_${1.5 * 1024 * 1024}_0`;
    // lastModified of a Playwright-set file is not 0 - read the real signature
    // the app computed by scanning localStorage is not possible pre-upload, so
    // build it from the selected file the app holds.
    const f = document.getElementById('fileInput').files[0];
    const realSig = `hebpipe_up_${f.name}_${f.size}_${f.lastModified}`;
    const key = 'resumekey123';
    localStorage.setItem(realSig, JSON.stringify({ key, completed: [], ts: Date.now() }));
    localStorage.setItem(realSig + '_r2', JSON.stringify({
      key, upload_id: 'mp-1', part_size: 512 * 1024,
      urls: [0, 1, 2].map(i => `${r2Host}/part/${i}`),
      etags: { 0: '"etag-0"', 1: '"etag-1"' }, ts: Date.now(),
    }));
  }, { r2Host: R2_HOST });

  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  const puts = seen.filter(s => s.kind === 'put').map(s => s.part);
  expect(puts).toEqual([2]);                              // only the missing part
  expect(seen.some(s => s.kind === 'init')).toBe(false);  // no fresh multipart
  const complete = seen.find(s => s.kind === 'complete');
  expect(complete.body.etags).toEqual(['"etag-0"', '"etag-1"', '"etag-2"']);
});
