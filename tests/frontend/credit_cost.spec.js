/**
 * What a run costs the user, and the length cap on the 4K upscale.
 *
 * One credit covers 10 minutes of source; a longer video and the upscale each
 * add one. The user has to be told the real price BEFORE spending it, and the
 * upscale has to be unavailable past the length where it would outrun the
 * server's job timeout and fail after burning the GPU time.
 */
const { test, expect } = require('@playwright/test');
const { mockAllApis, bootApp } = require('./helpers');
const { TINY_MP4_15S } = require('./fixtures');

// The fixture is a real (short) mp4; only the reported duration is overridden.
async function selectWithDuration(page, secs) {
  await page.evaluate(d => {
    Object.defineProperty(HTMLVideoElement.prototype, 'duration',
      { configurable: true, get: () => d });
  }, secs);
  await page.setInputFiles('#fileInput',
    { name: 'clip.mp4', mimeType: 'video/mp4', buffer: TINY_MP4_15S });
  await page.waitForSelector('#runBtn:not([disabled])');
}

test.beforeEach(async ({ page }) => {
  // A finite quota, so the confirm modal appears at all.
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 0, video_limit: 5 } });
  await mockAllApis(page);
});

test('a short video costs one credit', async ({ page }) => {
  await selectWithDuration(page, 100);
  await page.click('#runBtn');
  await expect(page.locator('#confirmOverlay')).toBeVisible();
  await expect(page.locator('#confirmTitle')).toHaveText('להשתמש בקרדיט אחד לסרטון?');
});

test('a video over ten minutes costs two credits, and says so before spending', async ({ page }) => {
  await selectWithDuration(page, 700);
  await page.click('#runBtn');
  await expect(page.locator('#confirmTitle')).toHaveText('להשתמש ב-2 קרדיטים לסרטונים?');
  await expect(page.locator('#confirmBody')).toContainText('2 קרדיטים');
});

test('the AI upscale adds a credit', async ({ page }) => {
  await selectWithDuration(page, 100);
  await page.locator('label[for="ev_esrgan"]').click();
  await page.click('#runBtn');
  await expect(page.locator('#confirmTitle')).toHaveText('להשתמש ב-2 קרדיטים לסרטונים?');
});

test('the AI upscale is unavailable past three minutes', async ({ page }) => {
  await selectWithDuration(page, 700);
  // Past this length the upscale would outrun the server's job timeout, fail,
  // and refund the credit only after the GPU time was already spent.
  await expect(page.locator('#ev_esrgan')).toBeDisabled();
  await expect(page.locator('#upscaleLimitNote')).toBeVisible();
  await expect(page.locator('#upscaleLimitNote')).toContainText('3 דקות');
  await expect(page.locator('label[for="ev_esrgan"]')).toHaveClass(/opt-disabled/);
});

test('a long file picked after the upscale was chosen resets the choice', async ({ page }) => {
  await selectWithDuration(page, 100);
  await page.locator('label[for="ev_esrgan"]').click();
  await expect(page.locator('#ev_esrgan')).toBeChecked();

  // Swapping in a long file must not leave an impossible run armed.
  await selectWithDuration(page, 700);
  await expect(page.locator('#ev_esrgan')).not.toBeChecked();
  await expect(page.locator('#ev_none')).toBeChecked();
  await expect(page.locator('#ev_esrgan')).toBeDisabled();
});

test('the measured duration is sent to /process so the server can price it', async ({ page }) => {
  const seen = [];
  await page.route(/\/process\/\?/, (route, request) => {
    seen.push(new URL(request.url()).searchParams.get('duration'));
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: '{"call_id":"c1"}' });
  });
  await selectWithDuration(page, 700);
  await page.click('#runBtn');
  await page.locator('#confirmOk').click();
  await expect.poll(() => seen.length).toBeGreaterThan(0);
  expect(Number(seen[0])).toBeCloseTo(700, 0);
});
