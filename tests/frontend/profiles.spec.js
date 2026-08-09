// Settings profiles: one named snapshot of everything configurable, synced
// per-account via /profiles; the starred default auto-applies at boot.
const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, bootApp, selectFile } = require('./helpers');

const PROFILE = {
  name: 'רק חיתוך',
  data: {
    options: { cut: true, aggr: 5, captions: false, enhance_audio: false,
               enhance_video: 'none', auto_broll: false, auto_hook: false },
    font: 'Rubik', font_size: 56,
    caption_style: { font_color: '#12ab34', border_size: 0, mode: 'word',
                     progress_bar: true, progress_color: '#00c2ff',
                     auto_zoom: true, zoom_strength: 'strong' },
    hook: { font: 'Rubik', font_color: '#ffee00', bg_color: '#000000',
            bg_opacity: '40', border_color: '#000000', border_size: '2', size: '1' },
  },
};

function mockProfiles(page, state, posted) {
  return page.route(/\/profiles\/?(\?.*)?$/, (route, request) => {
    if (request.method() === 'POST') {
      posted.push(JSON.parse(request.postData()));
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: request.postData() });
    }
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify(state) });
  });
}

test('chips render; applying a profile drives the option toggles and styles', async ({ page }) => {
  const posted = [];
  await bootApp(page);
  await mockAllApis(page);
  await mockProfiles(page, { profiles: [PROFILE], default: null }, posted);
  await page.reload();

  const chip = page.locator('.profile-chip-name');
  await expect(chip).toHaveText('רק חיתוך');
  await expect(page.locator('#burnCaptions')).toBeChecked();   // untouched yet
  await chip.click();
  await expect(page.locator('#burnCaptions')).not.toBeChecked();
  await expect(page.locator('#enhanceAudio')).not.toBeChecked();
  await expect(page.locator('#cutSilences')).toBeChecked();
  expect(await page.locator('#aggrSlider').inputValue()).toBe('5');
  // Caption style landed in the persisted payload for the next editor open.
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('captionStyle')));
  expect(saved.font_color).toBe('#12ab34');
  expect(saved.mode).toBe('word');
  expect(saved.progress_bar).toBe(true);
  expect(saved.auto_zoom).toBe(true);
});

test('the starred default auto-applies at boot, silently', async ({ page }) => {
  const posted = [];
  await bootApp(page);
  await mockAllApis(page);
  await mockProfiles(page, { profiles: [PROFILE], default: 'רק חיתוך' }, posted);
  await page.reload();
  await expect(page.locator('#burnCaptions')).not.toBeChecked();
  await expect(page.locator('.profile-chip')).toHaveClass(/active/);
  await expect(page.locator('.profile-chip-star')).toHaveClass(/on/);
});

test('saving harvests the current setup (keywords excluded) and POSTs it', async ({ page }) => {
  const posted = [];
  await bootApp(page);
  await mockAllApis(page);
  await mockProfiles(page, { profiles: [], default: null }, posted);
  await page.reload();

  // The checkbox hides behind the custom toggle track - drive it directly.
  await page.evaluate(() => {
    const e = document.getElementById('burnCaptions');
    e.checked = false;
    e.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#profileAdd');
  await page.fill('#profileName', 'שלי');
  await page.click('#profileSaveBtn');
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(1);
  const p = posted[0].profiles[0];
  expect(p.name).toBe('שלי');
  expect(p.data.options.captions).toBe(false);
  expect(p.data.caption_style).toBeTruthy();
  expect('highlight_keywords' in p.data.caption_style).toBe(false);
  expect(p.data.hook.font_color).toBeTruthy();
});

test('starring sets the default; delete confirms and persists', async ({ page }) => {
  const posted = [];
  await bootApp(page);
  await mockAllApis(page);
  await mockProfiles(page, { profiles: [PROFILE], default: null }, posted);
  await page.reload();

  await page.click('.profile-chip-star');
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(1);
  expect(posted[0].default).toBe('רק חיתוך');

  await page.click('.profile-chip-del');
  await page.click('#confirmOk');
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(2);
  expect(posted[1].profiles).toHaveLength(0);
  expect(posted[1].default).toBe(null);
  await expect(page.locator('.profile-chip-name')).toHaveCount(0);
});
