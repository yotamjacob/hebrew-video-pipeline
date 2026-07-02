const { test, expect } = require('@playwright/test');
const { mockAllApis, selectFile, runFullUpload, DEFAULT_CAPTIONS, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('page loads without unhandled JS errors', async ({ page }) => {
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.waitForLoadState('domcontentloaded');
  // Allow SW-related extension noise but not real app errors
  const realErrors = errors.filter(m => !m.includes('message channel closed'));
  expect(realErrors).toHaveLength(0);
});

test('file input exists and accepts video files', async ({ page }) => {
  const input = page.locator('#fileInput');
  await expect(input).toHaveAttribute('type', 'file');
  await expect(input).toHaveAttribute('accept', /video/);
});

test('run button is disabled before file selection', async ({ page }) => {
  await expect(page.locator('#runBtn')).toBeDisabled();
});

test('run button enables after file is selected', async ({ page }) => {
  await mockAllApis(page);
  await selectFile(page);
  await expect(page.locator('#runBtn')).toBeEnabled();
});

test('clicking process shows upload/processing UI', async ({ page }) => {
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  // Either the processing status or the caption editor (fast mock) should appear
  await expect(page.locator('#statusProc, #captionEditorCard')).toBeVisible({ timeout: 5_000 });
});

test('caption editor appears after processing completes', async ({ page }) => {
  await runFullUpload(page);
  await expect(page.locator('#captionEditorCard')).toBeVisible();
});

test('captions from response are rendered in the editor', async ({ page }) => {
  await runFullUpload(page, { captions: DEFAULT_CAPTIONS });
  const inputs = page.locator('.caption-input');
  await expect(inputs).toHaveCount(DEFAULT_CAPTIONS.length);
  await expect(inputs.first()).toHaveValue(DEFAULT_CAPTIONS[0].text);
  await expect(inputs.nth(1)).toHaveValue(DEFAULT_CAPTIONS[1].text);
});

test('caption inputs are right-to-left (RTL)', async ({ page }) => {
  await runFullUpload(page);
  const firstInput = page.locator('.caption-input').first();
  await expect(firstInput).toHaveAttribute('dir', 'rtl');
});

test('caption time spans are rendered', async ({ page }) => {
  await runFullUpload(page);
  const times = page.locator('.caption-time');
  await expect(times).toHaveCount(DEFAULT_CAPTIONS.length);
  // Each time wrap has a start-time input with a numeric value
  const firstStartInput = times.first().locator('.caption-start');
  const val = await firstStartInput.inputValue();
  expect(parseFloat(val)).toBeGreaterThanOrEqual(0);
});

test('empty captions response goes straight to download (no editor)', async ({ page }) => {
  await mockAllApis(page, { captions: [] });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // Caption editor should NOT appear; done state (download) should show
  await page.waitForFunction(
    () => document.getElementById('captionEditorCard').style.display === 'none'
       || !document.getElementById('captionEditorCard').offsetParent,
    { timeout: 10_000 }
  );
  await expect(page.locator('#captionEditorCard')).not.toBeVisible();
});

test('upload chunking: large file triggers multiple upload_chunk requests', async ({ page }) => {
  const uploadRequests = [];
  page.on('request', req => {
    if (req.url().includes('/upload_chunk/')) uploadRequests.push(req.url());
  });

  await mockAllApis(page);
  // 16 MB file → 4 chunks of 5 MB (CHUNK_SIZE = 5 MB)
  await selectFile(page, { sizeMB: 16 });
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  expect(uploadRequests.length).toBeGreaterThanOrEqual(3);
});

test('polling: process_poll receives the correct call_id', async ({ page }) => {
  const pollRequests = [];
  page.on('request', req => {
    if (req.url().includes('/process_poll/')) pollRequests.push(req.url());
  });

  await runFullUpload(page);
  expect(pollRequests.length).toBeGreaterThan(0);
  expect(pollRequests[0]).toContain('mock-process-call-id');
});

test('enhance video toggle is off by default and rides the process params', async ({ page }) => {
  let spawnUrl = null;
  await mockAllApis(page);
  await page.route(/\/process\/\?/, (route, request) => {
    spawnUrl = request.url();
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-process-call-id' }) });
  });
  await expect(page.locator('#enhanceVideo')).not.toBeChecked();
  await page.evaluate(() => {
    const el = document.getElementById('enhanceVideo');
    el.checked = true;
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect.poll(() => spawnUrl, { timeout: 10_000 }).toContain('enhance_video=true');
});
