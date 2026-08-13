const { test, expect } = require('@playwright/test');

// The landing page is standalone (no app.js) - its only network call is the
// public /stats counter, stubbed here so nothing escapes to the real API.
const STATS_RE = /hebrew-video-pipeline-api\.modal\.run\/stats/;

test('welcome page renders logo, platform links and the live counter', async ({ page }) => {
  await page.route(STATS_RE, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ videos: 1234 }),
  }));
  await page.goto('/welcome.html');

  await expect(page.locator('img.logo')).toBeVisible();
  await expect(page.locator('h1')).toHaveText('פייפליין');
  await expect(page.locator('.tagline')).toContainText('חוסך');

  // Web CTA carries campaign attribution (?src= is recorded on signup).
  await expect(page.locator('a.platform.web')).toHaveAttribute('href', '/?src=welcome');
  await expect(page.locator('a.platform.android')).toHaveAttribute(
    'href', /play\.google\.com\/apps\/testing\/com\.heb\.pipeline/);
  await expect(page.locator('a.platform.android .status')).toContainText('בטא');

  // iPhone is deliberately NOT a link - a disabled card until the App Store launch.
  const ios = page.locator('.platform.ios');
  await expect(ios).toHaveAttribute('aria-disabled', 'true');
  expect(await ios.evaluate((el) => el.tagName)).toBe('DIV');
  await expect(ios.locator('.status')).toContainText('בקרוב');

  // Counter reveals and counts up to the stubbed total (he-IL grouping).
  await expect(page.locator('#counter')).toBeVisible();
  await expect(page.locator('#counterNum')).toHaveText('1,234', { timeout: 5000 });
});

test('counter stays hidden when /stats fails', async ({ page }) => {
  await page.route(STATS_RE, (route) => route.fulfill({ status: 500, body: '' }));
  await page.goto('/welcome.html');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.locator('#counter')).toBeHidden();
});
