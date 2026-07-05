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

test('enhance video selector defaults to off and rides the process params', async ({ page }) => {
  let spawnUrl = null;
  await mockAllApis(page);
  await page.route(/\/process\/\?/, (route, request) => {
    spawnUrl = request.url();
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-process-call-id' }) });
  });
  await expect(page.locator('#ev_none')).toBeChecked();
  await page.click('label[for="ev_esrgan"]');
  await expect(page.locator('#ev_esrgan')).toBeChecked();
  await expect(page.locator('#ev_filters')).not.toBeChecked();   // mutually exclusive
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect.poll(() => spawnUrl, { timeout: 10_000 }).toContain('enhance_video=esrgan');
});

test('time estimate shows for a real video and grows with AI upscale', async ({ page }) => {
  const { TINY_MP4_15S } = require('./fixtures');
  await mockAllApis(page);
  await page.setInputFiles('#fileInput', { name: 'real.mp4', mimeType: 'video/mp4', buffer: TINY_MP4_15S });

  const est = page.locator('#timeEstimateText');
  await expect(page.locator('#timeEstimate')).toBeVisible({ timeout: 10_000 });
  await expect(est).toHaveText(/^הערכה: \d+-\d+ דקות$/);
  const base = (await est.textContent()).match(/(\d+)-(\d+)/).slice(1, 3).map(Number);

  await page.click('label[for="ev_esrgan"]');
  const ai = (await est.textContent()).match(/(\d+)-(\d+)/).slice(1, 3).map(Number);
  expect(ai[0]).toBeGreaterThan(base[0]);   // 15s @ esrgan adds ~4-8 min
  expect(ai[1]).toBeGreaterThan(base[1]);

  await page.click('label[for="ev_none"]');
  await expect(est).toHaveText(`הערכה: ${base[0]}-${base[1]} דקות`);
});

test('polling survives repeated network failures (mobile background/foreground)', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  await mockAllApis(page);
  // Simulate the OS killing in-flight poll fetches: first 5 poll requests die
  // with a network error ("Failed to fetch"), then connectivity returns.
  // The old code gave up after 3 retries and showed the error card.
  let failures = 0;
  await page.route(`${API_BASE}/process_poll/**`, (route) => {
    if (failures < 5) { failures++; return route.abort('failed'); }
    return route.fallback();
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 30_000 });
  expect(failures).toBe(5);
});
