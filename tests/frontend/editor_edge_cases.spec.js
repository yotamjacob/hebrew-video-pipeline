/**
 * Caption-list editing + hook timing edge cases, and the restore-defaults
 * buttons (captions + hook) with their confirmation flow.
 */
const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

// ── Caption list editing ─────────────────────────────────────────────────────

test('split adds a row, delete removes it, undo restores the text', async ({ page }) => {
  await runFullUpload(page);
  await expect(page.locator('.caption-row')).toHaveCount(3);

  await page.locator('.cap-btn-split').first().click();
  await expect(page.locator('.caption-row')).toHaveCount(4);

  await page.locator('.cap-btn-del').first().click();
  await expect(page.locator('.caption-row')).toHaveCount(3);

  const inp = page.locator('.caption-input').first();
  const before = await inp.inputValue();
  await inp.click();
  await inp.fill('משהו חדש');
  await expect(page.locator('#capUndoBtn')).toBeEnabled();
  await page.click('#capUndoBtn');
  // Undo restores the previous state (the text edit is undone).
  await expect(page.locator('.caption-input').first()).not.toHaveValue('משהו חדש');
});

test('the last remaining caption row cannot be deleted', async ({ page }) => {
  await runFullUpload(page);
  // Delete rows down to one.
  await page.locator('.cap-btn-del').first().click();
  await page.locator('.cap-btn-del').first().click();
  await expect(page.locator('.caption-row')).toHaveCount(1);
  await expect(page.locator('.cap-btn-del').first()).toBeDisabled();
});

test('caption time edits flow into the burn payload', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  await runFullUpload(page);
  let burnBody = null;
  await page.route(/\/burn\/?(\?|$)/, async (route, request) => {
    try { burnBody = JSON.parse(request.postData() || '{}'); } catch (_) {}
    await route.fallback();
  });
  const firstStart = page.locator('.caption-row').first().locator('.caption-time-input').first();
  await firstStart.fill('0.9');
  await firstStart.dispatchEvent('change');
  await page.click('#runBtn');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await expect.poll(() => (burnBody ? 'y' : 'n'), { timeout: 10_000 }).toBe('y');
  expect(burnBody.captions[0].start).toBeCloseTo(0.9, 2);
});

// ── Hook timing edges ────────────────────────────────────────────────────────

async function pickHook(page) {
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });
  await page.click('#hookOption0');
  await page.waitForSelector('#hookControls', { state: 'visible' });
}

test('a hook window that starts later than the playhead hides the overlay', async ({ page }) => {
  await runFullUpload(page);
  await pickHook(page);
  const setStart = v => page.evaluate(v => {
    const el = document.getElementById('hookStartSec');
    el.value = String(v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    updatePlayerHook();
  }, v);
  await setStart(0);
  await expect(page.locator('#playerHook')).toBeVisible();
  await setStart(25);          // playhead is at ~0 - outside [25, 28]
  await expect(page.locator('#playerHook')).toBeHidden();
});

// ── Restore defaults ─────────────────────────────────────────────────────────

test('caption restore-defaults asks for confirmation, then resets controls + storage', async ({ page }) => {
  await runFullUpload(page);
  // Mess up the style.
  await page.locator('#captionFontSizeSlider').selectOption('90');
  await page.locator('#fontSelect').selectOption('Rubik');
  await page.evaluate(() => {
    const c = document.getElementById('capFontColor');
    c.value = '#FF0000'; c.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.click('#capStyleResetBtn');
  await expect(page.locator('#confirmOverlay')).toBeVisible();
  await page.click('#confirmOk');
  await expect(page.locator('#captionFontSizeSlider')).toHaveValue('48');
  await expect(page.locator('#fontSelect')).toHaveValue('Heebo');
  await expect(page.locator('#capFontColor')).toHaveValue('#ffffff');
  const stored = await page.evaluate(() => ({
    style: localStorage.getItem('captionStyle'),
    size:  localStorage.getItem('captionFontSize'),
  }));
  expect(stored.style).toBeNull();
  expect(stored.size).toBe('48');
});

test('cancelling the caption reset keeps the edited style', async ({ page }) => {
  await runFullUpload(page);
  await page.locator('#captionFontSizeSlider').selectOption('90');
  await page.click('#capStyleResetBtn');
  await expect(page.locator('#confirmOverlay')).toBeVisible();
  await page.click('#confirmCancel');
  await expect(page.locator('#captionFontSizeSlider')).toHaveValue('90');
});

test('hook restore-defaults resets controls but keeps the hook text', async ({ page }) => {
  await runFullUpload(page);
  await pickHook(page);
  await page.locator('#hookText0').fill('הטקסט שלי נשאר');
  await page.locator('#hookFontSize').selectOption('200');
  await page.evaluate(() => {
    const p = document.getElementById('hookPosition');
    p.value = '90'; p.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await page.click('#hookStyleResetBtn');
  await expect(page.locator('#confirmOverlay')).toBeVisible();
  await page.click('#confirmOk');
  await expect(page.locator('#hookFontSize')).toHaveValue('100');
  await expect(page.locator('#hookPosition')).toHaveValue('10');
  await expect(page.locator('#hookText0')).toHaveValue('הטקסט שלי נשאר');   // text untouched
});
