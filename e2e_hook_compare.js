/**
 * Hook visual comparison test:
 * 1. Upload & process video
 * 2. Generate hook options (clicks "Generate Hook Options", waits for AI)
 * 3. Select first hook, set font size = 130%, position = 15% from top
 * 4. Screenshot hook preview canvas (expected)
 * 5. Burn and download
 * 6. Extract frame at hook timestamp
 * 7. Pixel-measure text block position in both and compare
 */
const { chromium } = require('@playwright/test');
const path = require('path');
const fs   = require('fs');
const { execSync } = require('child_process');

const APP_URL  = 'https://site-theta-six-76.vercel.app';
const VIDEO_IN = '/Users/yotamjacob/hebrew-video-pipeline/input_vids/original.mp4';
const OUT      = '/Users/yotamjacob/hebrew-video-pipeline/exports';
const FFMPEG   = '/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg';
fs.mkdirSync(OUT, { recursive: true });

function log(msg) { console.log(`[${new Date().toISOString().slice(11,19)}] ${msg}`); }

function measureTextBand(imgPath, label) {
  const { execFileSync } = require('child_process');
  // Use Python PIL to pixel-scan
  const script = `
from PIL import Image
img = Image.open(${JSON.stringify(imgPath)}).convert('RGB')
w, h = img.size
pixels = list(img.getdata())
text_rows = []
for y in range(h):
    row = pixels[y*w:(y+1)*w]
    white = sum(1 for r,g,b in row if r>200 and g>200 and b>200)
    if white > max(3, w*0.04):
        text_rows.append(y)
if text_rows:
    top, bot = text_rows[0], text_rows[-1]
    print(f"{w}x{h} text={top}-{bot} center={((top+bot)//2)} centerPct={(top+bot)/2/h*100:.1f}")
else:
    print(f"{w}x{h} NO_TEXT")
`.trim();
  const out = execFileSync('python3', ['-c', script]).toString().trim();
  console.log(`[measure] ${label}: ${out}`);
  const m = out.match(/(\d+)x(\d+) text=(\d+)-(\d+) center=(\d+) centerPct=(\S+)/);
  if (!m) return null;
  return { w: +m[1], h: +m[2], top: +m[3], bot: +m[4], center: +m[5], centerPct: +m[6] };
}

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 80 });
  const ctx     = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
  const page    = await ctx.newPage();

  // ── 1. Load & upload ─────────────────────────────────────────────────────────
  log('Loading app…');
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.locator('#runBtn').waitFor({ state: 'visible', timeout: 20_000 });
  await page.locator('input[type="file"]').setInputFiles(VIDEO_IN);
  await page.waitForTimeout(600);

  // ── 2. Process ───────────────────────────────────────────────────────────────
  log('Processing (waiting for captions)…');
  await page.locator('#runBtn').click();
  await page.locator('#captionBody').waitFor({ state: 'visible', timeout: 600_000 });
  await page.waitForTimeout(3000);
  log('Processing done.');

  // ── 3. Generate hook options ──────────────────────────────────────────────────
  log('Expanding hook card and generating hook options…');
  // hookCard is shown after processing; scroll to it
  await page.locator('#hookCard').waitFor({ state: 'visible', timeout: 10_000 });
  await page.locator('#hookCard').scrollIntoViewIfNeeded();
  await page.waitForTimeout(500);

  const genBtn = page.locator('#generateHookBtn');
  await genBtn.waitFor({ state: 'visible', timeout: 10_000 });
  const isDisabled = await genBtn.isDisabled();
  if (isDisabled) {
    log('  generateHookBtn is still disabled — waiting 3s…');
    await page.waitForTimeout(3000);
  }
  await genBtn.click();
  log('  Clicked "Generate Hook Options". Waiting up to 2 min for results…');
  // Options appear as divs with id="hookOption0", "hookOption1", etc.
  await page.locator('[id^="hookOption"]').first()
    .waitFor({ state: 'visible', timeout: 120_000 });
  await page.waitForTimeout(800);
  log('  Hook options received.');

  // ── 4. Select first hook option ───────────────────────────────────────────────
  log('Selecting first hook option…');
  await page.locator('#hookOption0').click();
  await page.waitForTimeout(800);

  // Log what hook text is selected
  const hookText = await page.locator('#hookText0').inputValue().catch(() => '');
  log(`  Hook text: "${hookText}"`);

  // ── 5. Set hook controls ──────────────────────────────────────────────────────
  log('Setting hook font size = 130%, position = 15…');
  await page.locator('#hookFontSize').evaluate(el => {
    el.value = '130';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(200);

  await page.locator('#hookPosition').evaluate(el => {
    el.value = '15';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.waitForTimeout(300);

  // ── 6. Read preview metrics from canvas ──────────────────────────────────────
  const previewMetrics = await page.evaluate(() => {
    const canvas = document.getElementById('hookPreviewCanvas');
    const vid    = document.getElementById('cutVideo');
    const fontSizeSlider  = document.getElementById('hookFontSize');
    const posSlider       = document.getElementById('hookPosition');
    const hookFontColor   = document.getElementById('hookFontColor');
    return {
      canvasW:      canvas?.width,
      canvasH:      canvas?.height,
      fontSizePct:  fontSizeSlider ? parseInt(fontSizeSlider.value) : null,
      position:     posSlider      ? parseInt(posSlider.value)      : null,
      fontColor:    hookFontColor?.value,
      vidVideoW:    vid?.videoWidth,
      vidVideoH:    vid?.videoHeight,
      vidClientW:   vid?.clientWidth,
      vidClientH:   vid?.clientHeight,
    };
  });
  log('\n=== Preview metrics ===');
  console.log(JSON.stringify(previewMetrics, null, 2));

  // Compute expected preview font size and position
  const { canvasW, canvasH, fontSizePct, position } = previewMetrics;
  const sizePct   = (fontSizePct || 100) / 100;
  const vPosFrac  = (position || 10) / 100;
  const previewFontPx = Math.max(12, Math.round(Math.min(canvasW, canvasH) * 0.075 * sizePct));
  const edgePad   = previewFontPx;
  const rawCY     = edgePad + (canvasH - 2 * edgePad) * vPosFrac;
  const centerPct = rawCY / canvasH * 100;
  log(`Preview font: min(${canvasW},${canvasH})*0.075*${sizePct} = ${previewFontPx}px`);
  log(`Preview center Y: ${rawCY.toFixed(1)}px / ${canvasH}px = ${centerPct.toFixed(1)}% from top`);

  // ── 7. Screenshot hook canvas ─────────────────────────────────────────────────
  const canvasEl = page.locator('#hookPreviewCanvas');
  const canvasShot = path.join(OUT, '05_hook_preview_expected.png');
  if (await canvasEl.isVisible({ timeout: 2000 }).catch(() => false)) {
    await canvasEl.screenshot({ path: canvasShot });
    log(`Screenshot saved: 05_hook_preview_expected.png`);
  } else {
    log('Hook canvas not visible!');
  }

  // Full page for context
  await page.screenshot({ path: path.join(OUT, '06_full_page_hook_configured.png') });

  // ── 8. Intercept burn URL ─────────────────────────────────────────────────────
  let burnUrlStr = null;
  await page.route(/\/burn\//, async (route, request) => {
    if (request.method() === 'POST') burnUrlStr = request.url();
    await route.fallback();
  });

  // ── 9. Burn ──────────────────────────────────────────────────────────────────
  log('\nStarting burn…');
  await page.locator('#runBtn').click();
  const confirmOk = page.locator('#confirmOk');
  if (await confirmOk.isVisible({ timeout: 3000 }).catch(() => false)) {
    await confirmOk.click();
  }
  const download = await page.waitForEvent('download', { timeout: 600_000 });
  const outMp4 = path.join(OUT, 'hook_burn_output.mp4');
  await download.saveAs(outMp4);
  log(`Download saved → ${outMp4}`);

  // ── 10. Parse burn URL ────────────────────────────────────────────────────────
  let hookParams = null;
  if (burnUrlStr) {
    const u = new URL(burnUrlStr);
    log('\n=== Burn URL params ===');
    log(`font_size: ${u.searchParams.get('font_size')}`);
    log(`margin_v:  ${u.searchParams.get('margin_v')}`);
    const hookRaw = u.searchParams.get('hook');
    if (hookRaw) {
      hookParams = JSON.parse(decodeURIComponent(hookRaw));
      log(`hook.font_size_pct:     ${hookParams.font_size_pct}`);
      log(`hook.vertical_position: ${hookParams.vertical_position}`);
      log(`hook.font_color:        ${hookParams.font_color}`);
      log(`hook.text:              "${hookParams.text?.slice(0,60)}"`);

      // Compute expected burn position
      const { vidVideoW: vw, vidVideoH: vh } = previewMetrics;
      const h_fsize_base = Math.max(24, Math.round(Math.min(vw, vh) * 0.075 * (hookParams.font_size_pct / 100)));
      const h_fsize      = Math.max(24, Math.round(h_fsize_base * 1.20));
      const h_pad        = h_fsize_base;
      const h_vpos       = hookParams.vertical_position / 100;
      const h_y          = h_pad + (vh - 2 * h_pad) * h_vpos;
      const n_lines      = hookParams.text?.split('\\N').length || 1;
      const block_h      = n_lines * h_fsize * 1.35;
      const h_margin_v   = Math.max(0, h_y - block_h / 2);
      const burnCenterPct = h_y / vh * 100;

      log(`\n=== Burn position math ===`);
      log(`Video: ${vw}x${vh}`);
      log(`h_fsize_base = max(24, min(${vw},${vh})*0.075*${hookParams.font_size_pct/100}) = ${h_fsize_base}px`);
      log(`h_fsize      = ${h_fsize_base} * 1.20 = ${h_fsize}px`);
      log(`h_pad        = ${h_pad}px`);
      log(`h_vpos       = ${hookParams.vertical_position}% = ${h_vpos}`);
      log(`h_y (center) = ${h_pad} + (${vh} - ${2*h_pad}) * ${h_vpos} = ${h_y.toFixed(1)}px = ${burnCenterPct.toFixed(1)}% from top`);
      log(`block_h      = ${n_lines} * ${h_fsize} * 1.35 = ${block_h.toFixed(0)}px`);
      log(`h_margin_v   = ${h_margin_v.toFixed(0)}px (top of text block from top of frame)`);

      log(`\n=== Position comparison ===`);
      log(`Preview center: ${centerPct.toFixed(1)}% from top`);
      log(`Burn center:    ${burnCenterPct.toFixed(1)}% from top`);
      log(`Delta:          ${Math.abs(centerPct - burnCenterPct).toFixed(1)}%  ${Math.abs(centerPct - burnCenterPct) < 2 ? '✓ match' : '✗ MISMATCH'}`);
    } else {
      log('No hook param in burn URL — hook not included in burn!');
    }
  }

  // ── 11. Extract hook frame ────────────────────────────────────────────────────
  const hookStart = hookParams?.start_seconds ?? 1.0;
  const hookFrameTime = hookStart + 0.5;
  const hookFrame = path.join(OUT, '07_hook_burn_frame.png');
  execSync(`${FFMPEG} -y -ss ${hookFrameTime} -i "${outMp4}" -vframes 1 -update 1 "${hookFrame}" 2>/dev/null`);
  log(`\nSaved hook burn frame at t=${hookFrameTime}s → 07_hook_burn_frame.png`);

  // ── 12. Pixel-measure positions ───────────────────────────────────────────────
  log('\n=== Pixel measurements ===');
  const previewMeasure = measureTextBand(canvasShot, 'Hook preview canvas');
  const burnMeasure    = measureTextBand(hookFrame,  'Hook burn frame');

  if (previewMeasure && burnMeasure) {
    log(`\nPreview center: ${previewMeasure.centerPct}% from top`);
    log(`Burn center:    ${burnMeasure.centerPct}% from top`);
    log(`Delta:          ${Math.abs(previewMeasure.centerPct - burnMeasure.centerPct).toFixed(1)}%`);

    log(`\nPreview text height: ${previewMeasure.bot - previewMeasure.top}px / ${previewMeasure.h}px = ${((previewMeasure.bot - previewMeasure.top)/previewMeasure.h*100).toFixed(1)}%`);
    log(`Burn text height:    ${burnMeasure.bot - burnMeasure.top}px / ${burnMeasure.h}px = ${((burnMeasure.bot - burnMeasure.top)/burnMeasure.h*100).toFixed(1)}%`);
  }

  await browser.close();
  log('\nDone. All files saved to exports/');
})().catch(err => {
  console.error('\n✗', err.message || err);
  process.exit(1);
});
