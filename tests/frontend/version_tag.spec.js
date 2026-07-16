// Build stamp - every view's footer carries the running frontend version so a
// fix can be verified on-device (the Android app loads the frontend remotely).
const { test, expect } = require('@playwright/test');
const { bootApp } = require('./helpers');

test('every footer shows the build tag and it matches APP_VERSION', async ({ page }) => {
  await bootApp(page);
  const version = await page.evaluate(() => window.__APP_VERSION);
  expect(version).toMatch(/^v\d+\.\d+\.\d+$/);
  const tags = page.locator('p.footer .footer-version');
  expect(await tags.count()).toBeGreaterThan(0);
  for (let i = 0; i < await tags.count(); i++) {
    await expect(tags.nth(i)).toHaveText(version);
  }
});
