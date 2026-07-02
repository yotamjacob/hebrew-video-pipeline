const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, runFullUpload, DEFAULT_CAPTIONS } = require('./helpers');

const MOCK_CUTS = [
  { index: 0, start: 2.0, end: 3.2, duration: 1.2, before: 'שלום', after: 'עולם' },
  { index: 1, start: 5.5, end: 6.1, duration: 0.6, before: 'בדיקה', after: 'מבחן' },
];

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('cuts card hidden when response has no cuts', async ({ page }) => {
  await runFullUpload(page);
  await expect(page.locator('#captionEditorCard')).toBeVisible();
  await expect(page.locator('#cutsCard')).toBeHidden();
});

test('cuts card lists removed silences with summary', async ({ page }) => {
  await runFullUpload(page, { cuts: MOCK_CUTS });
  await expect(page.locator('#cutsCard')).toBeVisible();
  await expect(page.locator('.cut-row')).toHaveCount(2);
  await expect(page.locator('#cutsSummary')).toContainText('2 pauses');
  await expect(page.locator('#cutsSummary')).toContainText('1.8s removed');
});

test('apply button appears only when selection changes', async ({ page }) => {
  await runFullUpload(page, { cuts: MOCK_CUTS });
  await expect(page.locator('#applyCutsBtn')).toBeHidden();
  await page.locator('.cut-check').first().check();
  await expect(page.locator('#applyCutsBtn')).toBeVisible();
  await page.locator('.cut-check').first().uncheck();
  await expect(page.locator('#applyCutsBtn')).toBeHidden();
});

test('restore flow posts indexes and updates captions', async ({ page }) => {
  let rerenderBody = null;
  await page.route(/\/rerender\/?$/, (route, request) => {
    if (request.method() === 'POST') {
      rerenderBody = JSON.parse(request.postData());
      return route.fulfill({ status: 202, contentType: 'application/json',
                             body: JSON.stringify({ call_id: 'mock-rerender-id' }) });
    }
    return route.fallback();
  });
  await page.route(/\/rerender_poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({
                  captions: [...DEFAULT_CAPTIONS, { start: 9.0, end: 10.5, text: 'כתובית חדשה' }],
                  video_key: 'new-video-key_cut.mp4',
                }) }));
  await runFullUpload(page, { cuts: MOCK_CUTS });
  await page.locator('.cut-check').first().check();
  await page.click('#applyCutsBtn');
  // POST body carries the restored boundary index
  await expect.poll(() => rerenderBody).toBeTruthy();
  expect(rerenderBody.restored).toEqual([0]);
  expect(rerenderBody.upload_key).toBeTruthy();
  // captions editor re-renders with the extra caption from the re-render
  await expect(page.locator('.caption-input')).toHaveCount(DEFAULT_CAPTIONS.length + 1);
  // selection is now "applied" → button hides again
  await expect(page.locator('#applyCutsBtn')).toBeHidden();
});

test('rerender failure shows error and keeps card usable', async ({ page }) => {
  await page.route(/\/rerender\/?$/, r =>
    r.fulfill({ status: 500, contentType: 'application/json',
                body: JSON.stringify({ error: 'source expired' }) }));
  await runFullUpload(page, { cuts: MOCK_CUTS });
  await page.locator('.cut-check').first().check();
  await page.click('#applyCutsBtn');
  await expect(page.locator('#cutsError')).toBeVisible();
  await expect(page.locator('#cutsError')).toContainText('source expired');
});
