/**
 * Auto punch-in zoom (Effects tab).
 *
 * The client computes explicit zoom windows from caption timing
 * (_computeZoomWindows) and sends them in caption_style.zooms; the burn
 * renders those exact spans (hook.lines contract). The moving preview mirrors
 * the punch with an instant CSS scale on #cutVideo, clipped by playerWrap.
 */
const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp, API_BASE } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('window heuristic: sentence starts, spaced, clamped durations', async ({ page }) => {
  await runFullUpload(page);
  const wins = await page.evaluate(() => _computeZoomWindows([
    { start: 0.5,  end: 2.0 },    // first sentence → window
    { start: 2.2,  end: 4.0 },    // continuous + too close → skipped
    { start: 9.0,  end: 11.0 },   // pause before it, but only 8.5s since last → skipped
    { start: 20.0, end: 20.4 },   // pause + 19.5s gap → window (short cap → 1.0s floor)
    { start: 40.0, end: 49.0 },   // → window, duration clamped to 2.6s max
  ]));
  expect(wins).toEqual([[0.5, 2.0], [20.0, 21.0], [40.0, 42.6]]);
  // start-to-start spacing is at least 15s everywhere
  for (let i = 1; i < wins.length; i++) expect(wins[i][0] - wins[i - 1][0]).toBeGreaterThanOrEqual(15);
});

test('enabled toggle sends the computed windows in the burn payload', async ({ page }) => {
  let burnBody = null;
  await runFullUpload(page);
  await page.route(/\/burn\/?(\?|$)/, async (route, request) => {
    try { burnBody = JSON.parse(request.postData() || '{}'); } catch {}
    await route.fallback();
  });

  await page.click('#tabBtnEffects');
  await page.check('#fxAutoZoom');
  await page.selectOption('#fxZoomStrength', 'strong');
  await page.locator('#runBtn').dispatchEvent('click');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();

  await expect.poll(() => (burnBody ? 'y' : 'n'), { timeout: 10_000 }).toBe('y');
  const cs = burnBody.caption_style;
  expect(cs.auto_zoom).toBe(true);
  expect(cs.zoom_strength).toBe('strong');
  // DEFAULT_CAPTIONS (0.5-2.3, 3.0-5.1, 5.8-8.4): one window at the first
  // sentence; the rest are inside the 15s spacing.
  expect(cs.zooms).toEqual([[0.5, 2.3]]);
});

test('disabled toggle sends no zooms key at all', async ({ page }) => {
  let burnBody = null;
  await runFullUpload(page);
  await page.route(/\/burn\/?(\?|$)/, async (route, request) => {
    try { burnBody = JSON.parse(request.postData() || '{}'); } catch {}
    await route.fallback();
  });
  await page.locator('#runBtn').dispatchEvent('click');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();

  await expect.poll(() => (burnBody ? 'y' : 'n'), { timeout: 10_000 }).toBe('y');
  expect(burnBody.caption_style.auto_zoom).toBe(false);
  expect('zooms' in burnBody.caption_style).toBe(false);
});

test('preview: the video snaps to scale() inside a window and back outside', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnEffects');
  await page.check('#fxAutoZoom');

  // Inside the first window (0.5-2.3 at medium = 1.18), face-biased origin
  await page.evaluate(() => _syncZoomPreview(1.0));
  await expect.poll(() => page.evaluate(() =>
    document.getElementById('cutVideo').style.transform)).toBe('scale(1.18)');
  expect(await page.evaluate(() =>
    document.getElementById('cutVideo').style.transformOrigin)).toBe('50% 30%');

  // Outside any window → transform cleared
  await page.evaluate(() => _syncZoomPreview(4.0));
  await expect.poll(() => page.evaluate(() =>
    document.getElementById('cutVideo').style.transform)).toBe('');

  // Strength select changes the factor live
  await page.selectOption('#fxZoomStrength', 'subtle');
  await page.evaluate(() => _syncZoomPreview(1.0));
  await expect.poll(() => page.evaluate(() =>
    document.getElementById('cutVideo').style.transform)).toBe('scale(1.1)');

  // Toggle off → cleared even inside the window
  await page.uncheck('#fxAutoZoom');
  await page.evaluate(() => _syncZoomPreview(1.0));
  await expect.poll(() => page.evaluate(() =>
    document.getElementById('cutVideo').style.transform)).toBe('');
});

test('toggle persists per device via the captionStyle payload', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnEffects');
  await page.check('#fxAutoZoom');
  await page.selectOption('#fxZoomStrength', 'strong');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('captionStyle')));
  expect(saved.auto_zoom).toBe(true);
  expect(saved.zoom_strength).toBe('strong');
});

test('applying a look-only preset does not reset the zoom toggle', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnEffects');
  await page.check('#fxAutoZoom');
  // Presets omit auto_zoom/zoom_strength - the 'in' guards must leave them be
  // (the progress-bar "resets every time I change a setting" class of bug).
  await page.evaluate(() => _applyCaptionStyleValues({ font_color: '#FFD400' }));
  await expect(page.locator('#fxAutoZoom')).toBeChecked();
});

test('moment list: each punch-in is a tappable chip; tapping one excludes it from the burn', async ({ page }) => {
  let burnBody = null;
  await runFullUpload(page);
  await page.route(/\/burn\/?(\?|$)/, async (route, request) => {
    try { burnBody = JSON.parse(request.postData() || '{}'); } catch {}
    await route.fallback();
  });

  await page.click('#tabBtnEffects');
  await page.check('#fxAutoZoom');
  // DEFAULT_CAPTIONS produce exactly one window (0.5-2.3) → one chip with
  // the caption snippet it lands on.
  const chips = page.locator('#zoomList .zoom-chip');
  await expect(chips).toHaveCount(1);
  await expect(chips.first()).toContainText('שלום עולם');

  // Tap to skip: the chip greys out, the preview stops zooming there, and
  // the burn payload carries no zooms at all.
  await chips.first().click();
  await expect(chips.first()).toHaveClass(/off/);
  await page.evaluate(() => _syncZoomPreview(1.0));
  await expect.poll(() => page.evaluate(() =>
    document.getElementById('cutVideo').style.transform)).toBe('');

  await page.locator('#runBtn').dispatchEvent('click');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await expect.poll(() => (burnBody ? 'y' : 'n'), { timeout: 10_000 }).toBe('y');
  expect('zooms' in burnBody.caption_style).toBe(false);

  // Tap again restores it.
  await chips.first().click();
  await expect(chips.first()).not.toHaveClass(/off/);
  await page.evaluate(() => _syncZoomPreview(1.0));
  await expect.poll(() => page.evaluate(() =>
    document.getElementById('cutVideo').style.transform)).toBe('scale(1.18)');
});

test('the punch ramps in and out like a camera (eased factor, not a snap)', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnEffects');
  await page.check('#fxAutoZoom');
  const zAt = async (t) => page.evaluate(async (tt) => {
    _syncZoomPreview(tt);
    const tr = document.getElementById('cutVideo').style.transform;
    return tr ? parseFloat(tr.slice(6)) : 1;
  }, t);
  // window is 0.5-2.3 (medium 1.18): early in the ramp the factor is partial
  const early = await zAt(0.6);
  expect(early).toBeGreaterThan(1);
  expect(early).toBeLessThan(1.1);
  // by mid-window the ramp has settled at the full factor
  expect(await zAt(1.0)).toBe(1.18);
  // and it eases back down near the end
  const late = await zAt(2.25);
  expect(late).toBeGreaterThan(1);
  expect(late).toBeLessThan(1.1);
});
