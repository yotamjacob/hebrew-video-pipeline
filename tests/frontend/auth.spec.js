const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp, mockAllApis, selectFile } = require('./helpers');

// These tests call page.goto('/') directly (no bootApp), so stub the Google
// Fonts CDN - otherwise the `load` event waits on the external stylesheet and
// flakes when the CDN is slow.
test.beforeEach(async ({ page }) => {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  // Stub the Google Identity Services CDN (loads on the login view on web).
  await page.route(/accounts\.google\.com\/gsi/, r => r.fulfill({ status: 200, contentType: 'text/javascript', body: '' }));
  // showApp() fires these the instant a sign-in succeeds. Unmocked they reach
  // the REAL api with a fake token, 401, and apiFetch's _sessionExpired tears
  // the app view back down - so every "signs in, app shows" assertion here was
  // racing a live network round-trip and lost on slow CI runners. Tests that
  // want a 401 re-register their own handler (last route wins).
  await page.route(/\/auth\/me/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: '{"username":"tester","role":"user","videos_used":0,"video_limit":-1,"email_verified":true}' }));
  await page.route(/\/auth\/media-token/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m.test"}' }));
  await page.route(/\/oauth\/status/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"connected":false}' }));
  await page.route(/\/profiles/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"profiles":[],"default":null}' }));
  await page.route(/\/error-report/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
});

test('no session: the choice landing shows, app hidden', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#authView')).toBeVisible();
  await expect(page.locator('#authChoice')).toBeVisible();
  await expect(page.locator('#authStepEmail')).toBeHidden();
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

// Passwordless helpers: existing-account request-code succeeds and a code goes
// out; verify-code returns a session token.
function _mockCode(page, { verifyStatus = 200, verifyBody } = {}) {
  page.route(/\/auth\/request-code/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ ok: true, is_new: false }) }));
  page.route(/\/auth\/verify-code/, r =>
    r.fulfill({ status: verifyStatus, contentType: 'application/json',
                body: verifyBody || JSON.stringify({ token: 'fresh-token', username: 'alina@example.com' }) }));
  page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
}

test('existing user: email-code login stores the token and reveals the app', async ({ page }) => {
  await _mockCode(page);
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await expect(page.locator('#authNewFields')).toBeHidden();   // no terms block in the login lane
  await page.fill('#authEmail', 'alina@example.com');
  await page.click('#authSubmitBtn');                 // send code → step 2
  await expect(page.locator('#authStepCode')).toBeVisible();
  await page.fill('#authCode', '424242');
  await page.click('#authVerifyBtn');
  await expect(page.locator('#pipelineView')).toBeVisible();
  await expect(page.locator('#tabsBar')).toBeVisible();
  const stored = await page.evaluate(() => localStorage.getItem('hebpipe_token'));
  expect(stored).toBe('fresh-token');
});

test('?src= campaign link is captured, persisted, and sent with the code verify', async ({ page }) => {
  let verifyBody = null;
  await page.route(/\/auth\/request-code/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ ok: true, is_new: false }) }));
  await page.route(/\/auth\/verify-code/, (route, request) => {
    verifyBody = JSON.parse(request.postData());
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ token: 'fresh-token', username: 'lead@example.com' }) });
  });
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));

  // Mixed case in the link is normalized; the value survives in localStorage
  // so a later visit (they think it over, then sign up) still attributes.
  await page.goto('/?src=LinkedIn');
  expect(await page.evaluate(() => localStorage.getItem('hebpipe_src'))).toBe('linkedin');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'lead@example.com');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authStepCode')).toBeVisible();
  await page.fill('#authCode', '424242');
  await page.click('#authVerifyBtn');
  await expect(page.locator('#pipelineView')).toBeVisible();
  expect(verifyBody.src).toBe('linkedin');
});

test('a garbage ?src= value is ignored', async ({ page }) => {
  await page.goto('/?src=<script>x</script>');
  expect(await page.evaluate(() => localStorage.getItem('hebpipe_src'))).toBe(null);
});

test('login lane sends mode=login; unknown email shows "no account"', async ({ page }) => {
  let sentBody = null;
  await page.route(/\/auth\/request-code/, (route, request) => {
    sentBody = JSON.parse(request.postData());
    return route.fulfill({ status: 403, contentType: 'application/json',
                           body: JSON.stringify({ error: 'No account found for this email.', no_account: true }) });
  });
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'stranger@example.com');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authError')).toContainText(/No account|לא נמצא/);
  await expect(page.locator('#authStepCode')).toBeHidden();
  expect(sentBody.mode).toBe('login');
});

test('"no account" error offers a CTA that switches to the register lane in place', async ({ page }) => {
  const bodies = [];
  await page.route(/\/auth\/request-code/, (route, request) => {
    bodies.push(JSON.parse(request.postData()));
    return bodies.length === 1
      ? route.fulfill({ status: 403, contentType: 'application/json',
                        body: JSON.stringify({ error: 'No account found for this email.', no_account: true }) })
      : route.fulfill({ status: 200, contentType: 'application/json',
                        body: JSON.stringify({ ok: true, is_new: true }) });
  });
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'stranger@example.com');
  await page.click('#authSubmitBtn');
  // The error is actionable: a real button, not just "go back" prose.
  const cta = page.locator('#authError .lane-switch-btn');
  await expect(cta).toBeVisible();
  await cta.click();
  // One click lands in the register lane with the typed email preserved.
  await expect(page.locator('#authNewFields')).toBeVisible();
  await expect(page.locator('#authError')).toBeHidden();
  await expect(page.locator('#authEmail')).toHaveValue('stranger@example.com');
  // And the next submit really registers (mode=register).
  await page.check('#authTermsCheck');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authStepCode')).toBeVisible();
  expect(bodies[1].mode).toBe('register');
});

test('"already has an account" error in the register lane switches back to sign-in', async ({ page }) => {
  await page.route(/\/auth\/request-code/, (route, request) => {
    const body = JSON.parse(request.postData());
    return body.mode === 'register'
      ? route.fulfill({ status: 409, contentType: 'application/json',
                        body: JSON.stringify({ error: 'This email already has an account.', exists: true }) })
      : route.fulfill({ status: 200, contentType: 'application/json',
                        body: JSON.stringify({ ok: true, is_new: false }) });
  });
  await page.goto('/');
  await page.click('#authChoiceNew');
  await page.fill('#authEmail', 'alina@example.com');
  await page.check('#authTermsCheck');
  await page.click('#authSubmitBtn');
  const cta = page.locator('#authError .lane-switch-btn');
  await expect(cta).toBeVisible();
  await cta.click();
  await expect(page.locator('#authNewFields')).toBeHidden();   // login lane now
  await expect(page.locator('#authEmail')).toHaveValue('alina@example.com');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authStepCode')).toBeVisible();
});

test('a wrong code shows the server error, app stays hidden', async ({ page }) => {
  await _mockCode(page, { verifyStatus: 401,
                          verifyBody: JSON.stringify({ error: 'Incorrect code. Try again.' }) });
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'alina@example.com');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authStepCode')).toBeVisible();
  await page.fill('#authCode', '000000');
  await page.click('#authVerifyBtn');
  await expect(page.locator('#authCodeError')).toHaveText('Incorrect code. Try again.');
  await expect(page.locator('#pipelineView')).toBeHidden();
});

test('new user: terms shown up front (no invite), then code signs them in', async ({ page }) => {
  const bodies = [];
  await page.route(/\/auth\/request-code/, (route, request) => {
    bodies.push(JSON.parse(request.postData()));
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ ok: true, is_new: true }) });
  });
  await page.route(/\/auth\/verify-code/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ token: 'new-token', username: 'newbie@example.com' }) }));
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
  await page.goto('/');
  await page.click('#authChoiceNew');
  await expect(page.locator('#authNewFields')).toBeVisible();   // terms up front
  // Signup is open: there is no invite field left to fill.
  await expect(page.locator('#authInvite')).toHaveCount(0);
  await page.fill('#authEmail', 'newbie@example.com');
  await page.check('#authTermsCheck');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authStepCode')).toBeVisible();
  await page.fill('#authCode', '424242');
  await page.click('#authVerifyBtn');
  await expect(page.locator('#pipelineView')).toBeVisible();
  expect(bodies[0].mode).toBe('register');
  expect(bodies[0].invite).toBeUndefined();
  expect(bodies[0].terms_accepted).toBe(true);
  expect(bodies[0].email).toBe('newbie@example.com');
});

test('register lane: an already-used email shows "email taken"', async ({ page }) => {
  await page.route(/\/auth\/request-code/, r =>
    r.fulfill({ status: 409, contentType: 'application/json',
                body: JSON.stringify({ error: 'This email already has an account.', exists: true }) }));
  await page.goto('/');
  await page.click('#authChoiceNew');
  await page.fill('#authEmail', 'taken@example.com');
  await page.check('#authTermsCheck');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authError')).toContainText(/already has an account|כבר יש חשבון/);
  await expect(page.locator('#authStepCode')).toBeHidden();
});

test('register lane blocks submit with the terms unchecked, before any request', async ({ page }) => {
  let posted = false;
  await page.route(/\/auth\/request-code/, r => { posted = true; return r.fulfill({ status: 200, body: '{}' }); });
  await page.goto('/');
  await page.click('#authChoiceNew');
  await page.fill('#authEmail', 'newbie@example.com');
  await page.click('#authSubmitBtn');                    // terms unchecked
  await expect(page.locator('#authError')).toBeVisible();
  expect(posted).toBe(false);
  // Checking the box is the only thing standing between them and a code.
  await page.check('#authTermsCheck');
  await page.click('#authSubmitBtn');
  await expect.poll(() => posted).toBe(true);
});

test('a non-email is rejected inline, before any request', async ({ page }) => {
  let posted = false;
  await page.route(/\/auth\/request-code/, r => { posted = true; return r.fulfill({ status: 400, body: '{}' }); });
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'not-an-email');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authEmailErr')).toBeVisible();
  await expect(page.locator('#authStepCode')).toBeHidden();
  expect(posted).toBe(false);
});

test('missing/short code is rejected inline, before any request', async ({ page }) => {
  let verified = false;
  await page.route(/\/auth\/request-code/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, is_new: false }) }));
  await page.route(/\/auth\/verify-code/, r => { verified = true; return r.fulfill({ status: 200, body: '{}' }); });
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'alina@example.com');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authStepCode')).toBeVisible();
  await page.fill('#authCode', '12');           // too short (input strips to digits)
  await page.click('#authVerifyBtn');
  await expect(page.locator('#authCodeErr')).toBeVisible();
  expect(verified).toBe(false);
});

test('remembered email skips the landing and prefills the login panel', async ({ page }) => {
  await _mockCode(page);
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'alina@example.com');
  await page.click('#authSubmitBtn');
  await page.fill('#authCode', '424242');
  await page.click('#authVerifyBtn');           // rememberMe checked by default
  await expect(page.locator('#pipelineView')).toBeVisible();
  await page.evaluate(() => localStorage.removeItem('hebpipe_token'));
  await page.reload();
  await expect(page.locator('#authView')).toBeVisible();
  // Straight into the existing-user panel - no landing, email prefilled.
  await expect(page.locator('#authChoice')).toBeHidden();
  await expect(page.locator('#authStepEmail')).toBeVisible();
  await expect(page.locator('#authEmail')).toHaveValue('alina@example.com');
});

test('unchecking remember me leaves no email behind', async ({ page }) => {
  await _mockCode(page);
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.fill('#authEmail', 'alina@example.com');
  await page.uncheck('#rememberMe');
  await page.click('#authSubmitBtn');
  await page.fill('#authCode', '424242');
  await page.click('#authVerifyBtn');
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

// ── Google (web GIS) ─────────────────────────────────────────────────────────

// Fake the GIS library: capture the initialize callback and invoke it as if
// Google returned a credential, so we exercise our exchange path deterministically.
function _fakeGis(page) {
  return page.addInitScript(() => {
    window.google = { accounts: { id: {
      _cb: null,
      initialize(opts) { this._cb = opts.callback; },
      renderButton(el) { el.setAttribute('data-gsi', '1'); },
      prompt() {},
    } } };
    // Let the app's dynamically-injected GIS <script> "load" instantly.
    const realCreate = document.createElement.bind(document);
    document.createElement = (tag) => {
      const el = realCreate(tag);
      if (tag === 'script') {
        setTimeout(() => { if (/gsi\/client/.test(el.src || '')) el.onload && el.onload(); }, 0);
      }
      return el;
    };
  });
}

test('web Google login: a GIS credential is exchanged for a session token', async ({ page }) => {
  let sentBody = null;
  await page.route(/\/auth\/google/, (route, request) => {
    sentBody = JSON.parse(request.postData());
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ token: 'google-token', username: 'guser@example.com' }) });
  });
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
  await _fakeGis(page);
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await expect(page.locator('#gsiButton')).toHaveAttribute('data-gsi', '1');
  await page.evaluate(() => window.google.accounts.id._cb({ credential: 'fake-id-token' }));
  await expect(page.locator('#pipelineView')).toBeVisible();
  expect(sentBody.id_token).toBe('fake-id-token');
  expect(sentBody.mode).toBe('login');
  expect(await page.evaluate(() => localStorage.getItem('hebpipe_token'))).toBe('google-token');
});

test('web Google register: sends terms + mode=register, no invite', async ({ page }) => {
  let sentBody = null;
  await page.route(/\/auth\/google/, (route, request) => {
    sentBody = JSON.parse(request.postData());
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ token: 'g2-token', username: 'n@example.com' }) });
  });
  await page.route(/\/warmup/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));
  await _fakeGis(page);
  await page.goto('/');
  await page.click('#authChoiceNew');
  await expect(page.locator('#authNewFields')).toBeVisible();
  await page.check('#authTermsCheck');
  await page.evaluate(() => window.google.accounts.id._cb({ credential: 'tok2' }));
  await expect(page.locator('#pipelineView')).toBeVisible();
  expect(sentBody.mode).toBe('register');
  expect(sentBody.invite).toBeUndefined();
  expect(sentBody.terms_accepted).toBe(true);
});

test('web Google in the login lane with no account shows "no account"', async ({ page }) => {
  await page.route(/\/auth\/google/, r =>
    r.fulfill({ status: 403, contentType: 'application/json',
                body: JSON.stringify({ error: 'No account found for this Google account.', no_account: true }) }));
  await _fakeGis(page);
  await page.goto('/');
  await page.click('#authChoiceExisting');
  await page.evaluate(() => window.google.accounts.id._cb({ credential: 'tokX' }));
  await expect(page.locator('#authError')).toContainText(/No account|לא נמצא/);
  await expect(page.locator('#pipelineView')).toBeHidden();
});

test('a transient /auth/me failure at launch keeps the session (no code re-prompt)', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 'good-token'));
  // /auth/me flakes with a 5xx (cold start) - must NOT log the user out.
  await page.route(/\/auth\/me/, r =>
    r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"cold container"}' }));
  await page.route(/\/auth\/media-token/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m.x"}' }));
  await page.route(/\/oauth\/status/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"connected":false}' }));
  await page.goto('/');
  await expect(page.locator('#pipelineView')).toBeVisible();
  await expect(page.locator('#authView')).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('hebpipe_token'))).toBe('good-token');
});

test('server errors render in the UI language (Hebrew default) via their code', async ({ page }) => {
  // A throttled code request + a wrong code both carry machine codes; the
  // Hebrew UI must show Hebrew, not the server's English string.
  let firstReq = true;
  await page.route(/\/auth\/request-code/, (route) => {
    if (firstReq) {
      firstReq = false;
      return route.fulfill({ status: 429, contentType: 'application/json',
                             body: JSON.stringify({ error: 'Too many code requests.', code: 'throttled', retry_min: 3 }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ ok: true, is_new: true }) });
  });
  await page.route(/\/auth\/verify-code/, r =>
    r.fulfill({ status: 401, contentType: 'application/json',
                body: JSON.stringify({ error: 'Incorrect code. Try again.', code: 'code_incorrect' }) }));
  await page.goto('/');
  await page.click('#authChoiceNew');
  await page.fill('#authEmail', 'newbie@example.com');
  await page.check('#authTermsCheck');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authError')).toHaveText('יותר מדי ניסיונות. נסו שוב בעוד 3 דקות.');
  await page.click('#authSubmitBtn');
  await expect(page.locator('#authStepCode')).toBeVisible();
  await page.fill('#authCode', '000000');
  await page.click('#authVerifyBtn');
  await expect(page.locator('#authCodeError')).toHaveText('קוד שגוי. נסו שוב.');
});

test('migration note shows on login view; dismiss persists across reloads', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#migrationNote')).toBeVisible();
  await page.click('#migrationNoteClose');
  await expect(page.locator('#migrationNote')).toBeHidden();
  await page.reload();
  await expect(page.locator('#authView')).toBeVisible();
  await expect(page.locator('#migrationNote')).toBeHidden();
});
