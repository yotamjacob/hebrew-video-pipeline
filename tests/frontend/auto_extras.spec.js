const { test, expect } = require('@playwright/test');
const { bootApp, mockAllApis, selectFile, API_BASE, DEFAULT_CAPTIONS } = require('./helpers');

test.beforeEach(async ({ page }) => { await bootApp(page); });

const setToggle = (page, id, v) => page.evaluate(({ id, v }) => {
  const el = document.getElementById(id); el.checked = v;
  el.dispatchEvent(new Event('change', { bubbles: true }));
}, { id, v });

test('auto B-roll/hook are gated on the captions toggle', async ({ page }) => {
  await mockAllApis(page);
  // Captions on by default -> children enabled.
  await expect(page.locator('#captionChildren')).not.toHaveClass(/disabled/);
  await expect(page.locator('#autoBroll')).toBeEnabled();
  await expect(page.locator('#autoHook')).toBeEnabled();
  // Captions off -> children greyed/disabled + "needs captions" note.
  await setToggle(page, 'burnCaptions', false);
  await expect(page.locator('#captionChildren')).toHaveClass(/disabled/);
  await expect(page.locator('#autoBroll')).toBeDisabled();
  await expect(page.locator('#captionChildrenNote')).toBeVisible();
});

test('enabling a child surfaces the extra-processing-time note', async ({ page }) => {
  await mockAllApis(page);
  await expect(page.locator('#autoExtraTimeNote')).toBeHidden();
  await setToggle(page, 'autoHook', true);
  await expect(page.locator('#autoExtraTimeNote')).toBeVisible();
});

test('toggled auto B-roll + hook auto-run after captions, in the background', async ({ page }) => {
  let hookCalled = false, brollCalled = false;
  await mockAllApis(page, { captions: DEFAULT_CAPTIONS });
  await page.route(new RegExp(`${API_BASE}/generate-hook/?$`), async (r) => { hookCalled = true; await r.fallback(); });
  await page.route(new RegExp(`${API_BASE}/stock-broll/?$`), async (r) => { brollCalled = true; await r.fallback(); });

  await selectFile(page);
  await setToggle(page, 'autoBroll', true);
  await setToggle(page, 'autoHook', true);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  // Both auto-triggered (in parallel) once captions were ready.
  await expect.poll(() => hookCalled, { timeout: 6000 }).toBe(true);
  await expect.poll(() => brollCalled, { timeout: 6000 }).toBe(true);
  // Their timers show as checklist rows in the processing card.
  await expect(page.locator('#checkHook')).toBeVisible();
  await expect(page.locator('#checkBroll')).toBeVisible();
  // Background mode: the caption editor is NOT locked, so the user can keep editing.
  await expect(page.locator('#captionEditorCard')).not.toHaveClass(/action-locked/);
});
