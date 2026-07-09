/**
 * Preview orientation tests
 *
 * The previews must follow the ACTUAL orientation of the uploaded video:
 *   - portrait input (9:16)  → previews are taller than wide
 *   - landscape input (16:9) → previews are wider than tall
 *   - the player box aspect-ratio equals videoWidth / videoHeight (no crop)
 *
 * Two previews are covered:
 *   1. the caption editor player (#playerWrap / #cutVideo)
 *   2. the hook design preview canvas (#hookPreviewCanvas), which cover-fits
 *      the source thumbnail and so must resize to the thumbnail's orientation.
 *
 * Strategy: mock /download/** with a real MP4 fixture (drives the player) and
 * /thumbnail/** with a real JPEG fixture (drives the hook canvas), then wait
 * for the elements to receive their dimensions and assert orientation.
 */
const { test, expect } = require('@playwright/test');
const fs   = require('fs');
const path = require('path');
const { mockAllApis, selectFile, bootApp } = require('./helpers');

const PORTRAIT_MP4     = path.join(__dirname, 'fixtures/portrait_1080x1920.mp4');
const LANDSCAPE_MP4    = path.join(__dirname, 'fixtures/landscape_1920x1080.mp4');
const ROTATED_META_MP4 = path.join(__dirname, 'fixtures/rotated_meta_90.mp4');
const LANDSCAPE_JPG    = path.join(__dirname, 'fixtures/thumb_landscape.jpg');
const PORTRAIT_JPG     = path.join(__dirname, 'fixtures/thumb_portrait.jpg');

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

/** Replace the generic thumbnail mock with one that serves a real JPEG file. */
async function mockThumbnailWithFile(page, filePath) {
  const buf = fs.readFileSync(filePath);
  await page.route(`${API_BASE}/thumbnail/**`, route =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=300' },
      body: buf,
    })
  );
}

/**
 * Run the upload flow and wait for the caption player to appear.
 * Optionally override the thumbnail so the hook preview can be exercised too.
 */
async function runAndWaitForPlayer(page, videoFixture, thumbFixture) {
  await mockAllApis(page);                        // sets all mocks including generic download/thumbnail
  await mockDownloadWithFile(page, videoFixture); // overrides download with real file
  if (thumbFixture) await mockThumbnailWithFile(page, thumbFixture);
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
  await bootApp(page);
});

// ── Caption player: orientation follows the video ──────────────────────────

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

test('landscape video: playerWrap aspect-ratio matches videoWidth/videoHeight', async ({ page }) => {
  await runAndWaitForPlayer(page, LANDSCAPE_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  // The box must mirror the source ratio (no forced 9/16, no crop).
  expect(dims.wrapAR).toBe(`${dims.videoWidth} / ${dims.videoHeight}`);
  const boxAR   = dims.wrapW / dims.wrapH;
  const videoAR = dims.videoWidth / dims.videoHeight;
  expect(Math.abs(boxAR - videoAR)).toBeLessThan(0.05);
});

test('portrait video: playerWrap aspect-ratio matches videoWidth/videoHeight', async ({ page }) => {
  await runAndWaitForPlayer(page, PORTRAIT_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  expect(dims.wrapAR).toBe(`${dims.videoWidth} / ${dims.videoHeight}`);
});

// The player must reflect whatever orientation the browser actually renders
// for a rotated-metadata clip (data-driven: follow videoWidth vs videoHeight,
// never a hard-coded portrait frame).
test('rotated-metadata video: playerWrap orientation follows the rendered video', async ({ page }) => {
  await runAndWaitForPlayer(page, ROTATED_META_MP4);
  await waitForPlayerSized(page);
  const dims = await readVideoDimensions(page);
  console.log('[rotated-meta wrap]', JSON.stringify(dims));
  expect(dims.wrapAR).toBe(`${dims.videoWidth} / ${dims.videoHeight}`);
  if (dims.videoWidth > dims.videoHeight) {
    expect(dims.wrapW).toBeGreaterThan(dims.wrapH);
  } else {
    expect(dims.wrapH).toBeGreaterThan(dims.wrapW);
  }
});

// ── Hook design preview canvas: orientation follows the source thumbnail ────

/** Wait for the hook preview canvas to be resized off its default 270×480. */
async function readHookCanvas(page) {
  return page.evaluate(() => {
    const c = document.getElementById('hookPreviewCanvas');
    return c ? { w: c.width, h: c.height } : null;
  });
}

test('landscape video: hook preview canvas becomes landscape', async ({ page }) => {
  await runAndWaitForPlayer(page, LANDSCAPE_MP4, LANDSCAPE_JPG);
  // drawHookPreview resizes the canvas once the thumbnail image decodes.
  await page.waitForFunction(() => {
    const c = document.getElementById('hookPreviewCanvas');
    return c && c.height < c.width;
  }, { timeout: 8_000 });
  const dims = await readHookCanvas(page);
  console.log('[hook canvas landscape]', JSON.stringify(dims));
  expect(dims.w).toBeGreaterThan(dims.h);
  // 16:9 source thumbnail → 16:9 canvas
  expect(Math.abs(dims.w / dims.h - 16 / 9)).toBeLessThan(0.1);
});

test('portrait video: hook preview canvas stays portrait', async ({ page }) => {
  await runAndWaitForPlayer(page, PORTRAIT_MP4, PORTRAIT_JPG);
  await page.waitForFunction(() => {
    const c = document.getElementById('hookPreviewCanvas');
    return c && c.height > c.width;
  }, { timeout: 8_000 });
  const dims = await readHookCanvas(page);
  console.log('[hook canvas portrait]', JSON.stringify(dims));
  expect(dims.h).toBeGreaterThan(dims.w);
});
