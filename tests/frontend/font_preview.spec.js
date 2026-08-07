const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp, DEFAULT_CAPTIONS } = require('./helpers');

// Must match #fontSelect in index.html, the Google Fonts <link>, and the
// server-installed set in pipeline_core.py.
const FONTS = [
  'Heebo', 'Assistant', 'Frank Ruhl Libre', 'Secular One', 'Rubik',
  'Suez One', 'Karantina', 'Playpen Sans Hebrew', 'Miriam Libre',
];

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('the font selector offers exactly the supported faces', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  const values = await page.locator('#fontSelect option').evaluateAll(
    opts => opts.map(o => o.value));
  expect(values).toEqual(FONTS);
});

test('every font option is applied to the caption preview overlay', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  await expect(page.locator('#captionPlayer')).toBeVisible();
  const cap = page.locator('#playerCap');
  for (const font of FONTS) {
    await page.evaluate(() => toggleCapDesign(true));
    await page.selectOption('#fontSelect', font);
    // The chosen family is applied to the overlay (ahead of the sans-serif fallback).
    const ff = await cap.evaluate(el => getComputedStyle(el).fontFamily);
    expect(ff.replace(/["']/g, ''), `font ${font} not applied`).toContain(font);
  }
});

test('every offered font actually loads (no silent fallback in the preview)', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  // The editor preloads all faces on open; force+await each and confirm it
  // registers, so switching to it repaints in the real face rather than a fallback.
  for (const font of FONTS) {
    const loaded = await page.evaluate(async (f) => {
      try {
        await Promise.all([
          document.fonts.load(`700 24px '${f}'`),
          document.fonts.load(`400 24px '${f}'`),
        ]);
      } catch (_) { /* ignore - checked below */ }
      return document.fonts.check(`700 24px '${f}'`) || document.fonts.check(`400 24px '${f}'`);
    }, font);
    expect(loaded, `font ${font} should be loaded`).toBe(true);
  }
});
