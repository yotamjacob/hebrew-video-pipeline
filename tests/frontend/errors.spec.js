const { test, expect } = require('@playwright/test');
const { mockAllApis, selectFile, runFullUpload, DEFAULT_CAPTIONS, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

// ─── Process spawn errors ────────────────────────────────────────────────────

test('500 from process spawn shows error message', async ({ page }) => {
  await mockAllApis(page, { processStatus: 500 });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  await page.waitForSelector('#errorMsg', { state: 'visible', timeout: 10_000 });
  const msg = await page.locator('#errorMsg').textContent();
  expect(msg).toMatch(/error|failed|500/i);
});

test('error state does not leave processing UI stuck', async ({ page }) => {
  await mockAllApis(page, { processStatus: 500 });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  await page.waitForSelector('#errorMsg', { state: 'visible', timeout: 10_000 });
  // Caption editor must NOT be visible when there's an error
  await expect(page.locator('#captionEditorCard')).not.toBeVisible();
});

// ─── Upload errors ───────────────────────────────────────────────────────────

test('400 from upload_chunk shows error message', async ({ page }) => {
  await mockAllApis(page, { uploadStatus: 400 });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  await page.waitForSelector('#errorMsg', { state: 'visible', timeout: 10_000 });
  const msg = await page.locator('#errorMsg').textContent();
  expect(msg.length).toBeGreaterThan(0);
});

// ─── Service worker ──────────────────────────────────────────────────────────

test('service worker is blocked and does not interfere with fetch mocks', async ({ page }) => {
  // sw.js is blocked by playwright config; fetch mocks must still work
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  // If SW were double-polling it could cause "Failed to fetch" - verify it doesn't
  await expect(page.locator('#captionEditorCard')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#errorMsg')).not.toBeVisible();
});

// ─── Auto-resume of saved background jobs ────────────────────────────────────
// Opening the app with a saved job resumes it WITHOUT a banner tap: the poll
// runs immediately and, when processing finished while away, the user lands
// straight in the ready editor.

test('reconnect banner is hidden on fresh page load', async ({ page }) => {
  await expect(page.locator('#reconnectBanner')).not.toBeVisible();
});

test('a saved process job auto-resumes into the ready editor on open', async ({ page }) => {
  const { mockAllApis } = require('./helpers');
  await page.addInitScript(() => {
    localStorage.setItem('hebpipe_job', JSON.stringify({
      type: 'process',
      callId: 'saved-call-id',
      ts: Date.now(),
      filename: 'old_video.mp4',
      key: 'saved-upload-key',
    }));
  });
  const pollUrls = [];
  page.on('request', req => {
    if (req.url().includes('/process_poll/')) pollUrls.push(req.url());
  });
  await mockAllApis(page);   // routes must exist BEFORE boot: resume polls right away
  await bootApp(page);
  // No tap anywhere: the editor appears on its own with the poll's captions.
  await expect(page.locator('#captionEditorCard')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.caption-input').first()).toBeVisible();
  expect(pollUrls.some(u => u.includes('saved-call-id'))).toBe(true);
  // The job is consumed - a reload must not resume again.
  const saved = await page.evaluate(() => localStorage.getItem('hebpipe_job'));
  expect(saved).toBeNull();
});

test('expired saved job (>24 h) does not auto-resume', async ({ page }) => {
  const EXPIRED = Date.now() - 25 * 60 * 60 * 1000;
  await page.addInitScript((ts) => {
    localStorage.setItem('hebpipe_job', JSON.stringify({
      type: 'process', callId: 'old-call-id', ts,
    }));
  }, EXPIRED);
  const pollUrls = [];
  page.on('request', req => {
    if (req.url().includes('/process_poll/')) pollUrls.push(req.url());
  });
  await bootApp(page);
  await page.waitForTimeout(700);
  expect(pollUrls).toHaveLength(0);
  await expect(page.locator('#captionEditorCard')).not.toBeVisible();
});

// ─── Mobile viewport ─────────────────────────────────────────────────────────

test('full flow works on mobile viewport', async ({ page, browserName }) => {
  await page.setViewportSize({ width: 390, height: 844 }); // iPhone 14
  await runFullUpload(page);

  // Caption editor must be usable on mobile
  await expect(page.locator('#captionEditorCard')).toBeVisible();
  const input = page.locator('.caption-input').first();
  await expect(input).toBeVisible();
  await input.fill('מבחן מובייל');
  await expect(input).toHaveValue('מבחן מובייל');
});
