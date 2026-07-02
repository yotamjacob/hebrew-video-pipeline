const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, bootApp } = require('./helpers');

const MOCK_JOBS = [
  { key: 'abc123_out.mp4', name: 'yoga_morning.mp4', ts: 1780000000, size: 52428800, duration: 95 },
  { key: 'def456_out.mp4', name: 'breathing.mp4',    ts: 1779000000, size: 31457280, duration: 62 },
];

function mockJobs(page, jobs = MOCK_JOBS) {
  return page.route(/\/jobs\/?(\?.*)?$/, (route, request) => {
    if (request.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: JSON.stringify({ jobs }) });
    }
    return route.fallback();
  });
}

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('history tab button exists and switches views', async ({ page }) => {
  await mockAllApis(page);
  await mockJobs(page, []);
  await page.click('#tabHistory');
  await expect(page.locator('#historyView')).toBeVisible();
  await expect(page.locator('#pipelineView')).toBeHidden();
});

test('empty history shows empty state', async ({ page }) => {
  await mockAllApis(page);
  await mockJobs(page, []);
  await page.click('#tabHistory');
  await expect(page.locator('#historyEmpty')).toBeVisible();
});

test('history renders one card per job with name and meta', async ({ page }) => {
  await mockAllApis(page);
  await mockJobs(page);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(2);
  await expect(page.locator('.history-name').first()).toHaveText('yoga_morning.mp4');
  await expect(page.locator('.history-meta').first()).toContainText('50 MB');
  await expect(page.locator('.history-meta').first()).toContainText('1:35');
});

test('delete asks for confirmation and calls DELETE', async ({ page }) => {
  await mockAllApis(page);
  await mockJobs(page);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  let deletedKey = null;
  await page.route(/\/jobs\/.+_out\.mp4\/?$/, (route, request) => {
    if (request.method() === 'DELETE') {
      deletedKey = request.url();
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
    }
    return route.fallback();
  });
  await page.click('#tabHistory');
  await page.locator('.history-btn-danger').first().click();
  await expect(page.locator('#confirmOverlay')).toBeVisible();
  await page.click('#confirmOk');
  await expect.poll(() => deletedKey).toContain('abc123_out.mp4');
});

test('history load failure shows friendly message', async ({ page }) => {
  await mockAllApis(page);
  await page.route(/\/jobs\/?(\?.*)?$/, r => r.fulfill({ status: 500, body: '{}' }));
  await page.click('#tabHistory');
  await expect(page.locator('#historyEmpty')).toContainText('Could not load history');
});
