const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp } = require('./helpers');

// Phone keyboards with "auto-space after punctuation" treat a geresh as
// punctuation: typing ג׳ונסון came out "ג׳ ונסון" (field report 2026-09-24).
// The editor never alters text by itself; guardGereshAutospace removes ONLY
// the keyboard's space. A real keyboard can't run here, so these replay the
// exact input events such keyboards send (value + insertText data).
async function keyboard(locator, steps) {
  await locator.evaluate((el, steps) => {
    for (const [value, data] of steps) {
      el.value = value;
      el.setSelectionRange(value.length, value.length);
      el.dispatchEvent(new InputEvent('input', { inputType: 'insertText', data, bubbles: true }));
    }
  }, steps);
}

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('caption: the keyboard space after a geresh is removed, both keyboard patterns', async ({ page }) => {
  await runFullUpload(page);
  const input = page.locator('.caption-input').first();
  // Pattern A: the space rides in with the next letter (" ו").
  await keyboard(input, [['ג', 'ג'], ['ג׳', '׳'], ['ג׳ ו', ' ו'], ['ג׳ונ', 'נ']]);
  await expect(input).toHaveValue('ג׳ונ');
  // Pattern B: the space is appended to the punctuation ("׳ "), then a letter.
  await keyboard(input, [['ג', 'ג'], ["ג' ", "' "], ["ג' ו", 'ו']]);
  await expect(input).toHaveValue("ג'ו");
  // Typographic apostrophe too.
  await keyboard(input, [['ג', 'ג'], ['ג’', '’'], ['ג’ ו', ' ו']]);
  await expect(input).toHaveValue('ג’ו');
});

test('caption: a space the user typed is kept, and non-Hebrew text is untouched', async ({ page }) => {
  await runFullUpload(page);
  const input = page.locator('.caption-input').first();
  // "ה׳ באייר": geresh, then a separately typed space, then a letter.
  await keyboard(input, [['ה', 'ה'], ['ה׳', '׳'], ['ה׳ ', ' '], ['ה׳ ב', 'ב']]);
  await expect(input).toHaveValue('ה׳ ב');
  await keyboard(input, [["rock '", "'"], ["rock ' n", ' n']]);
  await expect(input).toHaveValue("rock ' n");
  // A geresh ending a word before a new word typed as one chunk with the
  // space only counts when a HEBREW letter precedes the geresh.
  await keyboard(input, [['5׳', '׳'], ['5׳ ב', ' ב']]);
  await expect(input).toHaveValue('5׳ ב');
});

test('hook text gets the same guard', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });
  await page.click('#hookOption0');
  const ta = page.locator('#hookText0');
  await keyboard(ta, [['ג', 'ג'], ['ג׳', '׳'], ['ג׳ ו', ' ו']]);
  await expect(ta).toHaveValue('ג׳ו');
});
