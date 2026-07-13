const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp, DEFAULT_CAPTIONS, API_BASE } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('pausing the caption player renders the exact (libass) frame overlay', async ({ page }) => {
  let hits = 0;
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await page.route(new RegExp(`${API_BASE}/preview_frame/?$`), async (route, req) => {
    hits++;
    const body = JSON.parse(req.postData() || '{}');
    // The exact frame is requested with the current caption settings.
    expect(body).toHaveProperty('video_key');
    expect(body).toHaveProperty('captions');
    await route.fallback();   // hand to the PNG mock from runFullUpload
  });
  // Simulate the player pausing (seek-to-first-caption / user pause).
  await page.evaluate(() => document.getElementById('cutVideo').dispatchEvent(new Event('pause')));
  await expect(page.locator('#exactCap')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('#exactCap')).toHaveAttribute('src', /^blob:/);
  expect(hits).toBeGreaterThan(0);
});

test('exact overlay hides while the video plays (approximate preview shows)', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await page.evaluate(() => document.getElementById('cutVideo').dispatchEvent(new Event('pause')));
  await expect(page.locator('#exactCap')).toBeVisible({ timeout: 5000 });
  await page.evaluate(() => document.getElementById('cutVideo').dispatchEvent(new Event('play')));
  await expect(page.locator('#exactCap')).toBeHidden();
});

test('selecting a hook renders the exact frame on the main player', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8000 });
  await page.click('#hookOption0');
  // Selecting a hook seeks into its window and refreshes the player's exact
  // still (which includes the hook payload while inside the window).
  await expect(page.locator('#exactCap')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('#exactCap')).toHaveAttribute('src', /^blob:/);
  // The DOM hook overlay also shows on the player.
  await expect(page.locator('#playerHook')).toBeVisible();
});
