// Footer contract: three actions (contact / legal / delete-account) in one
// symmetric equal-width row, the version badge centered below, and no
// WhatsApp link or floating feedback button anywhere (removed 2026-08-08).
const { test, expect } = require('@playwright/test');
const { bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('the WhatsApp footer button and floating FAB are gone everywhere', async ({ page }) => {
  await expect(page.locator('.footer-whatsapp')).toHaveCount(0);
  await expect(page.locator('a[href*="wa.me"]')).toHaveCount(0);
  await expect(page.locator('#waFab, .wa-fab, .wa-fab-btn, .wa-fab-bubble')).toHaveCount(0);
  await expect(page.getByText('בעיה? משוב? ספרו לנו')).toHaveCount(0);
});

test('every footer action uses the same design', async ({ page }) => {
  const links = page.locator('p.footer:visible .footer-contact');
  const styles = await links.evaluateAll(elements => elements.map(element => {
    const style = getComputedStyle(element);
    return {
      color: style.color,
      backgroundColor: style.backgroundColor,
      borderColor: style.borderColor,
      borderWidth: style.borderWidth,
      borderRadius: style.borderRadius,
      fontWeight: style.fontWeight,
      padding: style.padding,
    };
  }));
  expect(styles.length).toBeGreaterThan(1);
  expect(new Set(styles.map(style => JSON.stringify(style))).size).toBe(1);
});

test('footer actions form one symmetric equal-size row', async ({ page }) => {
  const footer = page.locator('p.footer:visible').first();
  const links = footer.locator('.footer-contact');
  await expect(links).toHaveCount(3);

  const footerBox = await footer.boundingBox();
  const boxes = await links.evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, right: rect.right };
  }));
  const close = (a, b) => Math.abs(a - b) < 1;

  // Equal-size buttons on a single row, all inside the footer bounds.
  expect(new Set(boxes.map(box => Math.round(box.width))).size).toBe(1);
  expect(new Set(boxes.map(box => Math.round(box.height))).size).toBe(1);
  expect(close(boxes[0].y, boxes[1].y)).toBe(true);
  expect(close(boxes[1].y, boxes[2].y)).toBe(true);
  expect(boxes.every(box =>
    box.x >= footerBox.x - 1 && box.right <= footerBox.x + footerBox.width + 1
  )).toBe(true);

  // Version badge sits on its own row below the actions.
  const versionBox = await footer.locator('.footer-version').boundingBox();
  expect(versionBox.y).toBeGreaterThan(boxes[0].y + boxes[0].height - 1);
});

test('contact action and modal agree on the contact address', async ({ page }) => {
  const contact = page.locator('p.footer:visible a[data-i18n="footer.contact"]').first();
  await expect(contact).toHaveAttribute('href', 'mailto:hebrewpipeline@gmail.com');

  await page.evaluate(() => openContactModal());
  await expect(page.locator('#contactMailBtn')).toHaveAttribute('href', 'mailto:hebrewpipeline@gmail.com');
  await expect(page.locator('#contactMailBtn')).toContainText('hebrewpipeline@gmail.com');
});
