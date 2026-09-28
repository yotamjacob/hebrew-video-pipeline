const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, selectFile, runFullUpload, DEFAULT_CAPTIONS, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('checklist shows the real backend step times on completion', async ({ page }) => {
  await runFullUpload(page);   // helpers mock reports step_times {enhance: 12.3, cut: 20.1}
  await expect(page.locator('#checkEnhanceTime')).toHaveText('0:12');
  await expect(page.locator('#checkCutTime')).toHaveText('0:20');
});

test("cut step is labeled 'transcribe' when Cut silences is toggled off", async ({ page }) => {
  // The worker's 'cut' stage also covers transcription (captions need it), so
  // it runs even with the toggle off - labeled "Cut silences" it read as the
  // toggle being ignored. The label must follow what the run actually does.
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  const label = page.locator('#checkCut .check-label');
  // Toggle off → transcription label.
  await page.evaluate(() => {
    const el = document.getElementById('cutSilences');
    el.checked = false; el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#runBtn');
  await expect(label).toHaveText('תמלול הדיבור');
});

test('rows for disabled tools are hidden from the checklist', async ({ page }) => {
  // Cut + captions off (audio enhance still on): no transcription runs, so
  // neither a cut nor a transcribe row may appear; enhance stays listed.
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.evaluate(() => {
    for (const id of ['cutSilences', 'burnCaptions']) {
      const el = document.getElementById(id);
      el.checked = false; el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await page.click('#runBtn');
  await expect(page.locator('#statusChecklist')).toBeVisible();
  await expect(page.locator('#checkCut')).toBeHidden();
  await expect(page.locator('#checkEnhance')).toBeVisible();
});

test('live progress from process_poll drives step transitions', async ({ page }) => {
  await mockAllApis(page);
  // First two polls: still running, enhance finished for real in 8s, cut active.
  // Third poll: done.
  let polls = 0;
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r => {
    polls += 1;
    if (polls < 3) {
      return r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'running',
                               progress: { stage: 'cut', done: { enhance: 8.2 } } }) });
    }
    return r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { enhance: 8.2, cut: 14.9 } }) });
  });

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // While running: enhance closed with its real time, cut spinning
  await expect(page.locator('#checkEnhance')).toHaveClass(/done/, { timeout: 10_000 });
  await expect(page.locator('#checkEnhanceTime')).toHaveText('0:08');
  await expect(page.locator('#checkCut')).toHaveClass(/active/);

  // Done: cut closed with its real time
  await expect(page.locator('#checkCut')).toHaveClass(/done/, { timeout: 15_000 });
  await expect(page.locator('#checkCutTime')).toHaveText('0:15');
});

test('steps that never ran are hidden, not estimated', async ({ page }) => {
  await mockAllApis(page);
  // Backend reports only cut (enhance toggle off → never ran)
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { cut: 9.4 } }) }));

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  // The input is visually hidden behind a styled toggle - flip it directly
  await page.evaluate(() => {
    const el = document.getElementById('enhanceAudio');
    el.checked = false;
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#runBtn');

  await expect(page.locator('#checkCut')).toHaveClass(/done/, { timeout: 10_000 });
  await expect(page.locator('#checkCutTime')).toHaveText('0:09');
  await expect(page.locator('#checkEnhance')).toBeHidden();
});

test('AI upscale gets its own live progress row with real times', async ({ page }) => {
  await mockAllApis(page);
  let polls = 0;
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r => {
    polls += 1;
    if (polls < 3) {
      return r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'running',
                               progress: { stage: 'upscale', done: { enhance: 5.0, cut: 9.2 } } }) });
    }
    return r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { enhance: 5.0, cut: 9.2, upscale: 33.4 } }) });
  });

  await page.click('label[for="ev_esrgan"]');
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // While running: upscale row visible, labelled, spinning; earlier steps closed real
  await expect(page.locator('#checkUpscale')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#checkUpscale')).toHaveClass(/active/);
  await expect(page.locator('#checkUpscale .check-label')).toHaveText('שדרוג AI');
  await expect(page.locator('#checkCutTime')).toHaveText('0:09');

  // Done: closed with the backend's real duration
  await expect(page.locator('#checkUpscale')).toHaveClass(/done/, { timeout: 15_000 });
  await expect(page.locator('#checkUpscaleTime')).toHaveText('0:33');
});

test('every enabled tool is listed as pending from the moment the run starts, in order', async ({ page }) => {
  // Rows used to pop into existence only when their step STARTED (auto B-roll
  // and hook appear minutes in, after the editor opens) - the card must show
  // the full plan up front.
  await mockAllApis(page);
  // Hold the upload phase open: with every API mocked to answer instantly the
  // whole run (upload -> process -> editor -> auto tools) finishes before the
  // assertions below - rows are already active/done. Real chunk POSTs get a
  // delay (the index-9999 probe stays instant); last-registered route wins.
  await page.route(/\/upload_chunk\//, async (route, request) => {
    if (request.headers()['x-upload-index'] !== '9999')
      await new Promise(r => setTimeout(r, 3000));
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.evaluate(() => {
    for (const id of ['autoBroll', 'autoHook']) {
      const el = document.getElementById(id);
      if (el && !el.checked) { el.checked = true; el.dispatchEvent(new Event('change', { bubbles: true })); }
    }
  });
  await page.click('#runBtn');
  // Assert during the UPLOAD phase - before any backend stage has begun.
  await expect(page.locator('#checkUpload')).toHaveClass(/active/);
  for (const id of ['checkFinalize', 'checkBroll', 'checkHook']) {
    await expect(page.locator('#' + id)).toBeVisible();
    await expect(page.locator('#' + id)).toHaveClass(/pending/);
  }
  // "Loading preview" (finalize) precedes the background B-roll/hook rows -
  // that is the order the steps actually complete in.
  const order = await page.evaluate(() =>
    [...document.querySelectorAll('.check-item')].map(el => el.id));
  expect(order.indexOf('checkFinalize')).toBeLessThan(order.indexOf('checkBroll'));
  expect(order.indexOf('checkBroll')).toBeLessThan(order.indexOf('checkHook'));
  // Burn belongs to the export step - never pre-listed during processing.
  await expect(page.locator('#checkBurn')).toBeHidden();
});

test('attaching a file reveals a loud card and scrolls it into view', async ({ page }) => {
  await bootApp(page);
  await selectFile(page);
  const card = page.locator('#fileInfo');
  await expect(card).toBeVisible();
  await expect(card).toContainText('test.mp4');
  // The attach must be self-evident: the card scrolls into the viewport
  // (born-below-the-fold attaches used to read as a no-op on phones).
  await expect(card).toBeInViewport();
  // Bold treatment: the strongest border weight on the page (2px olive).
  expect(await card.evaluate((el) => getComputedStyle(el).borderTopWidth)).toBe('2px');
});

test('attach completes even when video metadata never loads (iOS silent-hang guard)', async ({ page }) => {
  // Simulate iOS Safari's Low-Power-Mode behavior: the media element accepts
  // a src but never fires loadedmetadata OR error. Without the probe deadline
  // the attach flow awaited forever - card visible, Run never enabled, no
  // error, no telemetry (iPhone field reports, 2026-08-13).
  await page.addInitScript(() => {
    Object.defineProperty(window.HTMLMediaElement.prototype, 'src', {
      set() { /* swallow - no events, like deferred loading */ },
      get() { return ''; },
    });
  });
  await bootApp(page);
  await page.clock.install();
  await selectFile(page);
  await expect(page.locator('#fileInfo')).toBeVisible();
  await page.clock.fastForward(8000);   // past META_PROBE_DEADLINE_MS
  await expect(page.locator('#runBtn')).toBeEnabled();
  // Size-only detail line (no duration) - the null-meta presentation.
  await expect(page.locator('#fileDetail')).toContainText('MB');
});

// ── "Loading preview" is green only when the preview is FULLY BUFFERED ────
// (2026-09-28, user directive after a 50 MB test: "green check, but play
// keeps buffering"). The whole file must be in memory, the player must be on
// that copy, and that copy must be playable. A streaming source that can
// play never ticks it. Real H.264 fixture; the download route below speaks
// byte ranges like the backend (206 + Content-Range).
const fs = require('fs');
const path = require('path');
const MP4 = fs.readFileSync(path.join(__dirname, 'fixtures/portrait_1080x1920.mp4'));

// mode: 'serve' (bounded JS ranges delayed `delay` ms), 'hold' (only the
// media element's open-ended range is answered; the JS prefetch + its
// single-stream fallback hang forever), 'fail' (those get a 500 instead).
function rangeServer({ mode = 'serve', delay = 0 } = {}) {
  return async (route, request) => {
    const range = request.headers()['range'] || '';
    const media = range === 'bytes=0-';
    if (!media && mode === 'hold') return;
    if (!media && mode === 'fail') return route.fulfill({ status: 500, body: 'nope' });
    if (!media && delay) await new Promise(r => setTimeout(r, delay));
    const m = /^bytes=(\d+)-(\d*)$/.exec(range);
    // The backend exposes these across origins - without it the app's
    // range downloader cannot read the total and treats the reply as truncated.
    const hdr = { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
                  'Access-Control-Expose-Headers': 'content-range, content-length, accept-ranges' };
    if (!m) return route.fulfill({ status: 200, headers: { ...hdr, 'Content-Length': String(MP4.length) }, body: MP4 });
    const start = +m[1], end = m[2] === '' ? MP4.length - 1 : Math.min(+m[2], MP4.length - 1);
    return route.fulfill({ status: 206,
      headers: { ...hdr, 'Content-Range': `bytes ${start}-${end}/${MP4.length}`, 'Content-Length': String(end - start + 1) },
      body: MP4.subarray(start, end + 1) });
  };
}

async function openEditorStreaming(page, server) {
  // Tiny grace + 512-byte ranges: the prefetch is several requests, the
  // editor opens on the STREAM first and the blob lands afterwards.
  await page.addInitScript(() => { window.__PREVIEW_READY_WAIT_MS = 100; window.__PREVIEW_BLOB_WAIT_MS = 0; window.__RANGE_CHUNK = 512; });
  await page.reload();
  await mockAllApis(page);
  await page.route(`${API_BASE}/download/**`, server);
  await page.evaluate(() => {
    // Record what the player was on the instant the row turned green, and
    // every percentage the download bar showed.
    window.__srcAtDone = null; window.__pcts = [];
    const row = document.getElementById('checkFinalize'), vid = document.getElementById('cutVideo');
    new MutationObserver(() => {
      if (row.classList.contains('done') && window.__srcAtDone === null) window.__srcAtDone = vid.src;
    }).observe(row, { attributes: true, attributeFilter: ['class'] });
    const pct = document.getElementById('previewBarPct');
    new MutationObserver(() => { window.__pcts.push(pct.textContent); }).observe(pct, { childList: true, characterData: true, subtree: true });
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
}
const streaming = (page) => page.waitForFunction(() => {
  const v = document.getElementById('cutVideo');
  return v && v.src && !v.src.startsWith('blob:') && v.videoHeight > 0;
}, { timeout: 15_000 });

test('"Loading preview" turns green only once the player is on the fully downloaded copy', async ({ page }) => {
  await openEditorStreaming(page, rangeServer({ delay: 600 }));
  const row = page.locator('#checkFinalize');
  await streaming(page);                       // the stream plays, the download is still going
  await expect(row).toHaveClass(/active/);
  await expect(page.locator('#previewBarRow')).toBeVisible();
  await expect(row).toHaveClass(/done/, { timeout: 20_000 });
  const srcAtDone = await page.evaluate(() => window.__srcAtDone);
  expect(srcAtDone).toMatch(/^blob:/);          // green = in-memory copy, not the network
  expect(await page.evaluate(() => document.getElementById('cutVideo').readyState)).toBeGreaterThanOrEqual(3);
  const pcts = await page.evaluate(() => window.__pcts);
  expect(pcts.length).toBeGreaterThan(1);       // the bar moved
  expect(pcts.some(p => parseInt(p, 10) < 100)).toBe(true);
  await expect(page.locator('#previewBarRow')).toBeHidden();
});

test('a streaming source that can play does not tick "Loading preview"', async ({ page }) => {
  await openEditorStreaming(page, rangeServer({ mode: 'hold' }));
  const row = page.locator('#checkFinalize');
  await streaming(page);
  await page.evaluate(() => document.getElementById('cutVideo').dispatchEvent(new Event('canplay')));
  await page.waitForTimeout(1500);
  await expect(row).toHaveClass(/active/);
  await expect(row).not.toHaveClass(/done/);
});

test('a failed preview download hides the row - the player streams, nothing goes green', async ({ page }) => {
  await openEditorStreaming(page, rangeServer({ mode: 'fail' }));
  const row = page.locator('#checkFinalize');
  await streaming(page);
  await expect(row).toBeHidden({ timeout: 10_000 });
  await expect(row).not.toHaveClass(/done/);
});

test('the backstop hides "Loading preview" instead of painting it green', async ({ page }) => {
  await page.addInitScript(() => { window.__PREVIEW_STEP_CAP_MS = 800; });
  await openEditorStreaming(page, rangeServer({ mode: 'hold' }));
  const row = page.locator('#checkFinalize');
  await expect(row).toBeHidden({ timeout: 5_000 });
  await expect(row).not.toHaveClass(/done/);
});

test('a previous run\'s preview finisher cannot tick the next run\'s row', async ({ page }) => {
  await openEditorStreaming(page, rangeServer({ mode: 'hold' }));
  await expect(page.locator('#checkFinalize')).toHaveClass(/active/);
  // Pick a new file: the checklist resets for the coming run while the old
  // player element (and its pending canplay listener) is still around.
  await selectFile(page, { name: 'second.mp4' });
  await page.waitForSelector('#runBtn:not([disabled])');
  await expect(page.locator('#checkFinalize')).toHaveClass(/pending/);
  await page.evaluate(() => document.getElementById('cutVideo').dispatchEvent(new Event('canplay')));
  await page.waitForTimeout(300);
  await expect(page.locator('#checkFinalize')).toHaveClass(/pending/);
  await expect(page.locator('#checkFinalize')).not.toHaveClass(/done/);
});
