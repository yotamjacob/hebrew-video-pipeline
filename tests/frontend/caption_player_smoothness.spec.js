/**
 * Caption player smoothness tests
 *
 * These lock the behaviours that make the caption editor pleasant instead of
 * glitchy:
 *   - jumping to any point shows the correct caption immediately (on 'seeked'),
 *     including empty in the gaps between captions;
 *   - during playback the caption overlay is NEVER redundantly rewritten (the
 *     old code re-set textContent + re-scanned every row on every ~4 Hz
 *     'timeupdate', which flickered/janked) - it only changes at segment
 *     boundaries;
 *   - playback advances the progress bar via a rAF loop that STOPS on pause
 *     (no runaway loop, no frozen-but-still-ticking bar);
 *   - play/pause cleanly toggles the control state.
 *
 * A real MP4 fixture is served for /download and short captions that all fit
 * inside its 2 s duration are injected so seeks/boundaries are reachable.
 */
const { test, expect } = require('@playwright/test');
const fs   = require('fs');
const path = require('path');
const { mockAllApis, selectFile, bootApp, API_BASE } = require('./helpers');

const PORTRAIT_MP4 = path.join(__dirname, 'fixtures/portrait_1080x1920.mp4');

// Captions that all fall inside the 2 s fixture, with gaps between them.
const CAPS = [
  { start: 0.2, end: 0.8, text: 'ראשון' },
  { start: 1.0, end: 1.6, text: 'שני'   },
];

async function runEditor(page, captions = CAPS) {
  await mockAllApis(page, { captions });
  const buf = fs.readFileSync(PORTRAIT_MP4);
  await page.route(`${API_BASE}/download/**`, route =>
    route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
                 'Content-Length': String(buf.length) },
      body: buf,
    }));
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  await page.waitForFunction(() => {
    const v = document.getElementById('cutVideo');
    return v && v.readyState >= 1 && isFinite(v.duration) && v.duration > 0;
  }, { timeout: 8_000 });
}

async function seekTo(page, t) {
  await page.evaluate((tt) => { document.getElementById('cutVideo').currentTime = tt; }, t);
}

async function capText(page) {
  return page.evaluate(() => document.getElementById('playerCap').textContent.trim());
}

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

// ── Seeking / jumping shows the right caption immediately ───────────────────

test('jumping to a segment shows that segment caption immediately', async ({ page }) => {
  await runEditor(page);

  await seekTo(page, 1.3);
  await page.waitForFunction(() => document.getElementById('playerCap').textContent.includes('שני'), { timeout: 4_000 });
  expect(await capText(page)).toContain('שני');

  await seekTo(page, 0.5);
  await page.waitForFunction(() => document.getElementById('playerCap').textContent.includes('ראשון'), { timeout: 4_000 });
  expect(await capText(page)).toContain('ראשון');
});

test('jumping into a gap between captions clears the overlay', async ({ page }) => {
  await runEditor(page);
  await seekTo(page, 0.9);   // gap between 0.8 and 1.0
  await page.waitForFunction(() => document.getElementById('playerCap').textContent.trim() === '', { timeout: 4_000 });
  expect(await capText(page)).toBe('');
});

// ── No redundant caption rewrites during playback (the anti-jank invariant) ──

test('caption overlay is never redundantly rewritten during playback', async ({ page }) => {
  await runEditor(page);

  await page.evaluate(() => {
    window.__caps = [];
    const el = document.getElementById('playerCap');
    window.__mo = new MutationObserver(() => window.__caps.push(el.textContent));
    window.__mo.observe(el, { childList: true, characterData: true, subtree: true });
  });

  // Play across at least one segment boundary via a real gesture.
  await page.click('#playerWrap');
  await page.waitForFunction(() => {
    const v = document.getElementById('cutVideo');
    return !v.paused && v.currentTime > 1.1;   // crossed 0.8 and 1.0 boundaries
  }, { timeout: 8_000 });
  await page.click('#playerWrap');  // pause
  await page.evaluate(() => window.__mo.disconnect());

  const caps = await page.evaluate(() => window.__caps);
  // Every recorded mutation must have CHANGED the text - no two consecutive
  // records may be identical (that would be a wasteful re-render → jank).
  for (let i = 1; i < caps.length; i++) {
    expect(caps[i]).not.toBe(caps[i - 1]);
  }
  // And it actually transitioned at least once (proves the loop ran).
  expect(caps.length).toBeGreaterThan(0);
});

// ── rAF loop lifecycle: advances on play, stops on pause ────────────────────

test('progress advances during playback and freezes after pause', async ({ page }) => {
  await runEditor(page);

  const width = () => page.evaluate(() => parseFloat(document.getElementById('playerProgFill').style.width) || 0);

  await page.click('#playerWrap');           // play
  await page.waitForFunction(() => document.getElementById('cutVideo').currentTime > 0.6, { timeout: 8_000 });
  const during = await width();
  expect(during).toBeGreaterThan(0);

  await page.click('#playerWrap');           // pause
  const atPause = await width();
  // Give any stray rAF frames time to (not) fire.
  await page.waitForTimeout(500);
  const afterPause = await width();
  expect(Math.abs(afterPause - atPause)).toBeLessThan(0.5);   // frozen → loop stopped
});

test('play/pause toggles the control state cleanly', async ({ page }) => {
  await runEditor(page);
  const playing = () => page.evaluate(() => !document.getElementById('cutVideo').paused);

  await page.click('#playerWrap');
  // The 'play' event handler flips the control state; wait for it (paused can
  // read false a tick before the event dispatches).
  await page.waitForFunction(() => document.getElementById('playerPlayBtn').classList.contains('is-playing'), { timeout: 6_000 });
  expect(await playing()).toBe(true);

  await page.click('#playerWrap');
  await page.waitForFunction(() => document.getElementById('playerBigPlay').style.opacity === '1', { timeout: 6_000 });
  expect(await playing()).toBe(false);
  expect(await page.evaluate(() => document.getElementById('playerPlayBtn').classList.contains('is-playing'))).toBe(false);
});
