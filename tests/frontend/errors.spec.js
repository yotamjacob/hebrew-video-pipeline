const { test, expect } = require('@playwright/test');
const { mockAllApis, selectFile, runFullUpload, DEFAULT_CAPTIONS } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await page.goto('/');
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
  // If SW were double-polling it could cause "Failed to fetch" — verify it doesn't
  await expect(page.locator('#captionEditorCard')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#errorMsg')).not.toBeVisible();
});

// ─── Reconnect banner ────────────────────────────────────────────────────────

test('reconnect banner is hidden on fresh page load', async ({ page }) => {
  await expect(page.locator('#reconnectBanner')).not.toBeVisible();
});

test('reconnect banner appears when localStorage has a saved job', async ({ page }) => {
  // Inject a saved job before the page initialises its check
  await page.addInitScript(() => {
    localStorage.setItem('hebpipe_job', JSON.stringify({
      type: 'process',
      callId: 'saved-call-id',
      ts: Date.now(),
      filename: 'old_video.mp4',
    }));
  });
  await page.goto('/');
  await expect(page.locator('#reconnectBanner')).toBeVisible({ timeout: 3_000 });
});

test('dismiss button hides the reconnect banner', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('hebpipe_job', JSON.stringify({
      type: 'process',
      callId: 'saved-call-id',
      ts: Date.now(),
    }));
  });
  await page.goto('/');
  await page.locator('#reconnectBanner').waitFor({ state: 'visible' });
  // Click dismiss (second button in the banner)
  await page.locator('#reconnectBanner button').last().click();
  await expect(page.locator('#reconnectBanner')).not.toBeVisible();
});

test('expired saved job (>45 min) does not show reconnect banner', async ({ page }) => {
  const EXPIRED = Date.now() - 46 * 60 * 1000;
  await page.addInitScript((ts) => {
    localStorage.setItem('hebpipe_job', JSON.stringify({
      type: 'process', callId: 'old-call-id', ts,
    }));
  }, EXPIRED);
  await page.goto('/');
  // Banner must not appear for stale jobs
  await page.waitForTimeout(500);
  await expect(page.locator('#reconnectBanner')).not.toBeVisible();
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

test('debug panel is present and toggleable', async ({ page }) => {
  // The debug panel is always present; clicking it should expand it
  const panel = page.locator('#debugPanel');
  await expect(panel).toBeVisible();
  const bar = page.locator('#debugBar');
  await bar.click();
  await expect(panel).toHaveClass(/open/);
});
