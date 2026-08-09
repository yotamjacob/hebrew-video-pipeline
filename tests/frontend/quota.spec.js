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

test('admin gets a buy-credits pill and the admin tab with user limit controls', async ({ page }) => {
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

  // Admins bypass the quota, so the pill carries no count - but it stays
  // present as the way into the purchase flow.
  await expect(page.locator('#quotaPill')).toHaveText('רכישת קרדיטים');
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


// ── Admin: measured compute cost ────────────────────────────────────────────

const COSTS = {
  days: 7, videos: 4, burns: 3, gpu_secs: 900, cpu_secs: 120, src_secs: 600,
  broll_jobs: 1, usd: 0.2136, usd_per_video: 0.0534,
  by_mode: {
    none:   { n: 3, gpu_secs: 300,  src_secs: 300, gpu_per_src: 1.0 },
    esrgan: { n: 1, gpu_secs: 6000, src_secs: 300, gpu_per_src: 20.0 },
  },
  ai_usd: 0.0864, ai_calls: 12, ai_tokens: 25000,
  ai_by_kind: { broll_vision: { n: 8, usd: 0.05 }, hook: { n: 2, usd: 0.02 }, keywords: { n: 2, usd: 0.0164 } },
  all_usd: 0.3, all_per_video: 0.075,
};

async function bootAdminWithCosts(page, costs = COSTS) {
  await bootApp(page, { me: { username: 'boss', role: 'admin', videos_used: 0, video_limit: null } });
  await page.route(`${API_BASE}/admin/users`, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"users":[]}' }));
  const seen = [];
  await page.route(/\/admin\/costs/, (route, request) => {
    seen.push(new URL(request.url()).searchParams.get('days'));
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify(costs) });
  });
  await page.locator('#tabCosts').click();
  return seen;
}

test('the Costs tab shows the all-in per-video figure, AI spend, and the per-mode split', async ({ page }) => {
  const seen = await bootAdminWithCosts(page);
  await expect(page.locator('#costBody')).toBeVisible();
  // The pricing-decision hero: compute + AI per delivered video.
  await expect(page.locator('#costAllPerVideo')).toHaveText('$0.075');
  await expect(page.locator('#costVideos')).toHaveText('4');
  await expect(page.locator('#costTotal')).toHaveText('$0.21');
  await expect(page.locator('#costAi')).toHaveText('$0.09');
  await expect(page.locator('#costGpu')).toHaveText('15m');
  // AI features ranked by spend.
  const aiRows = page.locator('#costAiKinds tr');
  await expect(aiRows).toHaveCount(3);
  await expect(aiRows.nth(0)).toContainText('broll_vision');
  await expect(aiRows.nth(0)).toContainText('$0.050');
  // The whole point of the panel: the upscale's cost per second of source is
  // visible next to a plain run instead of buried in the average.
  const rows = page.locator('#costModes tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText('esrgan');
  await expect(rows.nth(0)).toContainText('20.0x');
  await expect(rows.nth(1)).toContainText('none');
  await expect(rows.nth(1)).toContainText('1.0x');
  expect(seen[0]).toBe('7');
});

test('switching the range refetches for that window', async ({ page }) => {
  const seen = await bootAdminWithCosts(page);
  await page.locator('.cost-range-btn[data-days="30"]').click();
  await expect.poll(() => seen.length).toBe(2);
  expect(seen[1]).toBe('30');
  await expect(page.locator('.cost-range-btn[data-days="30"]')).toHaveClass(/is-on/);
  await expect(page.locator('.cost-range-btn[data-days="7"]')).not.toHaveClass(/is-on/);
});

test('a window with no jobs says so instead of showing zeros', async ({ page }) => {
  await bootAdminWithCosts(page, { days: 7, videos: 0, burns: 0, usd: 0, usd_per_video: 0, by_mode: {}, ai_calls: 0, ai_usd: 0, ai_by_kind: {} });
  await expect(page.locator('#costEmpty')).toBeVisible();
  await expect(page.locator('#costBody')).toBeHidden();
});

test('a failed cost fetch surfaces an error, not a blank card', async ({ page }) => {
  await bootApp(page, { me: { username: 'boss', role: 'admin', videos_used: 0, video_limit: null } });
  await page.route(`${API_BASE}/admin/users`, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"users":[]}' }));
  await page.route(/\/admin\/costs/, r => r.fulfill({ status: 500, body: 'boom' }));
  await page.locator('#tabCosts').click();
  await expect(page.locator('#costError')).toBeVisible();
  await expect(page.locator('#costLoading')).toBeHidden();
});
