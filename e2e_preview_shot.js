/**
 * Quick screenshot: load a pre-processed page, play video briefly,
 * capture caption preview, compare with burned frame.
 */
const { chromium } = require('@playwright/test');
const path = require('path');
const fs   = require('fs');

const APP_URL  = 'https://site-theta-six-76.vercel.app';
const VIDEO_IN = '/Users/yotamjacob/hebrew-video-pipeline/input_vids/original.mp4';
const OUT_DIR  = '/tmp/e2e_out';
fs.mkdirSync(OUT_DIR, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx     = await browser.newContext({ acceptDownloads: true });
  const page    = await ctx.newPage();

  page.on('console', m => {
    if (m.type() === 'error') console.error('  [page error]', m.text().slice(0, 120));
  });

  console.log('Loading app…');
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.locator('#runBtn').waitFor({ state: 'visible', timeout: 20_000 });
  console.log('✓ App loaded');

  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(VIDEO_IN);
  await page.waitForTimeout(800);

  await page.locator('#runBtn').click();
  console.log('✓ Pipeline started — waiting…');

  await page.locator('#captionBody').waitFor({ state: 'visible', timeout: 600_000 });
  console.log('✓ Caption editor visible');

  // Wait for video metadata to load
  await page.waitForTimeout(4000);

  // Play the video to trigger timeupdate caption rendering
  await page.locator('#cutVideo').evaluate(v => {
    v.currentTime = 1.5;
    v.play();
  });
  await page.waitForTimeout(600); // let timeupdate fire and caption render

  // Pause it so it doesn't move during screenshot
  await page.locator('#cutVideo').evaluate(v => v.pause());
  await page.waitForTimeout(200);

  // Screenshot the caption player
  const previewShot = path.join(OUT_DIR, 'preview_with_caption.png');
  const playerEl = page.locator('#captionPlayer');
  if (await playerEl.isVisible()) {
    await playerEl.screenshot({ path: previewShot });
    console.log(`✓ Preview screenshot → ${previewShot}`);
  } else {
    console.log('  captionPlayer not visible, screenshotting full page');
    await page.screenshot({ path: previewShot });
  }

  await browser.close();
  console.log('Done.');
})().catch(err => {
  console.error('\n✗', err.message || err);
  process.exit(1);
});
