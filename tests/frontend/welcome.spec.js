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
  // Android is out of beta (2026-08-19): the card links the production listing.
  await expect(page.locator('a.platform.android')).toHaveAttribute(
    'href', 'https://play.google.com/store/apps/details?id=com.heb.pipeline');
  await expect(page.locator('a.platform.android .status')).toContainText('Google Play');
  await expect(page.locator('a.platform.android .status')).not.toContainText('בטא');

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

test('SEO FAQ: 7 collapsed questions that expand, mirrored in FAQPage JSON-LD', async ({ page }) => {
  await page.route(STATS_RE, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ videos: 5 }),
  }));
  await page.goto('/welcome.html');

  await expect(page.locator('.faq details')).toHaveCount(7);
  const first = page.locator('.faq details').first();
  await expect(first.locator('p')).toBeHidden();     // collapsed by default
  await first.locator('summary').click();
  await expect(first.locator('p')).toBeVisible();
  // The EN items are LTR inside the RTL page.
  await expect(page.locator('.faq-en')).toHaveCount(2);
  for (const en of await page.locator('.faq-en').all()) {
    await expect(en).toHaveAttribute('dir', 'ltr');
  }
  // Structured data stays in sync with the visible questions.
  const ld = await page.evaluate(() => {
    const blocks = [...document.querySelectorAll('script[type="application/ld+json"]')]
      .map((s) => JSON.parse(s.textContent));
    return blocks.find((b) => b['@type'] === 'FAQPage');
  });
  expect(ld.mainEntity).toHaveLength(7);
  const visible = await page.locator('.faq summary').allInnerTexts();
  for (const [i, q] of ld.mainEntity.entries()) {
    expect(visible[i].trim()).toBe(q.name);
  }
});

test('counter stays hidden when /stats fails', async ({ page }) => {
  await page.route(STATS_RE, (route) => route.fulfill({ status: 500, body: '' }));
  await page.goto('/welcome.html');
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.locator('#counter')).toBeHidden();
});
