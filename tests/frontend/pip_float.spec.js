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

// This spec asserts the FLIP transform itself - restore real motion (the
// suite default is reducedMotion: 'reduce', which makes the FLIP a no-op).
test.use({ reducedMotion: 'no-preference' });

test('docking the player FLIPs (transform present mid-flight, cleared after)', async ({ page }) => {
  // This one runs a full upload + process and then loads a REAL mp4 before it
  // can even start measuring, plus ~1.4s of settle waits. The default 15s
  // budget ran out mid-test on CI, which surfaced as the dock wait timing out.
  test.setTimeout(45_000);
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

  // Dock = sentinel above the topbar AND the editor card still on screen, so
  // park the sentinel just past the topbar. Anchoring on the caption list
  // instead was flaky: its position depends on the portrait player's height,
  // which is only known once the video sizes, and the resulting target could
  // overshoot the card entirely (scrolling PAST the editor undocks again).
  await page.evaluate(() => {
    const sentinel = document.getElementById('playerStickySentinel');
    const topbar = document.querySelector('.app-topbar');
    const tbH = topbar ? topbar.offsetHeight : 52;
    window.scrollTo(0, sentinel.getBoundingClientRect().top + window.scrollY - tbH + 120);
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

test('any editor text edit hides the docked mini-player - hook fields included', async ({ page }) => {
  test.setTimeout(45_000);
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

  // Dock the player (same anchoring as the FLIP test above).
  await page.evaluate(() => {
    const sentinel = document.getElementById('playerStickySentinel');
    const topbar = document.querySelector('.app-topbar');
    const tbH = topbar ? topbar.offsetHeight : 52;
    window.scrollTo(0, sentinel.getBoundingClientRect().top + window.scrollY - tbH + 120);
  });
  await page.waitForFunction(() =>
    document.getElementById('captionPlayer').classList.contains('is-stuck'), { timeout: 5000 });

  // Focusing a hook-panel textarea (created exactly like the generated hook
  // options are) must undock - pre-fix only #captionsList fields did.
  await page.evaluate(() => {
    switchEditorTab('hook');
    const opts = document.getElementById('hookOptions');
    opts.style.display = 'block';
    const ta = document.createElement('textarea');
    ta.id = 'hookTextProbe';
    opts.appendChild(ta);
    ta.focus({ preventScroll: true });
  });
  await page.waitForFunction(() =>
    !document.getElementById('captionPlayer').classList.contains('is-stuck'), { timeout: 3000 });

  // Blur → the mini-player comes back (scroll position unchanged).
  await page.evaluate(() => document.getElementById('hookTextProbe').blur());
  await page.waitForFunction(() =>
    document.getElementById('captionPlayer').classList.contains('is-stuck'), { timeout: 3000 });

  // A non-text control must NOT hide it (only text-entry fields count).
  // Stays on the SAME tab: switching tabs reshapes the page and can undock
  // via the scroll observers on slow CI runners - unrelated to this check.
  // The undock/redock layout shifts can also land the sentinel right at the
  // 40px hysteresis band and legitimately drop the dock - so re-establish a
  // decisively docked, SETTLED state (re-scroll + hold through a quiet
  // window) before asserting anything about the range-input focus.
  await expect.poll(async () => {
    await page.evaluate(() => {
      const sentinel = document.getElementById('playerStickySentinel');
      const topbar = document.querySelector('.app-topbar');
      const tbH = topbar ? topbar.offsetHeight : 52;
      window.scrollTo(0, sentinel.getBoundingClientRect().top + window.scrollY - tbH + 120);
    });
    await page.waitForTimeout(900);
    return page.evaluate(() =>
      document.getElementById('captionPlayer').classList.contains('is-stuck'));
  }, { timeout: 20_000 }).toBe(true);
  await page.evaluate(() => {
    document.getElementById('hookBgOpacity').focus({ preventScroll: true });
  });
  await page.waitForTimeout(400);
  expect(await page.evaluate(() =>
    document.getElementById('captionPlayer').classList.contains('is-stuck'))).toBe(true);
});
