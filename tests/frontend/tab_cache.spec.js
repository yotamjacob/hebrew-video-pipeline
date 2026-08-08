// History/Admin tab render cache (2026-08-08): reopening a tab refetches in
// the background but must NOT clear and rebuild the rendered list unless the
// server data actually changed - a rebuild refetches every thumbnail and
// resets scroll, which read as "the tab reloads every time I open it".
const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, bootApp } = require('./helpers');

const JOBS = [
  { key: 'abc123_out.mp4', name: 'yoga_morning.mp4', ts: 1780000000, size: 52428800, duration: 95 },
  { key: 'def456_out.mp4', name: 'breathing.mp4',    ts: 1779000000, size: 31457280, duration: 62 },
];
const JOBS_PLUS_ONE = [
  { key: 'zzz999_out.mp4', name: 'fresh_export.mp4', ts: 1781000000, size: 1048576, duration: 10 },
  ...JOBS,
];

function mockJobs(page, jobs) {
  return page.route(/\/jobs\/?(\?.*)?$/, (route, request) => {
    if (request.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: JSON.stringify({ jobs }) });
    }
    return route.fallback();
  });
}

test('reopening History with unchanged data keeps the same DOM rows', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await mockJobs(page, JOBS);

  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(2);
  // Tag the live element - a rebuild creates fresh nodes without the tag.
  await page.evaluate(() => { document.querySelector('.history-card').__cached = true; });

  await page.click('#tabPipeline');
  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(2);
  await page.waitForTimeout(250);   // let the background refetch settle
  expect(await page.evaluate(() => document.querySelector('.history-card').__cached)).toBe(true);
  await expect(page.locator('#historyLoading')).toBeHidden();
});

test('reopening History re-renders when the server list changed', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await mockJobs(page, JOBS);

  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(2);

  await page.click('#tabPipeline');
  await mockJobs(page, JOBS_PLUS_ONE);   // last-registered route wins
  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(3);
  await expect(page.locator('.history-name').first()).toHaveText('fresh_export.mp4');
});

test('reopening Admin with unchanged data keeps the same DOM rows', async ({ page }) => {
  await bootApp(page, { me: { username: 'boss', role: 'admin', videos_used: 0, video_limit: null } });
  await mockAllApis(page);
  await page.route(`${API_BASE}/admin/users`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ users: [
                  { username: 'boss', role: 'admin', videos_used: 0, video_limit: null, created: 1 },
                  { username: 'user@example.com', videos_used: 2, video_limit: 5, created: 2 },
                ] }) }));

  await page.click('#tabAdmin');
  await expect(page.locator('.admin-row')).toHaveCount(2);
  await page.evaluate(() => { document.querySelector('.admin-row').__cached = true; });

  await page.click('#tabPipeline');
  await page.click('#tabAdmin');
  await expect(page.locator('.admin-row')).toHaveCount(2);
  await page.waitForTimeout(250);
  expect(await page.evaluate(() => document.querySelector('.admin-row').__cached)).toBe(true);
  await expect(page.locator('#adminLoading')).toBeHidden();
});

test('a language switch still redraws the open History tab', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await mockJobs(page, JOBS);

  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(2);
  await page.evaluate(() => { document.querySelector('.history-card').__cached = true; });

  await page.click('#langToggle');   // same data, but rows carry t() strings
  await expect(page.locator('.history-card')).toHaveCount(2);
  await expect.poll(() =>
    page.evaluate(() => document.querySelector('.history-card').__cached || false)
  ).toBe(false);
});
