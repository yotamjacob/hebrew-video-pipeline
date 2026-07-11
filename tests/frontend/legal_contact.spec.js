const { test, expect } = require('@playwright/test');
const { bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('footer "Privacy & Terms" opens an on-page modal without navigating', async ({ page }) => {
  const url = page.url();
  await page.locator('a[data-i18n="footer.legal"]:visible').first().click();
  await expect(page.locator('#legalOverlay')).toBeVisible();
  // The real legal page is loaded in an iframe (no navigation, no lost progress).
  await expect(page.locator('#legalFrame')).toHaveAttribute('src', '/legal.html');
  expect(page.url()).toBe(url);   // did NOT navigate away
});

test('footer "Contact" opens an on-page modal without navigating', async ({ page }) => {
  const url = page.url();
  await page.locator('a[data-i18n="footer.contact"]:visible').first().click();
  await expect(page.locator('#contactOverlay')).toBeVisible();
  await expect(page.locator('#contactMailBtn')).toHaveAttribute('href', /mailto:/);
  expect(page.url()).toBe(url);
});

test('info modals close via Escape and backdrop click', async ({ page }) => {
  await page.evaluate(() => openLegalModal());
  await expect(page.locator('#legalOverlay')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#legalOverlay')).toBeHidden();

  await page.evaluate(() => openContactModal());
  await expect(page.locator('#contactOverlay')).toBeVisible();
  await page.locator('#contactOverlay').click({ position: { x: 5, y: 5 } });  // backdrop
  await expect(page.locator('#contactOverlay')).toBeHidden();
});
