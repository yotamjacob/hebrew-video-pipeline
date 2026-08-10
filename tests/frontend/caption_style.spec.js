/** Caption styling (Phase C): text/outline colours + background box flow into
 *  the live preview, the exact-frame request and the burn payload. */
const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp, runFullUpload } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

async function setColor(page, id, value) {
  await page.locator('#' + id).evaluate((el, v) => {
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}

test('style controls update the live caption overlay', async ({ page }) => {
  await runFullUpload(page);
  await setColor(page, 'capFontColor', '#ff0000');
  await expect.poll(() =>
    page.evaluate(() => document.getElementById('playerCap').style.color)
  ).toBe('rgb(255, 0, 0)');
  // Background box appears once opacity > 0
  await page.locator('#capBgOpacity').evaluate(el => {
    el.value = '60'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const bg = await page.evaluate(() => document.getElementById('playerCap').style.background);
  expect(bg).toContain('rgba(0, 0, 0, 0.6)');
});

test('burn payload carries caption_style', async ({ page }) => {
  await runFullUpload(page);
  await setColor(page, 'capFontColor', '#ffee00');
  await page.locator('#capBorderSize').evaluate(el => {
    el.value = '4'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  let burnBody = null;
  await page.route(/\/burn\/\?/, (route, request) => {
    burnBody = JSON.parse(request.postData());
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnBody, { timeout: 10_000 }).not.toBeNull();
  expect(burnBody.caption_style).toMatchObject({
    font_color: '#ffee00', border_size: 4, bg_opacity: 0,
  });
});

test('style persists across reloads (localStorage)', async ({ page }) => {
  await runFullUpload(page);
  await setColor(page, 'capFontColor', '#00ff00');
  await page.reload();
  await expect.poll(() =>
    page.evaluate(() => document.getElementById('capFontColor').value)
  ).toBe('#00ff00');
});

test('style presets: chips render and one tap applies the whole look', async ({ page }) => {
  await runFullUpload(page);
  const chips = page.locator('.cap-preset-chip');
  await expect(chips).toHaveCount(6);
  // Karaoke preset: mode flips, highlight color control appears.
  await page.click('.cap-preset-chip[data-preset="karaoke"]');
  await expect(page.locator('#capStyleMode')).toHaveValue('karaoke');
  // The highlight row lives inside the collapsed design card - expand first.
  await page.evaluate(() => toggleCapDesign(true));
  await expect(page.locator('#capHighlightRow')).toBeVisible();
  await expect(page.locator('.cap-preset-chip[data-preset="karaoke"]')).toHaveClass(/active/);
  // Word-pop preset: sets font + mode + heavy outline.
  await page.click('.cap-preset-chip[data-preset="wordpop"]');
  await expect(page.locator('#capStyleMode')).toHaveValue('word');
  await expect(page.locator('#fontSelect')).toHaveValue('Secular One');
  await expect(page.locator('#capBorderSize')).toHaveValue('4');
  // A manual tweak drops the active mark (it's no longer exactly the preset).
  await page.locator('#capBorderSize').evaluate(el => {
    el.value = '2'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect(page.locator('.cap-preset-chip.active')).toHaveCount(0);
});

// Caption size is REFERENCE px (a 1080 short side); the burn scales it to the
// real frame so one choice looks the same on 720p, 1080p and 4K (2026-08-10).
// These pin the JS half of that mirror - the Python half lives in
// tests/backend/test_ass_generation.py (TestResolutionRelativeSize).
async function fakeVideoDims(page, w, h) {
  await page.evaluate(({ w, h }) => {
    const v = document.getElementById('cutVideo');
    Object.defineProperty(v, 'videoWidth', { value: w, configurable: true });
    Object.defineProperty(v, 'videoHeight', { value: h, configurable: true });
  }, { w, h });
}

test('size dropdown reaches 200 px and every option is a real burn size', async ({ page }) => {
  await runFullUpload(page);
  const values = await page.locator('#captionFontSizeSlider option')
    .evaluateAll(opts => opts.map(o => parseInt(o.value, 10)));
  expect(Math.max(...values)).toBe(200);           // was 150 - "small even on max"
  expect(values).toEqual([...values].sort((a, b) => a - b));   // ascending, no dupes
  expect(new Set(values).size).toBe(values.length);
  // The server clamps at 240 reference px, so nothing here can be silently cut.
  expect(Math.max(...values)).toBeLessThanOrEqual(240);
});

test('the burn size scales with video resolution, the SENT size stays reference px', async ({ page }) => {
  await runFullUpload(page);
  await page.selectOption('#captionFontSizeSlider', '48');

  // 1080-short-side portrait is the reference frame: a pure no-op.
  await fakeVideoDims(page, 1080, 1920);
  expect(await page.evaluate(() => _capBurnPx())).toBe(48);
  // 4K doubles it, 720p shrinks it - same fraction of the frame either way.
  await fakeVideoDims(page, 2160, 3840);
  expect(await page.evaluate(() => _capBurnPx())).toBe(96);
  await fakeVideoDims(page, 720, 1280);
  expect(await page.evaluate(() => _capBurnPx())).toBeCloseTo(32, 5);
  // Landscape scales on the short side too.
  await fakeVideoDims(page, 1920, 1080);
  expect(await page.evaluate(() => _capBurnPx())).toBe(48);

  // What travels to the server stays REFERENCE px - the burn does the scaling,
  // so sending a pre-scaled size would double it.
  await fakeVideoDims(page, 2160, 3840);
  let burnUrl = null;
  await page.route(/\/burn\/\?/, (route, request) => {
    burnUrl = request.url();
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnUrl, { timeout: 10_000 }).not.toBeNull();
  expect(new URL(burnUrl).searchParams.get('font_size')).toBe('48');
});

test('a preset look flows into the burn payload', async ({ page }) => {
  await runFullUpload(page);
  await page.click('.cap-preset-chip[data-preset="news"]');
  let burnBody = null, burnUrl = null;
  await page.route(/\/burn\/\?/, (route, request) => {
    burnBody = JSON.parse(request.postData());
    burnUrl = request.url();
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnBody, { timeout: 10_000 }).not.toBeNull();
  expect(burnBody.caption_style).toMatchObject({ bg_opacity: 0.6, border_size: 0, mode: 'classic' });
  // The font travels in the burn URL query, not the JSON body.
  expect(decodeURIComponent(burnUrl)).toContain('font=Frank+Ruhl+Libre');
});

test('caption design card is open by default, collapses on tap, and reopens on a fresh editor', async ({ page }) => {
  await runFullUpload(page);
  await expect(page.locator('#capDesignBody')).toBeVisible();
  await expect(page.locator('#capStyleMode')).toBeVisible();
  await page.click('#capDesignHead');
  await expect(page.locator('#capDesignBody')).toBeHidden();
  // The collapse is session-scoped only (user directive, 2026-08-10): every
  // fresh editor open re-expands the design card.
  await page.evaluate(() => showCaptionEditor());
  await expect(page.locator('#capDesignBody')).toBeVisible();
});

test('user styles: save the current look, apply it, delete it', async ({ page }) => {
  await runFullUpload(page);
  // Tune something distinctive, then save under "My styles".
  await setColor(page, 'capFontColor', '#12ab34');
  await page.click('#capPresetTabMine');
  await expect(page.locator('#capPresetRow .cap-preset-chip')).toHaveCount(1); // just the + chip
  await page.click('#capPresetAdd');
  await page.fill('#capPresetName', 'הסגנון שלי');
  await page.click('#capPresetSaveBtn');
  await expect(page.locator('#capPresetRow .cap-preset-name').first()).toHaveText('הסגנון שלי');
  // Change the color away, then apply the saved style - it comes back.
  await setColor(page, 'capFontColor', '#ffffff');
  await page.locator('#capPresetRow .cap-preset-chip').first().click();
  await expect(page.locator('#capFontColor')).toHaveValue('#12ab34');
  // Persisted (localStorage) - checked directly: a reload lands on the
  // pipeline view (no editor), so the UI can't be probed there.
  const stored = await page.evaluate(() => localStorage.getItem('captionUserPresets') || '');
  expect(stored).toContain('הסגנון שלי');
  // Delete (confirm modal) empties the list back to the + chip.
  await page.locator('.cap-preset-del').first().click();
  await page.click('#confirmOk');
  await expect(page.locator('.cap-preset-del')).toHaveCount(0);
});

test('color swatch opens the app-styled popover; a tile applies instantly', async ({ page }) => {
  await runFullUpload(page);
  await page.evaluate(() => toggleCapDesign(true));
  await page.click('#capFontColorSwatch');
  const pop = page.locator('#colorPopover');
  await expect(pop).toBeVisible();
  // Curated set: brand palette + essentials + pop colors + custom tile.
  await expect(pop.locator('.color-tile')).toHaveCount(25);
  await page.click('.color-tile[data-hex="#FFD400"]');
  await expect(pop).toBeHidden();
  await expect(page.locator('#capFontColor')).toHaveValue('#ffd400');
  // The live preview followed (input event path is shared with manual edits).
  await expect.poll(() =>
    page.evaluate(() => document.getElementById('playerCap').style.color)
  ).toBe('rgb(255, 212, 0)');
  // The swatch chip reflects the pick.
  const chip = await page.evaluate(() =>
    document.querySelector('#capFontColorSwatch .csb-chip').style.background);
  expect(chip).toContain('255, 212, 0');
});

test('selecting one saved style marks only that chip, and its delete badge is not clipped', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#capPresetTabMine');
  // Save two distinct styles.
  await page.click('#capPresetAdd');
  await page.fill('#capPresetName', 'סגנון א');
  await page.click('#capPresetSaveBtn');
  await page.click('#capPresetAdd');
  await page.fill('#capPresetName', 'סגנון ב');
  await page.click('#capPresetSaveBtn');
  await expect(page.locator('#capPresetRow .cap-preset-del')).toHaveCount(2);

  // Selecting ONE lights exactly one chip (pre-fix: undefined ids matched all).
  await page.locator('#capPresetRow .cap-preset-chip').first().click();
  await expect(page.locator('#capPresetRow .cap-preset-chip.active')).toHaveCount(1);
  await page.locator('#capPresetRow .cap-preset-chip').nth(1).click();
  await expect(page.locator('#capPresetRow .cap-preset-chip.active')).toHaveCount(1);

  // The delete badge overhangs its chip corner - it must be fully hit-testable
  // (pre-fix the chip's overflow:hidden + the row's 2px padding cut it in half).
  const clickable = await page.evaluate(() => {
    const del = document.querySelector('#capPresetRow .cap-preset-del');
    const r = del.getBoundingClientRect();
    const probe = document.elementFromPoint(r.left + r.width / 2, r.top + 2);
    return !!probe && (probe === del || del.contains(probe));
  });
  expect(clickable).toBe(true);
});

test('custom color tile opens the in-app creator, not the OS dialog; hex + hue apply live', async ({ page }) => {
  await runFullUpload(page);
  await page.evaluate(() => toggleCapDesign(true));
  await page.click('#capFontColorSwatch');
  await expect(page.locator('#colorPopover')).toBeVisible();

  // A click on the native input would open the OS dialog - trap it.
  await page.evaluate(() => {
    window.__nativeOpened = false;
    document.getElementById('capFontColor').addEventListener('click', () => { window.__nativeOpened = true; });
  });
  await page.click('.color-tile-custom');
  await expect(page.locator('#colorCustomPanel')).toBeVisible();
  expect(await page.evaluate(() => window.__nativeOpened)).toBe(false);

  // The expanded popover must stay fully on-screen (it grew after being
  // positioned - the panel used to hang past the bottom edge).
  const box = await page.locator('#colorPopover').boundingBox();
  const vp = page.viewportSize();
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(vp.height + 1);

  // Typing a full hex applies through the same input-event path as the tiles.
  await page.fill('#colorCustomHex', '#12ab34');
  await expect(page.locator('#capFontColor')).toHaveValue('#12ab34');
  await expect.poll(() =>
    page.evaluate(() => document.getElementById('playerCap').style.color)
  ).toBe('rgb(18, 171, 52)');

  // The hue slider keeps applying live.
  await page.locator('.color-hue').evaluate(el => {
    el.value = '0'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const v = await page.locator('#capFontColor').inputValue();
  expect(v).not.toBe('#12ab34');
});
