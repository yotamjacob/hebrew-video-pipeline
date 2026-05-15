/**
 * Player orientation tests
 *
 * Verifies that the caption player container (#playerWrap) is sized as
 * portrait when the video is portrait (9:16) and landscape when 16:9.
 *
 * Strategy: mock the /download/** route to serve a real MP4 fixture file,
 * then wait for the video element to receive its dimensions and check that
 * the wrapper has height > width (portrait) or width > height (landscape).
 */
const { test, expect } = require('@playwright/test');
const fs   = require('fs');
const path = require('path');
const { mockAllApis, selectFile } = require('./helpers');

const PORTRAIT_MP4  = path.join(__dirname, 'fixtures/portrait_1080x1920.mp4');
const LANDSCAPE_MP4 = path.join(__dirname, 'fixtures/landscape_1920x1080.mp4');
const ROTATED_META_MP4 = path.join(__dirname, 'fixtures/rotated_meta_90.mp4');

const API_BASE = 'https://yotamjacob--hebrew-video-pipeline-api.modal.run';

/** Replace the generic download mock with one that serves a real video file. */
async function mockDownloadWithFile(page, filePath) {
  const buf = fs.readFileSync(filePath);
  await page.route(`${API_BASE}/download/**`, route =>
    route.fulfill({
      status: 200,
      headers: {
        'Content-Type':  'video/mp4',
        'Accept-Ranges': 'bytes',
        'Content-Length': String(buf.length),
      },
      body: buf,
    })
  );
}

/** Run the upload flow and wait for the caption player to appear. */
async function runAndWaitForPlayer(page, videoFixture) {
  await mockAllApis(page);                        // sets all mocks including generic download
  await mockDownloadWithFile(page, videoFixture); // overrides download with real file
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
}

/** Wait for the player wrapper to get a non-zero height (set by loadedmetadata handler). */
async function waitForPlayerSized(page, timeout = 8_000) {
  await page.waitForFunction(() => {
    const wrap = document.getElementById('playerWrap');
    return wrap && wrap.offsetHeight > 0 && wrap.offsetWidth > 0;
  }, { timeout });
}

/** Read the video element's intrinsic and rendered dimensions. */
async function readVideoDimensions(page) {
  return page.evaluate(() => {
    const vid  = document.getElementById('cutVideo');
    const wrap = document.getElementById('playerWrap');
    if (!vid || !wrap) return null;
    return {
      videoWidth:   vid.videoWidth,
      videoHeight:  vid.videoHeight,
      clientWidth:  vid.clientWidth,
      clientHeight: vid.clientHeight,
      wrapW: wrap.offsetWidth,
      wrapH: wrap.offsetHeight,
      wrapAR: wrap.style.aspectRatio,
    };
  });
}

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

// ── Diagnostic: log actual values from Chromium ────────────────────────────

test('portrait video: log actual videoWidth/Height and rendered size', async ({ page }) => {
  await runAndWaitForPlayer(page, PORTRAIT_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  console.log('[portrait dims]', JSON.stringify(dims));
  expect(dims).not.toBeNull();
});

test('landscape video: log actual videoWidth/Height and rendered size', async ({ page }) => {
  await runAndWaitForPlayer(page, LANDSCAPE_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  console.log('[landscape dims]', JSON.stringify(dims));
  expect(dims).not.toBeNull();
});

// ── Orientation assertions ──────────────────────────────────────────────────

test('portrait video: playerWrap is taller than wide', async ({ page }) => {
  await runAndWaitForPlayer(page, PORTRAIT_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  console.log('[portrait wrap]', JSON.stringify(dims));
  expect(dims.wrapH).toBeGreaterThan(dims.wrapW);
});

test('landscape video: playerWrap is wider than tall', async ({ page }) => {
  await runAndWaitForPlayer(page, LANDSCAPE_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  console.log('[landscape wrap]', JSON.stringify(dims));
  expect(dims.wrapW).toBeGreaterThan(dims.wrapH);
});

test('rotated-metadata video: log what Chrome reports', async ({ page }) => {
  await runAndWaitForPlayer(page, ROTATED_META_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  console.log('[rotated-meta dims]', JSON.stringify(dims));
  expect(dims).not.toBeNull();
});

// Chrome does NOT swap videoWidth/videoHeight for format-level rotate= tags (only for
// tkhd display-matrix rotation as used by real iPhones).  The pipeline's render() bakes
// rotation in via ffmpeg transpose and clears the tag, so processed videos are always
// baked-pixel orientation and never reach this code path.
// Correct browser behaviour: landscape (1920×1080) reported and displayed as landscape.
test('rotated-metadata video: playerWrap is wider than tall (Chrome treats format-level rotate as landscape)', async ({ page }) => {
  await runAndWaitForPlayer(page, ROTATED_META_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  console.log('[rotated-meta wrap]', JSON.stringify(dims));
  expect(dims.wrapW).toBeGreaterThan(dims.wrapH);
});
