/**
 * Stock B-roll editor coverage - previously untested end-to-end.
 *
 * Mocks the /stock-broll spawn + poll with realistic moments/clips and drives
 * the B-roll tab: rendering, clip selection semantics (radio-like with
 * deselect), dismiss/restore, the burn payload, cost-limit + empty + error
 * states, and the "captions changed - re-run" banner.
 */
const { test, expect } = require('@playwright/test');
const { API_BASE, runFullUpload } = require('./helpers');
const { bootApp } = require('./helpers');

const CLIP = (n, src = 'pexels') => ({
  preview_url: `https://videos.pexels.com/video-files/${n}.mp4`,
  thumbnail:   `https://images.pexels.com/videos/${n}.jpg`,
  source: src,
  page_url: `https://www.pexels.com/video/${n}/`,
  score: 8, score_reason: 'clear subject',
  clip_use_start_seconds: 0, clip_use_end_seconds: 2.5,
});

const TWO_MOMENTS = {
  moments: [
    { start: 1.0, end: 3.5, broll_duration_seconds: 2.5, label: 'רגע ראשון',
      reasoning: 'מדגים את הנושא', moment_type: 'emphasis',
      broad_search_prompt: 'city timelapse', clips: [CLIP('aaa'), CLIP('bbb', 'pixabay')] },
    { start: 6.0, end: 8.0, broll_duration_seconds: 2.0, label: 'רגע שני',
      reasoning: 'קצב', moment_type: 'coverage',
      broad_search_prompt: 'nature drone', clips: [CLIP('ccc')] },
  ],
};

/** Override the generic b-roll mocks with a concrete analysis result. */
async function mockBroll(page, result, { spawnStatus = 202 } = {}) {
  await page.route(/\/stock-broll\/?(\?.*)?$/, (route, request) => {
    if (request.method() !== 'POST') return route.fallback();
    return spawnStatus === 202
      ? route.fulfill({ status: 202, contentType: 'application/json',
                        body: JSON.stringify({ call_id: 'mock-stock-call-id' }) })
      : route.fulfill({ status: spawnStatus, contentType: 'application/json',
                        body: JSON.stringify({ error: `stock spawn ${spawnStatus}` }) });
  });
  await page.route(`${API_BASE}/stock-broll-poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) }));
  // Media the clip cards reference - keep the network quiet.
  await page.route(/images\.pexels\.com|videos\.pexels\.com/, r => r.abort());
}

async function openBrollAndFind(page) {
  await page.click('#tabBtnBroll');
  await page.click('#findBrollBtn');
  await page.waitForSelector('.moment-card', { timeout: 8_000 });
}

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('find renders one card per moment with time badges and clips', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  await openBrollAndFind(page);
  await expect(page.locator('.moment-card')).toHaveCount(2);
  await expect(page.locator('.moment-time-badge').first()).toHaveText('0:01 - 0:03');
  await expect(page.locator('.clip-card')).toHaveCount(3);
  await expect(page.locator('.clip-source-badge.pixabay')).toHaveCount(1);
});

test('selecting a clip is radio-like within the moment, and reaches the burn payload', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  let burnBody = null;
  await page.route(/\/burn\/?(\?|$)/, async (route, request) => {
    try { burnBody = JSON.parse(request.postData() || '{}'); } catch (_) {}
    await route.fallback();
  });
  await openBrollAndFind(page);

  const moment0 = page.locator('.moment-card').first();
  const labels = moment0.locator('.clip-select-label');   // the visible control (input is hidden)
  const boxes  = moment0.locator('.clip-select-label input');
  await labels.nth(0).click();
  await labels.nth(1).click();                      // switching clips...
  await expect(boxes.nth(0)).not.toBeChecked();     // ...deselects the first
  await expect(boxes.nth(1)).toBeChecked();

  await page.click('#runBtn');
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await expect.poll(() => (burnBody ? 'y' : 'n'), { timeout: 10_000 }).toBe('y');
  expect(burnBody.broll).toHaveLength(1);
  expect(burnBody.broll[0].download_url).toContain('bbb');
  expect(burnBody.broll[0].start).toBe(1.0);
});

test('unchecking a selected clip clears the selection', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  await openBrollAndFind(page);
  const label = page.locator('.moment-card').first().locator('.clip-select-label').first();
  await label.click();   // select
  await label.click();   // toggle off
  const count = await page.evaluate(() => Object.keys(stockBrollSelections).length);
  expect(count).toBe(0);
});

test('dismissing an emphasis moment collapses it; restore brings it back', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  await openBrollAndFind(page);
  const moment0 = page.locator('.moment-card').first();
  await moment0.locator('.moment-dismiss-btn').click();
  await expect(moment0).toHaveClass(/skipped-emphasis/);
  await expect(moment0.locator('.clip-card').first()).toBeHidden();
  await moment0.locator('.moment-restore-btn').click();
  await expect(moment0).not.toHaveClass(/skipped-emphasis/);
  await expect(moment0.locator('.clip-card').first()).toBeVisible();
});

test('dismissing a coverage moment removes its card entirely', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  await openBrollAndFind(page);
  await page.locator('.moment-card.coverage .moment-dismiss-btn').click();
  await expect(page.locator('.moment-card')).toHaveCount(1);
});

test('cost-limit banner shows when the backend hit the budget', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, { ...TWO_MOMENTS, cost_limit_hit: true,
                          moments_processed: 2, total_moments_identified: 5 });
  await openBrollAndFind(page);
  await expect(page.locator('#stockCostLimitBanner')).toBeVisible();
});

test('zero moments shows a friendly empty message', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, { moments: [], filter_stats: {} });
  await page.click('#tabBtnBroll');
  await page.click('#findBrollBtn');
  await expect(page.locator('#stockBrollList p')).toBeVisible({ timeout: 8_000 });
  await expect(page.locator('.moment-card')).toHaveCount(0);
});

test('spawn failure shows an error with retry and re-enables the button', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS, { spawnStatus: 500 });
  await page.click('#tabBtnBroll');
  await page.click('#findBrollBtn');
  await expect(page.locator('#stockBrollList p')).toContainText(/.+/, { timeout: 8_000 });
  await expect(page.locator('#stockBrollList button')).toBeVisible();   // retry
  await expect(page.locator('#findBrollBtn')).toBeEnabled();
});

test('editing a caption after analysis reveals the re-run banner', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  await openBrollAndFind(page);
  await expect(page.locator('#stockBrollRerunBanner')).toBeHidden();
  await page.click('#tabBtnCaptions');
  const inp = page.locator('.caption-input').first();
  await inp.click();
  await inp.fill('טקסט אחר לגמרי');
  // The banner lives in the B-roll tab - it's revealed when the user returns.
  await page.click('#tabBtnBroll');
  await expect(page.locator('#stockBrollRerunBanner')).toBeVisible();
});

test('tapping a clip thumbnail plays the clip inline instead of opening the stock site', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  // Serve a real tiny mp4 for the clip preview (mockBroll aborts stock media,
  // which would trip the open-external fallback) - last route wins.
  const fs = require('fs');
  const path = require('path');
  const buf = fs.readFileSync(path.join(__dirname, 'fixtures/portrait_1080x1920.mp4'));
  await page.route(/videos\.pexels\.com/, r =>
    r.fulfill({ status: 200, headers: { 'Content-Type': 'video/mp4' }, body: buf }));
  await openBrollAndFind(page);
  const thumb = page.locator('.clip-thumb').first();
  await expect(thumb).toBeVisible();
  let popup = null;
  page.on('popup', p => { popup = p; });
  await thumb.click();
  await expect(thumb.locator('video.clip-inline-video')).toHaveCount(1);
  await expect(thumb).toHaveClass(/playing/);
  expect(popup).toBeNull();          // no external navigation
  // Tap again stops and restores the still.
  await thumb.click();
  await expect(thumb.locator('video.clip-inline-video')).toHaveCount(0);
});

test('a burst of killed polls (backgrounded WebView) does not fail the run', async ({ page }) => {
  // Minimizing the app kills in-flight polls; the old loop gave up after 3
  // such failures and showed "failed" while the job was still running.
  test.setTimeout(45_000);
  await runFullUpload(page);
  let polls = 0;
  await page.route(/\/stock-broll\/?(\?.*)?$/, (route, request) => {
    if (request.method() !== 'POST') return route.fallback();
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-stock-call-id' }) });
  });
  await page.route(`${API_BASE}/stock-broll-poll/**`, r => {
    polls++;
    if (polls <= 5) return r.abort('connectionfailed');   // > the old 3-retry budget
    return r.fulfill({ status: 200, contentType: 'application/json',
                       body: JSON.stringify(TWO_MOMENTS) });
  });
  await page.route(/images\.pexels\.com|videos\.pexels\.com/, r => r.abort());
  await page.click('#tabBtnBroll');
  await page.click('#findBrollBtn');
  await expect(page.locator('.moment-card')).toHaveCount(2, { timeout: 30_000 });
  expect(polls).toBeGreaterThan(5);
});

test('B-roll search is budgeted: 3 uses per video, then the button disables', async ({ page }) => {
  await runFullUpload(page);
  await mockBroll(page, TWO_MOMENTS);
  await page.click('#tabBtnBroll');
  const btn = page.locator('#findBrollBtn');
  await expect(btn).toContainText('3');
  for (let i = 0; i < 3; i++) {
    await btn.click();
    await page.waitForSelector('.moment-card', { timeout: 8_000 });
    await expect.poll(() => page.evaluate(() =>
      document.getElementById('findBrollBtn').textContent)).toContain(String(2 - i));
  }
  await expect(btn).toBeDisabled();
});
