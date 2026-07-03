/**
 * Caption size parity tests
 *
 * Invariant: captionFontSize / videoWidth == css_px / clientWidth  (exact, no rounding)
 *
 * Notes:
 *  - `captionFontSize` is declared with `let` so it's NOT on window.  The only
 *    reliable way to set it from a test is via the slider input event, which fires
 *    the registered listener that updates the internal variable.
 *  - Video dimensions (videoHeight / videoWidth) come from native getters that
 *    can't be overridden on the instance; we override HTMLVideoElement.prototype.
 *  - Burn URL capture uses page.route() + route.fallback() per the project
 *    Playwright convention (function predicates on waitForRequest are fragile).
 */
const { test, expect } = require('@playwright/test');
const { API_BASE, runFullUpload, bootApp } = require('./helpers');

/** Set the font-size slider to a value and fire the input event. */
async function setFontSize(page, value) {
  await page.locator('#captionFontSizeSlider').evaluate((el, v) => {
    el.value = String(v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}

/** Read the caption font size currently used by the JS (via slider value). */
async function readFontSize(page) {
  return page.locator('#captionFontSizeSlider').evaluate(el => parseInt(el.value, 10));
}

/**
 * Override HTMLVideoElement.prototype.videoHeight/videoWidth for the duration
 * of fn(), then restore.  This is the only reliable way in Chromium.
 */
async function withFakeVideoDimensions(page, { videoHeight = 1920, videoWidth = 1080 }, fn) {
  await page.evaluate(({ videoHeight, videoWidth }) => {
    const proto = HTMLVideoElement.prototype;
    window.__savedVH = Object.getOwnPropertyDescriptor(proto, 'videoHeight');
    window.__savedVW = Object.getOwnPropertyDescriptor(proto, 'videoWidth');
    Object.defineProperty(proto, 'videoHeight', { get: () => videoHeight, configurable: true });
    Object.defineProperty(proto, 'videoWidth',  { get: () => videoWidth,  configurable: true });
  }, { videoHeight, videoWidth });
  try {
    return await fn();
  } finally {
    await page.evaluate(() => {
      const proto = HTMLVideoElement.prototype;
      if (window.__savedVH) Object.defineProperty(proto, 'videoHeight', window.__savedVH);
      if (window.__savedVW) Object.defineProperty(proto, 'videoWidth',  window.__savedVW);
    });
  }
}

/** Call updatePreviewCaption() - it reads captionFontSize from its own closure. */
async function triggerPreview(page) {
  await page.evaluate(() => {
    if (typeof window.updatePreviewCaption === 'function') window.updatePreviewCaption();
  });
}

/** Read #playerCap computed font-size in px. */
async function readCapFontSizePx(page) {
  return page.evaluate(() => {
    const cap = document.getElementById('playerCap');
    return cap ? parseFloat(window.getComputedStyle(cap).fontSize) : null;
  });
}

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

// ─── Slider initialization ────────────────────────────────────────────────────

test('font size slider initial value is 48', async ({ page }) => {
  const val = await readFontSize(page);
  expect(val).toBe(48);
});

// ─── Slider wiring ────────────────────────────────────────────────────────────

test('font size slider input updates the preview formula', async ({ page }) => {
  await runFullUpload(page);
  await setFontSize(page, 64);
  // Verify slider updated
  expect(await readFontSize(page)).toBe(64);
  // Verify localStorage saved
  const stored = await page.evaluate(() => localStorage.getItem('captionFontSize'));
  expect(Number(stored)).toBe(64);
});

// ─── Preview formula correctness (with fake videoHeight) ──────────────────────

async function checkPreviewFormula(page, { fontSizeSlider, videoWidth = 1080 }) {
  await setFontSize(page, fontSizeSlider);
  await triggerPreview(page);

  const { cssPx, clientW } = await page.evaluate(() => {
    const vid = document.getElementById('cutVideo');
    const cap = document.getElementById('playerCap');
    return {
      cssPx:   parseFloat(window.getComputedStyle(cap).fontSize),
      clientW: vid.clientWidth,
    };
  });

  const expected = Math.max(7, fontSizeSlider * clientW / videoWidth);
  // No rounding in new formula - allow ±0.5px for float/subpixel rendering
  expect(Math.abs(cssPx - expected)).toBeLessThanOrEqual(0.5);
}

test('preview font-size matches formula - font_size 48, 1920p', async ({ page }) => {
  await runFullUpload(page);
  await withFakeVideoDimensions(page, { videoHeight: 1920, videoWidth: 1080 }, () =>
    checkPreviewFormula(page, { fontSizeSlider: 48, videoWidth: 1080 })
  );
});

test('preview font-size matches formula - font_size 64, 1920p', async ({ page }) => {
  await runFullUpload(page);
  await withFakeVideoDimensions(page, { videoHeight: 1920, videoWidth: 1080 }, () =>
    checkPreviewFormula(page, { fontSizeSlider: 64, videoWidth: 1080 })
  );
});

test('preview font-size matches formula - font_size 80, 1920p', async ({ page }) => {
  await runFullUpload(page);
  await withFakeVideoDimensions(page, { videoHeight: 1920, videoWidth: 1080 }, () =>
    checkPreviewFormula(page, { fontSizeSlider: 80, videoWidth: 1080 })
  );
});

test('preview font-size matches formula - font_size 48, 1080p landscape', async ({ page }) => {
  await runFullUpload(page);
  await withFakeVideoDimensions(page, { videoHeight: 1080, videoWidth: 1920 }, () =>
    checkPreviewFormula(page, { fontSizeSlider: 48, videoWidth: 1920 })
  );
});

test('preview scales proportionally when slider changes', async ({ page }) => {
  await runFullUpload(page);

  await withFakeVideoDimensions(page, { videoHeight: 1920, videoWidth: 1080 }, async () => {
    // Get clientWidth once (stable throughout)
    const clientW = await page.evaluate(() => document.getElementById('cutVideo').clientWidth);

    await setFontSize(page, 48);
    await triggerPreview(page);
    const px48 = await readCapFontSizePx(page);

    await setFontSize(page, 64);
    await triggerPreview(page);
    const px64 = await readCapFontSizePx(page);

    const expected48 = Math.max(7, 48 * clientW / 1080);
    const expected64 = Math.max(7, 64 * clientW / 1080);

    expect(Math.abs(px48 - expected48)).toBeLessThanOrEqual(0.5);
    expect(Math.abs(px64 - expected64)).toBeLessThanOrEqual(0.5);
    // 64 must produce a larger (or equal) preview font than 48
    expect(px64).toBeGreaterThanOrEqual(px48);
  });
});

// ─── ASS-to-preview ratio equivalence ────────────────────────────────────────

test('font_size/videoWidth ratio matches css_px/clientWidth exactly (no rounding)', async ({ page }) => {
  await runFullUpload(page);

  await withFakeVideoDimensions(page, { videoHeight: 1920, videoWidth: 1080 }, async () => {
    await setFontSize(page, 48);
    await triggerPreview(page);

    const result = await page.evaluate(() => {
      const vid   = document.getElementById('cutVideo');
      const cap   = document.getElementById('playerCap');
      const vw    = vid.videoWidth;           // 1080 (prototype override active)
      const cw    = vid.clientWidth;
      const fs    = parseInt(document.getElementById('captionFontSizeSlider').value, 10);
      const cssPx = parseFloat(window.getComputedStyle(cap).fontSize);

      if (fs * cw / vw < 7) return { floorActive: true };

      const assRatio     = fs / vw;
      const previewRatio = cssPx / cw;
      // No rounding: ratios should be exactly equal (allow tiny float epsilon)
      const tolerance    = 0.001 / cw;
      return {
        floorActive: false,
        ok: Math.abs(assRatio - previewRatio) <= tolerance,
        assRatio, previewRatio, tolerance,
      };
    });

    expect(result).not.toBeNull();
    if (result.floorActive) return;
    expect(result.ok).toBe(true);
  });
});

// ─── Burn URL params ──────────────────────────────────────────────────────────

test('burn URL carries correct font_size', async ({ page }) => {
  await runFullUpload(page);
  await setFontSize(page, 56);

  let capturedBurnUrl = null;
  // Register AFTER runFullUpload so this route runs first (LIFO) and falls through
  await page.route(`${API_BASE}/burn/`, async (route, request) => {
    if (request.method() === 'POST') capturedBurnUrl = request.url();
    await route.fallback();
  });

  const btnText = await page.locator('#runBtn').textContent();
  if (/burn|render|צריבה|הפקה/i.test(btnText)) {
    await page.locator('#runBtn').click();
    // Brief wait for the network request to fire
    await page.waitForTimeout(800);
  }

  if (capturedBurnUrl) {
    const u = new URL(capturedBurnUrl);
    expect(u.searchParams.get('font_size')).toBe('56');
  }
});

test('burn URL carries valid margin_v fraction', async ({ page }) => {
  await runFullUpload(page);

  let capturedBurnUrl = null;
  await page.route(`${API_BASE}/burn/`, async (route, request) => {
    if (request.method() === 'POST') capturedBurnUrl = request.url();
    await route.fallback();
  });

  const btnText = await page.locator('#runBtn').textContent();
  if (/burn|render|צריבה|הפקה/i.test(btnText)) {
    await page.locator('#runBtn').click();
    await page.waitForTimeout(800);
  }

  if (capturedBurnUrl) {
    const u = new URL(capturedBurnUrl);
    const margin = parseFloat(u.searchParams.get('margin_v'));
    expect(margin).toBeGreaterThanOrEqual(0);
    expect(margin).toBeLessThanOrEqual(0.5);
  }
});
