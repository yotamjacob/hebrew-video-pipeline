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
  await page.route(/accounts\.google\.com\/gsi/, r =>
    r.fulfill({ status: 200, contentType: 'text/javascript', body: '' }));
  await page.goto('/');
  await page.waitForSelector('#authView', { state: 'visible' });
  await expect(page.locator('#langToggle')).toBeVisible();
  // Hebrew by default (choice landing: existing user / new user)
  await expect(page.locator('#authView .options-title')).toHaveText('התחברות');
  await expect(page.locator('#authChoiceExisting')).toHaveText('יש לי חשבון');
  // …and English after toggling
  await page.click('#langToggle');
  await expect(page.locator('#authView .options-title')).toHaveText('Sign In');
  await expect(page.locator('#authChoiceExisting')).toHaveText('I have an account');
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
  // Aggressiveness desc follows too (default is now the most-aggressive level)
  await expect(page.locator('#aggrDesc')).toContainText('Very aggressive');
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

test('booting with English persisted fills the guide (no fallback text in the HTML)', async ({ page }) => {
  // The guide accordion's titles and bodies are EMPTY elements filled only by
  // applyI18n(). The boot pass used to be skipped for English ("the HTML is
  // already English"), which rendered a completely blank guide for anyone who
  // reopened the app with EN persisted.
  await page.addInitScript(() => localStorage.setItem('hebpipe_lang', 'en'));
  await bootApp(page);
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page.click('#tabGuide');
  await expect(page.locator('#guideView')).toBeVisible();
  await expect(page.locator('.guide-intro')).not.toHaveText('');
  await expect(page.locator('#gsec-getstarted .guide-sec-title'))
    .toHaveText('Getting started - account & sign in');
  await page.click('#gsec-getstarted .guide-sec-head');
  await expect(page.locator('#gsec-getstarted .guide-sec-text'))
    .toContainText('Signing in is passwordless');
});
