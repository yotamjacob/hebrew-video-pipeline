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
async function bootNative(page, {
  withUploader = true,
  withParallel = false,
  fireCompleted = true,
  cancelEventName = 'cancelled',
  pushPerm = null,           // 'granted' | 'denied' | 'prompt' - stubs PushNotifications
  hangPermRequest = false,   // requestPermissions() never settles (backgrounded activity)
} = {}) {
  await page.addInitScript(({ withUploader, withParallel, fireCompleted, cancelEventName, pushPerm, hangPermRequest }) => {
    window.__streamUploads = [];
    window.__parallelUploads = [];
    window.__ackEvents = [];
    window.__removedUploads = [];
    document.addEventListener('DOMContentLoaded', () => {
      const statusError = document.getElementById('statusError');
      if (!statusError) return;
      new MutationObserver(() => {
        if (statusError.classList.contains('visible'))
          localStorage.setItem('__testStatusErrorShown', '1');
      }).observe(statusError, { attributes: true, attributeFilter: ['class'] });
    });
    const Plugins = {};
    if (withUploader) {
      Plugins.Uploader = {
        addListener: (name, cb) => { window.__upCb = cb; return { remove() {} }; },
        acknowledgeEvent: ({ eventId }) => {
          window.__ackEvents.push(eventId);
          return Promise.resolve();
        },
        removeUpload: ({ id }) => {
          window.__removedUploads.push(id);
          localStorage.setItem('__testRemovedUpload', id);
          setTimeout(() => window.__upCb && window.__upCb(
            { name: cancelEventName, id, eventId: `cancel-${id}`,
              payload: cancelEventName === 'failed'
                ? { error: 'Cancelled by user' } : {} }), 0);
          return Promise.resolve();
        },
        startUpload: (opts) => {
          window.__streamUploads.push(opts);
          if (fireCompleted) setTimeout(() => window.__upCb && window.__upCb(
            { name: 'completed', id: 'up1', eventId: 'evt-up1',
              payload: { statusCode: 200 } }), 50);
          return Promise.resolve({ id: 'up1' });
        },
      };
    }
    if (withParallel) {
      // Mirrors the custom ParallelUploaderPlugin's contract (same event
      // shape as the stock uploader, its own listener slot).
      Plugins.ParallelUploader = {
        addListener: (name, cb) => { window.__puCb = cb; return { remove() {} }; },
        acknowledgeEvent: ({ eventId }) => {
          window.__ackEvents.push(eventId);
          return Promise.resolve();
        },
        removeUpload: ({ id }) => {
          window.__removedUploads.push(id);
          localStorage.setItem('__testRemovedUpload', id);
          setTimeout(() => window.__puCb && window.__puCb(
            { name: 'cancelled', id, eventId: `pcancel-${id}`, payload: {} }), 0);
          return Promise.resolve();
        },
        startUpload: (opts) => {
          window.__parallelUploads.push(opts);
          if (fireCompleted) setTimeout(() => window.__puCb && window.__puCb(
            { name: 'completed', id: 'pu1', eventId: 'evt-pu1',
              payload: { statusCode: 200 } }), 50);
          return Promise.resolve({ id: 'pu1' });
        },
      };
    }
    if (pushPerm) {
      // Android 13+ notification permission, as surfaced by the push plugin.
      // A 'prompt' state resolves to denied when asked (user taps Don't allow).
      window.__notifPermChecks = 0; window.__notifPermRequests = 0;
      Plugins.PushNotifications = {
        checkPermissions: () => { window.__notifPermChecks++; return Promise.resolve({ receive: pushPerm }); },
        requestPermissions: () => {
          window.__notifPermRequests++;
          // A backgrounded Android activity never delivers the permission
          // ActivityResult, so Capacitor's saved PluginCall is never resolved
          // and this promise stays pending FOREVER (not rejected).
          if (hangPermRequest) return new Promise(() => {});
          return Promise.resolve({ receive: 'denied' });
        },
        addListener: () => ({ remove() {} }),
        createChannel: () => Promise.resolve(),
      };
    }
    window.Capacitor = {
      isNativePlatform: () => true,
      Plugins,
      convertFileSrc: p => '/__native_file__',
    };
  }, { withUploader, withParallel, fireCompleted, cancelEventName, pushPerm, hangPermRequest });
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
  expect(streams[0].headers['X-Upload-Size']).toBe(String(200 * 1024 * 1024));
  expect(chunkPosts.length).toBe(0);
});

test('notifications denied: upload still runs and the user is told once why no progress bar shows', async ({ page }) => {
  await bootNative(page, { pushPerm: 'denied' });
  await mockAllApis(page);
  await primeNativePick(page, 512 * 1024);
  await page.click('#runBtn');
  const toast = page.locator('#celebrateToast');
  await expect(toast).toHaveClass(/show/, { timeout: 10_000 });
  await expect(toast).toContainText(/הגדרות הטלפון|phone settings/);
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  const streams = await page.evaluate(() => window.__streamUploads);
  expect(streams).toHaveLength(1);                      // the upload itself is never blocked
  expect(await page.evaluate(() => window.__notifPermChecks)).toBeGreaterThanOrEqual(1);
});

test('notifications still promptable: the OS prompt is re-raised before the upload', async ({ page }) => {
  await bootNative(page, { pushPerm: 'prompt' });
  await mockAllApis(page);
  await primeNativePick(page, 512 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  expect(await page.evaluate(() => window.__notifPermRequests)).toBeGreaterThanOrEqual(1);
});

test('notifications prompt that never answers does not stall the upload (backgrounded activity)', async ({ page }) => {
  // Field report 2026-08-23: "upload while minimized is stuck, or finishes
  // after several minutes". requestPermissions() parks a PluginCall that only
  // resolves from the permission ActivityResult; a non-resumed (minimized, or
  // destroyed-and-recreated) activity never delivers it, so the promise stays
  // PENDING - try/catch cannot see that. nativeUpload awaited it as its first
  // statement, so the upload never started until the user reopened the app.
  // docs/uploads.md: "The upload itself is never gated on the permission."
  await bootNative(page, { pushPerm: 'prompt', hangPermRequest: true });
  await mockAllApis(page);
  await primeNativePick(page, 512 * 1024);
  await page.click('#runBtn');
  // The upload must start regardless of the unanswered prompt.
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  const streams = await page.evaluate(() => window.__streamUploads);
  expect(streams).toHaveLength(1);
  expect(await page.evaluate(() => window.__notifPermRequests)).toBeGreaterThanOrEqual(1);
});

test('notifications granted: no warning toast', async ({ page }) => {
  await bootNative(page, { pushPerm: 'granted' });
  await mockAllApis(page);
  await primeNativePick(page, 512 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  await expect(page.locator('#celebrateToast')).not.toContainText(/הגדרות הטלפון|phone settings/);
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

test("a stale persisted completion cannot finish the current upload", async ({ page }) => {
  await bootNative(page, { fireCompleted: false });
  await mockAllApis(page);
  await primeNativePick(page, 350 * 1024 * 1024);
  await page.click('#runBtn');
  await expect.poll(() => page.evaluate(() => window.__streamUploads.length)).toBe(1);

  // The plugin replays unacknowledged terminal events from earlier uploads.
  // This old id must be cleared but must not advance the current run.
  await page.evaluate(() => window.__upCb({
    name: 'completed',
    id: 'old-upload',
    eventId: 'old-event',
    payload: { statusCode: 200 },
  }));
  await expect.poll(() => page.evaluate(() => window.__ackEvents))
    .toContain('old-event');
  await page.waitForTimeout(400);
  await expect(page.locator('#captionEditorCard')).toBeHidden();

  await page.evaluate(() => window.__upCb({
    name: 'completed',
    id: 'up1',
    eventId: 'current-event',
    payload: { statusCode: 200 },
  }));
  await page.waitForSelector('#captionEditorCard', {
    state: 'visible', timeout: 15_000,
  });
  await expect.poll(() => page.evaluate(() => window.__ackEvents))
    .toContain('current-event');
});

test('Start over stops the native foreground upload before reloading', async ({ page }) => {
  // Android may surface stopUpload as a generic failed event with this text,
  // rather than the plugin's nominal `cancelled` event.
  await bootNative(page, { fireCompleted: false, cancelEventName: 'failed' });
  await mockAllApis(page);
  await primeNativePick(page, 350 * 1024 * 1024);
  await page.click('#runBtn');
  await expect.poll(() => page.evaluate(() => window.__streamUploads.length)).toBe(1);
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem('pipelineActiveNativeUpload') || 'null')?.id
  )).toBe('up1');

  await page.click('#startOverBtn');
  await page.click('#confirmOk');

  // addInitScript runs again after reload, so use persisted test evidence.
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem('__testRemovedUpload'))).toBe('up1');
  await expect.poll(() => page.evaluate(() =>
    localStorage.getItem('pipelineActiveNativeUpload'))).toBeNull();
  expect(await page.evaluate(() =>
    localStorage.getItem('__testStatusErrorShown'))).toBeNull();
});

// ── Native R2 fast path ──────────────────────────────────────────────────────
// The foreground service PUTs the whole file to a presigned R2 URL (single
// object, ~4-5x the Modal-ingress stream), then /upload_r2/complete lands it
// on the volume. mockAllApis defaults /upload_r2/* to 503, so these tests
// re-register init/complete to simulate a live R2 config (last route wins).

async function enableNativeR2(page, seen, { parallel = false } = {}) {
  await page.route(/\/upload_r2\/init\//, (route, request) => {
    const body = request.postDataJSON();
    seen.push({ kind: 'init', body });
    // A parallel-capable backend answers parallel inits with part URLs +
    // control URLs; when `parallel` is false here we mimic an OLD backend
    // (no complete_url), which the client treats as parallel-unavailable.
    if (body.parallel) {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(parallel ? {
          upload_id: 'mpu-1', part_size: 512 * 1024,
          urls: [0, 1, 2].map(i => `https://fake-r2.test/part/${i}`),
          complete_url: 'https://fake-r2.test/complete',
          abort_url: 'https://fake-r2.test/abort',
        } : { upload_id: 'mpu-1', part_size: 512 * 1024,
              urls: ['https://fake-r2.test/part/0'] }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ url: 'https://fake-r2.test/native-put' }) });
  });
  await page.route(/\/upload_r2\/complete\//, (route, request) => {
    seen.push({ kind: 'complete', body: request.postDataJSON() });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
}

test('native upload PUTs to the presigned R2 URL (no auth headers) and completes server-side', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  const seen = [];
  await enableNativeR2(page, seen);
  await primeNativePick(page, 350 * 1024 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });

  const up = await page.evaluate(() => window.__streamUploads[0]);
  expect(up.serverUrl).toBe('https://fake-r2.test/native-put');
  expect(up.method).toBe('PUT');
  // The presigned URL is the auth - custom headers must stay off (only
  // signed headers may be validated; none were signed).
  expect(Object.keys(up.headers || {})).toEqual([]);
  const complete = seen.find(s => s.kind === 'complete');
  expect(complete.body.single).toBe(true);
  expect(complete.body.key).toBe(seen.find(s => s.kind === 'init').body.key);
});

test('native R2: a frozen webview that missed the completed event recovers via /upload_r2/complete', async ({ page }) => {
  await bootNative(page, { fireCompleted: false });
  await mockAllApis(page);
  const seen = [];
  await enableNativeR2(page, seen);
  // The upload has NOT spawned yet (nobody completed it) - process_pending
  // answers pending until /upload_r2/complete runs, which is exactly the
  // state a thawed webview finds after missing the completed event.
  let r2Completed = false;
  await page.route(/\/upload_r2\/complete\//, (route, request) => {
    r2Completed = true;
    seen.push({ kind: 'complete', body: request.postDataJSON() });
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.route(/\/process_pending\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: r2Completed ? '{"call_id":"call-1"}' : '{"pending":true}' }));
  await primeNativePick(page, 512 * 1024);
  await page.click('#runBtn');
  await page.waitForTimeout(500);
  // Thaw: the reconcile must ask the server to complete from R2 - the byte
  // probe is useless here (no chunk files ever land on the volume).
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
  expect(seen.some(s => s.kind === 'complete' && s.body.single === true)).toBe(true);
});

test('native R2: a failed PUT falls back to the /upload_stream path', async ({ page }) => {
  await bootNative(page, { fireCompleted: false });
  await mockAllApis(page);
  const seen = [];
  await enableNativeR2(page, seen);
  await primeNativePick(page, 350 * 1024 * 1024);
  await page.click('#runBtn');
  await expect.poll(() => page.evaluate(() => window.__streamUploads.length)).toBe(1);
  // R2 rejects / network dies mid-PUT → the plugin reports failed.
  await page.evaluate(() => window.__upCb({
    name: 'failed', id: 'up1', eventId: 'fail-1', payload: { error: 'network' } }));
  // Orchestrator falls back: a second startUpload targeting the Modal stream.
  await expect.poll(() => page.evaluate(() => window.__streamUploads.length), { timeout: 10_000 }).toBe(2);
  const second = await page.evaluate(() => window.__streamUploads[1]);
  expect(second.serverUrl).toContain('/upload_stream/');
  expect(second.headers['X-Upload-Key']).toBeTruthy();
  await page.evaluate(() => window.__upCb({
    name: 'completed', id: 'up1', eventId: 'done-2', payload: { statusCode: 200 } }));
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });
});

test('a ParallelUploader binary uploads via the parallel service with part urls + completeUrl', async ({ page }) => {
  await bootNative(page, { withParallel: true });
  await mockAllApis(page);
  const seen = [];
  await enableNativeR2(page, seen, { parallel: true });
  await primeNativePick(page, 350 * 1024 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });

  const up = await page.evaluate(() => window.__parallelUploads[0]);
  expect(up.urls.length).toBe(3);
  expect(up.completeUrl).toBe('https://fake-r2.test/complete');
  expect(up.abortUrl).toBe('https://fake-r2.test/abort');
  expect(up.partSize).toBe(512 * 1024);
  expect(up.concurrency).toBeGreaterThan(1);
  // The stock single-connection uploader must NOT have been used.
  expect(await page.evaluate(() => window.__streamUploads.length)).toBe(0);
  // Completion still lands through the shared server-complete flow.
  expect(seen.some(s => s.kind === 'complete' && s.body.single === true)).toBe(true);
});

test('ParallelUploader present but backend without parallel support falls back to the single PUT', async ({ page }) => {
  await bootNative(page, { withParallel: true });
  await mockAllApis(page);
  const seen = [];
  await enableNativeR2(page, seen, { parallel: false });   // old backend: no complete_url
  await primeNativePick(page, 350 * 1024 * 1024);
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });

  expect(await page.evaluate(() => window.__parallelUploads.length)).toBe(0);
  const up = await page.evaluate(() => window.__streamUploads[0]);
  expect(up.serverUrl).toBe('https://fake-r2.test/native-put');
});

test('native: tapping the upload zone opens the source chooser (record or file)', async ({ page }) => {
  await bootNative(page);
  await mockAllApis(page);
  await page.click('#uploadZone');
  await expect(page.locator('#sourceOverlay')).toBeVisible();
  await page.click('#srcCancelBtn');
  await expect(page.locator('#sourceOverlay')).toBeHidden();
});
