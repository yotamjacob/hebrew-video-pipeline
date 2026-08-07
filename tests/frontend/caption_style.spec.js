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

test('caption design card starts collapsed and expands on tap', async ({ page }) => {
  await runFullUpload(page);
  await expect(page.locator('#capDesignBody')).toBeHidden();
  await expect(page.locator('#capStyleMode')).toBeHidden();   // inside the card
  await page.click('#capDesignHead');
  await expect(page.locator('#capDesignBody')).toBeVisible();
  await expect(page.locator('#capStyleMode')).toBeVisible();
  await page.click('#capDesignHead');
  await expect(page.locator('#capDesignBody')).toBeHidden();
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
