const { test, expect } = require('@playwright/test');
const { bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('each footer has a localized WhatsApp feedback button', async ({ page }) => {
  const footers = page.locator('p.footer');
  const links = page.locator('p.footer .footer-whatsapp');
  expect(await links.count()).toBe(await footers.count());
  for (let i = 0; i < await links.count(); i++) {
    await expect(links.nth(i)).toHaveAttribute(
      'href',
      /^https:\/\/wa\.me\/972528828232\?text=.+/,
    );
    await expect(links.nth(i)).toHaveAttribute('target', '_blank');
    await expect(links.nth(i)).not.toHaveAttribute('title', /.+/);
  }
  await expect(page.locator('p.footer:visible .footer-whatsapp')).toContainText('משוב בוואטסאפ');
});

test('the floating WhatsApp button and its tooltip are removed', async ({ page }) => {
  await expect(page.locator('#waFab, .wa-fab, .wa-fab-btn, .wa-fab-bubble')).toHaveCount(0);
  await expect(page.getByText('בעיה? משוב? ספרו לנו')).toHaveCount(0);
});
