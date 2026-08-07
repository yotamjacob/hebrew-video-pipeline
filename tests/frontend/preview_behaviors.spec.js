/**
 * Preview behaviour tests
 *
 *  #1 Starting any long op (Generate Hook / Find B-Roll / …) pauses the
 *     preview videos.
 *  #3 A caption is visible in the caption player as soon as it loads, without
 *     the user having to scrub.
 *  #4 The hook preview canvas also renders the caption at its position, so hook
 *     and caption placement can be compared.
 *
 * Strategy: serve a real MP4 fixture for /download (drives the player) and a
 * distinctly-coloured JPEG for /thumbnail (drives the hook canvas so a white
 * caption pixel is unambiguous against the teal background).
 */
const { test, expect } = require('@playwright/test');
const fs   = require('fs');
const path = require('path');
const { mockAllApis, selectFile, bootApp, API_BASE } = require('./helpers');

const LANDSCAPE_MP4 = path.join(__dirname, 'fixtures/landscape_1920x1080.mp4');
const PORTRAIT_MP4  = path.join(__dirname, 'fixtures/portrait_1080x1920.mp4');
const TEAL_JPG      = path.join(__dirname, 'fixtures/thumb_landscape.jpg');
const PORTRAIT_JPG  = path.join(__dirname, 'fixtures/thumb_portrait.jpg');

async function mockDownloadWithFile(page, filePath) {
  const buf = fs.readFileSync(filePath);
  await page.route(`${API_BASE}/download/**`, route =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
                 'Content-Length': String(buf.length) },
      body: buf,
    }));
}

async function mockThumbnailWithFile(page, filePath) {
  const buf = fs.readFileSync(filePath);
  await page.route(`${API_BASE}/thumbnail/**`, route =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=300' },
      body: buf,
    }));
}

async function runToEditor(page, videoFixture = PORTRAIT_MP4, thumbFixture = TEAL_JPG) {
  await mockAllApis(page);
  await mockDownloadWithFile(page, videoFixture);
  await mockThumbnailWithFile(page, thumbFixture);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  // Let the player finish sizing itself to the real video before interacting:
  // metadata arrival grows the player and shifts everything below it, so a
  // click landing mid-resize hits the wrong element (mobile especially).
  await page.waitForFunction(() => {
    const v = document.getElementById('cutVideo');
    return v && v.readyState >= 1 && isFinite(v.duration) && v.duration > 0
      && v.clientHeight > 50;
  }, { timeout: 10_000 });
  await page.waitForTimeout(250);   // one settle tick for the layout reflow
}

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

// ── #3 caption visible on load ──────────────────────────────────────────────

test('caption is visible in the player on load without scrubbing', async ({ page }) => {
  await runToEditor(page);
  // The player seeks to the first caption on load, so #playerCap fills in with
  // no user interaction. DEFAULT_CAPTIONS[0].text = 'שלום עולם'.
  await page.waitForFunction(() => {
    const el = document.getElementById('playerCap');
    return el && el.textContent.trim().length > 0;
  }, { timeout: 8_000 });
  const capText = await page.evaluate(() => document.getElementById('playerCap').textContent.trim());
  expect(capText.length).toBeGreaterThan(0);
});

// ── #1 process start pauses preview videos ──────────────────────────────────

test('starting Generate Hook pauses the preview video', async ({ page }) => {
  await runToEditor(page);
  await page.waitForFunction(() => {
    const wrap = document.getElementById('playerWrap');
    return wrap && wrap.offsetHeight > 0;
  }, { timeout: 8_000 });

  // Count pause() calls on media elements from this point on.
  await page.evaluate(() => {
    window.__pausedCount = 0;
    const proto = HTMLMediaElement.prototype;
    const orig = proto.pause;
    proto.pause = function () { window.__pausedCount++; return orig.apply(this, arguments); };
  });

  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForFunction(() => window.__pausedCount > 0, { timeout: 6_000 });
  expect(await page.evaluate(() => window.__pausedCount)).toBeGreaterThan(0);
});

test('starting Find B-Roll pauses the preview video', async ({ page }) => {
  await runToEditor(page);
  await page.waitForFunction(() => {
    const wrap = document.getElementById('playerWrap');
    return wrap && wrap.offsetHeight > 0;
  }, { timeout: 8_000 });

  await page.evaluate(() => {
    window.__pausedCount = 0;
    const proto = HTMLMediaElement.prototype;
    const orig = proto.pause;
    proto.pause = function () { window.__pausedCount++; return orig.apply(this, arguments); };
  });

  await page.click('#tabBtnBroll');
  await page.click('#findBrollBtn');
  await page.waitForFunction(() => window.__pausedCount > 0, { timeout: 6_000 });
  expect(await page.evaluate(() => window.__pausedCount)).toBeGreaterThan(0);
});

// ── #2 B-roll moment shows the relevant transcript quote ────────────────────

test('B-roll moment card shows the verbatim transcript quote for its window', async ({ page }) => {
  await runToEditor(page);
  // Return one moment whose window overlaps DEFAULT_CAPTIONS[1] (3.0-5.1s,
  // 'בדיקה שנייה'). The backend excerpt is a DIFFERENT paraphrase, so the test
  // also proves the caption-derived verbatim quote wins over it.
  await page.route(`${API_BASE}/stock-broll-poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      moments: [{
        start: 3.0, end: 5.1, label: 'רגע', reasoning: 'סיבה',
        transcript_excerpt: 'טקסט אחר לגמרי', broad_search_prompt: 'q', clips: [],
      }],
      video_context: null,
    }) }));

  await page.click('#tabBtnBroll');
  await page.click('#findBrollBtn');
  await page.waitForSelector('.moment-excerpt', { state: 'visible', timeout: 8_000 });
  const quote = await page.evaluate(() => document.querySelector('.moment-excerpt').textContent);
  expect(quote).toContain('בדיקה שנייה');       // actual caption text in the window
  expect(quote).not.toContain('טקסט אחר לגמרי'); // NOT the backend paraphrase
});

// ── #4 hook preview renders the caption ─────────────────────────────────────

test('hook preview canvas renders the caption (white pixels over the teal thumbnail)', async ({ page }) => {
  await runToEditor(page, LANDSCAPE_MP4, TEAL_JPG);
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookControls', { state: 'visible', timeout: 8_000 });

  // Wait until the teal thumbnail has decoded AND a white caption pixel appears
  // in the lower third of the canvas (the caption region).
  await page.waitForFunction(() => {
    const c = document.getElementById('hookPreviewCanvas');
    if (!c || !c.width || !c.height) return false;
    const ctx = c.getContext('2d');
    const y0 = Math.floor(c.height * 0.66);
    const strip = ctx.getImageData(0, y0, c.width, c.height - y0).data;
    let teal = false, white = false;
    for (let i = 0; i < strip.length; i += 4) {
      const r = strip[i], g = strip[i + 1], b = strip[i + 2];
      if (r > 235 && g > 235 && b > 235) white = true;         // caption text
      if (r < 120 && g > 90 && b > 90 && r < g) teal = true;   // thumbnail bg
    }
    return white && teal;
  }, { timeout: 8_000 });

  expect(true).toBe(true);
});

test('hook preview caption is sized by the real video, not the 400px thumbnail', async ({ page }) => {
  // Portrait video (1080x1920) with a portrait thumbnail whose pixel size
  // differs from the video. The caption size must track the video (like the
  // editor: captionFontSize * canvasHeight / videoHeight), NOT the thumbnail
  // width (the old bug oversized it).
  await runToEditor(page, PORTRAIT_MP4, PORTRAIT_JPG);
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookControls', { state: 'visible', timeout: 8_000 });
  await page.waitForFunction(() => window.__hookCapFs > 0, { timeout: 8_000 });

  const d = await page.evaluate(() => ({
    capFs:   window.__hookCapFs,
    canvasW: document.getElementById('hookPreviewCanvas').width,
    canvasH: document.getElementById('hookPreviewCanvas').height,
    videoH:  document.getElementById('cutVideo').videoHeight,
    fs:      parseInt(document.getElementById('captionFontSizeSlider').value, 10),
  }));
  const expected = d.fs * d.canvasH / d.videoH;             // editor proportion
  expect(Math.abs(d.capFs - expected)).toBeLessThan(1);     // matches the editor
  // Sanity: it must NOT be the old thumbnail-width-scaled size, which would be
  // far larger (thumbnail is only 400px / here 720px wide vs a 1080px video).
  expect(d.capFs).toBeLessThan(d.fs * d.canvasW / 400);
});

// Mean vertical position (0=top .. 1=bottom) of white caption pixels on the
// hook canvas. Returns null if no caption pixels are found.
async function captionCentroidY(page) {
  return page.evaluate(() => {
    const c = document.getElementById('hookPreviewCanvas');
    const ctx = c.getContext('2d');
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let sum = 0, n = 0;
    for (let y = 0; y < c.height; y++) {
      for (let x = 0; x < c.width; x++) {
        const i = (y * c.width + x) * 4;
        if (d[i] > 235 && d[i + 1] > 235 && d[i + 2] > 235) { sum += y; n++; }
      }
    }
    return n ? (sum / n) / c.height : null;
  });
}

test('hook preview caption follows the caption position slider', async ({ page }) => {
  await runToEditor(page, LANDSCAPE_MP4, TEAL_JPG);
  // Semantic clicks: this test asserts canvas PIXEL positions; on mobile
  // emulation the landscape player's late reflows race pointer hit-testing
  // (see hook.spec note). Tap physics on these tabs is covered elsewhere.
  await page.locator('#tabBtnHook').dispatchEvent('click');
  await page.locator('#generateHookBtn').dispatchEvent('click');
  await page.waitForSelector('#hookControls', { state: 'visible', timeout: 8_000 });
  await page.waitForFunction(() => {
    const c = document.getElementById('hookPreviewCanvas');
    return c && c.width && c.height;
  }, { timeout: 8_000 });

  // Default position is near the bottom → caption sits low on the canvas.
  const before = await captionCentroidY(page);
  expect(before).not.toBeNull();

  // Drag the caption ON the player toward the top (the rail is gone - captions
  // are repositioned directly on the preview now).
  await page.locator('#playerWrap').scrollIntoViewIfNeeded();
  const wrap = await page.locator('#playerWrap').boundingBox();
  const cap  = await page.locator('#playerCap').boundingBox();
  await page.mouse.move(cap.x + cap.width / 2, cap.y + cap.height / 2);
  await page.mouse.down();
  await page.mouse.move(wrap.x + wrap.width / 2, wrap.y + wrap.height * 0.15, { steps: 8 });
  await page.mouse.up();

  await page.waitForFunction(
    (b) => {
      const c = document.getElementById('hookPreviewCanvas');
      const ctx = c.getContext('2d');
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let sum = 0, n = 0;
      for (let y = 0; y < c.height; y++)
        for (let x = 0; x < c.width; x++) {
          const i = (y * c.width + x) * 4;
          if (d[i] > 235 && d[i + 1] > 235 && d[i + 2] > 235) { sum += y; n++; }
        }
      return n && (sum / n) / c.height < b - 0.1;   // moved up by ≥10% of height
    },
    before,
    { timeout: 6_000 },
  );

  const after = await captionCentroidY(page);
  expect(after).toBeLessThan(before - 0.1);
});
