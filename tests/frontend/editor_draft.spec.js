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

test('Start over wipes the draft for good - the reload it triggers must not re-save it', async ({ page }) => {
  // Field report (2026-09-28): "start over just reloads the same video". The
  // reload fires pagehide, whose autosave wrote the draft back AFTER Start
  // over had cleared it, so boot restored the editor the user just left.
  await runFullUpload(page);
  await expect.poll(async () => (await draft(page))?.src_key).toBeTruthy();
  await page.click('#startOverBtn');
  await page.click('#confirmOk');
  await page.waitForLoadState('load');
  await page.waitForTimeout(600);   // past boot's deferred draft restore
  expect(await draft(page)).toBeNull();
  await expect(page.locator('#captionEditorCard')).toBeHidden();
});

test('a fresh launch does not reopen the last video; History offers it as an unfinished edit', async ({ page }) => {
  // User directive 2026-09-28: reopening the app must start clean. Only an
  // in-page refresh restores the draft; a launch (navigate) leaves it in
  // History, where it can be continued or discarded.
  await runFullUpload(page);
  await page.locator('.caption-input').first().fill('טיוטה מההפעלה הקודמת');
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  expect((await draft(page)).captions[0].text).toBe('טיוטה מההפעלה הקודמת');
  await page.route(/\/jobs\/?(\?.*)?$/, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"jobs":[]}' }));

  await page.goto('/');                       // a launch, not a reload
  await page.waitForTimeout(700);             // past boot's deferred restore window
  await expect(page.locator('#captionEditorCard')).toBeHidden();
  expect((await draft(page)).src_key).toBeTruthy();   // kept, not dropped

  await page.click('#tabHistory');
  const row = page.locator('#historyDraft .history-draft');
  await expect(row).toBeVisible();
  await expect(row.locator('.history-name')).toHaveText('test.mp4');
  await expect(page.locator('#historyEmpty')).toBeHidden();

  await row.locator('.history-btn').first().click();      // continue editing
  await expect(page.locator('#captionEditorCard')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.caption-input').first()).toHaveValue('טיוטה מההפעלה הקודמת');

  await page.click('#tabHistory');
  await page.locator('#historyDraft .history-btn-danger').click();   // discard
  await page.click('#confirmOk');
  await expect(page.locator('#historyDraft .history-draft')).toHaveCount(0);
  expect(await draft(page)).toBeNull();
  await expect(page.locator('#historyEmpty')).toBeVisible();
});
