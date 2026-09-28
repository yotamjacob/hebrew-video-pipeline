/**
 * Saved-job resume vs. the running session (2026-09-28, field report: one
 * user billed six jobs for one video).
 *
 *  - a stale 'pending' record from an abandoned NATIVE upload must never be
 *    declared "stalled" by the volume byte probe (native uploads go to R2,
 *    the probe reads 0 for the whole transfer), and the moment the user
 *    starts a new run the old resume goes silent - it must not paint an
 *    error over, or clear the record of, the new job
 *  - a new run cancels that abandoned upload server-side (no second bill)
 *  - a WEB (chunked) pending record with frozen bytes still surfaces the
 *    pick-again error: a reload killed that upload, frozen bytes are real
 *  - a 401 mid-poll re-mints the media token instead of dropping the job
 *  - a poll deadline keeps the saved job so the next open reconnects
 */
const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, selectFile, bootApp } = require('./helpers');

function seedPending(page, native) {
  return page.addInitScript((native) => {
    localStorage.setItem('hebpipe_job', JSON.stringify({
      type: 'pending', callId: null, ts: Date.now(), filename: 'old.mp4', key: 'oldkey', native }));
    window.__PENDING_RESUME_POLL_MS = 100;   // 20 polls in ~2 s instead of ~60 s
  }, native);
}
async function pendingRoutes(page, cancels) {
  await page.route(/\/process_pending\//, (r, req) =>
    r.fulfill({ status: 200, contentType: 'application/json',
      body: req.url().includes('oldkey') ? '{"pending":true}' : '{"call_id":"mock-process-call-id"}' }));
  await page.route(/\/upload_check\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"bytes":0}' }));
  await page.route(/\/cancel_upload\//, (r, req) => {
    cancels.push(new URL(req.url()).searchParams.get('key'));
    return r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"cancelled"}' });
  });
}

test('an abandoned native upload is never "stalled" by the byte probe, and a new run supersedes it silently', async ({ page }) => {
  await seedPending(page, true);
  await bootApp(page);
  await mockAllApis(page);
  const cancels = [];
  await pendingRoutes(page, cancels);
  await expect(page.locator('#checkUpload')).toHaveClass(/active/);
  await page.waitForTimeout(3500);                       // > 20 polls at the test cadence
  await expect(page.locator('#statusError')).not.toHaveClass(/visible/);
  // The user picks the file again and runs: the old resume must go quiet,
  // the abandoned registration is cancelled, the new job completes.
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 20_000 });   // under full-suite load
  await page.waitForTimeout(800);
  await expect(page.locator('#statusError')).not.toHaveClass(/visible/);
  expect(cancels).toContain('oldkey');
  expect(await page.evaluate(() => localStorage.getItem('hebpipe_job'))).toBeNull();
});

test('a web pending record with frozen bytes still surfaces the pick-again error', async ({ page }) => {
  await seedPending(page, false);
  await bootApp(page);
  await mockAllApis(page);
  await pendingRoutes(page, []);
  await expect(page.locator('#statusError')).toHaveClass(/visible/, { timeout: 10_000 });
  await expect(page.locator('#statusError')).toContainText('ההעלאה לא הסתיימה');
  expect(await page.evaluate(() => localStorage.getItem('hebpipe_job'))).toBeNull();
});

test('a 401 mid-poll re-mints the media token and the poll carries on', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  let mints = 0;
  await page.route(/\/auth\/media-token/, r => { mints++;
    return r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m.fresh"}' }); });
  let polls = 0;
  await page.route(`${API_BASE}/process_poll/**`, (r, req) => {
    polls++;
    if (polls === 1) return r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"Authentication required"}' });
    expect(req.url()).toContain('token=m.fresh');
    return r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: [{ start: 0.5, end: 2, text: 'שלום' }], video_key: 'mock-video-key_cut.mp4', step_times: { cut: 3 } }) });
  });
  const mintsBefore = mints;
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  expect(mints).toBeGreaterThan(mintsBefore);
  expect(polls).toBeGreaterThanOrEqual(2);
});

test('a poll deadline keeps the saved job for the next open', async ({ page }) => {
  await page.addInitScript(() => { window.__PROCESS_POLL_MS = 1500; });
  await bootApp(page);
  await mockAllApis(page);
  await page.route(`${API_BASE}/process_poll/**`, r =>
    r.fulfill({ status: 202, contentType: 'application/json', body: '{"status":"running"}' }));
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#statusError')).toHaveClass(/visible/, { timeout: 10_000 });
  await expect(page.locator('#statusError')).toContainText('עדיין מעבד');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('hebpipe_job') || 'null'));
  expect(saved && saved.type).toBe('process');
});
