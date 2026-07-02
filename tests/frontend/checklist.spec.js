const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, selectFile, runFullUpload, DEFAULT_CAPTIONS } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await page.goto('/');
});

test('checklist shows the real backend step times on completion', async ({ page }) => {
  await runFullUpload(page);   // helpers mock reports step_times {enhance: 12.3, cut: 20.1}
  await expect(page.locator('#checkEnhanceTime')).toHaveText('0:12');
  await expect(page.locator('#checkCutTime')).toHaveText('0:20');
});

test('live progress from process_poll drives step transitions', async ({ page }) => {
  await mockAllApis(page);
  // First two polls: still running, enhance finished for real in 8s, cut active.
  // Third poll: done.
  let polls = 0;
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r => {
    polls += 1;
    if (polls < 3) {
      return r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'running',
                               progress: { stage: 'cut', done: { enhance: 8.2 } } }) });
    }
    return r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { enhance: 8.2, cut: 14.9 } }) });
  });

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // While running: enhance closed with its real time, cut spinning
  await expect(page.locator('#checkEnhance')).toHaveClass(/done/, { timeout: 10_000 });
  await expect(page.locator('#checkEnhanceTime')).toHaveText('0:08');
  await expect(page.locator('#checkCut')).toHaveClass(/active/);

  // Done: cut closed with its real time
  await expect(page.locator('#checkCut')).toHaveClass(/done/, { timeout: 15_000 });
  await expect(page.locator('#checkCutTime')).toHaveText('0:15');
});

test('steps that never ran are hidden, not estimated', async ({ page }) => {
  await mockAllApis(page);
  // Backend reports only cut (enhance toggle off → never ran)
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { cut: 9.4 } }) }));

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  // The input is visually hidden behind a styled toggle — flip it directly
  await page.evaluate(() => {
    const el = document.getElementById('enhanceAudio');
    el.checked = false;
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#runBtn');

  await expect(page.locator('#checkCut')).toHaveClass(/done/, { timeout: 10_000 });
  await expect(page.locator('#checkCutTime')).toHaveText('0:09');
  await expect(page.locator('#checkEnhance')).toBeHidden();
});
