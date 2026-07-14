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

test('delete removes the card in place - no full-tab refetch', async ({ page }) => {
  await mockAllApis(page);
  let listFetches = 0;
  await page.route(/\/jobs\/?(\?.*)?$/, (route, request) => {
    if (request.method() === 'GET') {
      listFetches++;
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: JSON.stringify({ jobs: MOCK_JOBS }) });
    }
    return route.fallback();
  });
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await page.route(/\/jobs\/.+_out\.mp4\/?$/, (route, request) =>
    request.method() === 'DELETE'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' })
      : route.fallback());
  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(2);
  await page.locator('.history-btn-danger').first().click();
  await page.click('#confirmOk');
  await expect(page.locator('.history-card')).toHaveCount(1, { timeout: 5_000 });
  expect(listFetches).toBe(1);   // the initial load only - no reload after delete
  await expect(page.locator('.history-name').first()).toHaveText('breathing.mp4');
});

test('deleting the last card reveals the empty state in place', async ({ page }) => {
  await mockAllApis(page);
  await mockJobs(page, [MOCK_JOBS[0]]);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await page.route(/\/jobs\/.+_out\.mp4\/?$/, (route, request) =>
    request.method() === 'DELETE'
      ? route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' })
      : route.fallback());
  await page.click('#tabHistory');
  await page.locator('.history-btn-danger').first().click();
  await page.click('#confirmOk');
  await expect(page.locator('.history-card')).toHaveCount(0, { timeout: 5_000 });
  await expect(page.locator('#historyEmpty')).toBeVisible();
});

test('failed delete keeps the card', async ({ page }) => {
  await mockAllApis(page);
  await mockJobs(page);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await page.route(/\/jobs\/.+_out\.mp4\/?$/, (route, request) =>
    request.method() === 'DELETE'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"boom"}' })
      : route.fallback());
  await page.click('#tabHistory');
  await page.locator('.history-btn-danger').first().click();
  await page.click('#confirmOk');
  await page.waitForTimeout(600);
  await expect(page.locator('.history-card')).toHaveCount(2);   // nothing vanished
});

test('history load failure shows friendly message', async ({ page }) => {
  await mockAllApis(page);
  await page.route(/\/jobs\/?(\?.*)?$/, r => r.fulfill({ status: 500, body: '{}' }));
  await page.click('#tabHistory');
  await expect(page.locator('#historyEmpty')).toContainText('טעינת ההיסטוריה נכשלה');
});
