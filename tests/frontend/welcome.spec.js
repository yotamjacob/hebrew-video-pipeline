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

test('animated pitch settles: 4 ticked tools, pipeline, ready video, 3 gains', async ({ page }) => {
  await page.route(STATS_RE, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ videos: 5 }),
  }));
  // Reduced motion = animation:none, and base styles ARE the settled state -
  // this pins that contract (a regression here means reduced-motion visitors
  // see an empty diagram forever).
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/welcome.html');

  await expect(page.locator('.cl-item')).toHaveCount(4);
  await expect(page.locator('.cl-item .cl-check')).toHaveCount(4);
  await expect(page.locator('.flow .arrow')).toHaveCount(2);
  await expect(page.locator('.pipe svg')).toBeVisible();
  await expect(page.locator('.ready-label')).toHaveText('סרטון מוכן לפרסום');
  await expect(page.locator('.gain')).toHaveCount(3);
  for (const gain of await page.locator('.gain').all()) {
    await expect(gain).toBeVisible();
    expect(await gain.evaluate((el) => getComputedStyle(el).opacity)).toBe('1');
  }
});

test('pitch flow loops (rewinds) while gains stay put', async ({ page }) => {
  await page.route(STATS_RE, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ videos: 5 }),
  }));
  await page.goto('/welcome.html');
  // The ready-video pop finishes at ~4.3s; the loop rewinds everything at
  // 6.5s. At 7.2s a non-looping page would report currentTime pinned at the
  // ~4300ms end value - a rewound one is back near zero.
  await page.waitForTimeout(7200);
  const t = await page.evaluate(() => {
    const a = document.querySelector('.ready').getAnimations()[0];
    return a ? a.currentTime : null;
  });
  expect(t).not.toBeNull();
  expect(t).toBeLessThan(3000);
  // Gains are outside .flow - they must NOT rewind (readable content).
  const gainOpacity = await page.evaluate(
    () => getComputedStyle(document.querySelector('.gain')).opacity);
  expect(gainOpacity).toBe('1');
});

test('counter stays hidden when /stats fails', async ({ page }) => {
  await page.route(STATS_RE, (route) => route.fulfill({ status: 500, body: '' }));
  await page.goto('/welcome.html');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.locator('#counter')).toBeHidden();
});
