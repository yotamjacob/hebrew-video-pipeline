const { test, expect } = require('@playwright/test');
const { runFullUpload, DEFAULT_CAPTIONS, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('editing a caption input updates its value', async ({ page }) => {
  await runFullUpload(page);
  const input = page.locator('.caption-input').first();
  await input.fill('טקסט ערוך');
  await expect(input).toHaveValue('טקסט ערוך');
});

test('undo restores a deleted caption', async ({ page }) => {
  await runFullUpload(page);
  const rows = page.locator('#captionsList .caption-row');
  const before = await rows.count();
  await expect(page.locator('#capUndoBtn')).toBeDisabled();
  await rows.first().locator('.cap-btn-del').click();
  await expect(rows).toHaveCount(before - 1);
  await expect(page.locator('#capUndoBtn')).toBeEnabled();
  await page.click('#capUndoBtn');
  await expect(rows).toHaveCount(before);
  await expect(page.locator('#capUndoBtn')).toBeDisabled();
});

test('caption editor shows hook card', async ({ page }) => {
  await runFullUpload(page);
  // The hook card becomes visible alongside the caption editor
  await expect(page.locator('#hookCard')).toBeVisible();
});

test('caption editor shows generate hook button', async ({ page }) => {
  await runFullUpload(page);
  await expect(page.locator('#generateHookBtn')).toBeVisible();
});

test('run button switches to burn mode after captions load', async ({ page }) => {
  await runFullUpload(page);
  // After captions load, runBtn label should indicate burn
  const btnText = await page.locator('#runBtn').textContent();
  expect(btnText.toLowerCase()).toMatch(/burn|render|צריבה|הפקה/);
});

test('caption count matches API response', async ({ page }) => {
  const captions = [
    { start: 0, end: 1, text: 'ראשון' },
    { start: 2, end: 3, text: 'שני' },
    { start: 4, end: 5, text: 'שלישי' },
    { start: 6, end: 7, text: 'רביעי' },
  ];
  await runFullUpload(page, { captions });
  await expect(page.locator('.caption-input')).toHaveCount(4);
});

test('caption timing info is shown per row', async ({ page }) => {
  await runFullUpload(page);
  // Every caption row should have a time display
  const rows  = page.locator('.caption-row');
  const times = page.locator('.caption-time');
  const count = await rows.count();
  expect(count).toBeGreaterThan(0);
  await expect(times).toHaveCount(count);
});

test('caption editor is scrollable when many captions', async ({ page }) => {
  const many = Array.from({ length: 20 }, (_, i) => ({
    start: i * 3, end: i * 3 + 2, text: `פסקה ${i + 1}`,
  }));
  await runFullUpload(page, { captions: many });
  const count = await page.locator('.caption-input').count();
  expect(count).toBe(20);
  // Scroll to the last caption
  await page.locator('.caption-input').last().scrollIntoViewIfNeeded();
  await expect(page.locator('.caption-input').last()).toBeInViewport();
});
