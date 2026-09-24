const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp, DEFAULT_CAPTIONS } = require('./helpers');

// Editor draft (2026-09-24, user: "make everything persistent to refresh to
// not lose data"): a captions job reaches History only by its burn, so a
// refresh in the editor used to lose the processed video and every edit. The
// editor now autosaves a draft (History edit-state shape) and boot reopens it.
const draft = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('hebpipe_editor_draft') || 'null'));

test.beforeEach(async ({ page }) => { await bootApp(page); });

test('caption edits and style survive a refresh; the editor reopens on the same video', async ({ page }) => {
  await runFullUpload(page);
  const first = page.locator('.caption-input').first();
  await first.fill('טקסט ערוך אחרי עיבוד');
  await page.evaluate(() => {           // a style change rides the draft too
    const el = document.getElementById('capFontColor');
    el.value = '#FFE45C';
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect.poll(async () => (await draft(page))?.captions?.[0]?.text).toBe('טקסט ערוך אחרי עיבוד');
  const saved = await draft(page);
  expect(saved.src_key).toBeTruthy();
  expect(saved.caption_style.font_color.toUpperCase()).toBe('#FFE45C');
  expect(saved.captions).toHaveLength(DEFAULT_CAPTIONS.length);

  await page.reload();
  await expect(page.locator('#captionEditorCard')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.caption-input').first()).toHaveValue('טקסט ערוך אחרי עיבוד');
  await expect(page.locator('.caption-input')).toHaveCount(DEFAULT_CAPTIONS.length);
  expect((await page.inputValue('#capFontColor')).toUpperCase()).toBe('#FFE45C');
  expect((await draft(page)).src_key).toBe(saved.src_key);            // same processed video
});

test('the editor draft is saved the moment the page hides (no debounce loss)', async ({ page }) => {
  await runFullUpload(page);
  await page.locator('.caption-input').first().fill('נשמר מיד');
  // Hide right away - before the 600 ms debounce fires.
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect((await draft(page)).captions[0].text).toBe('נשמר מיד');
});

test('an expired draft is dropped, not restored', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('hebpipe_editor_draft', JSON.stringify({ v: 1, ts: Date.now() - 37 * 3600 * 1000,
      src_key: 'u1__old_cut.mp4', name: 'old.mp4', captions: [{ start: 0, end: 1, text: 'ישן' }] }));
  });
  await page.reload();
  await page.waitForTimeout(400);
  await expect(page.locator('#captionEditorCard')).toBeHidden();
  expect(await draft(page)).toBeNull();
});

test('a new run replaces the previous editing session', async ({ page }) => {
  await runFullUpload(page);
  await page.locator('.caption-input').first().fill('טיוטה ישנה');
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect((await draft(page)).captions[0].text).toBe('טיוטה ישנה');
  // Clearing happens at the start of run(); prove the hook is wired.
  const src = await page.evaluate(() => fetch('/app.js').then((r) => r.text()));
  const i = src.indexOf('if (!(await _confirmQuotaUse())) return;');
  expect(src.slice(i, i + 200)).toContain('clearEditorDraft();');
  for (const marker of ["_clearToken();\n    clearEditorDraft();", 'clearSavedJob();\n    clearEditorDraft();']) {
    expect(src).toContain(marker);                                  // logout / delete account / start over
  }
});

test('leaving while editing still warns', async ({ page }) => {
  await runFullUpload(page);
  expect(await page.evaluate(() => {
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  })).toBe(true);
});

test('a refresh keeps the same session\'s zoom; a NEW video after it starts with the punch-in off', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#tabBtnEffects');
  await page.check('#fxAutoZoom');
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect((await draft(page)).caption_style.auto_zoom).toBe(true);
  await page.reload();
  await expect(page.locator('#captionEditorCard')).toBeVisible({ timeout: 10_000 });
  await page.click('#tabBtnEffects');
  await expect(page.locator('#fxAutoZoom')).toBeChecked();          // same video, same session
  // A new run resets it (the directive: OFF for every new video).
  const src = await page.evaluate(() => fetch('/app.js').then((r) => r.text()));
  const i = src.indexOf('clearEditorDraft();   // a new job replaces the previous editing session');
  expect(src.slice(i, i + 400)).toContain("z.checked = false");
});
