// Free-tier video quota: pill, limit gating, admin tab
const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp, mockAllApis, selectFile } = require('./helpers');

test('quota pill shows remaining video credits', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 3, video_limit: 5 } });
  const pill = page.locator('#quotaPill');
  await expect(pill).toBeVisible();
  await expect(pill).toHaveText('נותרו 2 קרדיטים לסרטונים');
  await expect(page.locator('#heroGreeting')).toHaveText('שלום, tester');
});

test('pill turns red and run is blocked when the limit is used up', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 5, video_limit: 5 } });
  await mockAllApis(page);

  const pill = page.locator('#quotaPill');
  await expect(pill).toHaveText('לא נותרו קרדיטים לסרטונים');
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
  await expect(page.locator('#noticeBlockBody')).toContainText('Google Play');
  // Web users go to the Android app listing; payment is never arranged over
  // WhatsApp or another checkout.
  const cta = page.locator('#noticePlayCta');
  await expect(cta).toBeVisible();
  await expect(cta).toContainText('הורדת אפליקציית Android');
  await expect(cta).toHaveAttribute(
    'href',
    'https://play.google.com/store/apps/details?id=com.heb.pipeline',
  );
  await expect(page.locator('#noticeBlock a[href*="wa.me"]')).toHaveCount(0);
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
  await expect(page.locator('#errorMsg')).toContainText('Google Play', { timeout: 10_000 });
  const playCta = page.locator('#errorPlayCta');
  await expect(playCta).toBeVisible();
  await expect(playCta).toHaveAttribute(
    'href',
    'https://play.google.com/store/apps/details?id=com.heb.pipeline',
  );
  await expect(page.locator('#statusError a[href*="wa.me"]')).toHaveCount(0);
});

test('a failed job refreshes the quota pill so a refunded credit shows', async ({ page }) => {
  // A terminally-failed job is refunded server-side (quota key deleted +
  // videos_used decremented); the client re-queries /auth/me on error so the
  // pill reflects it. Simulate: pill starts at 3-of-5 (2 used); after the
  // failure /auth/me reports 1 used, so the pill returns to 4-of-5.
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 2, video_limit: 5 } });
  await mockAllApis(page);
  // Post-refund state (last-registered route wins over bootApp's /auth/me).
  await page.route(/\/auth\/me/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ username: 'tester', role: 'user', videos_used: 1, video_limit: 5 }) }));
  // Job fails at the poll (spawn ok, worker raised) - drives showError -> refreshQuota.
  await page.route(`${API_BASE}/process_poll/**`, r =>
    r.fulfill({ status: 500, contentType: 'application/json',
                body: JSON.stringify({ error: 'ffmpeg exited 1: no_audio' }) }));

  const pill = page.locator('#quotaPill');
  await expect(pill).toHaveText('נותרו 3 קרדיטים לסרטונים');
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.click('#confirmOk');   // quota confirmation modal
  await expect(page.locator('#statusError')).toBeVisible({ timeout: 10_000 });
  // no_audio maps to the friendly Hebrew message, and the pill shows the refund.
  await expect(page.locator('#errorMsg')).toContainText('אין פס קול');
  await expect(pill).toHaveText('נותרו 4 קרדיטים לסרטונים');
  // The Play Store purchase CTA is quota-only - hidden on a regular error.
  await expect(page.locator('#errorPlayCta')).toBeHidden();
});

test('non-admin confirms before spending a video credit; cancel spends nothing', async ({ page }) => {
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
  await expect(page.locator('#confirmTitle')).toHaveText('להשתמש בקרדיט אחד לסרטון?');
  await expect(page.locator('#confirmBody')).toContainText('נותרו לכם 4 קרדיטים');

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
                  { username: 'tester', role: 'user',  videos_used: 2, video_limit: 5,    created: 2, src: 'linkedin' },
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
  await expect(rows.nth(0)).toContainText('boss');
  await expect(rows.nth(0).locator('.admin-star svg')).toBeVisible();   // admin marker is an SVG star, not an emoji
  // Campaign attribution badge shows the signup source (?src= link).
  await expect(rows.nth(1).locator('.admin-src')).toHaveText('linkedin');
  await expect(rows.nth(0).locator('.admin-src')).toHaveCount(0);       // no src recorded → no badge
  // Bump tester's limit to 50 (they paid)
  const input = rows.nth(1).locator('.admin-limit-input');
  await input.fill('50');
  await rows.nth(1).locator('.admin-save-btn').click();
  await expect(rows.nth(1).locator('.admin-save-btn svg')).toBeVisible();   // success = SVG check
  expect(limitPosts).toEqual([{ username: 'tester', limit: 50 }]);
});

test('admin can reset a user password from the admin tab', async ({ page }) => {
  await bootApp(page, { me: { username: 'boss', role: 'admin', videos_used: 0, video_limit: null } });
  await page.route(`${API_BASE}/admin/users`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ users: [
                  { username: 'alinag', role: 'user', videos_used: 0, video_limit: 30, created: 1 },
                ] }) }));
  const resetPosts = [];
  await page.route(`${API_BASE}/admin/reset-password`, async (route, request) => {
    resetPosts.push(request.postDataJSON());
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: '{"ok":true,"username":"alinag"}' });
  });

  await page.locator('#tabAdmin').click();
  const row = page.locator('.admin-row').first();

  // Too-short passwords never hit the network.
  await row.locator('.admin-reset-btn').click();
  await row.locator('.admin-pw-input').fill('short');
  await row.locator('.admin-pw-ok').click();
  await expect(row.locator('.admin-pw-ok')).toHaveText('לפחות 8 תווים');
  expect(resetPosts).toEqual([]);

  // A valid password posts and confirms.
  await row.locator('.admin-pw-input').fill('bahuoss33');
  await row.locator('.admin-pw-ok').click();
  await expect(row.locator('.admin-reset-btn svg')).toBeVisible();   // success = SVG check
  expect(resetPosts).toEqual([{ username: 'alinag', new_password: 'bahuoss33' }]);
});

test('regular users never see the admin tab', async ({ page }) => {
  await bootApp(page, { me: { username: 'tester', role: 'user', videos_used: 0, video_limit: 5 } });
  await expect(page.locator('#quotaPill')).toBeVisible();
  await expect(page.locator('#tabAdmin')).toBeHidden();
});
