/**
 * Measure exact font size ratio between preview and burn by rendering
 * the same text at both resolutions and measuring pixel heights.
 */
const { chromium } = require('@playwright/test');
const VIDEO_IN = '/Users/yotamjacob/hebrew-video-pipeline/input_vids/original.mp4';
const APP_URL  = 'https://site-theta-six-76.vercel.app';

(async () => {
  // Test at multiple viewport widths to see if clientWidth changes
  for (const vpWidth of [375, 768, 1280]) {
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: vpWidth, height: 900 }, acceptDownloads: false });
    const page = await ctx.newPage();

    await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await page.locator('#runBtn').waitFor({ state: 'visible', timeout: 20_000 });
    await page.locator('input[type="file"]').setInputFiles(VIDEO_IN);
    await page.waitForTimeout(800);
    await page.locator('#runBtn').click();
    await page.locator('#captionBody').waitFor({ state: 'visible', timeout: 600_000 });
    await page.waitForTimeout(4000);

    const metrics = await page.evaluate(() => {
      const vid = document.getElementById('cutVideo');
      const wrap = document.getElementById('playerWrap');
      const cap = document.getElementById('playerCap');
      return {
        videoWidth:  vid?.videoWidth,
        videoHeight: vid?.videoHeight,
        vidClientW:  vid?.clientWidth,
        vidClientH:  vid?.clientHeight,
        wrapW:       wrap?.clientWidth,
        wrapH:       wrap?.clientHeight,
        capFontSize: cap ? window.getComputedStyle(cap).fontSize : null,
        capMaxWidth: cap ? window.getComputedStyle(cap).maxWidth : null,
        wrapAR:      wrap ? window.getComputedStyle(wrap).aspectRatio : null,
      };
    });

    const scale = metrics.vidClientW / metrics.videoWidth;
    const cssFontRaw = 48 * scale;
    const cssFontRounded = Math.round(cssFontRaw);
    const assFontEquiv = 48 * metrics.vidClientW / metrics.videoWidth; // same as cssFontRaw

    console.log(`\n=== viewport ${vpWidth}px ===`);
    console.log(`video: ${metrics.videoWidth}x${metrics.videoHeight}`);
    console.log(`vid element: ${metrics.vidClientW}x${metrics.vidClientH}`);
    console.log(`wrap: ${metrics.wrapW}x${metrics.wrapH}  aspect-ratio: ${metrics.wrapAR}`);
    console.log(`scale = ${scale.toFixed(4)}`);
    console.log(`CSS font (raw): ${cssFontRaw.toFixed(2)}px  (rounded: ${cssFontRounded}px)`);
    console.log(`ASS equivalent in preview space: ${assFontEquiv.toFixed(2)}px`);
    console.log(`Round inflation: ${((cssFontRounded / cssFontRaw - 1)*100).toFixed(1)}%`);

    await browser.close();
  }
})().catch(err => { console.error(err); process.exit(1); });
