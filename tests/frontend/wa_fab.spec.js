const { test, expect } = require('@playwright/test');
const { bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('WhatsApp feedback FAB links to the right number and shows the annotation', async ({ page }) => {
  const link = page.locator('.wa-fab-btn');
  // href now carries a language-appropriate prefilled message (?text=...).
  await expect(link).toHaveAttribute('href', /^https:\/\/wa\.me\/972528828232\?text=.+/);
  await expect(link).toHaveAttribute('target', '_blank');
  // The feedback annotation bubble is present.
  await expect(page.locator('#waFabBubble')).toBeVisible();
});

test('dismissing the FAB bubble collapses it and persists for the session', async ({ page }) => {
  await expect(page.locator('#waFab')).not.toHaveClass(/wa-collapsed/);
  await page.locator('#waFabClose').click();
  await expect(page.locator('#waFab')).toHaveClass(/wa-collapsed/);
  const flag = await page.evaluate(() => sessionStorage.getItem('waFabDismissed'));
  expect(flag).toBe('1');
});
