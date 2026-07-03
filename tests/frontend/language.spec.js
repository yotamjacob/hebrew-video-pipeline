// Language toggle: English ⇄ Hebrew with full RTL
const { test, expect } = require('@playwright/test');
const { bootApp, mockAllApis, selectFile } = require('./helpers');

test('site boots in English LTR with the toggle visible everywhere', async ({ page }) => {
  await bootApp(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await expect(page.locator('#langToggle')).toBeVisible();
  await expect(page.locator('#langToggle')).toHaveText('עברית');
  await expect(page.locator('.hero-sub')).toHaveText('Hebrew-first social video studio');
  await expect(page.locator('#tabPipeline')).toHaveText('🎬 Create');
});

test('toggle switches to Hebrew, sets RTL, and translates static text', async ({ page }) => {
  await bootApp(page);
  await page.click('#langToggle');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('html')).toHaveAttribute('lang', 'he');
  await expect(page.locator('#langToggle')).toHaveText('English');
  await expect(page.locator('.hero-sub')).toHaveText('סטודיו לסרטוני רשת בעברית');
  await expect(page.locator('#tabPipeline')).toContainText('יצירה');
  await expect(page.locator('.upload-main')).toHaveText('גררו את הסרטון לכאן');
  await expect(page.locator('#runBtn')).toContainText('הפעלת העיבוד');
  // toggle back
  await page.click('#langToggle');
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await expect(page.locator('.upload-main')).toHaveText('Drop your video here');
});

test('language choice persists across reloads', async ({ page }) => {
  await bootApp(page);
  await page.click('#langToggle');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('.upload-main')).toHaveText('גררו את הסרטון לכאן');
});

test('login view is translated too', async ({ page }) => {
  // No token → auth view shows
  await page.route(/fonts\.googleapis\.com/, r =>
    r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/fonts\.gstatic\.com/, r => r.abort());
  await page.goto('/');
  await page.waitForSelector('#authView', { state: 'visible' });
  await expect(page.locator('#langToggle')).toBeVisible();
  await page.click('#langToggle');
  await expect(page.locator('#authSubmitBtn')).toHaveText('התחברות');
  await expect(page.locator('#authModeBtn')).toHaveText('חדשים כאן? צרו חשבון');
});

test('state-driven labels re-render on language switch', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await selectFile(page);
  // EV desc (state-driven innerHTML) — switch to Hebrew and check
  await page.click('label[for="ev_esrgan"]');
  await page.click('#langToggle');
  await expect(page.locator('#enhanceVideoDesc')).toContainText('מוסיף כמה דקות');
  const warnColor = await page.locator('#enhanceVideoDesc .ev-warn').evaluate(el => getComputedStyle(el).color);
  expect(warnColor).toBe('rgb(220, 38, 38)');
  // Aggressiveness desc follows too
  await expect(page.locator('#aggrDesc')).toContainText('אגרסיבי');
});

test('RTL flips layout primitives correctly', async ({ page }) => {
  await bootApp(page);
  await page.click('#langToggle');
  // Toggle thumb sits on the right in RTL
  const thumbRight = await page.locator('#cutSilences ~ .toggle-thumb').evaluate(el => getComputedStyle(el).right);
  expect(thumbRight).toBe('3px');
  // Option rows lay out right-to-left: icon's box starts right of the text box
  const [iconBox, textBox] = await page.evaluate(() => {
    const row = document.querySelector('.option-row');
    return [row.querySelector('.option-icon').getBoundingClientRect().x,
            row.querySelector('.option-text').getBoundingClientRect().x];
  });
  expect(iconBox).toBeGreaterThan(textBox);
});
