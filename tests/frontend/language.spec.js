// Language toggle: English ⇄ Hebrew with full RTL
const { test, expect } = require('@playwright/test');
const { bootApp, mockAllApis, selectFile } = require('./helpers');

test('site boots in Hebrew RTL by default with the toggle visible', async ({ page }) => {
  await bootApp(page);
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('html')).toHaveAttribute('lang', 'he');
  await expect(page.locator('#langToggle')).toBeVisible();
  await expect(page.locator('#langToggle')).toHaveText('English');
  await expect(page.locator('.hero-sub')).toHaveText('עריכת וידאו בעברית בלחיצת כפתור');
  await expect(page.locator('#tabPipeline')).toContainText('יצירה');
});

test('toggle switches to English, sets LTR, and translates static text', async ({ page }) => {
  await bootApp(page);
  await page.click('#langToggle');
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('#langToggle')).toHaveText('עברית');
  await expect(page.locator('.hero-sub')).toHaveText('Hebrew video editing at the click of a button');
  await expect(page.locator('#tabPipeline')).toHaveText('Create');
  await expect(page.locator('.upload-main')).toHaveText('Drop your video here');
  await expect(page.locator('#runBtn')).toContainText('Run Pipeline');
  // toggle back
  await page.click('#langToggle');
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
  await expect(page.locator('.upload-main')).toHaveText('גררו את הסרטון לכאן');
});

test('language choice persists across reloads', async ({ page }) => {
  await bootApp(page);
  await page.click('#langToggle');
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('dir', 'ltr');
  await expect(page.locator('.upload-main')).toHaveText('Drop your video here');
});

test('login view is translated too', async ({ page }) => {
  // No token → auth view shows
  await page.route(/fonts\.googleapis\.com/, r =>
    r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/fonts\.gstatic\.com/, r => r.abort());
  await page.goto('/');
  await page.waitForSelector('#authView', { state: 'visible' });
  await expect(page.locator('#langToggle')).toBeVisible();
  // Hebrew by default
  await expect(page.locator('#authSubmitBtn')).toHaveText('התחברות');
  await expect(page.locator('#authModeBtn')).toHaveText('חדשים כאן? צרו חשבון');
  // …and English after toggling
  await page.click('#langToggle');
  await expect(page.locator('#authSubmitBtn')).toHaveText('Sign in');
  await expect(page.locator('#authModeBtn')).toHaveText('New here? Create an account');
});

test('state-driven labels re-render on language switch', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await selectFile(page);
  // EV desc (state-driven innerHTML) - Hebrew by default, English after toggle
  await page.click('label[for="ev_esrgan"]');
  await expect(page.locator('#enhanceVideoDesc')).toContainText('מוסיף כמה דקות');
  await page.click('#langToggle');
  await expect(page.locator('#enhanceVideoDesc')).toContainText('adds a few minutes');
  const warnColor = await page.locator('#enhanceVideoDesc .ev-warn').evaluate(el => getComputedStyle(el).color);
  expect(warnColor).toBe('rgb(176, 80, 60)');   // --red (muted brick)
  // Aggressiveness desc follows too
  await expect(page.locator('#aggrDesc')).toContainText('Aggressive');
});

test('RTL flips layout primitives correctly', async ({ page }) => {
  await bootApp(page);
  // Hebrew (RTL) is the default - no toggle needed
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
