// Free-tier video quota: pill, limit gating, admin tab
const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp, mockAllApis, selectFile } = require('./helpers');

test('quota pill shows remaining trial videos', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 3, video_limit: 5 } });
  const pill = page.locator('#quotaPill');
  await expect(pill).toBeVisible();
  await expect(pill).toHaveText('נשארו 2 מתוך 5 סרטוני ניסיון');
});

test('pill turns red and run is blocked when the limit is used up', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 5, video_limit: 5 } });
  await mockAllApis(page);

  const pill = page.locator('#quotaPill');
  await expect(pill).toHaveText('מכסת סרטוני הניסיון נוצלה');
  await expect(pill).toHaveClass(/quota-pill-empty/);

  let processCalled = false;
  await page.route(/\/process\/[^_]/, (route, request) => {
    if (request.method() === 'POST') processCalled = true;
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: '{"call_id":"x"}' });
  });

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#noticeBlock')).toBeVisible();
  await expect(page.locator('#noticeBlockBody')).toContainText('צרו קשר עם מנהל האפליקציה');
  expect(processCalled).toBe(false);
});

test('server 402 limit_reached maps to the friendly message', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 4, video_limit: 5 } });
  await mockAllApis(page);
  // Server says no (e.g. count advanced in another session)
  await page.route(/\/process\/[^_]/, (route, request) => {
    if (request.method() !== 'POST') return route.continue();
    return route.fulfill({ status: 402, contentType: 'application/json',
                           body: '{"error":"limit_reached"}' });
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.click('#confirmOk');   // quota confirmation modal
  await expect(page.locator('#errorMsg')).toContainText('צרו קשר עם מנהל האפליקציה', { timeout: 10_000 });
});

test('non-admin confirms before spending a trial video; cancel spends nothing', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 1, video_limit: 5 } });
  await mockAllApis(page);
  let processCalls = 0;
  await page.route(/\/process\/[^_]/, (route, request) => {
    if (request.method() === 'POST') processCalls++;
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-process-call-id' }) });
  });

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // Modal shows remaining count
  await expect(page.locator('#confirmOverlay')).toBeVisible();
  await expect(page.locator('#confirmTitle')).toHaveText('להשתמש בסרטון ניסיון אחד?');
  await expect(page.locator('#confirmBody')).toContainText('נשארו לכם 4 מתוך 5');

  // Cancel: nothing spent
  await page.click('#confirmCancel');
  await expect(page.locator('#confirmOverlay')).toBeHidden();
  expect(processCalls).toBe(0);

  // Confirm: processing starts
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  expect(processCalls).toBe(1);
});

test('admins process without a confirmation modal', async ({ page }) => {
  await bootApp(page, { me: { username: 'boss', role: 'admin', videos_used: 0, video_limit: null } });
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await expect(page.locator('#confirmOverlay')).toBeHidden();
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
});

test('admin sees no pill and gets the admin tab with user limit controls', async ({ page }) => {
  await bootApp(page, { me: { username: 'boss', role: 'admin', videos_used: 0, video_limit: null } });
  await page.route(`${API_BASE}/admin/users`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ users: [
                  { username: 'boss',   role: 'admin', videos_used: 0, video_limit: null, created: 1 },
                  { username: 'tester', role: 'user',  videos_used: 2, video_limit: 5,    created: 2 },
                ] }) }));
  const limitPosts = [];
  await page.route(`${API_BASE}/admin/limit`, async (route, request) => {
    limitPosts.push(request.postDataJSON());
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: '{"ok":true}' });
  });

  await expect(page.locator('#quotaPill')).toBeHidden();
  const adminTab = page.locator('#tabAdmin');
  await expect(adminTab).toBeVisible();
  await adminTab.click();
  await expect(page.locator('#adminView')).toBeVisible();

  const rows = page.locator('.admin-row');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText('boss ★');
  // Bump tester's limit to 50 (they paid)
  const input = rows.nth(1).locator('.admin-limit-input');
  await input.fill('50');
  await rows.nth(1).locator('.admin-save-btn').click();
  await expect(rows.nth(1).locator('.admin-save-btn')).toHaveText('✓');
  expect(limitPosts).toEqual([{ username: 'tester', limit: 50 }]);
});

test('regular users never see the admin tab', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 0, video_limit: 5 } });
  await expect(page.locator('#quotaPill')).toBeVisible();
  await expect(page.locator('#tabAdmin')).toBeHidden();
});
