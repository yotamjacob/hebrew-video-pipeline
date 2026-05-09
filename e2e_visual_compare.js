/**
 * Visual comparison test:
 * 1. Upload & process video
 * 2. Set caption font size = 64, hook font size = 150%, hook color = #FFFF00 (yellow)
 * 3. Screenshot the preview (expected state)
 * 4. Burn and download
 * 5. Extract frame at hook timestamp and a caption timestamp
 * 6. Save everything to exports/ and report metrics
 */
const { chromium } = require('@playwright/test');
const path = require('path');
const fs   = require('fs');
const { execSync } = require('child_process');

const APP_URL  = 'https://site-theta-six-76.vercel.app';
const VIDEO_IN = '/Users/yotamjacob/hebrew-video-pipeline/input_vids/original.mp4';
const OUT      = '/Users/yotamjacob/hebrew-video-pipeline/exports';
const FFMPEG   = '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg';
const FFPROBE  = '/opt/homebrew/opt/ffmpeg-full/bin/ffprobe';
fs.mkdirSync(OUT, { recursive: true });

function log(msg) { console.log(`[${new Date().toISOString().slice(11,19)}] ${msg}`); }

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 100 });
  const ctx     = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
  const page    = await ctx.newPage();

  // ── 1. Load app ──────────────────────────────────────────────────────────────
  log('Loading app…');
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.locator('#runBtn').waitFor({ state: 'visible', timeout: 20_000 });

  // ── 2. Upload video ──────────────────────────────────────────────────────────
  log('Uploading video…');
  await page.locator('input[type="file"]').setInputFiles(VIDEO_IN);
  await page.waitForTimeout(800);

  // ── 3. Process ───────────────────────────────────────────────────────────────
  log('Processing (waiting for captions — up to 10 min)…');
  await page.locator('#runBtn').click();
  await page.locator('#captionBody').waitFor({ state: 'visible', timeout: 600_000 });
  await page.waitForTimeout(4000); // let video load, thumbnail fetch, etc.
  log('Processing complete.');

  // ── 4a. Set CAPTION font size = 64 ──────────────────────────────────────────
  log('Setting caption font size = 64…');
  await page.locator('#captionFontSizeSlider').evaluate(el => {
    el.value = '64';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(300);

  // ── 4b. Set HOOK font size = 150%, color = yellow ───────────────────────────
  // First pick the first hook option if available
  const hookOption = page.locator('.hook-option').first();
  if (await hookOption.isVisible({ timeout: 3000 }).catch(() => false)) {
    log('Selecting first hook option…');
    await hookOption.click();
    await page.waitForTimeout(500);
  }

  log('Setting hook font size = 150%…');
  await page.locator('#hookFontSize').evaluate(el => {
    el.value = '150';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(200);

  log('Setting hook font color = #FFFF00 (yellow)…');
  await page.locator('#hookFontColor').evaluate(el => {
    el.value = '#FFFF00';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.waitForTimeout(300);

  // ── 5. Screenshot PREVIEW state (expected) ───────────────────────────────────
  log('Screenshotting preview state…');

  // Caption preview — seek to first caption
  await page.locator('#cutVideo').evaluate(v => {
    v.currentTime = 2.0;
  });
  await page.waitForTimeout(800);
  await page.locator('#captionPlayer').screenshot({
    path: path.join(OUT, '01_caption_preview_expected.png')
  });
  log('  Saved 01_caption_preview_expected.png');

  // Hook canvas preview
  const hookCanvas = page.locator('#hookPreviewCanvas');
  if (await hookCanvas.isVisible({ timeout: 2000 }).catch(() => false)) {
    await hookCanvas.screenshot({ path: path.join(OUT, '02_hook_preview_expected.png') });
    log('  Saved 02_hook_preview_expected.png');
  } else {
    log('  Hook canvas not visible — skipping');
  }

  // Full page screenshot for context
  await page.screenshot({ path: path.join(OUT, '00_full_page_before_burn.png'), fullPage: false });
  log('  Saved 00_full_page_before_burn.png');

  // ── 6. Read preview metrics ──────────────────────────────────────────────────
  const metrics = await page.evaluate(() => {
    const vid = document.getElementById('cutVideo');
    const cap = document.getElementById('playerCap');
    const canvas = document.getElementById('hookPreviewCanvas');
    const hookFontSizeSlider = document.getElementById('hookFontSize');
    return {
      captionFontSize:    parseInt(document.getElementById('captionFontSizeSlider').value, 10),
      vidClientW:         vid?.clientWidth,
      vidClientH:         vid?.clientHeight,
      vidVideoW:          vid?.videoWidth,
      vidVideoH:          vid?.videoHeight,
      capCSSpx:           cap ? parseFloat(window.getComputedStyle(cap).fontSize) : null,
      capText:            cap?.textContent?.slice(0, 60),
      hookCanvasW:        canvas?.width,
      hookCanvasH:        canvas?.height,
      hookFontSizePct:    hookFontSizeSlider ? parseInt(hookFontSizeSlider.value, 10) : null,
      hookFontColor:      document.getElementById('hookFontColor')?.value,
      hookPosition:       document.getElementById('hookPosition')?.value,
    };
  });

  log('\n=== Preview metrics ===');
  console.log(JSON.stringify(metrics, null, 2));

  // Expected caption CSS px formula
  const expectedCapPx = metrics.captionFontSize * metrics.vidClientW / metrics.vidVideoW;
  log(`\nCaption formula: ${metrics.captionFontSize} * ${metrics.vidClientW} / ${metrics.vidVideoW} = ${expectedCapPx.toFixed(2)}px`);
  log(`Caption actual CSS: ${metrics.capCSSpx?.toFixed(2)}px`);
  log(`Caption size match: ${Math.abs((metrics.capCSSpx||0) - expectedCapPx) < 0.5 ? '✓' : '✗ MISMATCH'}`);

  // Expected hook font formula:  min(canvasW, canvasH) * 0.075 * sizePct/100
  const hookSizePct = (metrics.hookFontSizePct || 100) / 100;
  const expectedHookPx = Math.max(12, Math.round(Math.min(metrics.hookCanvasW, metrics.hookCanvasH) * 0.075 * hookSizePct));
  log(`\nHook canvas: ${metrics.hookCanvasW} x ${metrics.hookCanvasH}`);
  log(`Hook formula: min(${metrics.hookCanvasW},${metrics.hookCanvasH}) * 0.075 * ${hookSizePct} = ${expectedHookPx}px (preview)`);

  // ── 7. Intercept burn URL ────────────────────────────────────────────────────
  let burnUrl = null;
  await page.route(/\/burn\//, async (route, request) => {
    if (request.method() === 'POST') burnUrl = request.url();
    await route.fallback();
  });

  // ── 8. Burn ──────────────────────────────────────────────────────────────────
  log('\nStarting burn…');
  await page.locator('#runBtn').click();
  const confirmOk = page.locator('#confirmOk');
  if (await confirmOk.isVisible({ timeout: 3000 }).catch(() => false)) {
    await confirmOk.click();
  }

  const download = await page.waitForEvent('download', { timeout: 600_000 });
  const outMp4 = path.join(OUT, 'burn_output.mp4');
  await download.saveAs(outMp4);
  log(`Download saved → ${outMp4}`);

  // ── 9. Parse burn URL ────────────────────────────────────────────────────────
  if (burnUrl) {
    const u = new URL(burnUrl);
    const burnFontSize = parseInt(u.searchParams.get('font_size') || '0', 10);
    const burnMarginV  = parseFloat(u.searchParams.get('margin_v') || '0');
    const burnHook     = u.searchParams.get('hook');

    log('\n=== Burn URL params ===');
    log(`font_size:  ${burnFontSize}`);
    log(`margin_v:   ${burnMarginV}`);
    log(`font:       ${u.searchParams.get('font')}`);
    if (burnHook) {
      try {
        const h = JSON.parse(decodeURIComponent(burnHook));
        log(`hook.font_size_pct: ${h.font_size_pct}`);
        log(`hook.font_color:    ${h.font_color}`);
        log(`hook.vertical_pos:  ${h.vertical_position}`);
      } catch (e) { log(`hook (raw): ${burnHook.slice(0, 100)}`); }
    }

    // Caption: burn sends font_size * 1.10 vs preview
    const burnCapPx = burnFontSize * metrics.vidClientW / metrics.vidVideoW;
    log(`\nCaption burn font in preview-space: ${burnCapPx.toFixed(2)}px`);
    log(`Caption preview CSS: ${metrics.capCSSpx?.toFixed(2)}px`);
    log(`Caption size match: ${Math.abs(burnCapPx - (metrics.capCSSpx||0)) < 1 ? '✓' : '✗ MISMATCH'}`);
  }

  // ── 10. Extract frames from burned video ─────────────────────────────────────
  log('\nExtracting frames from burned video…');

  // Get video info
  const probeOut = execSync(`${FFPROBE} -v quiet -print_format json -show_streams "${outMp4}" 2>/dev/null`).toString();
  const probe    = JSON.parse(probeOut);
  const vs       = probe.streams.find(s => s.codec_type === 'video');
  log(`Burned video: ${vs?.width}x${vs?.height}, duration ${vs?.duration}s`);

  // Caption frame at t=2s
  const capFrame = path.join(OUT, '03_burn_caption_frame_t2s.png');
  execSync(`${FFMPEG} -y -ss 2.0 -i "${outMp4}" -vframes 1 -update 1 "${capFrame}" 2>/dev/null`);
  log(`  Saved 03_burn_caption_frame_t2s.png`);

  // Hook frame at t=1.5s (hook usually starts at ~1s)
  const hookFrame = path.join(OUT, '04_burn_hook_frame_t1s.png');
  execSync(`${FFMPEG} -y -ss 1.0 -i "${outMp4}" -vframes 1 -update 1 "${hookFrame}" 2>/dev/null`);
  log(`  Saved 04_burn_hook_frame_t1s.png`);

  // ── 11. Summary ───────────────────────────────────────────────────────────────
  log('\n=== FILES SAVED ===');
  fs.readdirSync(OUT).sort().forEach(f => log(`  exports/${f}`));

  log('\n=== KEY COMPARISONS ===');
  log(`Caption font size slider:  ${metrics.captionFontSize}`);
  log(`Caption preview CSS px:    ${metrics.capCSSpx?.toFixed(2)}`);
  log(`Caption burn font_size:    ${burnUrl ? new URL(burnUrl).searchParams.get('font_size') : 'N/A'}`);
  log(`  (expected = slider*1.10 = ${Math.round(metrics.captionFontSize * 1.10)})`);
  log(``);
  log(`Hook canvas: ${metrics.hookCanvasW}x${metrics.hookCanvasH}`);
  log(`Hook font size%: ${metrics.hookFontSizePct}`);
  log(`Hook color in preview: ${metrics.hookFontColor}`);
  log(`Burned video resolution: ${vs?.width}x${vs?.height}`);
  log(`  Hook burn font_size_base = min(${vs?.width},${vs?.height})*0.075*${hookSizePct} = ${Math.max(24, Math.round(Math.min(vs?.width||0, vs?.height||0)*0.075*hookSizePct))}`);
  log(`  Hook burn font_size (1.20x) = ${Math.max(24, Math.round(Math.max(24, Math.round(Math.min(vs?.width||0, vs?.height||0)*0.075*hookSizePct))*1.20))}`);

  await browser.close();
  log('\nDone.');
})().catch(err => {
  console.error('\n✗', err.message || err);
  process.exit(1);
});
