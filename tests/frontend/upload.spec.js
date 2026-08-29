const { test, expect } = require('@playwright/test');
const { mockAllApis, selectFile, runFullUpload, DEFAULT_CAPTIONS, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('page loads without unhandled JS errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.waitForLoadState('domcontentloaded');
  // Allow SW-related extension noise but not real app errors
  const realErrors = errors.filter(m => !m.includes('message channel closed'));
  expect(realErrors).toHaveLength(0);
});

test('file input exists and accepts video files', async ({ page }) => {
  const input = page.locator('#fileInput');
  await expect(input).toHaveAttribute('type', 'file');
  await expect(input).toHaveAttribute('accept', /video/);
});

test('run button is disabled before file selection', async ({ page }) => {
  await expect(page.locator('#runBtn')).toBeDisabled();
});

test('run button enables after file is selected', async ({ page }) => {
  await mockAllApis(page);
  await selectFile(page);
  await expect(page.locator('#runBtn')).toBeEnabled();
});

test('clicking process shows upload/processing UI', async ({ page }) => {
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  // Either the processing status or the caption editor (fast mock) should appear
  await expect(page.locator('#statusProc, #captionEditorCard')).toBeVisible({ timeout: 5_000 });
});

test('caption editor appears after processing completes', async ({ page }) => {
  await runFullUpload(page);
  await expect(page.locator('#captionEditorCard')).toBeVisible();
});

test('captions from response are rendered in the editor', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  const inputs = page.locator('.caption-input');
  await expect(inputs).toHaveCount(DEFAULT_CAPTIONS.length);
  await expect(inputs.first()).toHaveValue(DEFAULT_CAPTIONS[0].text);
  await expect(inputs.nth(1)).toHaveValue(DEFAULT_CAPTIONS[1].text);
});

test('caption inputs are right-to-left (RTL)', async ({ page }) => {
  await runFullUpload(page);
  const firstInput = page.locator('.caption-input').first();
  await expect(firstInput).toHaveAttribute('dir', 'rtl');
});

test('caption time spans are rendered', async ({ page }) => {
  await runFullUpload(page);
  const times = page.locator('.caption-time');
  await expect(times).toHaveCount(DEFAULT_CAPTIONS.length);
  // Each time wrap has a start-time input with a numeric value
  const firstStartInput = times.first().locator('.caption-start');
  const val = await firstStartInput.inputValue();
  expect(parseFloat(val)).toBeGreaterThanOrEqual(0);
});

test('empty captions response goes straight to download (no editor)', async ({ page }) => {
  await mockAllApis(page, { captions: [] });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // Caption editor should NOT appear; done state (download) should show
  await page.waitForFunction(
    () => document.getElementById('captionEditorCard').style.display === 'none'
       || !document.getElementById('captionEditorCard').offsetParent,
    { timeout: 10_000 }
  );
  await expect(page.locator('#captionEditorCard')).not.toBeVisible();
});

test('upload chunking: large file triggers multiple upload_chunk requests', async ({ page }) => {
  const uploadRequests = [];
  page.on('request', req => {
    if (req.url().includes('/upload_chunk/')) uploadRequests.push(req.url());
  });

  await mockAllApis(page);
  // 16 MB file → 8 chunks of 2 MB (CHUNK_SIZE = 2 MB)
  await selectFile(page, { sizeMB: 16 });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  expect(uploadRequests.length).toBeGreaterThanOrEqual(3);
});

test('chunk uploads use a fixed URL with key/index in headers (one CORS preflight)', async ({ page }) => {
  const chunkReqs = [];
  page.on('request', req => {
    if (req.url().includes('/upload_chunk')) {
      chunkReqs.push({ url: req.url(), method: req.method(),
                       key: req.headers()['x-upload-key'], index: req.headers()['x-upload-index'] });
    }
  });

  await mockAllApis(page);
  await selectFile(page, { sizeMB: 8 });   // 8 chunks @ 1 MB
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  // Exclude the one-shot uplink probe (index 9999, its own throwaway key).
  const posts = chunkReqs.filter(r => r.method === 'POST' && r.index !== '9999');
  expect(posts.length).toBeGreaterThanOrEqual(3);
  // Every POST hits the SAME url with no query string, so the browser reuses a
  // single cached preflight instead of re-flying per chunk.
  const urls = new Set(posts.map(r => r.url));
  expect(urls.size).toBe(1);
  expect([...urls][0]).not.toContain('?');
  // key/index travel in headers, and all chunks share one key with distinct indices.
  const keys = new Set(posts.map(r => r.key));
  expect(keys.size).toBe(1);
  expect([...keys][0]).toBeTruthy();
  const indices = posts.map(r => r.index).sort();
  expect(new Set(indices).size).toBe(posts.length);   // each index sent once
});

test('upfront upload estimate appears after selecting a file', async ({ page }) => {
  const { TINY_MP4_15S } = require('./fixtures');
  await mockAllApis(page);   // /upload_chunk/ 200 → probe resolves fast
  await page.setInputFiles('#fileInput', { name: 'real.mp4', mimeType: 'video/mp4', buffer: TINY_MP4_15S });

  // The uplink probe fills the estimate line in once it resolves.
  const est = page.locator('#uploadEstimateText');
  await expect(est).toBeVisible({ timeout: 8_000 });
  await expect(est).toHaveText(/min|דקות/);
});

test('live upload ETA is shown during the upload', async ({ page }) => {
  await mockAllApis(page);
  // Make ETA compute from the first progress tick, and slow chunks a touch so
  // there are intermediate progress steps to compute from.
  await page.route(/\/upload_chunk\//, async route => {
    await new Promise(r => setTimeout(r, 200));
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await selectFile(page, { sizeMB: 12 });   // 12 chunks @ 1 MB
  await page.evaluate(() => { window.__UPLOAD_ETA_MIN_MS = 0; });
  await page.waitForSelector('#runBtn:not([disabled])');
  // Record every value the % label takes: the bar must top out at 99% - the
  // server-side finalize after the last byte can take seconds, and a bar
  // frozen at 100% reads as a hang.
  await page.evaluate(() => {
    window.__pctSeen = [];
    const el = document.getElementById('uploadBarPct');
    new MutationObserver(() => window.__pctSeen.push(el.textContent))
      .observe(el, { childList: true, characterData: true, subtree: true });
  });
  await page.click('#runBtn');

  // The ETA appears DURING the upload (chunks are throttled above)...
  await page.waitForFunction(
    () => /left|נותרו/.test(document.getElementById('uploadEta').textContent),
    null, { timeout: 15_000 });
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 15_000 });

  // ...and once all bytes are sent it flips to the finalizing label.
  const eta = await page.evaluate(() => document.getElementById('uploadEta').textContent);
  expect(eta).toMatch(/Finishing|מסיימים/);
  const pcts = await page.evaluate(() => window.__pctSeen);
  expect(pcts.length).toBeGreaterThan(0);
  expect(pcts).not.toContain('100%');
});

test('upload telemetry is recorded for diagnostics (not shown in the UI)', async ({ page }) => {
  await mockAllApis(page);
  await selectFile(page, { sizeMB: 4 });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  const stats = await page.evaluate(() => window.__lastUploadStats);
  expect(stats).toBeTruthy();
  expect(stats.mb).toBeGreaterThan(0);
  expect(stats.mbps).toBeGreaterThan(0);
  expect(typeof stats.stalls).toBe('number');
  expect(typeof stats.resentMB).toBe('number');
  expect(stats.chunks).toBeGreaterThanOrEqual(3);   // 4 MB @ 1 MB chunks

  // The debug stats line is NOT surfaced in the UI.
  expect(await page.locator('#uploadStats').count()).toBe(0);
});

test('polling: process_poll receives the correct call_id', async ({ page }) => {
  const pollRequests = [];
  page.on('request', req => {
    if (req.url().includes('/process_poll/')) pollRequests.push(req.url());
  });

  await runFullUpload(page);
  expect(pollRequests.length).toBeGreaterThan(0);
  expect(pollRequests[0]).toContain('mock-process-call-id');
});

test('enhance video selector defaults to off and rides the process params', async ({ page }) => {
  let spawnUrl = null;
  await mockAllApis(page);
  await page.route(/\/process\/\?/, (route, request) => {
    spawnUrl = request.url();
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-process-call-id' }) });
  });
  await expect(page.locator('#ev_none')).toBeChecked();
  await page.click('label[for="ev_esrgan"]');
  await expect(page.locator('#ev_esrgan')).toBeChecked();
  await expect(page.locator('#ev_filters')).not.toBeChecked();   // mutually exclusive
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect.poll(() => spawnUrl, { timeout: 10_000 }).toContain('enhance_video=esrgan');
});

test('time estimate shows for a real video and grows with AI upscale', async ({ page }) => {
  const { TINY_MP4_15S } = require('./fixtures');
  await mockAllApis(page);
  await page.setInputFiles('#fileInput', { name: 'real.mp4', mimeType: 'video/mp4', buffer: TINY_MP4_15S });

  const est = page.locator('#timeEstimateText');
  await expect(page.locator('#timeEstimate')).toBeVisible({ timeout: 10_000 });
  // Copy is intentionally loose here (see est.simple in i18n) — assert the
  // behaviour (a minute range that grows with AI upscale), not the exact label.
  await expect(est).toHaveText(/\d+-\d+ דקות/);
  const base = (await est.textContent()).match(/(\d+)-(\d+)/).slice(1, 3).map(Number);

  await page.click('label[for="ev_esrgan"]');
  const ai = (await est.textContent()).match(/(\d+)-(\d+)/).slice(1, 3).map(Number);
  expect(ai[0]).toBeGreaterThan(base[0]);   // 15s @ esrgan adds ~4-8 min
  expect(ai[1]).toBeGreaterThan(base[1]);

  await page.click('label[for="ev_none"]');
  const back = (await est.textContent()).match(/(\d+)-(\d+)/).slice(1, 3).map(Number);
  expect(back).toEqual(base);               // reverts to the base estimate
});

test('polling survives repeated network failures (mobile background/foreground)', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  await mockAllApis(page);
  // Simulate the OS killing in-flight poll fetches: first 5 poll requests die
  // with a network error ("Failed to fetch"), then connectivity returns.
  // The old code gave up after 3 retries and showed the error card.
  let failures = 0;
  await page.route(`${API_BASE}/process_poll/**`, (route) => {
    if (failures < 5) { failures++; return route.abort('failed'); }
    return route.fallback();
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 30_000 });
  expect(failures).toBe(5);
});

test('network error shows friendly message plus technical detail line', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  await mockAllApis(page);
  // Spawn request dies with a network error (phone lost connectivity)
  await page.route(/\/process\/\?/, route => route.abort('failed'));
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#statusError')).toBeVisible({ timeout: 15_000 });
  // Friendly message replaces the raw "Failed to fetch"
  const msg = await page.locator('#errorMsg').textContent();
  expect(msg.toLowerCase()).not.toContain('failed to fetch');
  // Technical detail line: stage + raw error + connectivity, for bug reports
  await expect(page.locator('#errorDetail')).toBeVisible();
  const detail = await page.locator('#errorDetail').textContent();
  expect(detail).toContain('spawn');
  expect(detail.toLowerCase()).toContain('fetch');
  expect(detail).toContain('online');
  // Expandable console log carries the actual error trail
  await expect(page.locator('#errorLogWrap')).toBeVisible();
  const log = await page.locator('#errorLog').textContent();
  expect(log.toLowerCase()).toContain('fetch');
});

test('upload chunk survives repeated network failures (mobile backgrounding)', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  await mockAllApis(page);
  // Chunk 0 dies with a network error 5 times, then connectivity returns.
  // The old code gave up after 4 attempts per chunk.
  let failures = 0;
  // key/index now ride in headers (fixed URL for one cached CORS preflight).
  await page.route(/\/upload_chunk\//, (route, req) => {
    if (req.headers()['x-upload-index'] !== '0') return route.fallback();
    if (failures < 5) { failures++; return route.abort('failed'); }
    return route.fallback();
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 40_000 });
  expect(failures).toBe(5);
});

test('persistent upload failure surfaces an error instead of spinning forever', async ({ page }) => {
  test.setTimeout(60_000);   // error surfaces after ~24s of capped retries
  const { API_BASE } = require('./helpers');
  await mockAllApis(page);
  // Connection is genuinely broken: every chunk request dies, forever.
  await page.route(/\/upload_chunk\//, route => route.abort('failed'));
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  // Must give up after the awake-attempt cap (~30s), not spin for minutes
  await expect(page.locator('#statusError')).toBeVisible({ timeout: 45_000 });
  const detail = await page.locator('#errorDetail').textContent();
  expect(detail).toContain('upload');
  const log = await page.locator('#errorLog').textContent();
  expect(log).toContain('giving up');
});

test('401 during upload fails fast to the login view, no endless retry', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  await mockAllApis(page);
  await page.route(/\/upload_chunk\//, route =>
    route.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"unauthorized"}' }));
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  // Session-expired path must surface within seconds - not retry blindly
  await expect(page.locator('#authView')).toBeVisible({ timeout: 10_000 });
});

test('unreadable picked file is caught at selection with a download-first message', async ({ page }) => {
  // Android: a gallery-picked file can be cloud-only (Google Photos) - its bytes
  // aren't on the device, so reads fail. We snapshot the bytes at selection, so
  // an unreadable file is caught immediately (before any upload or credit is
  // spent) and blocks with a "download to the device first" message, rather
  // than failing partway through a long upload. Mock the read BEFORE selecting.
  await mockAllApis(page);
  await page.evaluate(() => {
    Blob.prototype.arrayBuffer = () =>
      Promise.reject(new DOMException('The requested file could not be read', 'NotReadableError'));
  });
  await selectFile(page);
  // Blocked at selection - notice shown, run never becomes available.
  await expect(page.locator('#noticeBlock')).toBeVisible({ timeout: 10_000 });
  const body = await page.locator('#noticeBlockBody').textContent();
  expect(body).toMatch(/download it to the device|הורידו אותו למכשיר/);
  await expect(page.locator('#runBtn')).toBeDisabled();
});

test('a stalled (silent) chunk connection aborts and surfaces an error, not an endless spin', async ({ page }) => {
  test.setTimeout(60_000);
  await mockAllApis(page);
  await selectFile(page);
  // Shrink the stall watchdog so the test runs fast, then make chunk requests
  // hang forever (connect, no response) - the classic "0% for minutes" stall.
  await page.evaluate(() => { window.__CHUNK_STALL_MS = 400; });
  await page.route(/\/upload_chunk\//, () => { /* never fulfill: hang */ });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#statusError')).toBeVisible({ timeout: 40_000 });
  const log = await page.locator('#errorLog').textContent();
  expect(log.toLowerCase()).toContain('stall');
  expect(log).toContain('giving up');
});

test('backgrounding-killed chunk retries do not burn the give-up budget', async ({ page }) => {
  test.setTimeout(60_000);
  const { mockAllApis: mk, selectFile: sf, bootApp: boot } = require('./helpers');
  // Stub document.hidden and expose a helper that pulses "hidden" (bumping the
  // app's _hiddenEpoch) then returns to visible - simulating a brief minimize.
  await page.addInitScript(() => {
    let hidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    window.__pulseHidden = () => {
      hidden = true;  document.dispatchEvent(new Event('visibilitychange'));
      hidden = false; document.dispatchEvent(new Event('visibilitychange'));
    };
  });
  await boot(page);
  await mk(page);
  await sf(page);
  // Chunk 0 fails 10 times; each failure is made to coincide with a background
  // pulse. Old code gave up at 6 network failures - now these don't count.
  let failures = 0;
  await page.route(/\/upload_chunk\//, async (route, req) => {
    if (req.headers()['x-upload-index'] !== '0') return route.fallback();
    if (failures < 10) {
      failures++;
      await page.evaluate(() => window.__pulseHidden());
      return route.abort('failed');
    }
    return route.fallback();
  });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 45_000 });
  expect(failures).toBe(10);
});

test('resume: a retry skips chunks the server already received', async ({ page }) => {
  test.setTimeout(60_000);
  const { API_BASE } = require('./helpers');
  await mockAllApis(page);
  // 6 MB file @ 2 MB chunks = 3 chunks (indices 0,1,2).
  await selectFile(page, { name: 'resume.mp4', sizeMB: 6 });
  // Count POSTs per index across BOTH runs. First run: index 2 fails forever
  // (visible) so the upload errors after 0 and 1 succeed. Second run (retry)
  // must skip 0 and 1 (already on server) and only send 2.
  const posts = {};
  let allowIndex2 = false;
  await page.route(/\/upload_chunk\//, (route, req) => {
    const idx = req.headers()['x-upload-index'];
    posts[idx] = (posts[idx] || 0) + 1;
    if (idx === '2' && !allowIndex2) return route.abort('failed');
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#statusError')).toBeVisible({ timeout: 30_000 });
  const postsAfterFirst = { ...posts };
  expect(postsAfterFirst['0']).toBe(1);   // sent once
  expect(postsAfterFirst['1']).toBe(1);

  // Now let index 2 through and retry.
  allowIndex2 = true;
  await page.click('#retryBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 30_000 });
  // Chunks 0 and 1 must NOT have been re-sent on the retry.
  expect(posts['0']).toBe(1);
  expect(posts['1']).toBe(1);
  expect(posts['2']).toBeGreaterThanOrEqual(2);   // retried until it succeeded
});

test('4K video shows a slow-upload warning (quality kept as-is)', async ({ page }) => {
  const { TINY_MP4_15S } = require('./fixtures');
  await mockAllApis(page);
  // Report 4K dimensions from the loaded video's metadata (the real fixture
  // loads fine unpadded; we just override the reported resolution).
  await page.evaluate(() => {
    Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth',  { configurable: true, get: () => 3840 });
    Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', { configurable: true, get: () => 2160 });
  });
  await page.setInputFiles('#fileInput', { name: 'uhd.mp4', mimeType: 'video/mp4', buffer: TINY_MP4_15S });
  await expect(page.locator('#noticeWarn')).toBeVisible({ timeout: 10_000 });
  const title = await page.locator('#noticeWarnTitle').textContent();
  expect(title).toMatch(/4K/);
  // It's a warning, not a block - the user can still process.
  await expect(page.locator('#runBtn')).toBeEnabled();
});

// ── Mobile source chooser (record vs file) ──────────────────────────────────
test('mobile: tapping the upload zone offers record-or-file; desktop goes straight to the dialog', async ({ page, browserName }, testInfo) => {
  await bootApp(page);
  await page.click('#uploadZone');
  const isMobile = testInfo.project.name === 'Mobile Chrome';
  if (isMobile) {
    await expect(page.locator('#sourceOverlay')).toBeVisible();
    // The record path is a capture-input opening the camera recorder.
    await expect(page.locator('#recordInput')).toHaveAttribute('capture', 'user');
    await expect(page.locator('#recordInput')).toHaveAttribute('accept', 'video/*');
    // Cancel closes without side effects.
    await page.click('#srcCancelBtn');
    await expect(page.locator('#sourceOverlay')).toBeHidden();
  } else {
    await expect(page.locator('#sourceOverlay')).toBeHidden();
  }
});

test('a recorded video flows into the normal selection path', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'Mobile Chrome', 'chooser is mobile-only');
  await bootApp(page);
  await mockAllApis(page);
  await page.click('#uploadZone');
  await expect(page.locator('#sourceOverlay')).toBeVisible();
  // Playwright can't drive the OS camera - set the file on the record input
  // directly, which is exactly what the camera hands back.
  await page.click('#srcCancelBtn');
  await page.setInputFiles('#recordInput', {
    name: 'recorded.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(512 * 1024),
  });
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await expect(page.locator('#fileName')).toHaveText('recorded.mp4');
});

test('cut-only done is not a dead end: options unlock and re-process is offered', async ({ page }) => {
  let spawnCalls = 0;
  page.on('request', r => {
    if (r.method() === 'POST' && /\/process\/\?/.test(r.url()) && !r.url().includes('defer=true')) spawnCalls++;
  });
  await mockAllApis(page, { captions: [] });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#statusDone')).toBeVisible({ timeout: 10_000 });

  // The tool toggles are editable again and a re-process CTA is present
  // (previously everything stayed setup-locked with no way back in).
  await expect(page.locator('#optionsCard')).not.toHaveClass(/setup-locked/);
  await expect(page.locator('#reprocessBtn')).toBeVisible();

  // Clicking it confirms and spawns a fresh run with the current toggles.
  await page.click('#reprocessBtn');
  await page.click('#confirmOk');
  await expect.poll(() => spawnCalls, { timeout: 10_000 }).toBe(1);
});

test('the save stage renders as a live checklist row with a flow-aware label', async ({ page }) => {
  await mockAllApis(page, { captions: [] });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#statusDone')).toBeVisible({ timeout: 10_000 });

  // Re-drive the checklist the way the poll's progress payload does: the
  // server now reports a real 'save' stage while publishing the result.
  // Cut-only run (captions toggle OFF): the label is the download one.
  await page.evaluate(() => {
    document.getElementById('burnCaptions').checked = false;
    _resetChecklist();
    _applyProgress({ stage: 'save', done: {} });
  });
  await expect(page.locator('#checkSave')).toHaveClass(/active/);
  await expect(page.locator('#checkSave .check-label')).toHaveAttribute('data-i18n', 'prog.saveDl');
  // A captions run labels the same row as plain saving (user lands in the
  // editor, not the download panel).
  await page.evaluate(() => {
    document.getElementById('burnCaptions').checked = true;
    _resetChecklist();
  });
  await expect(page.locator('#checkSave .check-label')).toHaveAttribute('data-i18n', 'prog.save');
});

test('done card keeps the finished checklist and its runtimes visible', async ({ page }, testInfo) => {
  // 2026-08-29 report: the "video is ready" card replaced the progress list,
  // hiding the per-step runtimes the user had just watched.
  await mockAllApis(page, { captions: [] });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#statusDone')).toBeVisible({ timeout: 10_000 });
  const checklist = page.locator('#statusChecklist');
  await expect(checklist).toBeVisible();
  await expect(checklist).toHaveClass(/finished/);
  await expect(page.locator('#checkUpload')).toHaveClass(/done/);
  await expect(page.locator('#checkUploadTime')).not.toHaveText('');
  // Rows that never started are dropped, finished rows stay.
  const pendingVisible = await page.locator('#statusChecklist .check-item.pending:visible').count();
  expect(pendingVisible).toBe(0);
  await page.locator('#statusCard').screenshot({ path: testInfo.outputPath('done-card.png') });
});
