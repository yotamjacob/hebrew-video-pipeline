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

test('the WhatsApp button uses the same design as every footer action', async ({ page }) => {
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

test('footer actions form a symmetrical equal-size two-by-two grid', async ({ page }) => {
  const footer = page.locator('p.footer:visible');
  const links = footer.locator('.footer-contact');
  await expect(links).toHaveCount(4);

  const footerBox = await footer.boundingBox();
  const boxes = await links.evaluateAll(elements => elements.map(element => {
    const rect = element.getBoundingClientRect();
    return {
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
      right: rect.right,
    };
  }));
  const close = (a, b) => Math.abs(a - b) < 1;

  expect(new Set(boxes.map(box => Math.round(box.width))).size).toBe(1);
  expect(new Set(boxes.map(box => Math.round(box.height))).size).toBe(1);
  expect(close(boxes[0].y, boxes[1].y)).toBe(true);
  expect(close(boxes[2].y, boxes[3].y)).toBe(true);
  expect(close(boxes[0].x, boxes[2].x)).toBe(true);
  expect(close(boxes[1].x, boxes[3].x)).toBe(true);
  expect(boxes[2].y).toBeGreaterThan(boxes[0].y);
  expect(boxes.every(box =>
    box.x >= footerBox.x - 1 && box.right <= footerBox.x + footerBox.width + 1
  )).toBe(true);
});

test('the floating WhatsApp button and its tooltip are removed', async ({ page }) => {
  await expect(page.locator('#waFab, .wa-fab, .wa-fab-btn, .wa-fab-bubble')).toHaveCount(0);
  await expect(page.getByText('בעיה? משוב? ספרו לנו')).toHaveCount(0);
});
