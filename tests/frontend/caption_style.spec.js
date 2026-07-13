/** Caption styling (Phase C): text/outline colours + background box flow into
 *  the live preview, the exact-frame request and the burn payload. */
const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp, runFullUpload } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

async function setColor(page, id, value) {
  await page.locator('#' + id).evaluate((el, v) => {
    el.value = v;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, value);
}

test('style controls update the live caption overlay', async ({ page }) => {
  await runFullUpload(page);
  await setColor(page, 'capFontColor', '#ff0000');
  await expect.poll(() =>
    page.evaluate(() => document.getElementById('playerCap').style.color)
  ).toBe('rgb(255, 0, 0)');
  // Background box appears once opacity > 0
  await page.locator('#capBgOpacity').evaluate(el => {
    el.value = '60'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const bg = await page.evaluate(() => document.getElementById('playerCap').style.background);
  expect(bg).toContain('rgba(0, 0, 0, 0.6)');
});

test('burn payload carries caption_style', async ({ page }) => {
  await runFullUpload(page);
  await setColor(page, 'capFontColor', '#ffee00');
  await page.locator('#capBorderSize').evaluate(el => {
    el.value = '4'; el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  let burnBody = null;
  await page.route(/\/burn\/\?/, (route, request) => {
    burnBody = JSON.parse(request.postData());
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnBody, { timeout: 10_000 }).not.toBeNull();
  expect(burnBody.caption_style).toMatchObject({
    font_color: '#ffee00', border_size: 4, bg_opacity: 0,
  });
});

test('style persists across reloads (localStorage)', async ({ page }) => {
  await runFullUpload(page);
  await setColor(page, 'capFontColor', '#00ff00');
  await page.reload();
  await expect.poll(() =>
    page.evaluate(() => document.getElementById('capFontColor').value)
  ).toBe('#00ff00');
});
