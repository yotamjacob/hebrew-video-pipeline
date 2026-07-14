/**
 * PiP dock FLIP regression: docking/undocking the floating mini-player must
 * animate a real transform from the player's previous position (the smooth
 * "fly to the corner"), and leave NO residual transform once settled.
 */
const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const { API_BASE, mockAllApis, selectFile, bootApp } = require('./helpers');

const MP4 = path.join(__dirname, 'fixtures/portrait_1080x1920.mp4');

test('docking the player FLIPs (transform present mid-flight, cleared after)', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  const buf = fs.readFileSync(MP4);
  await page.route(`${API_BASE}/download/**`, r =>
    r.fulfill({ status: 200, headers: { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
                'Content-Length': String(buf.length) }, body: buf }));
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  await page.waitForFunction(() => {
    const w = document.getElementById('playerWrap');
    return w && w.offsetHeight > 100;
  });

  // Watch the wrap for any inline transform applied during the dock.
  await page.evaluate(() => {
    window.__sawTransform = false;
    const wrap = document.getElementById('playerWrap');
    const obs = new MutationObserver(() => {
      if (wrap.style.transform && wrap.style.transform !== 'none') window.__sawTransform = true;
    });
    obs.observe(wrap, { attributes: true, attributeFilter: ['style'] });
  });

  // Scroll deep into the editor so the sentinel passes the topbar → dock.
  await page.evaluate(() => {
    const list = document.getElementById('captionsList');
    window.scrollTo(0, list.getBoundingClientRect().top + window.scrollY + 300);
  });
  await page.waitForFunction(() =>
    document.getElementById('captionPlayer').classList.contains('is-stuck'), { timeout: 5000 });

  const sawTransform = await page.evaluate(() => window.__sawTransform);
  expect(sawTransform).toBe(true);

  // After the animation settles the transform must be cleared (no residual scale).
  await page.waitForTimeout(700);
  const residual = await page.evaluate(() => document.getElementById('playerWrap').style.transform);
  expect(residual === '' || residual === 'none').toBeTruthy();

  // (screenshot removed - behavior asserted above)

  // Scroll back up → undock, FLIP back, transform cleared again.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForFunction(() =>
    !document.getElementById('captionPlayer').classList.contains('is-stuck'), { timeout: 5000 });
  await page.waitForTimeout(700);
  const residual2 = await page.evaluate(() => document.getElementById('playerWrap').style.transform);
  expect(residual2 === '' || residual2 === 'none').toBeTruthy();
});
