// Scratch probe: with a red-fill/white-border caption style, what does the
// live overlay actually compute for color and stroke?
const { test } = require('@playwright/test');
const fs = require('fs');
const path = require('path');
const { mockAllApis, selectFile, bootApp, API_BASE } = require('./helpers');

const PORTRAIT_MP4 = path.join(__dirname, 'fixtures/portrait_1080x1920.mp4');
const CAPS = [{ start: 0.5, end: 2.3, text: 'שלום עולם' }];

test('inspect live overlay computed styles', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page, { captions: CAPS });
  const buf = fs.readFileSync(PORTRAIT_MP4);
  await page.route(`${API_BASE}/download/**`, r =>
    r.fulfill({ status: 200,
                headers: { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
                           'Content-Length': String(buf.length) }, body: buf }));
  await page.route(/\/preview_frame/, r => r.abort());

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  // Set the caption style: RED fill, WHITE border - like the user's video.
  await page.evaluate(() => {
    document.getElementById('capFontColor').value = '#FF0000';
    document.getElementById('capBorderColor').value = '#FFFFFF';
    document.getElementById('capFontColor').dispatchEvent(new Event('input', { bubbles: true }));
  });

  await page.evaluate(() => {
    const v = document.getElementById('cutVideo');
    v.pause(); v.currentTime = 1.0;
  });
  await page.waitForFunction(() =>
    document.getElementById('playerCap')?.textContent?.includes('שלום עולם'), { timeout: 6_000 });

  const styles = await page.evaluate(() => {
    const el = document.getElementById('playerCap');
    const cs = getComputedStyle(el);
    return {
      inlineColor: el.style.color,
      inlineStroke: el.style.webkitTextStroke,
      computedColor: cs.color,
      computedFill: cs.webkitTextFillColor,
      computedStrokeColor: cs.webkitTextStrokeColor,
      computedStrokeWidth: cs.webkitTextStrokeWidth,
      paintOrder: cs.paintOrder,
      payload: (() => { return {
        font: document.getElementById('capFontColor').value,
        border: document.getElementById('capBorderColor').value }; })(),
    };
  });
  console.log('STYLES:', JSON.stringify(styles, null, 2));
});
