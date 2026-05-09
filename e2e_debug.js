/**
 * Debug: log actual clientWidth, scale, fontSize, maxWidth used in preview
 */
const { chromium } = require('@playwright/test');

const APP_URL  = 'https://site-theta-six-76.vercel.app';
const VIDEO_IN = '/Users/yotamjacob/hebrew-video-pipeline/input_vids/original.mp4';

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx     = await browser.newContext({ acceptDownloads: true });
  const page    = await ctx.newPage();

  // Intercept console from page
  page.on('console', m => console.log('  [page]', m.text()));

  console.log('Loading app…');
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.locator('#runBtn').waitFor({ state: 'visible', timeout: 20_000 });

  await page.locator('input[type="file"]').setInputFiles(VIDEO_IN);
  await page.waitForTimeout(800);
  await page.locator('#runBtn').click();
  console.log('✓ Processing…');

  await page.locator('#captionBody').waitFor({ state: 'visible', timeout: 600_000 });
  await page.waitForTimeout(4000);

  // Play briefly to trigger timeupdate, then read computed values
  const info = await page.locator('#cutVideo').evaluate(v => {
    v.currentTime = 1.5;
    v.play();
    return new Promise(res => {
      v.addEventListener('timeupdate', () => {
        v.pause();
        const cap = document.querySelector('#captionOverlay');
        res({
          videoWidth:  v.videoWidth,
          videoHeight: v.videoHeight,
          clientWidth: v.clientWidth,
          clientHeight: v.clientHeight,
          computedFontSize: cap ? window.getComputedStyle(cap).fontSize : null,
          computedMaxWidth: cap ? window.getComputedStyle(cap).maxWidth : null,
          captionText: cap ? cap.textContent : null,
        });
      }, { once: true });
    });
  });

  console.log('\n=== Video element metrics ===');
  console.log('videoWidth:', info.videoWidth, '  videoHeight:', info.videoHeight);
  console.log('clientWidth:', info.clientWidth, '  clientHeight:', info.clientHeight);
  console.log('scale (clientWidth/videoWidth):', (info.clientWidth / info.videoWidth).toFixed(4));
  console.log('scale (clientHeight/videoHeight):', (info.clientHeight / info.videoHeight).toFixed(4));
  console.log('computed fontSize:', info.computedFontSize);
  console.log('computed maxWidth:', info.computedMaxWidth);
  console.log('caption text:', JSON.stringify(info.captionText));

  await browser.close();
})().catch(err => { console.error('\n✗', err.message || err); process.exit(1); });
