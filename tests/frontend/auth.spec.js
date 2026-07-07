const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp, mockAllApis, selectFile } = require('./helpers');

// These tests call page.goto('/') directly (no bootApp), so stub the Google
// Fonts CDN - otherwise the `load` event waits on the external stylesheet and
// flakes when the CDN is slow.
test.beforeEach(async ({ page }) => {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
});

test('no session: login view shows, app hidden', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#authView')).toBeVisible();
  await expect(page.locator('#pipelineView')).toBeHidden();
  await expect(page.locator('#tabsBar')).toBeHidden();
});

test('expired session drops back to login', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 'stale-token'));
  await page.route(/\/auth\/me/, r =>
    r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"Authentication required"}' }));
  await page.goto('/');
  await expect(page.locator('#authView')).toBeVisible();
  await expect(page.locator('#pipelineView')).toBeHidden();
});

test('login stores the token and reveals the app', async ({ page }) => {
  await page.route(/\/auth\/login/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ token: 'fresh-token', username: 'alina@example.com' }) }));
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
  await page.goto('/');
  await page.fill('#authEmail', 'alina@example.com');
  await page.fill('#authPassword', 'secret-password');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#pipelineView')).toBeVisible();
  await expect(page.locator('#tabsBar')).toBeVisible();
  const stored = await page.evaluate(() => localStorage.getItem('hebpipe_token'));
  expect(stored).toBe('fresh-token');
});

test('failed login shows the server error, app stays hidden', async ({ page }) => {
  await page.route(/\/auth\/login/, r =>
    r.fulfill({ status: 401, contentType: 'application/json',
                body: JSON.stringify({ error: 'Invalid email or password' }) }));
  await page.goto('/');
  await page.fill('#authEmail', 'alina@example.com');
  await page.fill('#authPassword', 'wrong');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authError')).toHaveText('Invalid email or password');
  await expect(page.locator('#pipelineView')).toBeHidden();
});

test('register mode reveals the invite field and posts it with the email', async ({ page }) => {
  let sentBody = null;
  await page.route(/\/auth\/register/, (route, request) => {
    sentBody = JSON.parse(request.postData());
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ token: 'new-token', username: 'newbie@example.com' }) });
  });
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
  await page.goto('/');
  await expect(page.locator('#authInviteRow')).toBeHidden();
  await page.click('#authModeBtn');
  await expect(page.locator('#authInviteRow')).toBeVisible();
  await page.fill('#authEmail', 'newbie@example.com');
  await page.fill('#authPassword', 'longenough');
  await page.fill('#authInvite', 'the-invite');
  await page.check('#authTermsCheck');   // Terms acceptance is mandatory
  await page.click('#authSubmitBtn');
  await expect(page.locator('#pipelineView')).toBeVisible();
  expect(sentBody.invite).toBe('the-invite');
  expect(sentBody.email).toBe('newbie@example.com');
});

test('register rejects a non-email identifier inline, before any request', async ({ page }) => {
  let posted = false;
  await page.route(/\/auth\/register/, r => { posted = true; return r.fulfill({ status: 400, body: '{}' }); });
  await page.goto('/');
  await page.click('#authModeBtn');
  await page.fill('#authEmail', 'not-an-email');
  await page.fill('#authPassword', 'longenough');
  await page.fill('#authInvite', 'the-invite');
  await page.check('#authTermsCheck');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authEmailErr')).toBeVisible();
  await expect(page.locator('#pipelineView')).toBeHidden();
  expect(posted).toBe(false);
});

test('remembered email prefills the login form on the next visit', async ({ page }) => {
  await page.route(/\/auth\/login/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ token: 'fresh-token', username: 'alina@example.com' }) }));
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
  await page.goto('/');
  await page.fill('#authEmail', 'alina@example.com');
  await page.fill('#authPassword', 'secret-password');
  await page.click('#authSubmitBtn');   // rememberMe is checked by default
  await expect(page.locator('#pipelineView')).toBeVisible();
  // Simulate a later visit where the session is gone but the device remembers.
  await page.evaluate(() => localStorage.removeItem('hebpipe_token'));
  await page.reload();
  await expect(page.locator('#authView')).toBeVisible();
  await expect(page.locator('#authEmail')).toHaveValue('alina@example.com');
});

test('unchecking remember me leaves no email behind', async ({ page }) => {
  await page.route(/\/auth\/login/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ token: 'fresh-token', username: 'alina@example.com' }) }));
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
  await page.goto('/');
  await page.fill('#authEmail', 'alina@example.com');
  await page.fill('#authPassword', 'secret-password');
  await page.uncheck('#rememberMe');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#pipelineView')).toBeVisible();
  const saved = await page.evaluate(() => localStorage.getItem('hebpipe_email'));
  expect(saved).toBeNull();
});

test('api requests carry the bearer token', async ({ page }) => {
  let authHeader = null;
  await bootApp(page);
  await mockAllApis(page);
  await page.route(/\/warmup/, (route, request) => {
    authHeader = request.headers()['authorization'];
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' });
  });
  await selectFile(page);
  await expect.poll(() => authHeader, { timeout: 5000 }).toBe('Bearer test-token');
});

test('logout clears the session and returns to login', async ({ page }) => {
  // Seed the token via one-off localStorage (not addInitScript, which would
  // re-seed it on the reload that logout performs)
  await page.route(/\/auth\/me/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"username":"tester"}' }));
  // Boot fires these on every showApp — stub so they don't 401-bounce in CI.
  await page.route(/\/auth\/media-token/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m.test"}' }));
  await page.route(/\/oauth\/status/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"connected":false}' }));
  await page.goto('/');
  await page.evaluate(() => localStorage.setItem('hebpipe_token', 'test-token'));
  await page.reload();
  await expect(page.locator('#pipelineView')).toBeVisible();
  await page.click('#logoutTab');
  // Logout now asks for confirmation.
  await page.click('#confirmOk');
  await expect(page.locator('#authView')).toBeVisible();
  const stored = await page.evaluate(() => localStorage.getItem('hebpipe_token'));
  expect(stored).toBeNull();
});

test('burn spawn carries the bearer token', async ({ page }) => {
  const { runFullUpload } = require('./helpers');
  await bootApp(page);
  await runFullUpload(page);
  let burnAuth = null;
  await page.route(/\/burn\/\?/, (route, request) => {
    burnAuth = request.headers()['authorization'];
    return route.fulfill({ status: 202, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnAuth, { timeout: 10_000 }).toBe('Bearer test-token');
});
