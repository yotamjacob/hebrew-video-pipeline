// Build stamp - every view's footer carries the running frontend version so a
// fix can be verified on-device (the Android app loads the frontend remotely).
const { test, expect } = require('@playwright/test');
const { bootApp, mockAllApis, selectFile } = require('./helpers');

test('every footer shows the build tag and it matches APP_VERSION', async ({ page }) => {
  await bootApp(page);
  const version = await page.evaluate(() => window.__APP_VERSION);
  expect(version).toMatch(/^v\d+\.\d+\.\d+$/);
  const tags = page.locator('p.footer .footer-version');
  expect(await tags.count()).toBeGreaterThan(0);
  for (let i = 0; i < await tags.count(); i++) {
    await expect(tags.nth(i)).toHaveText(version);
  }
  // Keep the on-device build proof visually distinct from the muted footer:
  // it should render as a bordered, semibold badge rather than faint text.
  const badgeStyle = await tags.first().evaluate(el => {
    const s = getComputedStyle(el);
    return {
      borderStyle: s.borderTopStyle,
      borderRadius: s.borderRadius,
      fontSize: parseFloat(s.fontSize),
      fontWeight: Number(s.fontWeight),
      background: s.backgroundColor,
    };
  });
  expect(badgeStyle.borderStyle).toBe('solid');
  expect(parseFloat(badgeStyle.borderRadius)).toBeGreaterThan(10);
  expect(badgeStyle.fontSize).toBeGreaterThanOrEqual(11);
  expect(badgeStyle.fontWeight).toBeGreaterThanOrEqual(600);
  expect(badgeStyle.background).not.toBe('rgba(0, 0, 0, 0)');
});

// A long-lived tab keeps running the JS it booted with across deploys (field
// lesson: days of "bug still happening" reports were a stale tab). The app
// polls the deployed app.js head; a new version → refresh pill when work
// would be lost, silent auto-reload when idle.
test('a newer deployed version shows the refresh pill when work is in progress', async ({ page }) => {
  await page.addInitScript(() => { window.__VERSION_CHECK_MS = 500; });
  await bootApp(page);
  await mockAllApis(page);
  await page.route(/app\.js\?vchk=/, r =>
    r.fulfill({ status: 200, contentType: 'text/javascript',
                body: "  const APP_VERSION = '99.0.0';" }));
  await selectFile(page);   // selected file = not safe to auto-reload
  const pill = page.locator('#updatePill');
  await expect(pill).toBeVisible({ timeout: 10_000 });
  await expect(pill).toContainText(/גרסה חדשה|new version/i);
  // Dismiss works and it stays dismissed for this version.
  await pill.locator('.update-pill-x').click();
  await expect(pill).toBeHidden();
  await page.waitForTimeout(1200);
  await expect(page.locator('#updatePill')).toHaveCount(0);
});

test('idle app auto-reloads onto the new version', async ({ page }) => {
  await page.addInitScript(() => { window.__VERSION_CHECK_MS = 500; });
  await bootApp(page);
  // Serve the fake new version ONCE; after the reload the real file answers
  // (same version → no loop).
  let served = 0;
  await page.route(/app\.js\?vchk=/, (r, req) => {
    if (served++) return r.fallback();
    return r.fulfill({ status: 200, contentType: 'text/javascript',
                       body: "  const APP_VERSION = '99.0.0';" });
  });
  await page.waitForEvent('load', { timeout: 10_000 });   // the auto-reload
});
