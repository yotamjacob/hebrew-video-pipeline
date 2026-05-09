/**
 * Real end-to-end: upload original.mp4 via the live app, process,
 * screenshot the preview, burn, download the output, open it.
 */
const { chromium } = require('@playwright/test');
const path = require('path');
const fs   = require('fs');
const { execSync } = require('child_process');

const APP_URL  = 'https://site-theta-six-76.vercel.app';
const VIDEO_IN = path.resolve('input_vids/original.mp4');
const OUT_DIR  = '/tmp/e2e_out';
fs.mkdirSync(OUT_DIR, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx     = await browser.newContext({ acceptDownloads: true });
  const page    = await ctx.newPage();

  page.on('console', m => {
    if (m.type() === 'error') console.error('  [page error]', m.text().slice(0, 120));
  });

  // 1. Load app
  console.log('Loading app…');
  await page.goto(APP_URL, { waitUntil: 'domcontentloaded', timeout: 30_000 });
  await page.locator('#runBtn').waitFor({ state: 'visible', timeout: 20_000 });
  console.log('✓ App loaded');

  // 2. Select video file
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(VIDEO_IN);
  await page.waitForTimeout(800);
  console.log('✓ File selected');

  // 3. Click "Run Pipeline"
  await page.locator('#runBtn').click();
  console.log('✓ Pipeline started — waiting for transcription + captions (may take 2-3 min)…');

  // 4. Wait for caption editor to appear
  await page.locator('#captionBody').waitFor({ state: 'visible', timeout: 600_000 });
  console.log('✓ Processing complete — caption editor visible');

  // Wait for video metadata and aspect ratio to correct
  await page.waitForTimeout(4000);

  // Seek to first caption so overlay is visible
  await page.locator('#cutVideo').evaluate(v => { v.currentTime = 1.3; });
  await page.waitForTimeout(1500);

  // 5. Screenshot the preview player with caption overlay
  const previewShot = path.join(OUT_DIR, 'preview_caption.png');
  const playerEl = page.locator('#captionPlayer');
  if (await playerEl.isVisible()) {
    await playerEl.screenshot({ path: previewShot });
  } else {
    await page.screenshot({ path: previewShot });
  }
  console.log(`✓ Preview screenshot → ${previewShot}`);

  // 6. Click burn (#runBtn is now in burn mode after processing)
  const runBtn = page.locator('#runBtn');
  const btnText = await runBtn.textContent();
  console.log(`  Burn button text: "${btnText?.trim()}"`);
  await runBtn.click();

  // Dismiss confirmation modal if it appears
  const confirmOk = page.locator('#confirmOk');
  if (await confirmOk.isVisible({ timeout: 3000 }).catch(() => false)) {
    await confirmOk.click();
    console.log('✓ Confirmed burn dialog');
  }
  console.log('✓ Burn started…');

  // 7. Wait for the download (triggerDownload creates a <a> and clicks it)
  const download = await page.waitForEvent('download', { timeout: 600_000 });
  const outMp4 = path.join(OUT_DIR, 'original_edited.mp4');
  await download.saveAs(outMp4);
  const sizeMB = (fs.statSync(outMp4).size / 1024 / 1024).toFixed(1);
  console.log(`✓ Downloaded → ${outMp4}  (${sizeMB} MB)`);

  // 8. Screenshot final state
  const afterShot = path.join(OUT_DIR, 'after_burn.png');
  await page.screenshot({ path: afterShot });
  console.log(`✓ Post-burn screenshot → ${afterShot}`);

  await browser.close();
  console.log('\nOpening video…');
  execSync(`open "${outMp4}"`);
})().catch(err => {
  console.error('\n✗', err.message || err);
  process.exit(1);
});
