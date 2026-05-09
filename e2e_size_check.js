/**
 * Verify font_size in burn URL matches preview, and pixel-measure both.
 * Sets slider to 70, position near top, then burns and compares.
 */
const { chromium } = require('@playwright/test');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');

const APP_URL  = 'https://site-theta-six-76.vercel.app';
const VIDEO_IN = '/Users/yotamjacob/hebrew-video-pipeline/input_vids/original.mp4';
const OUT_DIR  = '/tmp/e2e_out';
fs.mkdirSync(OUT_DIR, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx     = await browser.newContext({ acceptDownloads: true });
  const page    = await ctx.newPage();

  // ── Load and process ────────────────────────────────────────────────────────
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.locator('#runBtn').waitFor({ state: 'visible', timeout: 20_000 });
  await page.locator('input[type="file"]').setInputFiles(VIDEO_IN);
  await page.waitForTimeout(800);
  await page.locator('#runBtn').click();
  console.log('Processing…');
  await page.locator('#captionBody').waitFor({ state: 'visible', timeout: 600_000 });
  await page.waitForTimeout(4000);

  // ── Set font size slider to 70 ───────────────────────────────────────────
  await page.locator('#captionFontSizeSlider').evaluate(el => {
    el.value = '70';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(300);

  // ── Read what the preview is actually showing ────────────────────────────
  const previewMetrics = await page.evaluate(() => {
    const vid = document.getElementById('cutVideo');
    const cap = document.getElementById('playerCap');
    return {
      captionFontSize: parseInt(document.getElementById('captionFontSizeSlider').value),
      vidClientW: vid.clientWidth,
      vidVideoW:  vid.videoWidth,
      capFontPx:  cap ? parseFloat(window.getComputedStyle(cap).fontSize) : null,
      captionMarginPct: window.captionMarginPct,
    };
  });
  console.log('\n=== Preview metrics ===');
  console.log(previewMetrics);
  const expectedBurnFontEquiv = previewMetrics.captionFontSize * previewMetrics.vidClientW / previewMetrics.vidVideoW;
  console.log(`Preview CSS font: ${previewMetrics.capFontPx?.toFixed(2)}px`);
  console.log(`Expected (formula): ${expectedBurnFontEquiv.toFixed(2)}px`);
  console.log(`Match: ${Math.abs((previewMetrics.capFontPx || 0) - expectedBurnFontEquiv) < 0.5 ? '✓' : '✗'}`);

  // ── Play video to trigger caption in preview, screenshot ─────────────────
  await page.locator('#cutVideo').evaluate(v => { v.currentTime = 1.5; v.play(); });
  await page.waitForTimeout(500);
  await page.locator('#cutVideo').evaluate(v => v.pause());
  await page.waitForTimeout(200);
  const previewShot = path.join(OUT_DIR, 'size_check_preview.png');
  const playerEl = page.locator('#captionPlayer');
  if (await playerEl.isVisible()) {
    await playerEl.screenshot({ path: previewShot });
  }

  // ── Intercept burn URL to log what's sent ────────────────────────────────
  let capturedBurnUrl = null;
  await page.route(/\/burn\//, async (route, request) => {
    capturedBurnUrl = request.url();
    await route.fallback();
  });

  // ── Burn ─────────────────────────────────────────────────────────────────
  await page.locator('#runBtn').click();
  const confirmOk = page.locator('#confirmOk');
  if (await confirmOk.isVisible({ timeout: 3000 }).catch(() => false)) {
    await confirmOk.click();
  }
  const download = await page.waitForEvent('download', { timeout: 600_000 });
  const outMp4 = path.join(OUT_DIR, 'size_check_burn.mp4');
  await download.saveAs(outMp4);

  console.log('\n=== Burn URL params ===');
  if (capturedBurnUrl) {
    const u = new URL(capturedBurnUrl);
    console.log('font_size:', u.searchParams.get('font_size'));
    console.log('margin_v: ', u.searchParams.get('margin_v'));
    console.log('font:     ', u.searchParams.get('font'));
    const fs_val = parseInt(u.searchParams.get('font_size') || '48');
    const vw = previewMetrics.vidVideoW;
    const cw = previewMetrics.vidClientW;
    console.log(`\nBurn font in preview-space: ${(fs_val * cw / vw).toFixed(2)}px`);
    console.log(`Preview CSS font:            ${previewMetrics.capFontPx?.toFixed(2)}px`);
    console.log(`Size match: ${Math.abs(fs_val * cw / vw - (previewMetrics.capFontPx || 0)) < 0.5 ? '✓ perfect' : '✗ mismatch'}`);
  }

  // ── Extract burn frame ────────────────────────────────────────────────────
  const burnFrame = path.join(OUT_DIR, 'size_check_burn_frame.png');
  execSync(`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -y -ss 1.5 -i "${outMp4}" -vframes 1 -update 1 "${burnFrame}" 2>/dev/null`);
  console.log(`\nScreenshots saved:`);
  console.log(`  Preview: ${previewShot}`);
  console.log(`  Burn frame: ${burnFrame}`);

  await browser.close();
})().catch(err => { console.error('\n✗', err.message || err); process.exit(1); });
