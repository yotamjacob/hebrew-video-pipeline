const { test, expect } = require('@playwright/test');

// Standalone /claude page (docs/connector.md): creates the personal connector
// link a person pastes into Claude. It loads NO app.js - its only network call
// is /auth/connector-key, stubbed here. The link is a credential shown ONCE, so
// the states that matter are: logged out, no link yet, link created (visible +
// copyable), a link already exists (never re-shown), revoked, expired session.
const LINK = 'https://yotamjacob--hebrew-video-pipeline-api.modal.run/mcp/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_AbCdE';

async function boot(page, { exists = false, token = 'test-token', status = 200 } = {}) {
  const calls = [];
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/\/auth\/connector-key\/?$/, async (route, request) => {
    calls.push({ method: request.method(), auth: request.headers()['authorization'] });
    if (status !== 200) return route.fulfill({ status, contentType: 'application/json', body: '{"error":"x"}' });
    const m = request.method();
    const body = m === 'POST' ? { exists: true, url: LINK } : m === 'DELETE' ? { ok: true, exists: false } : { exists };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  if (token) await page.addInitScript(t => localStorage.setItem('hebpipe_token', t), token);
  await page.goto('/claude.html');
  return calls;
}

test('logged out: asks to sign in and never calls the API', async ({ page }) => {
  const calls = await boot(page, { token: '' });
  await expect(page.locator('#needLogin')).toBeVisible();
  await expect(page.locator('#linkCard')).toBeHidden();
  expect(calls).toHaveLength(0);
});

test('a session kept in sessionStorage ("remember me" off) is recognised', async ({ page }) => {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/\/auth\/connector-key\/?$/, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"exists":false}' }));
  await page.addInitScript(() => sessionStorage.setItem('hebpipe_token', 'session-only'));
  await page.goto('/claude.html');
  await expect(page.locator('#linkCard')).toBeVisible();
});

test('creating a link shows it once, sends the session token, and copies it', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  const calls = await boot(page);
  await expect(page.locator('#linkRow')).toBeHidden();
  await expect(page.locator('#revokeBtn')).toBeHidden();
  await page.click('#createBtn');
  await expect(page.locator('#linkInput')).toHaveValue(LINK);
  await expect(page.locator('#onceNote')).toBeVisible();
  await expect(page.locator('#revokeBtn')).toBeVisible();
  expect(calls.map(c => c.method)).toEqual(['GET', 'POST']);
  expect(calls[1].auth).toBe('Bearer test-token');
  await page.click('#copyBtn');
  await expect(page.locator('#copyBtn')).toHaveText('הועתק');
});

test('an existing link is never re-shown; a new one can be made or the link revoked', async ({ page }) => {
  const calls = await boot(page, { exists: true });
  await expect(page.locator('#linkRow')).toBeHidden();          // only its hash is stored
  await expect(page.locator('#createBtn')).toHaveText('יצירת קישור חדש');
  await page.click('#revokeBtn');
  await expect(page.locator('#revokeBtn')).toBeHidden();
  await expect(page.locator('#createBtn')).toHaveText('יצירת קישור');
  expect(calls.map(c => c.method)).toEqual(['GET', 'DELETE']);
});

test('an expired session falls back to the sign-in card; a server error is shown inline', async ({ page }) => {
  await boot(page, { status: 401 });
  await expect(page.locator('#needLogin')).toBeVisible();
  await expect(page.locator('#linkCard')).toBeHidden();

  const p2 = await page.context().newPage();
  await boot(p2, { status: 500 });
  await expect(p2.locator('#err')).toBeVisible();
  await expect(p2.locator('#linkCard')).toBeVisible();
});

test('layout: no horizontal overflow and the link row fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await boot(page);
  await page.click('#createBtn');
  await expect(page.locator('#linkInput')).toHaveValue(LINK);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const row = await page.locator('#linkRow').boundingBox();
  expect(row.x).toBeGreaterThanOrEqual(0);
  expect(row.x + row.width).toBeLessThanOrEqual(360);
});
