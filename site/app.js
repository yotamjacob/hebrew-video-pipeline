  const API_BASE = 'https://yotamjacob--hebrew-video-pipeline-api.modal.run';
  const API      = API_BASE + '/process/';
  // Frontend version, shown in every footer. The app loads this site LIVE
  // (remote webview), so bumping this on each deploy is how we confirm the
  // installed app is running the latest push.
  const APP_VERSION = '1.48.8';
  // Every fix report to the user ends with this version; they verify the
  // footer tag on-device matches before re-testing (workflow, 2026-07-16).
  window.__APP_VERSION = 'v' + APP_VERSION;
  console.info('Pipeline frontend v' + APP_VERSION);

  // ── iOS safe areas ────────────────────────────────────────────────────────
  // The iOS WebView is full-screen, so without this the wordmark sits behind
  // the Dynamic Island and the language toggle collides with the clock (seen
  // on an iPhone 17 Pro Max simulator, 2026-08-11). Android never needed it:
  // there the WebView is laid out below a coloured status bar.
  // `env()` only reports real insets once the viewport opts into
  // `viewport-fit=cover`, so both halves are set here, and ONLY on the native
  // iOS build - the website keeps its current layout in mobile Safari.
  // The CSS side reads --sat/--sab, which default to 0px in :root, so every
  // rule using them is a no-op on web and Android.
  (function _applyIOSSafeArea() {
    const cap = window.Capacitor;
    const isIOSNative = !!(cap && cap.isNativePlatform && cap.isNativePlatform()
                           && cap.getPlatform && cap.getPlatform() === 'ios');
    if (!isIOSNative) return;
    const viewport = document.querySelector('meta[name="viewport"]');
    if (viewport && !/viewport-fit/.test(viewport.content)) {
      viewport.content += ', viewport-fit=cover';
    }
    document.documentElement.classList.add('ios-native');
  })();
  document.querySelectorAll('p.footer').forEach(f => {
    const v = document.createElement('span');
    v.className = 'footer-version';
    v.textContent = 'v' + APP_VERSION;
    f.appendChild(v);
  });

  // Google Sign-In OAuth Web client ID (PUBLIC - ships in the app + web page).
  // Used as the native plugin's serverClientId and the web GIS client_id; the
  // backend verifies the returned ID token against this same audience.
  const GOOGLE_WEB_CLIENT_ID = '229326610541-4870rqhqu4sckii6rv15sjm3bqogeku1.apps.googleusercontent.com';

  // ── Console capture ──
  // Ring buffer of recent console output, surfaced in the error card so
  // mobile users (no devtools) can see and report what actually happened.
  const consoleLog = [];
  ['error', 'warn', 'info'].forEach(level => {
    const orig = console[level].bind(console);
    console[level] = (...args) => {
      try {
        const line = args.map(a => typeof a === 'string' ? a : (a && a.message) || String(a)).join(' ');
        const d = new Date();
        const ts = [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
        consoleLog.push(`${ts} [${level}] ${line}`.slice(0, 300));
        if (consoleLog.length > 40) consoleLog.shift();
      } catch (_) {}
      orig(...args);
    };
  });
  window.addEventListener('error', ev =>
    console.error(`Uncaught: ${ev.message} (${ev.filename ? ev.filename.split('/').pop() : ''}:${ev.lineno || ''})`));
  window.addEventListener('unhandledrejection', ev =>
    console.error(`Unhandled rejection: ${(ev.reason && ev.reason.message) || ev.reason}`));

  // ── Inline SVG line-icons ──
  // Thin-stroke, currentColor, sized at 1em so each icon inherits its
  // container's font-size and color (design system: no emoji in chrome).
  const ICON = {
    x:        '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>',
    check:    '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 13l4 4L19 7"/></svg>',
    play:     '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>',
    stop:     '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
    scissors: '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><line x1="8.1" y1="7.6" x2="20" y2="18"/><line x1="8.1" y1="16.4" x2="20" y2="6"/></svg>',
    star:     '<svg width="1em" height="1em" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3.4l2.47 5.01 5.53.8-4 3.9.94 5.5L12 16.9l-4.95 2.6.94-5.5-4-3.9 5.53-.8z"/></svg>',
  };

  // ── Refresh-for-update ──
  // The editor is a SPA: a long-lived tab (or the Android webview, which
  // survives for days) keeps running the JS it booted with across deploys -
  // a field lesson: several "bug still happening" reports were a stale tab.
  // Poll the deployed app.js head for APP_VERSION; when it differs,
  // auto-reload while nothing would be lost, else show a refresh pill.
  const VERSION_CHECK_MS = (window.__VERSION_CHECK_MS != null) ? window.__VERSION_CHECK_MS : 10 * 60_000;
  let _updateDismissed = null;   // version the user dismissed the pill for
  function _safeToAutoReload() {
    // Mirrors the beforeunload guard, plus "nothing selected yet".
    const editing = document.getElementById('captionEditorCard')?.style.display !== 'none';
    const brollWork = typeof stockBrollSelections === 'object'
                      && Object.keys(stockBrollSelections).length > 0;
    return !selectedFile && !isUploading && !pollController && !editing
           && !resultBlob && !brollWork;
  }
  async function _checkDeployedVersion() {
    try {
      const r = await fetch('/app.js?vchk=' + Date.now(),
                            { headers: { 'Range': 'bytes=0-2047' }, cache: 'no-store' });
      if (!r.ok) return;
      const m = /APP_VERSION = '([0-9.]+)'/.exec(await r.text());
      if (!m || m[1] === APP_VERSION) return;
      if (_safeToAutoReload()) { location.reload(); return; }
      if (m[1] !== _updateDismissed) _showUpdatePill(m[1]);
    } catch (_) {}
  }
  function _showUpdatePill(ver) {
    if (document.getElementById('updatePill')) return;
    const pill = document.createElement('div');
    pill.id = 'updatePill';
    pill.className = 'update-pill';
    const txt = document.createElement('span');
    txt.textContent = t('update.available');
    const btn = document.createElement('button');
    btn.className = 'update-pill-btn';
    btn.textContent = t('update.refresh');
    btn.onclick = () => location.reload();
    const x = document.createElement('button');
    x.className = 'update-pill-x';
    x.setAttribute('aria-label', 'dismiss');
    x.innerHTML = ICON.x;
    x.onclick = () => { _updateDismissed = ver; pill.remove(); };
    pill.append(txt, btn, x);
    document.body.appendChild(pill);
  }
  setInterval(_checkDeployedVersion, VERSION_CHECK_MS);
  // A thawed webview is the classic stale moment - check right then.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) _checkDeployedVersion();
  });

  // Gates all user-facing email flows (verify nudge + password reset). Off
  // until a sending domain is verified in Resend — with the test sender,
  // emails only reach the account owner, so these flows would send mail that
  // never arrives. Flip to true once EMAIL_FROM uses a verified domain.
  const EMAIL_UI_ENABLED = false;

  // ── Auth: session token, authenticated fetch, login gate ──
  // Remembered sessions live in localStorage (survive browser close); when
  // "remember me" is off, the token lives in sessionStorage (cleared on close).
  let authToken = localStorage.getItem('hebpipe_token') || sessionStorage.getItem('hebpipe_token') || '';

  function _storeToken(tok, remember) {
    authToken = tok;
    if (remember) { localStorage.setItem('hebpipe_token', tok); sessionStorage.removeItem('hebpipe_token'); }
    else          { sessionStorage.setItem('hebpipe_token', tok); localStorage.removeItem('hebpipe_token'); }
  }
  function _clearToken() {
    authToken = ''; mediaToken = '';
    localStorage.removeItem('hebpipe_token');
    sessionStorage.removeItem('hebpipe_token');
    // NOTE: intentionally does NOT drop the remembered email/password - a
    // logout is meant to land on a pre-filled login form. They are cleared
    // only on a login where "remember me" was unchecked (see _forgetCreds).
  }

  // Remember the last-used email for prefill (passwordless - there's no password
  // to store anymore). Kept only when "remember me" was checked at sign-in.
  function _rememberCreds(email) {
    localStorage.setItem('hebpipe_email', email || '');
    localStorage.removeItem('hebpipe_pw');   // clear any legacy stored password
  }
  function _forgetCreds() {
    localStorage.removeItem('hebpipe_email');
    localStorage.removeItem('hebpipe_pw');
  }

  // ── Signup source attribution ──
  // A campaign link (/?src=linkedin, ?utm_source= also accepted) is captured at
  // load and persisted - the visitor often creates the account on a LATER visit
  // (they think it over, then do the email-code round-trip) and the query is long
  // gone by then. Sent with account creation; the backend records it on NEW
  // records only, so signing in never overwrites an existing attribution.
  try {
    const _sp = new URLSearchParams(location.search);
    const _src = (_sp.get('src') || _sp.get('utm_source') || '').trim().toLowerCase().slice(0, 32);
    if (/^[a-z0-9_-]+$/.test(_src)) localStorage.setItem('hebpipe_src', _src);
  } catch (_) {}
  function _signupSrc() {
    try { return localStorage.getItem('hebpipe_src') || undefined; } catch (_) { return undefined; }
  }

  // Short-lived, GET-only token used in media URLs (img/video src, downloads)
  // so the long-lived session token never rides in a query string / browser
  // history. Falls back to the session token until the first one arrives.
  let mediaToken = '';

  async function refreshMediaToken() {
    if (!authToken) { mediaToken = ''; return; }
    try {
      const r = await apiFetch(`${API_BASE}/auth/media-token`);
      if (r.ok) mediaToken = (await r.json()).token || '';
    } catch { /* keep falling back to the session token */ }
  }
  // Re-mint well before the 1h server TTL so long sessions never lapse.
  setInterval(() => { refreshMediaToken(); }, 45 * 60 * 1000);

  function _withToken(url) {
    const tok = mediaToken || authToken;
    if (!tok) return url;
    return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(tok);
  }

  function _sessionExpired() {
    _clearToken();
    showAuthView();
  }

  // Swap a button's content for a spinner while an async action runs, and
  // restore its original label afterwards. Reusable across the app.
  function _btnBusy(btn, busy) {
    if (!btn) return;
    if (busy) {
      if (btn.dataset.label == null) btn.dataset.label = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>';
    } else {
      btn.disabled = false;
      if (btn.dataset.label != null) { btn.innerHTML = btn.dataset.label; delete btn.dataset.label; }
    }
  }

  // All API calls go through here - attaches the bearer token, and drops the
  // user back to the sign-in view when the session is missing/expired.
  async function apiFetch(url, opts = {}) {
    opts = Object.assign({}, opts);
    opts.headers = Object.assign({}, opts.headers || {},
      authToken ? { 'Authorization': 'Bearer ' + authToken } : {});
    const resp = await fetch(url, opts);
    if (resp.status === 401) {
      _sessionExpired();
      throw new Error(t('auth.sessionExpired'));
    }
    return resp;
  }

  // apiFetch + retry for SPAWN requests (hooks / B-roll / burn): a pure
  // network failure (fetch TypeError - connection blip, network switch, the
  // OS killing the request on backgrounding) retries with backoff, parking
  // until the page is visible again. Server replies (4xx/5xx) are answers,
  // not blips - they surface immediately. Payloads with a long transcript
  // take seconds to send on a weak uplink, which makes these requests the
  // most blip-prone in the app ("Failed to fetch" on 10-minute videos).
  async function apiFetchRetry(url, opts = {}, attempts = 3) {
    for (let i = 0; ; i++) {
      try {
        return await apiFetch(url, opts);
      } catch (e) {
        const isNetBlip = e instanceof TypeError;
        if (!isNetBlip || i >= attempts - 1) {
          throw isNetBlip ? new Error(t('err.netBlip')) : e;
        }
        console.warn(`spawn fetch blip (attempt ${i + 1}/${attempts}):`, e && e.message);
        if (typeof _whenVisible === 'function') await _whenVisible();
        await new Promise(r => setTimeout(r, 1000 * (i + 1)));
      }
    }
  }

  function _hideBootLoader() { const bl = document.getElementById('bootLoader'); if (bl) bl.style.display = 'none'; }

  function showAuthView() {
    _hideBootLoader();
    document.getElementById('authView').style.display = 'block';
    // TEMPORARY auth-migration nudge (passwords -> email codes/Google).
    // Never on iOS: the App Store build has no legacy password users to migrate,
    // and the copy promises a Google lane that this build deliberately hides
    // (Guideline 4.8), which would just confuse a first-time user.
    { const mn = document.getElementById('migrationNote');
      if (mn && !_isIOS() && localStorage.getItem('hebpipe_migration_dismissed') !== '1') {
        mn.style.display = 'flex';
      } }
    // Prefill the last-used email + password (saved on sign-in while "remember
    // me" was checked) so a returning user - e.g. right after logging out -
    // lands on a ready-to-submit form.
    const emailInput = document.getElementById('authEmail');
    if (emailInput && !emailInput.value) {
      emailInput.value = localStorage.getItem('hebpipe_email') || '';
    }
    document.getElementById('tabsBar').style.display = 'none';
    const vb = document.getElementById('verifyBanner');
    if (vb) vb.style.display = 'none';
    const mc = document.getElementById('metricoolChip');
    if (mc) mc.style.display = 'none';
    const lo = document.getElementById('logoutTab');
    if (lo) lo.style.display = 'none';
    ['pipelineView', 'historyView', 'guideView', 'adminView', 'costsView'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    applyAuthMode();   // reflect EMAIL_UI_ENABLED (forgot link) on first paint
  }

  function showApp() {
    _hideBootLoader();
    document.getElementById('authView').style.display = 'none';
    document.getElementById('tabsBar').style.display = 'flex';
    document.getElementById('logoutTab').style.display = 'inline-block';
    document.getElementById('pipelineView').style.display = 'block';
    refreshMediaToken();
    refreshMetricoolChip();
    loadProfiles();
    if (quotaInfo) { updateQuotaUI(); updateVerifyBanner(); } else refreshQuota();
    restoreTab();
    if (typeof _initPush === 'function') _initPush();
    _initBillingListener();
    // Reconcile completed/unconsumed Play purchases after every fresh app
    // session. This covers a purchase finishing while the app was closed.
    setTimeout(() => { _restorePlayPurchases().catch(() => {}); }, 100);
    // AUTO-resume a saved background job - no banner tap required. Opening the
    // app (e.g. from the "ready to edit" push) lands straight back in the
    // flow; when processing finished while away, the poll returns instantly
    // and the editor + preview load immediately.
    if (!window.__resumeStarted && loadSavedJob()) {
      window.__resumeStarted = true;
      setTimeout(() => { resumeSavedJob().catch(e => console.warn('auto-resume failed', e)); }, 50);
    }
  }

  // ── Email verification nudge (non-blocking) ──
  function updateVerifyBanner() {
    const banner = document.getElementById('verifyBanner');
    if (!EMAIL_UI_ENABLED || !quotaInfo || quotaInfo.email_verified) { banner.style.display = 'none'; return; }
    const hasEmail = !!quotaInfo.email;
    document.getElementById('verifyBannerMsg').textContent =
      hasEmail ? t('verify.pending', { email: quotaInfo.email }) : t('verify.noEmail');
    document.getElementById('verifyEmailInput').style.display = hasEmail ? 'none' : 'inline-block';
    document.getElementById('verifyBannerBtn').textContent =
      hasEmail ? t('verify.resend') : t('verify.add');
    banner.style.display = 'flex';
  }

  async function requestVerification() {
    const btn = document.getElementById('verifyBannerBtn');
    const input = document.getElementById('verifyEmailInput');
    const body = {};
    if (input.style.display !== 'none') {
      const em = input.value.trim();
      if (!em) return;
      body.email = em;
    }
    btn.disabled = true;
    try {
      const r = await apiFetch(`${API_BASE}/auth/request-verification`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || 'error');
      if (body.email && quotaInfo) quotaInfo.email = body.email;
      document.getElementById('verifyBannerMsg').textContent = t('verify.sent', { email: data.email || '' });
      input.style.display = 'none';
      btn.style.display = 'none';
    } catch (e) {
      document.getElementById('verifyBannerMsg').textContent = t('verify.sendFailed');
    } finally {
      btn.disabled = false;
    }
  }

  // ── Video quota (free tier) ──
  let quotaInfo = null;

  async function refreshQuota() {
    try {
      const resp = await apiFetch(`${API_BASE}/auth/me`);
      if (!resp.ok) return;
      quotaInfo = await resp.json();
    } catch { return; }
    updateQuotaUI();
    updateVerifyBanner();
  }

  // What this run costs, mirroring _credit_cost on the server. One credit
  // covers 10 minutes of source; a longer video and the 4K upscale each add
  // one. The server prices it again from its own probe - this is only so the
  // user is told the price before spending it.
  const CREDIT_SECONDS = 600;
  const UPSCALE_MAX_SECONDS = 180;
  const CREDIT_DURATION_TOLERANCE = 5;
  function _creditCost() {
    let n = 1;
    if (videoDuration && videoDuration > CREDIT_SECONDS + CREDIT_DURATION_TOLERANCE) n += 1;
    if (!isAudioInput && _enhanceVideoMode() === 'esrgan') n += 1;
    return n;
  }

  // Non-admin users confirm before spending credits
  async function _confirmQuotaUse() {
    if (!quotaInfo || quotaInfo.role === 'admin' ||
        quotaInfo.video_limit == null || quotaInfo.video_limit < 0) return true;
    const left = Math.max(0, quotaInfo.video_limit - quotaInfo.videos_used);
    const cost = _creditCost();
    return showConfirmModal(
      cost > 1 ? t('quota.confirmTitleN', {n: cost}) : t('quota.confirmTitle'),
      cost > 1
        ? t('quota.confirmBodyN', {n: cost, left: left})
        : t('quota.confirmBody', {left: left, limit: quotaInfo.video_limit}),
      t('quota.confirmOk'));
  }

  function _isAdminUser() {
    return !!(quotaInfo && quotaInfo.role === 'admin');
  }

  function _quotaExhausted() {
    return !!(quotaInfo && quotaInfo.role !== 'admin' &&
              quotaInfo.video_limit != null && quotaInfo.video_limit >= 0 &&
              quotaInfo.videos_used >= quotaInfo.video_limit);
  }

  function updateQuotaUI() {
    if (!quotaInfo) return;
    const greet = document.getElementById('heroGreeting');
    if (greet && quotaInfo.username) {
      // Accounts are keyed by email - greet with the part before the @.
      greet.textContent = t('hero.hello', {name: quotaInfo.username.split('@')[0]});
      greet.style.display = '';
    }
    const adminTab = document.getElementById('tabAdmin');
    if (adminTab) adminTab.style.display = quotaInfo.role === 'admin' ? '' : 'none';
    const costsTab = document.getElementById('tabCosts');
    if (costsTab) costsTab.style.display = quotaInfo.role === 'admin' ? '' : 'none';
    // A saved Admin/Costs tab could only be restored once the role loaded.
    if ((_pendingTabRestore === 'admin' || _pendingTabRestore === 'costs')
        && quotaInfo.role === 'admin') {
      const tb = _pendingTabRestore;
      _pendingTabRestore = null;
      switchTab(tb);
    }
    const pill = document.getElementById('quotaPill');
    if (!pill) return;
    // Admins bypass the quota, so there is no remaining count to show - but the
    // purchase flow must stay reachable for them (buying, and exercising the
    // Play Billing path end to end). The pill becomes a plain "buy credits"
    // action instead of a counter.
    if (_isAdminUser()) {
      pill.textContent = t('billing.buyCredits');
      pill.classList.remove('quota-pill-empty');
      pill.classList.add('billing-enabled');
      pill.title = _billingAvailable() ? t('billing.open') : t('billing.openPlayStore');
      pill.style.display = '';
      return;
    }
    if (quotaInfo.video_limit == null || quotaInfo.video_limit < 0) {
      pill.style.display = 'none';
      return;
    }
    const left = Math.max(0, quotaInfo.video_limit - quotaInfo.videos_used);
    pill.textContent = left > 0
      ? t('quota.pill', {left: left, limit: quotaInfo.video_limit})
      : t('quota.pillZero');
    pill.classList.toggle('quota-pill-empty', left === 0);
    pill.classList.toggle('billing-enabled', _billingAvailable() || left === 0);
    pill.title = _billingAvailable()
      ? t('billing.open')
      : (left === 0 ? t('billing.openPlayStore') : '');
    pill.style.display = '';
  }

  // ── Passwordless auth: choose a lane → Google or email code → session ──
  // Landing offers "I have an account" / "I'm new here". Existing users sign in
  // with Google or an emailed 6-digit code; new users do the same but must also
  // accept the Terms (shown up front). Signup is OPEN - the invite-code field
  // was removed 2026-08-01, so the emailed code is the only barrier. The backend
  // gets an explicit `mode` so an existing email can't register a duplicate and
  // a nonexistent email gets a clear "no account". No passwords anywhere.
  const _EMAIL_JS_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  let _authEmail = '';        // the email awaiting a code (drives step 2 + resend)
  let _authFlow  = 'login';   // 'login' (existing user) | 'register' (new user)

  function _fieldErr(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = msg || '';
    el.style.display = msg ? 'block' : 'none';
  }

  // Translate a server auth error into the UI language via its machine `code`
  // (structured flags first, then the code map, then the raw English fallback
  // for anything unmapped / an older backend).
  const _AUTH_ERR_KEYS = {
    rate_limited: 'autherr.rate_limited', throttled: 'autherr.throttled',
    invalid_email: 'autherr.invalid_email',
    terms_required: 'auth.termsError', google_failed: 'auth.googleFailed',
    code_format: 'autherr.code_format', code_expired: 'autherr.code_expired',
    code_tries: 'autherr.code_tries', code_incorrect: 'autherr.code_incorrect',
  };
  function _authErrMsg(data, status) {
    if (data && data.no_account) return t('auth.noAccount');
    if (data && data.exists) return t('auth.emailTaken');
    const key = data && _AUTH_ERR_KEYS[data.code];
    if (key) return t(key, { n: (data && data.retry_min) || 1 });
    return (data && data.error) || t('auth.errStatus', { status });
  }

  function _showAuthStep(step) {
    document.getElementById('authChoice').style.display    = step === 'choice' ? 'block' : 'none';
    document.getElementById('authStepEmail').style.display = step === 'email'  ? 'block' : 'none';
    document.getElementById('authStepCode').style.display  = step === 'code'   ? 'block' : 'none';
  }

  function _clearAuthMessages() {
    ['authError', 'authInfo', 'authCodeError', 'authCodeInfo'].forEach(id => {
      const e = document.getElementById(id); if (e) e.style.display = 'none';
    });
    ['authEmailErr', 'authCodeErr'].forEach(id => _fieldErr(id, ''));
  }

  // Login lane submits straight to "sign in" wording; only the register lane
  // talks about sending a code (the user meets the code on the next screen anyway).
  function _syncAuthSubmitLabel() {
    const b = document.getElementById('authSubmitBtn');
    if (b) b.textContent = t(_authFlow === 'register' ? 'auth.sendCode' : 'auth.loginEmail');
  }

  function _enterAuthFlow(flow) {
    _authFlow = flow;
    _pendingGoogleToken = null;
    _clearAuthMessages();
    document.getElementById('authNewFields').style.display = flow === 'register' ? 'block' : 'none';
    _syncAuthSubmitLabel();
    _showAuthStep('email');
    document.getElementById('authEmail').focus();
  }

  // Wrong-lane errors ("no account yet" / "already has an account") are the
  // most common dead end for new users, so they get a real CTA button that
  // jumps straight to the right lane - not just text telling them to go back.
  // The typed email survives the switch (same input), and a pending native
  // Google token is kept so they don't have to pick their account again.
  function _laneSwitchErr(errEl, targetFlow) {
    const msgKey = targetFlow === 'register' ? 'auth.noAccount'    : 'auth.emailTaken';
    const ctaKey = targetFlow === 'register' ? 'auth.noAccountCta' : 'auth.emailTakenCta';
    errEl.innerHTML = '<span>' + _escapeHtml(t(msgKey)) + '</span>' +
      '<button type="button" class="lane-switch-btn">' + _escapeHtml(t(ctaKey)) + '</button>';
    errEl.style.display = 'block';
    errEl.querySelector('.lane-switch-btn').addEventListener('click', () => {
      const keepGoogle = _pendingGoogleToken;
      _enterAuthFlow(targetFlow);
      _pendingGoogleToken = keepGoogle;
    });
  }

  // Reset the auth view. A device that remembers its email skips the landing
  // straight into the existing-user panel; anyone else picks a lane first.
  function applyAuthMode() {
    _clearAuthMessages();
    _syncAuthSubmitLabel();
    document.getElementById('authVerifyBtn').textContent = t('auth.verify');
    if (localStorage.getItem('hebpipe_email')) {
      _authFlow = 'login';
      document.getElementById('authNewFields').style.display = 'none';
      _showAuthStep('email');
    } else {
      _showAuthStep('choice');
    }
  }

  function validateEmail(onSubmit) {
    const v = document.getElementById('authEmail').value.trim();
    let msg = '';
    if (!v) { if (onSubmit) msg = t('valid.emailRequired'); }
    else if (!_EMAIL_JS_RE.test(v)) msg = t('valid.emailInvalid');
    _fieldErr('authEmailErr', msg);
    return !msg;
  }

  { const mc = document.getElementById('migrationNoteClose');
    if (mc) mc.addEventListener('click', () => {
      document.getElementById('migrationNote').style.display = 'none';
      try { localStorage.setItem('hebpipe_migration_dismissed', '1'); } catch (_) {}
    }); }
  document.getElementById('authChoiceExisting').addEventListener('click', () => _enterAuthFlow('login'));
  document.getElementById('authChoiceNew').addEventListener('click', () => _enterAuthFlow('register'));
  document.getElementById('authBackToChoice').addEventListener('click', () => _showAuthStep('choice'));
  document.getElementById('authEmail').addEventListener('input', () => validateEmail(false));
  // Step 1 goes through the real <form> submit (Enter in the email field works).
  document.getElementById('authForm').addEventListener('submit', (e) => {
    e.preventDefault();
    sendLoginCode(false);
  });
  document.getElementById('authVerifyBtn').addEventListener('click', () => verifyLoginCode());
  document.getElementById('authResendBtn').addEventListener('click', () => sendLoginCode(true));
  document.getElementById('authBackBtn').addEventListener('click', () => {
    _clearAuthMessages();
    _showAuthStep('email');
  });
  const _codeInput = document.getElementById('authCode');
  _codeInput.addEventListener('input', () => {
    _codeInput.value = _codeInput.value.replace(/\D/g, '').slice(0, 6);
    _fieldErr('authCodeErr', '');
  });
  _codeInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); verifyLoginCode(); }
  });

  // Step 1: request a login code. `resend` re-requests for the same email.
  async function sendLoginCode(resend) {
    const btn = document.getElementById(resend ? 'authResendBtn' : 'authSubmitBtn');
    const errEl = document.getElementById('authError');
    if (!resend && !validateEmail(true)) return;
    const email = resend ? _authEmail : document.getElementById('authEmail').value.trim();
    const registering = _authFlow === 'register';
    if (registering && !document.getElementById('authTermsCheck').checked) {
      errEl.textContent = t('auth.termsError'); errEl.style.display = 'block'; return;
    }
    _btnBusy(btn, true);
    errEl.style.display = 'none';
    try {
      const body = { email, mode: _authFlow };
      if (registering) body.terms_accepted = true;
      const resp = await fetch(`${API_BASE}/auth/request-code`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await resp.json().catch(() => ({}));
      // Wrong lane gets an actionable CTA that switches lanes in place.
      if (!resp.ok && data && (data.no_account || data.exists)) {
        _laneSwitchErr(errEl, data.no_account ? 'register' : 'login');
        return;
      }
      // Missing terms, throttle, etc - all translated to the UI language.
      if (!resp.ok) throw new Error(_authErrMsg(data, resp.status));
      // Code sent → step 2.
      _authEmail = email;
      _showAuthStep('code');
      document.getElementById('authCodeHint').textContent = t('auth.codeHint', { email });
      if (resend) {
        const ci = document.getElementById('authCodeInfo');
        ci.textContent = t('auth.codeResent'); ci.style.display = 'block';
      }
      const ci2 = document.getElementById('authCode');
      ci2.value = ''; ci2.focus();
    } catch (e) {
      errEl.textContent = e.message || String(e);
      errEl.style.display = 'block';
    } finally {
      _btnBusy(btn, false);
    }
  }

  // Step 2: verify the code → session token.
  async function verifyLoginCode() {
    const btn = document.getElementById('authVerifyBtn');
    const errEl = document.getElementById('authCodeError');
    const code = document.getElementById('authCode').value.trim();
    if (!code) { _fieldErr('authCodeErr', t('valid.codeRequired')); return; }
    if (!/^\d{6}$/.test(code)) { _fieldErr('authCodeErr', t('valid.codeInvalid')); return; }
    _btnBusy(btn, true);
    errEl.style.display = 'none';
    try {
      const resp = await fetch(`${API_BASE}/auth/verify-code`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: _authEmail, code, src: _signupSrc() }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(_authErrMsg(data, resp.status));
      const remember = document.getElementById('rememberMe')?.checked ?? true;
      _storeToken(data.token, remember);
      if (remember) _rememberCreds(_authEmail); else _forgetCreds();
      showApp();
      fetch(API_BASE + '/warmup/', { headers: { 'Authorization': 'Bearer ' + authToken } }).catch(() => {});
    } catch (e) {
      errEl.textContent = e.message || String(e);
      errEl.style.display = 'block';
    } finally {
      _btnBusy(btn, false);
    }
  }

  // ── Continue with Google ──────────────────────────────────────────────────
  // Exchange a Google ID token for a session token. Same account keying as the
  // code flow (backend normalizes the email), so Google + code = one account.
  let _pendingGoogleToken = null;   // native: reuse the ID token across a lane switch / terms fix
  async function _exchangeGoogleToken(idToken) {
    const body = { id_token: idToken, mode: _authFlow, src: _signupSrc() };
    if (_authFlow === 'register') {
      body.terms_accepted = document.getElementById('authTermsCheck').checked;
    }
    const resp = await fetch(`${API_BASE}/auth/google`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errEl = document.getElementById('authError');
      // Wrong lane: this Google account has no record yet → one-click switch
      // to the register lane (the pending token survives the switch).
      if (data.no_account) {
        _pendingGoogleToken = idToken;   // native: reuse after switching lanes
        _laneSwitchErr(errEl, 'register');
        return;
      }
      if (data.exists) {
        _pendingGoogleToken = idToken;
        _laneSwitchErr(errEl, 'login');
        return;
      }
      // Register lane with the Terms box unchecked. `need_invite` is still
      // accepted so an older backend's flag name keeps working.
      if (data.need_terms || data.need_invite) {
        _pendingGoogleToken = idToken;   // reuse on native so no second account picker
        errEl.textContent = data.code ? _authErrMsg(data, resp.status) : t('auth.googleNeedTerms');
        errEl.style.display = 'block';
        document.getElementById('authTermsCheck').focus();
        return;
      }
      throw new Error(_authErrMsg(data, resp.status));
    }
    _pendingGoogleToken = null;
    const remember = document.getElementById('rememberMe')?.checked ?? true;
    _storeToken(data.token, remember);
    if (remember && data.username) _rememberCreds(data.username); else _forgetCreds();
    showApp();
    fetch(API_BASE + '/warmup/', { headers: { 'Authorization': 'Bearer ' + authToken } }).catch(() => {});
  }

  function _googleAuthError(e) {
    const msg = (e && (e.message || e.errorMessage)) || String(e || '');
    try { console.error('google sign-in failed:', msg, e); } catch (_) {}
    if (/cancel|closed|popup|dismiss|USER_CANCELED|USER_CANCELLED/i.test(msg)) return;   // backed out - stay quiet
    const errEl = document.getElementById('authError');
    // Surface the raw reason on a detail line so device users (no devtools) can
    // report exactly what Google returned (SHA-1/client-id mismatches show here).
    errEl.innerHTML = _escapeHtml(t('auth.googleFailed')) +
      (msg ? '<br><span style="font-size:0.72rem;opacity:0.7;word-break:break-word">' + _escapeHtml(msg) + '</span>' : '');
    errEl.style.display = 'block';
  }

  let _glInited = false;
  // Native: the SocialLogin plugin (Credential Manager) returns an ID token
  // minted for our Web client ID (serverClientId).
  async function _googleNativeSignIn() {
    const SL = _capPlugin('SocialLogin');
    const btn = document.getElementById('googleSignInBtn');
    if (!SL) { _googleAuthError(new Error('unavailable')); return; }
    // Register lane: require the Terms BEFORE opening the account picker, so
    // nobody authenticates with Google only to bounce on the checkbox.
    if (_authFlow === 'register' && !document.getElementById('authTermsCheck').checked) {
      const errEl = document.getElementById('authError');
      errEl.textContent = t('auth.termsError'); errEl.style.display = 'block'; return;
    }
    _btnBusy(btn, true);
    try {
      // Retry after a lane switch / terms fix: reuse the token we already got,
      // so the user doesn't have to pick their Google account a second time.
      if (_pendingGoogleToken) { await _exchangeGoogleToken(_pendingGoogleToken); return; }
      if (!_glInited) {
        await SL.initialize({ google: { webClientId: GOOGLE_WEB_CLIENT_ID, mode: 'online' } });
        _glInited = true;
      }
      // NO scopes here: on Android, passing scopes switches the plugin to an
      // authorization-code flow that needs a modified MainActivity ("You CANNOT
      // use scopes without modifying the main activity"). Plain Credential
      // Manager sign-in already returns an ID token with email + profile claims.
      const res = await SL.login({ provider: 'google', options: {} });
      const r = (res && res.result) ? res.result : {};
      const idToken = r.idToken || r.id_token || (r.credential && r.credential.idToken) || null;
      if (!idToken) throw new Error('no ID token in result (keys: ' + Object.keys(r).join(',') + ')');
      await _exchangeGoogleToken(idToken);
    } catch (e) {
      _googleAuthError(e);
    } finally {
      _btnBusy(btn, false);
    }
  }

  // Web: the token exchange after Google's popup closes takes seconds on a
  // cold backend, and GIS's iframe button gives no feedback - swap it for a
  // spinner pill while connecting so the wait reads as progress, not a hang.
  function _gsiConnecting(on) {
    const cont = document.getElementById('gsiButton');
    let el = document.getElementById('googleConnecting');
    if (on) {
      if (!el) {
        el = document.createElement('div');
        el.id = 'googleConnecting';
        el.className = 'google-connecting';
        el.setAttribute('role', 'status');
        el.innerHTML = '<span class="spinner" aria-hidden="true"></span><span>' +
          _escapeHtml(t('auth.connecting')) + '</span>';
        (cont && cont.parentElement ? cont.parentElement : document.body)
          .insertBefore(el, cont || null);
      }
      el.style.display = 'flex';
      if (cont) cont.style.display = 'none';
    } else {
      if (el) el.style.display = 'none';
      if (cont) cont.style.display = 'flex';
    }
  }
  async function _gsiExchange(credential) {
    _gsiConnecting(true);
    try {
      await _exchangeGoogleToken(credential);
    } catch (e) {
      _googleAuthError(e);
    } finally {
      // Also runs on the non-throwing "failure" branches (lane switch, terms
      // nudge) - the button must come back in every path that stays on auth.
      _gsiConnecting(false);
    }
  }

  // Web: Google Identity Services renders its own compliant button into
  // #gsiButton and hands back a credential (ID token) via the callback.
  function _initWebGoogle() {
    const s = document.createElement('script');
    s.src = 'https://accounts.google.com/gsi/client';
    s.async = true; s.defer = true;
    s.onload = () => {
      if (!(window.google && window.google.accounts && window.google.accounts.id)) return;
      window.google.accounts.id.initialize({
        client_id: GOOGLE_WEB_CLIENT_ID,
        callback: (resp) => {
          if (resp && resp.credential) _gsiExchange(resp.credential);
        },
      });
      const cont = document.getElementById('gsiButton');
      const custom = document.getElementById('googleSignInBtn');
      if (custom) custom.style.display = 'none';
      if (cont) {
        cont.style.display = 'flex';
        window.google.accounts.id.renderButton(cont, {
          theme: 'outline', size: 'large', text: 'continue_with', shape: 'pill',
        });
      }
    };
    s.onerror = () => { /* GIS blocked - the email-code flow still works */ };
    document.head.appendChild(s);
  }

  function _setupGoogleAuth() {
    const btn = document.getElementById('googleSignInBtn');
    // App Store Guideline 4.8: an app that offers a third-party sign-in service
    // must also offer an equivalent privacy-preserving option, and our email
    // code lane cannot satisfy it (it can't hide the address the way Sign in
    // with Apple does). Rather than take on Sign in with Apple, the iOS build
    // offers ONLY the email code - which puts 4.8 out of scope entirely.
    // Deliberately not applied to Safari on iOS: the website is not the app.
    if (_isIOS()) {
      if (btn) btn.style.display = 'none';
      const gsi = document.getElementById('gsiButton');
      if (gsi) gsi.style.display = 'none';
      document.querySelectorAll('.auth-or').forEach(el => { el.style.display = 'none'; });
      // Re-point the "continue with Google or email" hint. Swapping the
      // data-i18n attribute too keeps it correct across language switches.
      const hint = document.getElementById('authNewHint');
      if (hint) {
        hint.setAttribute('data-i18n', 'auth.newHereEmail');
        hint.textContent = t('auth.newHereEmail');
      }
      return;
    }
    if (_isNative()) {
      if (btn) btn.addEventListener('click', _googleNativeSignIn);
    } else {
      _initWebGoogle();
    }
  }
  _setupGoogleAuth();

  async function logout() {
    const ok = await showConfirmModal(t('logout.title'), t('logout.body'), t('logout.confirm'));
    if (!ok) return;
    _clearToken();
    _intentionalNav = true;   // confirmed in-app; skip the browser "leave page?" prompt
    location.reload();
  }

  // ── Delete this account, from inside the app ──────────────────────────────
  // App Store Guideline 5.1.1(v) requires account deletion to be initiated in
  // the app itself; Google Play's data-deletion policy asks for the same. The
  // old footer link opened delete-account.html, which only offers a mailto -
  // that page stays as the signed-out/web-facing explainer, but a signed-in
  // user now gets the real thing. Two confirmations, because this destroys
  // every rendered video and any credits still on the account.
  async function deleteAccountFlow() {
    if (!authToken) {                       // signed out: nothing to delete yet
      openLegalModal('/delete-account.html', 'footer.deleteData');
      return;
    }
    if (!await showConfirmModal(t('deleteAcct.title'), t('deleteAcct.body'),
                                t('deleteAcct.confirm'))) return;
    if (!await showConfirmModal(t('deleteAcct.title'), t('deleteAcct.confirmAgain'),
                                t('deleteAcct.confirmFinal'))) return;
    try {
      const response = await apiFetch(`${API_BASE}/account/delete`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({confirm: true}),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (error) {
      console.warn('account deletion failed', error);
      _reportError('deleteAccount', (error && error.message) || 'failed');
      await showConfirmModal(t('deleteAcct.title'), t('deleteAcct.failed'), t('confirm.ok'));
      return;
    }
    // The session token is a stateless HMAC that outlives the record, so the
    // local copy has to go or the app would keep presenting a dead account.
    _clearToken();
    _intentionalNav = true;
    location.reload();
  }
  window.deleteAccountFlow = deleteAccountFlow;


  // Limits
  const MAX_BYTES    = 1024 * 1024 * 1024; // 1 GB - 500 MB sources must pass with headroom
  const WARN_BYTES   = 150 * 1024 * 1024; // 150 MB
  const MAX_SECS     = 20 * 60;            // 20 min
  const WARN_SECS    = 8  * 60;            // 8 min

  const uploadZone   = document.getElementById('uploadZone');
  const fileInput    = document.getElementById('fileInput');
  const fileInfo     = document.getElementById('fileInfo');
  const fileName     = document.getElementById('fileName');
  const fileDetail   = document.getElementById('fileDetail');
  const clearFile    = document.getElementById('clearFile');
  const runBtn       = document.getElementById('runBtn');
  const statusCard   = document.getElementById('statusCard');
  const checklistEl    = document.getElementById('statusChecklist');
  const statusDone     = document.getElementById('statusDone');
  const statusError    = document.getElementById('statusError');
  const errorMsg       = document.getElementById('errorMsg');

  // Which phase of the flow is running - gives errors context ("while uploading").
  let flowStage = null;
  function _setStage(s) { flowStage = s; _crumb('stage', s); }
  // Browsers word network-layer TypeErrors differently: Chrome "Failed to fetch",
  // Safari/iOS "Load failed", Firefox "NetworkError when attempting to fetch".
  function _isNetErr(e) {
    return /failed to fetch|load failed|networkerror|network error|network request failed/i.test(e && e.message || '');
  }

  // Checklist step elements
  const checkItems = {
    upload:  document.getElementById('checkUpload'),
    enhance: document.getElementById('checkEnhance'),
    cut:     document.getElementById('checkCut'),
    upscale: document.getElementById('checkUpscale'),
    save:    document.getElementById('checkSave'),
    broll:   document.getElementById('checkBroll'),
    hook:    document.getElementById('checkHook'),
    burn:    document.getElementById('checkBurn'),
    finalize: document.getElementById('checkFinalize'),
  };
  const checkTimeEls = {
    upload:  document.getElementById('checkUploadTime'),
    enhance: document.getElementById('checkEnhanceTime'),
    cut:     document.getElementById('checkCutTime'),
    upscale: document.getElementById('checkUpscaleTime'),
    save:    document.getElementById('checkSaveTime'),
    broll:   document.getElementById('checkBrollTime'),
    hook:    document.getElementById('checkHookTime'),
    burn:    document.getElementById('checkBurnTime'),
    finalize: document.getElementById('checkFinalizeTime'),
  };
  let stepTimers = {};         // step name → { start, intervalId }
  let stepEndSecs = {};        // step name → seconds taken (persists across hide/show)
  const downloadBtn  = document.getElementById('downloadBtn');
  const retryBtn     = document.getElementById('retryBtn');
  const aggrSlider   = document.getElementById('aggrSlider');
  const aggrDesc     = document.getElementById('aggrDesc');
  const AGGR_MAP = [
    { silence: 1.5, padding: 0.35, label: 'aggr.1' },
    { silence: 0.8, padding: 0.25, label: 'aggr.2' },
    { silence: 0.5, padding: 0.20, label: 'aggr.3' },
    { silence: 0.3, padding: 0.12, label: 'aggr.4' },
    { silence: 0.2, padding: 0.06, label: 'aggr.5' },
    { silence: 0.1, padding: 0.04, label: 'aggr.6' },
  ];
  const noticeBlock  = document.getElementById('noticeBlock');
  const noticeWarn   = document.getElementById('noticeWarn');
  const noticeNet    = document.getElementById('noticeNet');
  const noticeAudio  = document.getElementById('noticeAudio');

  let selectedFile = null;
  let videoDuration = null;
  // When the file was picked via the NATIVE picker (Capacitor app), this holds
  // { path, name, size, mimeType }. run() then uploads via the native
  // background uploader (survives the app being minimized) instead of the JS
  // chunked upload. null on web / when no native file is picked.
  let nativeUploadDesc = null;
  // Audio-only upload: gates the pipeline to captions + silence-cut, output is
  // a clean .m4a (no video processing, no burn). Set in handleFile.
  let isAudioInput = false;
  const _AUDIO_EXT_RE = /\.(ogg|mp3|wav|m4a|aac|flac|opus|oga|weba)$/i;
  function _isAudioFile(file) {
    return (file.type && file.type.startsWith('audio/')) || _AUDIO_EXT_RE.test(file.name || '');
  }
  // Stable in-memory/disk copy of the picked file's bytes, snapshotted at
  // selection time so a long upload can't be broken by the OS moving the file
  // (Google Photos cloud-sync). `_snapshotId` is the IndexedDB key when the
  // file was too big to hold in memory. See snapshotFile().
  let stableBlob = null;
  let _snapshotId = null;
  let resultBlob      = null;
  let resultDownloadUrl = null; // direct /download URL for the finished file (browser-native save)
  let resultName      = 'edited_video.mp4';
  let uploadTimer     = null;  // drives the upload step's elapsed clock
  let blocked         = false;
  let videoKey        = null;
  let captionsData    = [];
  let cutFilename     = '';
  let pollController  = null;
  let currentCallId   = null;
  const swResolvers   = new Map(); // callId -> resolve, for SW-won polls
  let currentPollInfo = null;       // { callId, pollUrl, deadline } while a poll is active
  let isUploading     = false;
  let intentionalStartOver = false;
  const ACTIVE_NATIVE_UPLOAD_STORAGE = 'pipelineActiveNativeUpload';
  let activeNativeUploadId = null;
  let activeNativeUploadKey = null;
  let activeNativeUploadReady = null;
  let activeNativeUploadPlugin = null;   // 'Uploader' | 'ParallelUploader' - cancel must target the right one
  try {
    const savedActiveUpload = JSON.parse(
      localStorage.getItem(ACTIVE_NATIVE_UPLOAD_STORAGE) || 'null');
    if (savedActiveUpload && savedActiveUpload.id) {
      activeNativeUploadId = savedActiveUpload.id;
      activeNativeUploadKey = savedActiveUpload.key || null;
      activeNativeUploadPlugin = savedActiveUpload.plugin || null;
    }
  } catch (_) {}
  let runStartTime    = null;
  let captionFont      = 'Heebo';
  let captionMarginPct = 0.08;
  let captionFontSize  = 48;
  let burnMode        = false;
  let hasBurnedOnce   = false;   // flips the burn button to "Re-burn & Download" after the first burn
  let currentUploadKey = null;
  let pendingAnalyses        = 0;
  let stockBrollAnalyzed     = false;
  let lastAnalyzedSignature  = '';
  let _hookGenSignature      = '';   // caption signature when hooks were last generated
  let selectedHookIdx        = -1;
  let hookGenAborted         = false;
  let hookThumbnail          = null;
  // The hook lines exactly as the preview canvas wrapped them (via measureText).
  // Sent to the backend so libass renders the SAME line breaks instead of
  // re-wrapping (which diverged - libass fits more Hebrew per line).
  let _hookLines             = [];
  let _playerSetupDone       = false;
  let _previewObjURL         = null; // object URL for the blob-backed preview player (revoked on reset)
  // Prefetch the cut-video blob the moment processing finishes (video_key known)
  // so the editor's preview is ready instantly instead of downloading on open.
  let _previewBlobPromise    = null;
  let _previewBlobKey        = null;   // remembered so a failed prefetch can be retried
  function _prefetchPreviewBlob(key) {
    if (!key || String(key).endsWith('.m4a')) { _previewBlobPromise = null; _previewBlobKey = null; return; }  // audio has no video preview
    _previewBlobKey = key;
    _previewBlobPromise = (async () => {
      // Parallel byte ranges (2-3x a single stream) - this download is the
      // longest pole between "processing finished" and "editor ready", both on
      // a fresh run and when resuming after reopening the app.
      let blob = null;
      try { blob = await _rangeFetchBlob(`${API_BASE}/download/${key}`); }
      catch (_) { /* range hiccup - single stream below */ }
      if (!blob) {
        const resp = await apiFetch(_withToken(`${API_BASE}/download/${key}`));
        if (!resp.ok) throw new Error('download ' + resp.status);
        blob = await resp.blob();
      }
      if (!blob || blob.size < 1024) throw new Error('preview blob truncated (' + (blob ? blob.size : 0) + ' bytes)');
      return URL.createObjectURL(blob);
    })().catch(e => { console.error('preview prefetch failed', e); return null; });
  }
  // Confirm the blob URL actually DECODES as video (metadata parses) - a
  // truncated/HTML-error blob would otherwise show a dead black player.
  function _probeVideoURL(url, timeoutMs = 15000) {
    return new Promise((resolve) => {
      const v = document.createElement('video');
      const done = (ok) => { v.removeAttribute('src'); try { v.load(); } catch (_) {} resolve(ok); };
      const tm = setTimeout(() => done(false), timeoutMs);
      v.onloadedmetadata = () => { clearTimeout(tm); done(true); };
      v.onerror = () => { clearTimeout(tm); done(false); };
      v.preload = 'metadata';
      v.src = url;
    });
  }
  // "Loading preview" checklist step - a SHORT head start only (2026-07-16).
  // Small clips land the blob within the grace window, so their player still
  // opens on the instant in-memory copy. Long videos (a 9-min cut is hundreds
  // of MB) no longer hold the checklist for the FULL download - the old
  // 90s+60s block plus the player's own wait kept the user staring at
  // spinners for minutes. The editor opens immediately; the player STREAMS
  // from the server (byte-range 206) within seconds and hot-swaps to the
  // downloaded blob when the background prefetch lands (_armPreviewBlobUpgrade).
  async function _ensurePreviewReady() {
    if (isAudioInput || !_previewBlobPromise) return;
    _stepActivate('finalize');
    const bounded = (p, ms) => Promise.race([p, new Promise(r => setTimeout(() => r(undefined), ms))]);
    const _w = (window.__PREVIEW_READY_WAIT_MS != null) ? window.__PREVIEW_READY_WAIT_MS : 2500;   // test seam
    try { await bounded(_previewBlobPromise, _w); } catch (_) {}
    // Deliberately NOT _stepDone here: the step ticks off only when the real
    // player reports it can play (_finishPreviewStepWhenPlayable) - a fixed
    // grace marked it done while the video still buffered (field report:
    // "preview wasn't actually ready when the step finished").
  }

  // "Loading preview" completes when the PLAYER can play through the first
  // frames. Bounded so a dead source can't spin the row forever (the player
  // has its own error recovery UI).
  function _finishPreviewStepWhenPlayable(vid) {
    if (!stepTimers.finalize) return;   // step never ran (audio / cut-only)
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(cap);
      _stepDone('finalize');
    };
    const cap = setTimeout(finish, 20000);
    if (vid.readyState >= 3) { finish(); return; }
    vid.addEventListener('canplay', finish, { once: true });
  }
  // Hot-swap the STREAMING player onto the downloaded blob once the background
  // prefetch lands - streaming starts in seconds but seeks pay a network
  // round-trip; the in-memory blob scrubs with zero latency. The swap waits
  // for a paused moment (most editing time) so playback is never interrupted,
  // restores the playhead, and only applies if this player setup is still the
  // live one for the same video.
  function _armPreviewBlobUpgrade(vid) {
    const promise = _previewBlobPromise;
    const keyAtArm = videoKey;
    if (!promise) return;
    promise.then(async (url) => {
      if (!url) return;
      if (!_playerSetupDone || videoKey !== keyAtArm || (vid.src || '').startsWith('blob:')) return;
      if (!(await _probeVideoURL(url))) { console.warn('preview blob failed validation - staying on stream'); return; }
      const doSwap = () => {
        if (!_playerSetupDone || videoKey !== keyAtArm || (vid.src || '').startsWith('blob:')) return;
        const at = vid.currentTime || 0;
        if (_previewObjURL) { try { URL.revokeObjectURL(_previewObjURL); } catch (_) {} }
        _previewObjURL = url;
        vid.src = url;
        vid.addEventListener('loadedmetadata', () => {
          try { vid.currentTime = at; } catch (_) {}
        }, { once: true });
      };
      if (vid.paused) doSwap();
      else vid.addEventListener('pause', doSwap, { once: true });
    }).catch(() => {});
  }
  let _playerDispW           = 0;   // detected display width (accounts for browser rotation)
  let videoOrientation       = 'portrait'; // 'portrait' | 'landscape' | 'square' - drives B-roll orientation
  let _uplinkMbps            = null;        // measured upload speed (best-effort probe at file selection)

  // Classify a video's orientation from its pixel dimensions. Near-square
  // clips are treated as 'square'; a comfortable dead-band avoids flip-flop on
  // slightly-off ratios.
  function _orientationFor(w, h) {
    if (!w || !h) return 'portrait';
    const r = w / h;
    if (r > 1.15) return 'landscape';
    if (r < 0.87) return 'portrait';
    return 'square';
  }

  // ── Background-job persistence (survives tab close/reload on mobile) ──
  const JOB_KEY = 'hebpipe_job';
  // A COMPLETED Modal result stays fetchable for ~a day, and the cut video
  // survives on the volume for 48h - so a user who gets the "ready to edit"
  // push and opens the app hours later must still land in their editor. (The
  // old 45-min TTL silently dropped the job and orphaned the processed video.)
  const JOB_TTL = 24 * 60 * 60 * 1000;

  function saveJob(type, callId, extra) {
    localStorage.setItem(JOB_KEY, JSON.stringify({ type, callId, ts: Date.now(), ...extra }));
  }
  function clearSavedJob() {
    localStorage.removeItem(JOB_KEY);
  }
  function loadSavedJob() {
    try {
      const raw = localStorage.getItem(JOB_KEY);
      if (!raw) return null;
      const job = JSON.parse(raw);
      if (Date.now() - job.ts > JOB_TTL) { clearSavedJob(); return null; }
      return job;
    } catch { clearSavedJob(); return null; }
  }

  // After a deferred upload completes in THIS session, the server has already
  // spawned processing (inside the final upload request) - fetch the call id.
  // Brief retries cover the tiny race between the last chunk's response and
  // the done-marker write.
  async function _awaitPendingSpawn(key, timeoutMs = 90_000) {
    const t0 = Date.now();
    // A 404 is only fatal when it PERSISTS: the server-side spawn pops the
    // registration BEFORE it spawns and writes the done-marker AFTER - a poll
    // landing inside that window (seconds; also hit when the 5-min R2 sweep
    // is the one claiming the job) sees neither and would fail a job that is
    // actually spawning fine (field report: mobile "spawn 404" while the job
    // ran and the credit was spent).
    let notFound = 0;
    while (Date.now() - t0 < timeoutMs) {
      let resp = null;
      try { resp = await apiFetch(`${API_BASE}/process_pending/?key=${encodeURIComponent(key)}`); } catch (_) {}
      if (resp && resp.ok) {
        notFound = 0;
        const d = await resp.json().catch(() => ({}));
        if (d.call_id) return d.call_id;
      } else if (resp && resp.status === 404) {
        if (++notFound >= 4) throw new Error(t('err.spawn', { status: 404 }));
      }
      await new Promise(r => setTimeout(r, 1500));
    }
    throw new Error(t('err.spawn', { status: 0 }));
  }

  // Resume flavour: the app reopened with a job registered but no call id yet.
  // Either the native background uploader is STILL uploading (wait - watch the
  // landed bytes grow), processing was spawned while we were away (call id
  // arrives on the first poll), or the upload died with the app (bytes frozen
  // → give up and tell the user to re-pick; their chunks resume).
  async function _awaitPendingResume(key, timeoutMs = 10 * 60_000) {
    const t0 = Date.now();
    let lastBytes = -1, stale = 0, notFound = 0;
    while (Date.now() - t0 < timeoutMs) {
      let resp = null;
      try { resp = await apiFetch(`${API_BASE}/process_pending/?key=${encodeURIComponent(key)}`); } catch (_) {}
      if (resp && resp.ok) {
        notFound = 0;
        const d = await resp.json().catch(() => ({}));
        if (d.call_id) return d.call_id;
      } else if (resp && resp.status === 404) {
        // Transient by design: the spawn pops the registration before the
        // done-marker exists (see _awaitPendingSpawn) - only persistent
        // absence means the job is really gone.
        if (++notFound >= 3) throw new Error('pending job gone');
      }
      try {
        const c = await apiFetch(`${API_BASE}/upload_check/?key=${encodeURIComponent(key)}`);
        if (c.ok) {
          const { bytes } = await c.json();
          stale = bytes > lastBytes ? 0 : stale + 1;
          lastBytes = Math.max(lastBytes, bytes);
        }
      } catch (_) {}
      if (stale >= 20) throw new Error('upload stalled');   // ~60s with no new bytes
      await new Promise(r => setTimeout(r, 3000));
    }
    throw new Error('pending resume timeout');
  }

  async function resumeSavedJob() {
    const job = loadSavedJob();
    document.getElementById('reconnectBanner').style.display = 'none';
    if (!job) return;
    document.getElementById('startOverBtn').style.display = '';

    if (job.type === 'pending') {
      // Registered before the upload; the call id may exist by now (server
      // spawned while the app was away) or the background upload may still be
      // running. Resolve it, then continue as a normal process resume.
      statusCard.classList.add('visible');
      expandCard('statusBody');
      checklistEl.style.display = 'block';
      statusDone.classList.remove('visible');
      statusError.classList.remove('visible');
      _resetChecklist();
      _stepActivate('upload');
      try {
        job.callId = await _awaitPendingResume(job.key);
        saveJob('process', job.callId, { filename: job.filename, key: job.key });
        job.type = 'process';
      } catch (e) {
        console.warn('pending resume failed:', e && e.message);
        clearSavedJob();
        showError(t('resume.uploadIncomplete'));
        return;
      }
    }

    if (job.type === 'process') {
      // Reconnect: real progress from the poll tells us which step is running
      statusCard.classList.add('visible');
      expandCard('statusBody');
      checklistEl.style.display = 'block';
      statusDone.classList.remove('visible');
      statusError.classList.remove('visible');
      _resetChecklist();
      checkItems.upload.className = 'check-item done';
      checkTimeEls.upload.textContent = t('err.cached');
      _procStartMs = Date.now();
      if (job.key) currentUploadKey = job.key;
      try {
        _setStage('processing');
        const keyQs = job.key ? `?key=${encodeURIComponent(job.key)}` : '';
        const result = await pollForJSON(`${API_BASE}/process_poll/${job.callId}/${keyQs}`, 900_000, job.callId, _applyProgress);
        _stepsDoneProcessing(result.step_times);
        clearSavedJob();
        captionsData = result.captions || [];
        videoKey     = result.video_key;
      _prefetchPreviewBlob(videoKey);
        // Derive audio-mode from the output key so a reload-resumed job (where
        // the picked File is gone) still renders the right editor.
        isAudioInput = (videoKey || '').endsWith('.m4a');
        applyAudioMode(isAudioInput);
        cutFilename  = (job.filename || 'video').replace(/\.[^/.]+$/, '') + (isAudioInput ? '_clean.m4a' : '_cut.mp4');
        if (isAudioInput || captionsData.length > 0) {
          showCaptionEditor();
        } else {
          await _finalizeAndDownload(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(cutFilename)}`, cutFilename);
        }
      } catch (err) {
        if (!_isNetErr(err)) clearSavedJob();
        if (err.name !== 'AbortError')
          showError(_isNetErr(err) ? err.message : t('err.reconnect'));
      }

    } else if (job.type === 'burn') {
      statusCard.classList.add('visible');
      expandCard('statusBody');
      checklistEl.style.display = 'block';
      statusDone.classList.remove('visible');
      statusError.classList.remove('visible');
      _resetChecklist();
      checkItems.upload.className = 'check-item done';
      checkItems.enhance.className = 'check-item done';
      checkItems.cut.className = 'check-item done';
      _stepActivate('burn');
      runBtn.style.display = 'block';
      runBtn.disabled = true;
      lockPipelineActions({ activeBtn: 'runBtn' });
      try {
        _setStage('burn');
        const burnResult = await pollForJSON(`${API_BASE}/burn_poll/${job.callId}/`, 600_000, job.callId);
        _stepDone('burn');
        clearSavedJob();
        // Video is ready - reveal the schedule card immediately; the success
        // banner follows once the device download settles.
        checklistEl.style.display = 'none';
        window._schedCtx = {
          outputKey: burnResult.output_key,
          filename:  job.outputFilename,
          videoKey:  (typeof videoKey !== 'undefined' ? videoKey : null),
          hasTranscript: _hasTranscript(),
        };
        // Video is ready — reveal the Schedule button (opens the modal). Keep
        // the rest greyed until the device download settles.
        unlockPipelineActions();
        lockPipelineActions({ activeBtn: 'openScheduleBtn' });
        const _osb = document.getElementById('openScheduleBtn');
        if (_osb) _osb.style.display = 'block';
        runBtn.disabled = true;
        // Hand the finished video to the browser to stream to disk (native
        // progress, no RAM buffering); non-blocking and independent of scheduling.
        resultDownloadUrl = `${API_BASE}/download/${burnResult.output_key}/?filename=${encodeURIComponent(job.outputFilename)}`;
        resultName = job.outputFilename;
        triggerDownload();
        document.getElementById('burnSuccessBanner').style.display = 'flex';
        _maybeShowShare();
        celebrateExport();
      } catch (err) {
        if (!_isNetErr(err)) clearSavedJob();
        if (err.name !== 'AbortError')
          showError(_isNetErr(err) ? err.message : t('err.reconnect'));
      } finally {
        unlockPipelineActions();
        runBtn.disabled = false;
      }
    }
  }

  function bumpPending(delta) {
    pendingAnalyses = Math.max(0, pendingAnalyses + delta);
    validateCaptionTimes();
  }

  function getCaptionsFromEditor() {
    return Array.from(document.querySelectorAll('#captionsList .caption-row')).map(row => {
      const cap = {
        start: parseFloat(row.querySelector('.caption-start').value) || 0,
        end:   parseFloat(row.querySelector('.caption-end').value)   || 0,
        text:  row.querySelector('.caption-input').value,
      };
      if (row.dataset.words) {
        try { cap.words = JSON.parse(row.dataset.words); } catch (_) {}
      }
      return cap;
    });
  }

  function getEditedCaptions() { return getCaptionsFromEditor(); }

  // ── Caption editor undo ──────────────────────────────────────────────────
  // Snapshot the whole caption list before each change so a mis-tap (especially
  // a delete) can be reverted. Snapshots are plain {start,end,text}[] arrays.
  let captionUndoStack = [];
  const CAPTION_UNDO_MAX = 60;
  function _pushCaptionUndo(snapshot) {
    captionUndoStack.push(snapshot || getCaptionsFromEditor());
    if (captionUndoStack.length > CAPTION_UNDO_MAX) captionUndoStack.shift();
    _updateUndoBtn();
  }
  function _resetCaptionUndo() { captionUndoStack = []; _updateUndoBtn(); }
  function _updateUndoBtn() {
    const b = document.getElementById('capUndoBtn');
    if (b) b.disabled = captionUndoStack.length === 0;
  }
  function undoCaptions() {
    if (!captionUndoStack.length) return;
    const snap = captionUndoStack.pop();
    const list = document.getElementById('captionsList');
    list.innerHTML = '';
    snap.forEach(cap => list.appendChild(_createCaptionRow(cap)));
    _updateDeleteButtons();
    captionsData = getCaptionsFromEditor();
    updatePreviewCaption();
    validateCaptionTimes();
    _updateUndoBtn();
  }

  function getCaptionsSignature() {
    return getCaptionsFromEditor().map(c => c.text).join('|');
  }

  // An SRT is generated in the page, so there is no server URL for Android's
  // DownloadManager to fetch and a blob: `<a download>` is a silent no-op in the
  // WebView. Write the text into the app cache and hand the file to the system
  // share sheet - the only reliable way to keep a locally generated file on the
  // native app. Returns false when the plugins are missing (old shell), so the
  // caller can fall back to the browser path.
  async function _nativeSaveSrt(name, text) {
    const Filesystem = _capPlugin('Filesystem');
    const Share = _capPlugin('Share');
    if (!Filesystem || !Share) return false;
    const btn = document.getElementById('downloadSrtBtn');
    const label = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.textContent = t('srt.preparing'); }
    try {
      const safe = _safeDownloadName(name);
      await Filesystem.writeFile({ path: safe, directory: 'CACHE', data: text, encoding: 'utf8' });
      const { uri } = await Filesystem.getUri({ directory: 'CACHE', path: safe });
      await Share.share({ title: safe, files: [uri], dialogTitle: t('srt.dialog') });
    } catch (e) {
      // A dismissed share sheet is not a failure, and neither is the app being
      // backgrounded mid-flow.
      const msg = (e && e.message) || '';
      if (!/cancel/i.test(msg) && !document.hidden) {
        console.error('srt save failed', e);
        _reportError('srt', msg || 'srt save failed');
        celebrateToast(t('srt.failed'), { kind: 'error', duration: 7000 });
      }
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = label; }
    }
    return true;
  }

  async function downloadSRT() {
    const captions = getCaptionsFromEditor();
    if (!captions.length) return;
    function toSrtTime(s) {
      const h   = Math.floor(s / 3600);
      const m   = Math.floor((s % 3600) / 60);
      const sec = Math.floor(s % 60);
      const ms  = Math.round((s % 1) * 1000);
      return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')},${String(ms).padStart(3,'0')}`;
    }
    const srt = captions.map((c, i) =>
      `${i + 1}\n${toSrtTime(c.start)} --> ${toSrtTime(c.end)}\n${c.text}`
    ).join('\n\n') + '\n';
    const base = selectedFile ? selectedFile.name.replace(/\.[^.]+$/, '') : 'captions';
    const name = base + '.srt';
    if (_isNative() && await _nativeSaveSrt(name, srt)) return;
    const blob = new Blob([srt], { type: 'text/plain;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url; a.download = name; a.style.display = 'none';
    // Mobile browsers ignore a click on a detached anchor, and revoking the
    // object URL in the same tick cancels the download before it starts.
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 10_000);
  }

  // Download the processed clean-audio (.m4a) file. Fetches through apiFetch so
  // the bearer token is applied, then saves with a friendly filename.
  function downloadAudio() {
    if (!videoKey) return;
    // Stream straight to disk (browser-native) rather than buffering into a Blob.
    const name = cutFilename || 'audio.m4a';
    _browserDownload(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(name)}`, name);
  }

  // ── Global action lock ──
  // One rule: while any long operation is in flight (processing, burn,
  // download, hook/caption/B-roll generation, scheduling), every pipeline
  // section except the operation's own card is greyed out, and every
  // cross-section action button is disabled. The active flow manages its
  // own button (spinner text etc.), so activeBtn is exempted.
  const LOCK_BTN_IDS  = ['runBtn', 'reprocessBtn', 'generateHookBtn',
                         'findBrollBtn', 'openScheduleBtn',
                         'burnDownloadBtn'];   // startOverBtn stays clickable always
  const LOCK_CARD_IDS = ['optionsCard', 'captionEditorCard'];
  let _actionLockDepth = 0;
  const _actionLockSaved = new Map();

  function lockPipelineActions({ activeBtn = null, activeCard = null, exempt = [] } = {}) {
    if (++_actionLockDepth > 1) return;
    // A long operation is starting (process, burn, hooks, B-roll, schedule) -
    // pause every preview video (caption player, B-roll clip previews) so
    // nothing keeps playing behind the spinner/greyed-out card.
    document.querySelectorAll('video').forEach(v => { try { v.pause(); } catch (_) {} });
    // Switching language mid-flow re-renders state-driven labels and can corrupt
    // an in-flight process/burn/download — lock the toggle for the duration.
    const _lt = document.getElementById('langToggle');
    if (_lt) _lt.disabled = true;
    LOCK_BTN_IDS.forEach(id => {
      if (id === activeBtn) return;               // the active flow manages its own button
      if (exempt.includes(id)) return;            // flows that may run in parallel
      const el = document.getElementById(id);
      if (!el) return;
      _actionLockSaved.set(id, el.disabled);
      el.disabled = true;
    });
    LOCK_CARD_IDS.forEach(id => {
      if (id === activeCard) return;              // keep the active card interactive
      document.getElementById(id)?.classList.add('action-locked');
    });
  }

  function unlockPipelineActions() {
    if (_actionLockDepth === 0 || --_actionLockDepth > 0) return;
    const _lt = document.getElementById('langToggle');
    if (_lt) _lt.disabled = false;
    LOCK_BTN_IDS.forEach(id => {
      const el = document.getElementById(id);
      if (el && _actionLockSaved.has(id)) el.disabled = _actionLockSaved.get(id);
    });
    _actionLockSaved.clear();
    LOCK_CARD_IDS.forEach(id => document.getElementById(id)?.classList.remove('action-locked'));
  }

  function showConfirmModal(title, body, okText) {
    return new Promise(resolve => {
      document.getElementById('confirmTitle').textContent = title;
      document.getElementById('confirmBody').textContent  = body;
      document.getElementById('confirmOk').textContent    = okText || t('confirm.ok');
      const overlay = document.getElementById('confirmOverlay');
      overlay.style.display = 'flex';
      function cleanup(result) {
        overlay.style.display = 'none';
        document.getElementById('confirmOk').onclick     = null;
        document.getElementById('confirmCancel').onclick = null;
        resolve(result);
      }
      document.getElementById('confirmOk').onclick     = () => cleanup(true);
      document.getElementById('confirmCancel').onclick = () => cleanup(false);
    });
  }

  function setSetupLocked(locked) {
    ['uploadCard', 'optionsCard'].forEach(id =>
      document.getElementById(id).classList.toggle('setup-locked', locked));
  }

  // ── File selection ──
  fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('drag-over'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault(); uploadZone.classList.remove('drag-over');
    handleFile(e.dataTransfer.files[0]);
  });

  // ── Native (Capacitor) file pick + background upload ──────────────────────
  // The web upload (File -> chunkedUpload) freezes when the app is
  // backgrounded because the OS throttles WebView JS. On the native app we pick
  // the file with the native FilePicker (to get a real filesystem path) and
  // hand it to @capgo's native uploader, which runs in a foreground service and
  // keeps uploading while the app is minimized/locked. Everything after upload
  // (/process, editor) is identical. All of this is a no-op on the web.
  function _isNative() {
    return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
  }
  function _capPlugin(name) {
    return window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins[name];
  }
  // 'ios' | 'android' | 'web'. Only ever 'ios' inside the App Store binary -
  // Safari on an iPhone still reports 'web', which is correct: the App Store
  // rules below apply to the native app, not to the website.
  function _platform() {
    const cap = window.Capacitor;
    return (cap && cap.getPlatform && cap.getPlatform()) || 'web';
  }
  function _isIOS() {
    return _isNative() && _platform() === 'ios';
  }
  // App Store Guideline 3.1.1: an iOS app may not link to, or even point at,
  // any purchase mechanism outside Apple's IAP - and a Google Play link is the
  // most flagrant version of that. `_billingAvailable()` is normally true in
  // the iOS shell (the StoreKit plugin is compiled in), so these Play surfaces
  // are already unreachable there; this is the belt-and-braces guard that keeps
  // them unreachable even if the plugin ever fails to register.
  function _playStoreCtaAllowed() {
    return !_isIOS();
  }
  // Store-aware i18n: on iOS prefer a "<key>.ios" entry (App Store wording) and
  // fall back to the Play wording. t() returns the key itself when missing,
  // which is how the fallback is detected.
  function _tStore(key, vars) {
    if (_isIOS()) {
      const iosKey = key + '.ios';
      const text = t(iosKey, vars);
      if (text !== iosKey) return text;
    }
    return t(key, vars);
  }
  // Static markup carrying a store name has to be re-pointed after every
  // applyI18n pass (boot and language switch), since data-i18n always writes
  // the base Play string back in.
  function _syncStoreCopy() {
    if (!_isIOS()) return;
    document.querySelectorAll('[data-i18n="billing.secure"]').forEach(el => {
      el.textContent = _tStore('billing.secure');
    });
  }

  // ── Google Play Billing (native Android only) ─────────────────────────────
  // Must match PLAY_CREDIT_PRODUCTS on the server. Retired IDs stay listed for
  // as long as the product is still live in Play, so a purchase of one still
  // grants; the modal only offers what getProducts() actually returns.
  const BILLING_CREDITS = {
    pipeline_credits_10: 10,
    pipeline_credits_30: 30,
    pipeline_credits_100: 100,
    pipeline_credits_5: 5,
    pipeline_credits_20: 20,
    pipeline_credits_50: 50,
  };
  let _billingRestoreStarted = false;
  let _billingListenerStarted = false;
  let _billingBusy = false;

  function _billingAvailable() {
    return _isNative() && !!_capPlugin('NativeBilling');
  }

  const PLAY_STORE_URL =
    'https://play.google.com/store/apps/details?id=com.heb.pipeline';

  function _playStoreQuotaCopy() {
    // An iOS shell without a working StoreKit plugin must say something true
    // that names no other store and offers no outside purchase route.
    if (_isIOS()) return t('billing.iosUnavailable');
    return _isNative() ? t('billing.updateRequired') : t('billing.webOnly');
  }

  function _configurePlayStoreCta(element) {
    if (!element) return;
    element.href = PLAY_STORE_URL;
    const label = element.querySelector('span');
    if (label) {
      label.textContent = _isNative()
        ? t('billing.updateApp')
        : t('billing.getAndroidApp');
    }
  }

  function openQuotaPurchase() {
    if (_billingAvailable()) {
      openBillingModal();
      return;
    }
    // Web (and pre-billing native shells) can only point at the Play listing.
    // Regular users see that only once they are out of credits; an admin has no
    // exhausted state, so their pill always routes there.
    if (!_playStoreCtaAllowed()) return;
    if (!_quotaExhausted() && !_isAdminUser()) return;
    const opened = window.open(PLAY_STORE_URL, '_blank', 'noopener');
    if (!opened) window.location.href = PLAY_STORE_URL;
  }

  function _initBillingListener() {
    if (_billingListenerStarted || !_billingAvailable()) return;
    const billing = _capPlugin('NativeBilling');
    if (!billing.addListener) return;  // test/older bridge compatibility
    _billingListenerStarted = true;
    billing.addListener('purchaseUpdated', async purchase => {
      // The active buyCredits promise handles its own immediate callback.
      // This listener is for a pending payment completing later.
      if (_billingBusy || !purchase || purchase.state !== 'purchased') return;
      const productId = (purchase.products || []).find(id => BILLING_CREDITS[id]);
      if (!productId) return;
      try {
        await _verifyPurchase(purchase, productId);
        await refreshQuota();
      } catch (error) {
        console.warn('delayed billing verification failed', error);
      }
    });
  }

  function _setBillingStatus(message, isError = false) {
    const el = document.getElementById('billingStatus');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('error', !!isError);
    el.style.display = message ? 'block' : 'none';
  }

  function _setBillingBusy(busy) {
    _billingBusy = busy;
    document.querySelectorAll('.billing-product').forEach(btn => { btn.disabled = busy; });
  }

  async function openBillingModal() {
    if (!_billingAvailable()) return;
    const overlay = document.getElementById('billingOverlay');
    overlay.style.display = 'flex';
    _syncStoreCopy();
    _setBillingStatus(_tStore('billing.loading'));
    try {
      const result = await _capPlugin('NativeBilling').getProducts();
      const products = (result && result.products || [])
        .filter(p => BILLING_CREDITS[p.productId])
        .sort((a, b) => BILLING_CREDITS[a.productId] - BILLING_CREDITS[b.productId]);
      const container = document.getElementById('billingProducts');
      container.replaceChildren();
      for (const product of products) {
        const credits = BILLING_CREDITS[product.productId];
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'billing-product';
        button.dataset.productId = product.productId;
        button.onclick = () => buyCredits(product.productId);
        const copy = document.createElement('span');
        copy.className = 'billing-product-copy';
        const name = document.createElement('span');
        name.className = 'billing-product-name';
        name.textContent = t('billing.pack', {count: credits});
        const detail = document.createElement('span');
        detail.className = 'billing-product-detail';
        detail.textContent = t('billing.packDetail', {count: credits});
        const price = document.createElement('span');
        price.className = 'billing-product-price';
        price.textContent = product.formattedPrice || '';
        copy.append(name, detail);
        button.append(copy, price);
        container.appendChild(button);
      }
      _setBillingStatus(products.length ? '' : _tStore('billing.unavailable'), !products.length);
    } catch (error) {
      console.warn('billing product load failed', error);
      _setBillingStatus(_tStore('billing.unavailable'), true);
    }
  }

  function closeBillingModal() {
    if (_billingBusy) return;
    const overlay = document.getElementById('billingOverlay');
    if (overlay) overlay.style.display = 'none';
  }

  // One verification entry point for both stores. The store-specific parts are
  // the endpoint, the identifier the client is allowed to send (Play: the
  // purchase token; Apple: the transaction id, which the server then looks up
  // against Apple), and what has to happen AFTER a successful grant: Play
  // consumes server-side, StoreKit must be finished on the device.
  async function _verifyPurchase(purchase, productId) {
    const apple = _isIOS();
    const response = await apiFetch(
      `${API_BASE}/billing/${apple ? 'apple' : 'play'}/verify`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(apple
          ? { transaction_id: purchase.transactionId }
          : { purchase_token: purchase.purchaseToken, product_id: productId }),
      });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(data.code || data.error || `HTTP ${response.status}`);
      err.code = data.code;
      throw err;
    }
    if (apple) {
      // Only now is it safe to finish: before the grant, an unfinished
      // transaction is the user's sole proof of purchase, and StoreKit re-offers
      // it on the next launch so the idempotent verify route can retry.
      const billing = _capPlugin('NativeBilling');
      if (billing && billing.finishTransaction) {
        try {
          await billing.finishTransaction({ transactionId: purchase.transactionId });
        } catch (error) {
          console.warn('finishTransaction failed', error);
        }
      }
    }
    return data;
  }

  // The account binding the store attaches to the purchase, so the server can
  // prove the transaction belongs to the signed-in user. StoreKit demands a
  // UUID, Play a free-form hash, so /auth/me returns one of each.
  function _billingAccountId() {
    if (!quotaInfo) return '';
    return (_isIOS() ? quotaInfo.apple_account_token : quotaInfo.billing_account_id) || '';
  }

  async function buyCredits(productId) {
    if (_billingBusy || !_billingAvailable() || !_billingAccountId()) return;
    _setBillingBusy(true);
    _setBillingStatus(_tStore('billing.openingPlay'));
    try {
      const purchase = await _capPlugin('NativeBilling').purchase({
        productId,
        accountId: _billingAccountId(),
      });
      if (!purchase || purchase.state === 'pending') {
        _setBillingStatus(_tStore('billing.pending'));
        return;
      }
      _setBillingStatus(t('billing.verifying'));
      const result = await _verifyPurchase(purchase, productId);
      await refreshQuota();
      _setBillingStatus(t('billing.success', {count: result.credits_granted}));
      setTimeout(() => {
        if (!_billingBusy) closeBillingModal();
      }, 1400);
    } catch (error) {
      if (error && error.code === 'purchase_cancelled') {
        _setBillingStatus('');
      } else if (error && error.code === 'purchase_pending') {
        _setBillingStatus(_tStore('billing.pending'));
      } else {
        console.warn('billing purchase failed', error);
        _setBillingStatus(_tStore('billing.failed'), true);
      }
    } finally {
      _setBillingBusy(false);
    }
  }

  async function _restorePlayPurchases() {
    if (_billingRestoreStarted || !_billingAvailable() || !_billingAccountId()) return;
    _billingRestoreStarted = true;
    try {
      const result = await _capPlugin('NativeBilling').restorePurchases();
      let restored = false;
      for (const purchase of result && result.purchases || []) {
        if (!purchase || purchase.state !== 'purchased') continue;
        const productId = (purchase.products || []).find(id => BILLING_CREDITS[id]);
        if (!productId) continue;
        await _verifyPurchase(purchase, productId);
        restored = true;
      }
      if (restored) await refreshQuota();
    } catch (error) {
      // Restoration is best-effort. A network/Play outage is retried on the
      // next full app session and must not interrupt the editing flow.
      console.warn('billing restore failed', error);
    }
  }

  async function pickNativeFile() {
    const FilePicker = _capPlugin('FilePicker');
    if (!FilePicker) { showBlockNotice(t('file.badTypeTitle'), 'File picker unavailable'); return; }
    let desc;
    try {
      const res = await FilePicker.pickFiles({ types: ['video/*', 'audio/*'], readData: false });
      const f = res && res.files && res.files[0];
      if (!f) return;                       // user cancelled
      desc = { path: f.path, name: f.name || 'video.mp4', size: f.size || 0, mimeType: f.mimeType || '' };
    } catch (e) {
      if (/cancel/i.test((e && e.message) || '')) return;
      showBlockNotice(t('file.badTypeTitle'), (e && e.message) || 'Could not open the file');
      return;
    }
    if (!desc.path) { showBlockNotice(t('file.badTypeTitle'), 'No file path from picker'); return; }

    const _isAudio = _isAudioFile(desc);
    const _isVideo = (desc.mimeType || '').startsWith('video/') || /\.(mp4|mov|mkv|avi|webm)$/i.test(desc.name);
    if (!_isVideo && !_isAudio) { showBlockNotice(t('file.badTypeTitle'), t('file.badType')); return; }

    // Populate the same selection UI the web path uses (a synthetic file object
    // carries name/size/type for the checks, params, and History label).
    selectedFile = { name: desc.name, size: desc.size, type: desc.mimeType };
    nativeUploadDesc = desc;
    stableBlob = null;                      // native file on disk is already stable
    isAudioInput = _isAudio && !_isVideo;
    applyAudioMode(isAudioInput);
    fileName.textContent = desc.name;
    fileInfo.classList.add('visible');
    document.getElementById('startOverBtn').style.display = '';
    clearNotices();
    resetStatus();
    if (isAudioInput && noticeAudio) noticeAudio.classList.add('visible');

    if (desc.size > MAX_BYTES) {
      fileDetail.textContent = t('file.tooLargeMeta', { size: formatSize(desc.size) });
      showBlockNotice(t('file.tooLargeTitle'), t('file.tooLarge', { size: formatSize(desc.size) }));
      blocked = true; runBtn.disabled = true; return;
    }
    fileDetail.textContent = formatSize(desc.size);
    if (desc.size > WARN_BYTES) showWarnNotice(t('file.largeWarnTitle'), t('file.largeWarn', { size: formatSize(desc.size) }));
    blocked = false; runBtn.disabled = false;
    updateTimeEstimate();
    apiFetch(API_BASE + '/warmup/').catch(() => {});
  }

  function _rememberActiveNativeUpload(id, key, pluginName) {
    activeNativeUploadId = id;
    activeNativeUploadKey = key;
    activeNativeUploadPlugin = pluginName || 'Uploader';
    try {
      localStorage.setItem(
        ACTIVE_NATIVE_UPLOAD_STORAGE,
        JSON.stringify({ id, key, plugin: activeNativeUploadPlugin }));
    } catch (_) {}
  }

  function _forgetActiveNativeUpload(id) {
    if (id && activeNativeUploadId && id !== activeNativeUploadId) return;
    activeNativeUploadId = null;
    activeNativeUploadKey = null;
    activeNativeUploadReady = null;
    activeNativeUploadPlugin = null;
    try { localStorage.removeItem(ACTIVE_NATIVE_UPLOAD_STORAGE); } catch (_) {}
  }

  async function _cancelActiveNativeUpload() {
    const Uploader = _isNative() && _capPlugin(activeNativeUploadPlugin || 'Uploader');
    let id = activeNativeUploadId;
    let key = activeNativeUploadKey;
    if (!id && activeNativeUploadReady) {
      const started = await Promise.race([
        activeNativeUploadReady,
        new Promise(resolve => setTimeout(() => resolve(null), 4000)),
      ]);
      id = started && started.id;
      key = started && started.key;
    }
    if (id) {
      if (!Uploader || !Uploader.removeUpload) {
        throw new Error('Uploader cancellation is unavailable');
      }
      await Uploader.removeUpload({ id });
    }
    if (key) {
      // The foreground-service cancellation is authoritative for the user:
      // once it succeeds, Start Over must not be held hostage by a transient
      // cleanup failure (or a frontend/backend rolling-deploy mismatch).
      // Fire-and-forget with keepalive so cleanup continues through the reload.
      apiFetch(
        `${API_BASE}/cancel_upload/?key=${encodeURIComponent(key)}`,
        { method: 'POST', keepalive: true })
        .then(response => {
          if (!response.ok) console.warn(`Upload cleanup failed (${response.status})`);
        })
        .catch(error => console.warn('Upload cleanup request failed', error));
      // A native R2 upload may have finished its PUT already - delete the
      // staged object too (no-op when nothing is there).
      try {
        fetch(`${API_BASE}/upload_r2/abort/`, {
          method: 'POST', keepalive: true,
          headers: { 'Content-Type': 'application/json',
                     ...(authToken ? { 'Authorization': 'Bearer ' + authToken } : {}) },
          body: JSON.stringify({ key }),
        }).catch(() => {});
      } catch (_) {}
    }
    _forgetActiveNativeUpload(id);
  }

  // Ask for a single-object presigned R2 PUT for the native uploader. The
  // foreground service can't slice multipart parts, but ONE HTTP/1.1 PUT to
  // R2 measured ~4-5x the Modal-ingress stream path (no h2 upload window on
  // the R2 endpoint - see the r2Upload note). Resolves to the URL or null
  // (old backend / creds missing / any failure → caller streams to Modal).
  async function _r2NativeInit(key, size) {
    try {
      const resp = await apiFetch(`${API_BASE}/upload_r2/init/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, size: size || 1, single: true }),
      });
      if (!resp.ok) return null;
      const d = await resp.json().catch(() => ({}));
      return d && d.url ? d.url : null;
    } catch (_) { return null; }
  }

  // Parallel-multipart variant for the custom ParallelUploader service (new
  // Android binaries): part URLs plus presigned complete/abort so the service
  // finishes the multipart directly against R2 - the assembled object then
  // exists even if the app dies, and the server sweep spawns the job.
  async function _r2NativeInitParallel(key, size) {
    try {
      const resp = await apiFetch(`${API_BASE}/upload_r2/init/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, size: size || 1, parallel: true }),
      });
      if (!resp.ok) return null;
      const d = await resp.json().catch(() => ({}));
      return (d && d.upload_id && Array.isArray(d.urls) && d.urls.length
              && d.complete_url) ? d : null;
    } catch (_) { return null; }
  }

  // Upload a native file path via the background uploader. Resolves with the
  // upload key (same key the /process call expects). Rejects on failure.
  // Fast path: the foreground service PUTs the file to a presigned R2 URL,
  // then /upload_r2/complete lands it on the volume. Any R2-path failure
  // falls back to the classic /upload_stream target - except a user cancel,
  // and except after the PUT succeeded (the bytes are in R2; the server
  // sweep spawns the job even if the app dies, so re-uploading is wrong).
  async function nativeUpload(desc, onProgress, presetKey) {
    const key = presetKey || crypto.randomUUID().replace(/-/g, '');
    // Fastest: N parallel byte-range PUTs via the custom foreground service
    // (binaries that ship ParallelUploader). Then: single presigned PUT via
    // the stock uploader. Last: the classic /upload_stream to Modal.
    const PU = _capPlugin('ParallelUploader');
    if (PU) {
      const cfg = await _r2NativeInitParallel(key, desc.size);
      if (cfg) {
        try {
          return await _nativeUploadRun(desc, onProgress, key, {
            plugin: PU, pluginName: 'ParallelUploader',
            startOpts: {
              filePath: desc.path,
              urls: cfg.urls,
              partSize: cfg.part_size,
              size: desc.size || 0,
              completeUrl: cfg.complete_url,
              abortUrl: cfg.abort_url || '',
              concurrency: 5,
              notificationTitle: t('upload.title') || 'Uploading video',
            },
          });
        } catch (e) {
          if ((e && e.name === 'AbortError') || intentionalStartOver || (e && e.r2NoFallback)) throw e;
          console.warn('parallel R2 upload failed - falling back:', e && e.message);
          onProgress(0);
        }
      }
    }
    const r2Url = await _r2NativeInit(key, desc.size);
    if (r2Url) {
      try {
        return await _nativeUploadRun(desc, onProgress, key, { r2Url });
      } catch (e) {
        if ((e && e.name === 'AbortError') || intentionalStartOver || (e && e.r2NoFallback)) throw e;
        console.warn('native R2 upload failed - falling back to /upload_stream:', e && e.message);
        onProgress(0);
      }
    }
    return _nativeUploadRun(desc, onProgress, key, null);
  }

  function _nativeUploadRun(desc, onProgress, key, mode) {
    // mode: null = classic /upload_stream via the stock uploader;
    // {r2Url} = single presigned PUT via the stock uploader;
    // {plugin, pluginName, startOpts} = the ParallelUploader service.
    const r2Url  = mode && mode.r2Url;
    const isR2   = !!(mode && (mode.r2Url || mode.startOpts));
    const plugName = (mode && mode.pluginName) || 'Uploader';
    return new Promise((resolve, reject) => {
      const Uploader = (mode && mode.plugin) || _capPlugin('Uploader');
      if (!Uploader) { reject(new Error('Uploader plugin unavailable')); return; }
      const expected = desc.size || 0;
      let handle = null, settled = false, reconciling = false, uploadId = null;
      const earlyEvents = [];
      let resolveUploadReady;
      const uploadReady = new Promise(resolve => { resolveUploadReady = resolve; });
      activeNativeUploadReady = uploadReady;
      const _buzz = () => {
        // Native Haptics is reliable (no user-gesture requirement like
        // navigator.vibrate, which silently no-ops when the upload finishes
        // without a recent tap). Falls back to the web Vibration API.
        try {
          const H = _capPlugin('Haptics');
          if (H && H.vibrate) { H.vibrate({ duration: 90 }); return; }
        } catch (_) {}
        try { navigator.vibrate && navigator.vibrate(90); } catch (_) {}
      };
      const cleanup = () => {
        try { handle && handle.remove(); } catch (_) {}
        document.removeEventListener('visibilitychange', _onVis);
      };
      const _finish = () => {
        settled = true; cleanup(); _forgetActiveNativeUpload(uploadId);
        _buzz(); onProgress(1); resolve(key);
      };
      // While the app is backgrounded the WebView is frozen, so the uploader's
      // 'completed' event can be MISSED and the promise would hang forever. On
      // return to foreground, confirm with the server whether the upload landed.
      // AUTHORITATIVE check first: a call_id on /process_pending means the last
      // byte arrived and processing was spawned - and unlike the chunk-bytes
      // check, the done-marker OUTLIVES the chunk files (processing deletes
      // them at reassembly, so a user who reopens after the "ready" push would
      // see upload_check report 0 bytes forever - the stuck-at-5% bug). The
      // byte check remains as the in-flight fallback (also refreshes the bar),
      // and must not gate the pending check on `expected` (a picker that
      // reports no size would otherwise disable reconciliation entirely).
      // R2 mode: after the service's PUT lands, the server must copy the
      // object to the volume + spawn. Idempotent, so retried freely.
      async function _r2Complete(attempts) {
        for (let a = 0; a < attempts && !settled; a++) {
          if (a) await new Promise(r => setTimeout(r, 2000 * a));
          try {
            const resp = await apiFetch(`${API_BASE}/upload_r2/complete/`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ key, single: true }),
            });
            if (resp.ok) return true;
          } catch (e) { if (!authToken) return false; }
        }
        return false;
      }
      // The PUT succeeded but /complete keeps failing: the bytes ARE in R2
      // and the 5-minute server sweep will land + spawn them - wait for that
      // instead of failing an upload that actually finished.
      async function _r2FinishOrWait() {
        if (await _r2Complete(4)) { _finish(); return; }
        for (let i = 0; i < 80 && !settled; i++) {   // ~7 min > one sweep period
          try {
            const pr = await apiFetch(`${API_BASE}/process_pending/?key=${key}`);
            if (pr.ok) {
              const d = await pr.json().catch(() => ({}));
              if (d.call_id) { _finish(); return; }
            }
          } catch (_) {}
          await new Promise(r => setTimeout(r, 5000));
        }
        if (!settled) {
          settled = true; cleanup(); _forgetActiveNativeUpload(uploadId);
          reject(Object.assign(new Error(t('err.netBlip')), { r2NoFallback: true }));
        }
      }
      async function reconcile() {
        if (settled || reconciling) return;
        reconciling = true;
        try {
          for (let i = 0; i < 30 && !settled; i++) {
            try {
              const pr = await apiFetch(`${API_BASE}/process_pending/?key=${key}`);
              if (pr.ok) {
                const d = await pr.json().catch(() => ({}));
                if (d.call_id) { _finish(); return; }
              }
            } catch (_) {}
            // R2 mode: a frozen WebView misses the 'completed' event, and no
            // chunk bytes ever land on the volume - the byte probe below reads
            // 0 forever. Ask the server to complete from R2 instead: succeeds
            // exactly when the object is fully there (a single PUT is atomic,
            // and the parallel service only creates the object at its own
            // multipart-complete step).
            if (isR2) {
              if (await _r2Complete(1)) { _finish(); return; }
            } else {
              try {
                const r = await apiFetch(`${API_BASE}/upload_check/?key=${key}`);
                if (r.ok && expected) {
                  const { bytes } = await r.json();
                  if (bytes >= expected) { _finish(); return; }
                  if (bytes > 0) onProgress(Math.min(0.99, bytes / expected));
                }
              } catch (_) {}
            }
            await new Promise(res => setTimeout(res, 2500));
          }
        } finally { reconciling = false; }
      }
      const _onVis = () => { if (!document.hidden && !settled) reconcile(); };
      document.addEventListener('visibilitychange', _onVis);
      // addListener may return the handle directly OR a Promise<handle>
      // depending on the plugin/Capacitor version - normalize with
      // Promise.resolve so ".then is not a function" can't happen.
      const _acknowledge = (ev) => {
        if (ev && ev.eventId && Uploader.acknowledgeEvent) {
          Promise.resolve(Uploader.acknowledgeEvent({ eventId: ev.eventId }))
            .catch(() => {});
        }
      };
      const _handleUploadEvent = (ev) => {
        if (!ev) return;
        if (!uploadId) {
          earlyEvents.push(ev);
          return;
        }
        const terminal = ev.name === 'completed' || ev.name === 'failed'
          || ev.name === 'cancelled';
        // The plugin persists terminal events until acknowledged and may replay
        // an OLD upload's completion when a new listener attaches. Never let a
        // stale id finish the current promise (the 350 MB upload raced ahead to
        // ffprobe at ~25%). Acknowledge terminal events so they are not replayed
        // forever.
        if (ev.id !== uploadId || settled) {
          if (terminal) _acknowledge(ev);
          return;
        }
        if (ev.name === 'uploading') {
          const p = ev.payload && typeof ev.payload.percent === 'number' ? ev.payload.percent : 0;
          onProgress(Math.max(0, Math.min(1, p / 100)));
        } else if (ev.name === 'completed') {
          _acknowledge(ev);
          const sc = ev.payload && ev.payload.statusCode;
          if (sc && sc >= 400) {
            settled = true; cleanup(); _forgetActiveNativeUpload(uploadId);
            // R2 mode: a 4xx from R2 (expired presign etc.) left no object
            // behind (PUT is atomic) - the orchestrator falls back to the
            // stream path.
            reject(new Error(t('err.chunk', { i: 0, status: sc })));
          }
          else if (isR2) { onProgress(0.999); _r2FinishOrWait(); }
          else _finish();
        } else if (ev.name === 'failed') {
          _acknowledge(ev);
          settled = true; cleanup(); _forgetActiveNativeUpload(uploadId);
          reject(new Error((ev.payload && ev.payload.error) || t('err.chunk', { i: 0, status: 0 })));
        } else if (ev.name === 'cancelled') {
          _acknowledge(ev);
          settled = true; cleanup(); _forgetActiveNativeUpload(uploadId);
          reject(new DOMException('Upload cancelled', 'AbortError'));
        }
      };
      const _listener = Uploader.addListener('events', _handleUploadEvent);
      Promise.resolve(_listener).then((h) => {
        handle = h;
        if (settled && handle && handle.remove) handle.remove();
      }).catch(() => {});
      Uploader.startUpload(mode && mode.startOpts ? mode.startOpts : {
        filePath: desc.path,
        // R2 mode: the presigned URL IS the auth - extra headers must stay
        // off (only signed headers may be validated; none were signed).
        serverUrl: r2Url || `${API_BASE}/upload_stream/`,
        method: 'PUT',
        uploadType: 'binary',
        mimeType: desc.mimeType || 'application/octet-stream',
        headers: r2Url ? {} : Object.assign(
          { 'X-Upload-Key': key },
          expected ? { 'X-Upload-Size': String(expected) } : {},
          authToken ? { Authorization: 'Bearer ' + authToken } : {}),
        notificationTitle: t('upload.title') || 'Uploading video',
        maxRetries: 3,
      }).then((result) => {
        uploadId = result && result.id;
        if (!uploadId && !settled) {
          resolveUploadReady(null);
          settled = true; cleanup();
          reject(new Error('Uploader did not return an upload id'));
          return;
        }
        _rememberActiveNativeUpload(uploadId, key, plugName);
        resolveUploadReady({ id: uploadId, key });
        earlyEvents.splice(0).forEach(_handleUploadEvent);
      }).catch((e) => {
        resolveUploadReady(null);
        if (!settled) {
          settled = true; cleanup(); _forgetActiveNativeUpload(uploadId);
          reject(e);
        }
      });
    });
  }

  // Share the finished video via the OS share sheet (WhatsApp/Instagram/etc).
  // The result lives on the server, so it's pre-downloaded to the app cache the
  // moment the video is ready (_prepareShareFile) - so tapping Share is instant
  // instead of a multi-second "preparing" wait. Native only.
  let _sharing = false;
  let _sharePrep = null;         // { url, uri } once the finished file is cached locally
  let _sharePrepPromise = null;  // in-flight warm-up (native) - a tap awaits this
  let _sharePrepUrl = null;      // which url that warm-up is fetching
  function _safeShareName(name) { return (name || 'video.mp4').replace(/[^\w.\-]+/g, '_'); }

  // Share buttons are 'ready' the MOMENT a result exists - no spinner phase.
  // The file warm-up (caching the finished video locally) runs silently in the
  // background; a tap that lands mid-warm-up awaits the SAME in-flight download
  // (showing "preparing..." on the button) instead of starting a second one.
  function _setShareState(state) {
    // 'hidden' | 'preparing' (native: file still caching - not tappable yet)
    // | 'ready'. The button is enabled ONLY once the bytes are on the device,
    // so a tap always shares instantly (user request - the instant-"ready"
    // button used to block for the whole download when tapped early).
    ['burnShareBtn', 'shareBtn'].forEach(id => {
      const b = document.getElementById(id);
      if (!b) return;
      if (state === 'hidden') { b.style.display = 'none'; return; }
      b.style.display = '';
      b.disabled = state === 'preparing';
      b.textContent = state === 'preparing' ? t('share.preparing') : t('share.btn');
    });
  }

  // Keep the share warm-up alive until the file is REALLY cached. The fetch
  // dies silently when Android freezes the WebView (user leaves the app), and
  // the old flow only retried on tap - so "preparing" restarted from zero at
  // the worst moment. Supervisor: (re)start the warm-up whenever a result
  // exists and the app is visible; flip the button to 'ready' only when the
  // bytes are on disk. Bounded retries per result.
  let _shareWarmTries = 0;
  async function _ensureShareWarm() {
    const url = resultDownloadUrl;
    if (!_isNative() || !url) return;
    if (_sharePrep && _sharePrep.url === url) { _setShareState('ready'); return; }
    if (_sharePrepPromise && _sharePrepUrl === url) return;   // already running
    if (_sharePrepUrl !== url) _shareWarmTries = 0;           // new result - fresh budget
    if (_shareWarmTries++ >= 5) return;                       // persistent failure - tap path still works
    _sharePrepUrl = url;
    const attempt = _prepareShareFile(url, resultName)
      .finally(() => { if (_sharePrepPromise === attempt) _sharePrepPromise = null; });
    _sharePrepPromise = attempt;
    await attempt;
    if (resultDownloadUrl !== url) return;   // a newer result took over
    if (_sharePrep && _sharePrep.url === url) _setShareState('ready');
    else if (!document.hidden) setTimeout(_ensureShareWarm, 4000);
  }
  document.addEventListener('visibilitychange', () => {
    // Coming back to the app resumes a warm-up the freeze killed.
    if (!document.hidden) _ensureShareWarm();
  });
  function _escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // Write an in-memory Blob to the app cache in base64 chunks (Filesystem.writeFile
  // only takes strings). Used to reuse the ALREADY-DOWNLOADED preview blob for
  // sharing instead of re-downloading the same file over the network.
  function _blobToB64(blob) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result).split(',', 2)[1] || '');
      r.onerror = reject;
      r.readAsDataURL(blob);
    });
  }
  async function _writeBlobToCache(Filesystem, blob, path) {
    const CHUNK = 6 * 1024 * 1024;
    for (let off = 0; off < blob.size; off += CHUNK) {
      const data = await _blobToB64(blob.slice(off, off + CHUNK));
      await Filesystem.writeFile({ path, directory: 'CACHE', data, append: off > 0 });
    }
  }
  // The /download/<key> segment identifies the file regardless of query params.
  function _downloadKeyOf(url) { const m = /\/download\/([^/?]+)/.exec(url || ''); return m ? m[1] : null; }

  // ── Parallel range download (share warm-up) ─────────────────────────────
  // A single-stream fetch rarely saturates a wifi link; fetching the file in
  // N concurrent byte ranges typically lands 2-3x the throughput, so the share
  // file is ready in a fraction of the time. The backend /download route
  // serves arbitrary Range spans (206 + Content-Range, exposed via CORS).
  // Falls back to plain fetch when the server answers 200 (no range support).
  // maxBytes: throw early (before buffering the tail) so callers with a RAM
  // budget can fall back to a disk-streaming path instead.
  const _RANGE_CONC = 4;
  function _rangeChunkBytes() { return window.__RANGE_CHUNK || 8 * 1024 * 1024; }
  // Per-request timeout: a stalled socket must FAIL (so callers hit their
  // single-stream / streaming fallbacks) instead of hanging the whole fetch.
  function _rangeTimeoutSig() {
    try { return AbortSignal.timeout(60_000); } catch (_) { return undefined; }
  }
  async function _rangeFetchBlob(url, { maxBytes = Infinity } = {}) {
    const tokUrl = _withToken(url);
    const CHUNK = _rangeChunkBytes();
    const first = await fetch(tokUrl, { headers: { 'Range': `bytes=0-${CHUNK - 1}` }, signal: _rangeTimeoutSig() });
    if (first.status !== 206) {
      if (!first.ok) throw new Error('share download ' + first.status);
      return await first.blob();               // server ignored Range - single stream
    }
    const m = /\/(\d+)\s*$/.exec(first.headers.get('Content-Range') || '');
    const total = m ? parseInt(m[1], 10) : NaN;
    if (total > maxBytes) throw new Error('file too large for in-memory download');
    const firstBuf = await first.arrayBuffer();
    if (!total || firstBuf.byteLength >= total) return new Blob([firstBuf], { type: 'video/mp4' });
    const parts = [firstBuf];
    const jobs = [];
    for (let off = firstBuf.byteLength; off < total; off += CHUNK) {
      jobs.push({ idx: parts.length, start: off, end: Math.min(off + CHUNK, total) - 1 });
      parts.push(null);
    }
    let next = 0;
    async function worker() {
      while (next < jobs.length) {
        const j = jobs[next++];
        const r = await fetch(tokUrl, { headers: { 'Range': `bytes=${j.start}-${j.end}` }, signal: _rangeTimeoutSig() });
        if (r.status !== 206) throw new Error('range fetch ' + r.status);
        parts[j.idx] = await r.arrayBuffer();
      }
    }
    await Promise.all(Array.from({ length: Math.min(_RANGE_CONC, jobs.length) }, worker));
    return new Blob(parts, { type: 'video/mp4' });
  }

  async function _prepareShareFile(url, name) {
    if (!_isNative() || !url) return;
    if (_sharePrep && _sharePrep.url === url) return;   // already cached
    const Filesystem = _capPlugin('Filesystem');
    if (!Filesystem) return;
    try {
      // The automatic save-to-Documents starts immediately before the share
      // warm-up. Reuse that exact native stream instead of downloading a
      // second full copy into CACHE at the same time.
      if (_nativeDownloadTask && _nativeDownloadUrl === url) {
        try { await _nativeDownloadTask; } catch (_) {}
        if (_sharePrep && _sharePrep.url === url) return;
      }
      const safe = _safeShareName(name);
      // Fast path: the preview player already downloaded this exact file into a
      // blob - write it to the cache locally (seconds) instead of a SECOND full
      // network download (the old behavior, which also competed with the preview
      // fetch for bandwidth and made the button take ages to become ready).
      let reused = false;
      if (_previewBlobPromise && videoKey && _downloadKeyOf(url) === videoKey) {
        try {
          const objURL = await _previewBlobPromise;
          if (objURL) {
            const blob = await (await fetch(objURL)).blob();   // in-memory, no network
            await _writeBlobToCache(Filesystem, blob, safe);
            reused = true;
          }
        } catch (_) { /* fall through to the network download */ }
      }
      if (!reused) {
        await _downloadToCache(Filesystem, url, safe);
      }
      const { uri } = await Filesystem.getUri({ directory: 'CACHE', path: safe });
      // cachePath lets the download button COPY these bytes locally instead of
      // pulling a second full copy over the network (the "download takes 30s
      // after the video is ready" report).
      _sharePrep = { url, uri, cachePath: safe };
    } catch (_) { /* silent: this is a background warm-up; a tap retries on demand */ }
  }

  // Fetch a server file into the app cache: parallel byte ranges (2-3x faster
  // than the plugin's single stream) for files that fit a sane RAM budget,
  // falling back to the plugin's disk-streaming download for huge files or any
  // range failure.
  const _SHARE_RAM_LIMIT = 200 * 1024 * 1024;
  async function _downloadToCache(Filesystem, url, safe) {
    try {
      const blob = await _rangeFetchBlob(url, { maxBytes: _SHARE_RAM_LIMIT });
      await _writeBlobToCache(Filesystem, blob, safe);
      return;
    } catch (_) { /* too large / range hiccup - stream to disk below */ }
    await Filesystem.downloadFile({ url: _withToken(url), path: safe, directory: 'CACHE' });
  }

  async function nativeShareVideo(url, name) {
    const Share = _capPlugin('Share');
    const Filesystem = _capPlugin('Filesystem');
    if (!Share || !url || _sharing) return;
    _sharing = true;
    const btns = [document.getElementById('burnShareBtn'), document.getElementById('shareBtn')].filter(Boolean);
    const labels = btns.map(b => b.innerHTML);
    try {
      let uri = (_sharePrep && _sharePrep.url === url) ? _sharePrep.uri : null;
      if (!uri) {
        btns.forEach(b => { b.disabled = true; b.textContent = t('share.preparing'); });
        // A background warm-up may already be downloading this exact file -
        // wait for IT rather than racing a duplicate download.
        if (_sharePrepPromise) {
          try { await _sharePrepPromise; } catch (_) {}
          uri = (_sharePrep && _sharePrep.url === url) ? _sharePrep.uri : null;
        }
      }
      if (!uri && Filesystem) {                 // no warm-up ran (or it failed) - fetch now
        const safe = _safeShareName(name);
        await _downloadToCache(Filesystem, url, safe);
        uri = (await Filesystem.getUri({ directory: 'CACHE', path: safe })).uri;
        _sharePrep = { url, uri };
      }
      // FILE shares carry ONLY the file. Bundling EXTRA_TEXT with the video
      // stream makes Instagram open without the media on a cold start (field:
      // "works only on the 2nd attempt") - IG mishandles text+stream intents.
      await Share.share({ files: [uri], dialogTitle: t('share.dialog') });
    } catch (e) {
      const msg = (e && e.message) || '';
      // Ignore user cancel + interruptions from the app being backgrounded/closed;
      // only a genuine failure gets a brief, NON-blocking toast (never the
      // processing/status area).
      if (!/cancel/i.test(msg) && !document.hidden) {
        console.error('share failed', e);
        try { celebrateToast(t('share.failed')); } catch (_) {}
      }
    } finally {
      _sharing = false;
      btns.forEach((b, i) => { b.disabled = false; b.innerHTML = labels[i]; });
    }
  }
  // ── Web share (browser): the OS share sheet via the Web Share API ──
  // Mobile browsers (Android Chrome, iOS Safari) can share a video FILE; the
  // button only shows where the capability probe passes. The file is pre-cached
  // in memory when the button appears, because navigator.share() must run
  // within the tap's transient activation (~seconds) - fetching a large video
  // on demand would expire it and throw NotAllowedError.
  let _webShareFile = null;   // { url, file } once the finished video is cached
  function _canWebShareFiles() {
    try {
      return !!(navigator.canShare &&
                navigator.canShare({ files: [new File([''], 'v.mp4', { type: 'video/mp4' })] }));
    } catch (_) { return false; }
  }
  async function _fetchShareBlob(url) {
    // Fast path: the preview player already downloaded this exact file.
    if (_previewBlobPromise && videoKey && _downloadKeyOf(url) === videoKey) {
      try {
        const objURL = await _previewBlobPromise;
        if (objURL) return await (await fetch(objURL)).blob();   // in-memory, no network
      } catch (_) { /* fall through to the network */ }
    }
    // Parallel ranges first (2-3x a single stream); plain fetch as fallback.
    try {
      return await _rangeFetchBlob(url);
    } catch (_) { /* range hiccup - single stream below */ }
    const resp = await fetch(_withToken(url));
    if (!resp.ok) throw new Error('share download ' + resp.status);
    return await resp.blob();
  }
  let _webSharePrepPromise = null;   // in-flight web warm-up - a tap awaits this
  async function _prepareWebShareFile(url, name) {
    if (_webShareFile && _webShareFile.url === url) return;
    try {
      const blob = await _fetchShareBlob(url);
      _webShareFile = { url, file: new File([blob], _safeShareName(name), { type: 'video/mp4' }) };
    } catch (_) { /* background warm-up only; a tap still tries on demand */ }
  }
  async function webShareVideo(url, name) {
    if (!url || _sharing) return;
    _sharing = true;
    const btns = [document.getElementById('burnShareBtn'), document.getElementById('shareBtn')].filter(Boolean);
    const labels = btns.map(b => b.innerHTML);
    let waited = false;   // the tap had to wait → the browser may void its activation
    try {
      let file = (_webShareFile && _webShareFile.url === url) ? _webShareFile.file : null;
      if (!file) {                              // not warmed yet - wait for / run the fetch
        waited = true;
        btns.forEach(b => { b.disabled = true; b.textContent = t('share.preparing'); });
        if (_webSharePrepPromise) { try { await _webSharePrepPromise; } catch (_) {} }
        file = (_webShareFile && _webShareFile.url === url) ? _webShareFile.file : null;
        if (!file) {
          file = new File([await _fetchShareBlob(url)], _safeShareName(name), { type: 'video/mp4' });
          _webShareFile = { url, file };
        }
      }
      // Files only - text+stream makes Instagram drop the media (see the
      // native share note).
      await navigator.share({ files: [file] });
    } catch (e) {
      const msg = (e && (e.name + ' ' + e.message)) || '';
      if (/abort|cancel/i.test(msg) || document.hidden) { /* user backed out */ }
      else if (waited && /NotAllowed/i.test(msg)) {
        // The wait outlived the tap's transient activation. The file IS cached
        // now, so the next tap shares instantly - say so instead of "failed".
        try { celebrateToast(t('share.again')); } catch (_) {}
      } else {
        console.error('web share failed', e);
        try { celebrateToast(t('share.failed')); } catch (_) {}
      }
    } finally {
      _sharing = false;
      btns.forEach((b, i) => { b.disabled = false; b.innerHTML = labels[i]; });
    }
  }

  // Reveal the Share button the moment a result exists - native always (OS
  // share sheet via the Share plugin), web wherever the Web Share API can share
  // files. Native shows 'preparing' (disabled) until the warm-up lands the
  // file on the device, then flips to a tap-shares-instantly 'ready'.
  function _maybeShowShare() {
    if (!resultDownloadUrl) { _setShareState('hidden'); return; }
    if (_isNative()) {
      if (_sharePrep && _sharePrep.url === resultDownloadUrl) { _setShareState('ready'); return; }
      _setShareState('preparing');
      _ensureShareWarm();
    } else if (_canWebShareFiles()) {
      _setShareState('ready');
      _webSharePrepPromise = _prepareWebShareFile(resultDownloadUrl, resultName)
        .finally(() => { _webSharePrepPromise = null; });
    } else {
      _setShareState('hidden');
    }
  }
  ['burnShareBtn', 'shareBtn'].forEach(id => {
    const b = document.getElementById(id);
    if (b) b.addEventListener('click', () => _isNative()
      ? nativeShareVideo(resultDownloadUrl, resultName)
      : webShareVideo(resultDownloadUrl, resultName));
  });

  // Register this device for "your video is ready" push notifications. Native
  // only; needs google-services.json in the build (fails gracefully without it).
  // Runs once per app session after login.
  let _pushInited = false;
  async function _initPush() {
    if (!_isNative() || _pushInited) return;
    const Push = _capPlugin('PushNotifications');
    if (!Push) return;
    _pushInited = true;
    try {
      let perm = await Push.checkPermissions();
      if (perm.receive === 'prompt' || perm.receive === 'prompt-with-rationale') {
        perm = await Push.requestPermissions();
      }
      if (perm.receive !== 'granted') return;
      // "Video ready" notifications show but do NOT vibrate (user request).
      // A dedicated channel with vibration disabled; the backend targets it via
      // android.notification.channel_id.
      try {
        if (Push.createChannel) await Push.createChannel({
          id: 'video_ready', name: 'Video ready',
          description: 'Notifies when your edited video is ready',
          importance: 4, vibration: false, visibility: 1,
        });
      } catch (_) {}
      Push.addListener('registration', (tok) => {
        const value = tok && tok.value;
        // The platform decides the TRANSPORT, not just a label: Android's
        // token goes to FCM, iOS hands back a raw APNs device token that the
        // backend must send straight to Apple. Sending the wrong one silently
        // black-holes every notification for that device.
        if (value) apiFetch(`${API_BASE}/push/register/`, {
          method: 'POST',
          body: JSON.stringify({ token: value, platform: _platform() }),
        }).catch(() => {});
      });
      Push.addListener('registrationError', (e) => console.warn('push reg error', e));
      // Tapping the "video ready" notification opens the app (default) and jumps
      // to History where the finished video is.
      Push.addListener('pushNotificationActionPerformed', (action) => {
        // An admin error alert is the one push whose payoff is NOT the creation
        // flow: it exists to be investigated, and the investigation lives in the
        // Admin tab's Errors panel. Everything else lands on the CREATION tab -
        // a finished video's next step is share/schedule (right there in the
        // success banner), and processing/edit_ready taps continue the
        // auto-resumed flow. Forcing the tab guards against restoreTab()
        // reopening History/Guide from a previous session.
        let kind = '';
        try {
          const data = (action && action.notification && action.notification.data) || {};
          kind = String(data.kind || '');
        } catch (_) {}
        try {
          if (typeof switchTab === 'function') {
            switchTab(kind === 'admin_alert' && _isAdminUser() ? 'admin' : 'pipeline');
          }
        } catch (_) {}
      });
      await Push.register();
    } catch (e) { console.warn('push init failed', e); }
  }

  // ── Mobile source chooser: record with the camera OR pick a file ──────────
  // Phones can CREATE the video, not just hold one - tapping the upload zone
  // on a mobile device asks which. "Record" uses the hidden capture-input
  // (opens the camera recorder; Capacitor's WebView file-chooser handles it
  // natively too); the recorded File flows through the standard web upload
  // path. Desktop keeps the plain file dialog.
  function _isMobileDevice() {
    return _isNative() || /Android|iPhone|iPad|Mobi/i.test(navigator.userAgent || '');
  }
  let _srcBypass = false;   // set while a chooser button forwards to a picker
  function _openSourceChooser() {
    const ov = document.getElementById('sourceOverlay');
    if (!ov) { _isNative() ? pickNativeFile() : fileInput.click(); return; }
    ov.style.display = 'flex';
  }
  (function initSourceChooser() {
    const ov = document.getElementById('sourceOverlay');
    if (!ov) return;
    const hide = () => { ov.style.display = 'none'; };
    const forward = (fn) => {
      hide();
      _srcBypass = true;
      try { fn(); } finally { setTimeout(() => { _srcBypass = false; }, 0); }
    };
    document.getElementById('srcRecordBtn')?.addEventListener('click', () =>
      forward(() => document.getElementById('recordInput')?.click()));
    document.getElementById('srcFileBtn')?.addEventListener('click', () =>
      forward(() => (_isNative() ? pickNativeFile() : fileInput.click())));
    document.getElementById('srcCancelBtn')?.addEventListener('click', hide);
    ov.addEventListener('click', (e) => { if (e.target === ov) hide(); });
    document.getElementById('recordInput')?.addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      if (f) handleFile(f);
      e.target.value = '';   // allow re-recording the same flow
    });
  })();
  // Mobile WEB: intercept the zone tap (the covering <input> would open the
  // file dialog directly) and offer the chooser instead.
  if (!_isNative()) {
    uploadZone.addEventListener('click', (e) => {
      if (!_isMobileDevice() || _srcBypass) return;
      e.preventDefault(); e.stopPropagation();
      _openSourceChooser();
    }, true);
  }

  // On native, route the upload zone / browse tap to the source chooser (its
  // file option uses the native picker - the hidden <input> would yield a
  // pathless browser File that can't ride the foreground-service uploader).
  if (_isNative()) {
    if (fileInput) fileInput.style.display = 'none';
    uploadZone.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      _openSourceChooser();
    }, true);

    // Show the brand animation as the loading screen, then hand off to the app.
    // Hide the native splash once our video overlay is up so there's no flash of
    // blank screen; the video plays (~2.5s), then fades out.
    const SplashScreen = _capPlugin('SplashScreen');
    _runAppLoader(() => {
      if (SplashScreen) { try { SplashScreen.hide(); } catch (_) {} }
    });
  }

  // Show the brand badge + spinner over the app (matching the native splash),
  // then fade it away. onReady fires as soon as the overlay is visible (so the
  // native splash can hide behind it with no flash). Brief dwell, then fade.
  function _runAppLoader(onReady) {
    const loader = document.getElementById('appLoader');
    if (!loader) { if (onReady) onReady(); return; }
    loader.classList.add('show');
    if (onReady) onReady();
    setTimeout(() => {
      loader.classList.add('fade-out');
      setTimeout(() => { loader.classList.remove('show', 'fade-out'); }, 500);
    }, 2000);   // dwell so the badge + spinner register, then fade
  }

  async function handleFile(file) {
    if (!file) return;
    const _isVideo = file.type.startsWith('video/') || /\.(mp4|mov|mkv|avi|webm)$/i.test(file.name);
    const _isAudio = _isAudioFile(file);
    if (!_isVideo && !_isAudio) {
      showBlockNotice(t('file.badTypeTitle'), t('file.badType'));
      return;
    }

    selectedFile = file;
    isAudioInput = _isAudio && !_isVideo;
    applyAudioMode(isAudioInput);
    fileName.textContent = file.name;
    fileInfo.classList.add('visible');
    document.getElementById('startOverBtn').style.display = '';
    clearNotices();
    resetStatus();

    // Audio input: tell the user up front that only captions / clean audio are
    // produced (B-roll, hooks, video enhance are off) so the reduced editor
    // isn't a surprise. Shown for the whole selection, alongside any warnings.
    if (isAudioInput && noticeAudio) noticeAudio.classList.add('visible');

    // Size check (instant)
    if (file.size > MAX_BYTES) {
      fileDetail.textContent = t('file.tooLargeMeta', {size: formatSize(file.size)});
      showBlockNotice(t('file.tooLargeTitle'), t('file.tooLarge', {size: formatSize(file.size)}));
      blocked = true;
      runBtn.disabled = true;
      return;
    }

    // Read duration (+ resolution for video). Audio has no dimensions, so probe
    // with an <audio> element and skip the 4K/orientation logic entirely.
    fileDetail.textContent = t('file.reading', {size: formatSize(file.size)});
    const meta = isAudioInput ? await getAudioMeta(file) : await getVideoMeta(file);
    videoDuration = meta.duration;
    // 4K if the long edge is ~3840 (landscape) or ~2160 tall portrait 4K -
    // i.e. the larger dimension reaches ~3000px. QHD (2560) is not flagged.
    const is4K = !isAudioInput && Math.max(meta.width || 0, meta.height || 0) >= 3000;
    // Early orientation hint (refined from the processed video once it loads in
    // the caption player) so B-roll can match the input's orientation.
    if (!isAudioInput) videoOrientation = _orientationFor(meta.width, meta.height);

    if (videoDuration !== null) {
      fileDetail.textContent = formatSize(file.size) + ' · ' + formatDuration(videoDuration);
    } else {
      fileDetail.textContent = formatSize(file.size);
    }
    // The upscale cap depends on THIS file's length.
    _syncUpscaleLimit();

    // Validate duration
    if (videoDuration !== null && videoDuration > MAX_SECS) {
      showBlockNotice(t('notice.tooLongTitle'), t('notice.tooLong', {dur: formatDuration(videoDuration)}));
      blocked = true;
      runBtn.disabled = true;
      return;
    }

    blocked = false;
    runBtn.disabled = false;

    // Snapshot the bytes now, while the OS file reference is still valid. If
    // this throws it's almost always a cloud-only file (Google Photos) that was
    // never downloaded to the device - block here with clear guidance instead
    // of letting it fail mid-upload after wasting the user's time and a credit.
    try {
      fileDetail.textContent = t('file.reading', { size: formatSize(file.size) });
      stableBlob = await snapshotFile(file);
      fileDetail.textContent = (videoDuration !== null)
        ? formatSize(file.size) + ' · ' + formatDuration(videoDuration)
        : formatSize(file.size);
    } catch (readErr) {
      console.error(`Snapshot failed - ${(readErr && readErr.message) || readErr}`);
      showBlockNotice(t('file.cloudTitle'), t('file.cloud'));
      blocked = true;
      runBtn.disabled = true;
      return;
    }

    // Warnings (one, most-specific first). 4K takes precedence: it explains
    // WHY the upload is slow (huge bitrate) without asking the user to lower
    // quality - just sets the expectation.
    if (is4K) {
      showWarnNotice(t('file.res4kTitle'), t('file.res4k'));
    } else if (file.size > WARN_BYTES) {
      showWarnNotice(t('file.largeWarnTitle'), t('file.largeWarn', {size: formatSize(file.size)}));
    } else if (videoDuration !== null && videoDuration > WARN_SECS) {
      showWarnNotice(t('notice.longTitle'), t('notice.long', {dur: formatDuration(videoDuration)}));
    }

    updateTimeEstimate();

    // Probe the uplink once per session so the upload estimate reflects THIS
    // connection (fills in the estimate line when it resolves).
    if (_uplinkMbps == null) {
      _probeUplinkMbps().then(m => { if (m) { _uplinkMbps = m; updateTimeEstimate(); } });
    }

    checkNetwork();

    // Fire-and-forget GPU warmup so the container is ready by the time the user clicks Run
    apiFetch(API_BASE + '/warmup/').catch(() => {});
  }

  // The X on the file card is a full start-over (same confirm modal + reset
  // as the bottom button) - clearing just the selection left half-done state
  // (an in-flight upload, a registered job) behind it.
  clearFile.addEventListener('click', () => { startOverFlow(); });

  // ── Metadata helper (duration + resolution in one pass) ──
  function getVideoMeta(file) {
    return new Promise(resolve => {
      const video = document.createElement('video');
      const url   = URL.createObjectURL(file);
      // Detach src before revoking - Chrome keeps fetching the blob after
      // loadedmetadata and logs ERR_FILE_NOT_FOUND if it's already revoked.
      const done = meta => {
        video.removeAttribute('src');
        video.load();
        URL.revokeObjectURL(url);
        resolve(meta);
      };
      video.preload = 'metadata';
      video.onloadedmetadata = () => done({ duration: video.duration, width: video.videoWidth, height: video.videoHeight });
      video.onerror = () => done({ duration: null, width: 0, height: 0 });
      video.src = url;
    });
  }

  // Duration-only probe for audio uploads (no width/height).
  function getAudioMeta(file) {
    return new Promise(resolve => {
      const audio = document.createElement('audio');
      const url   = URL.createObjectURL(file);
      const done = meta => {
        audio.removeAttribute('src'); audio.load();
        URL.revokeObjectURL(url);
        resolve(meta);
      };
      audio.preload = 'metadata';
      audio.onloadedmetadata = () => done({ duration: audio.duration, width: 0, height: 0 });
      audio.onerror = () => done({ duration: null, width: 0, height: 0 });
      audio.src = url;
    });
  }

  // Toggle the options UI between video and audio modes. In audio mode only
  // silence-cut + enhance-audio apply; captions are always produced (for SRT).
  function applyAudioMode(on) {
    const hide = ['burnCaptionsRow', 'captionChildren', 'enhanceVideoRow', 'enhanceVideoPanel'];
    hide.forEach(id => { const el = document.getElementById(id); if (el) el.style.display = on ? 'none' : ''; });
    document.body.classList.toggle('audio-mode', on);
    if (typeof _syncCaptionChildren === 'function') _syncCaptionChildren();
  }

  function checkToolsEnabled() {
    if (!selectedFile || burnMode) return;
    // Audio always transcribes (captions/SRT are the point), so Run is always
    // available regardless of which audio-safe toggles are on.
    if (isAudioInput) { runBtn.disabled = false; return; }
    const ids = ['cutSilences', 'burnCaptions', 'enhanceAudio'];
    runBtn.disabled = !ids.some(id => document.getElementById(id)?.checked)
                      && _enhanceVideoMode() === 'none';
  }
  ['cutSilences', 'burnCaptions', 'enhanceAudio'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', () => { checkToolsEnabled(); updateTimeEstimate(); _syncCaptionChildren(); });
  });
  ['autoBroll', 'autoHook'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', _syncCaptionChildren);
  });

  // Auto B-roll / hook toggles are children of "Burn Hebrew captions" (both need
  // the transcript). Enable them only when captions are on; grey + note otherwise,
  // and surface the extra-processing-time estimate when either is enabled.
  function _autoExtraTimeEstimate() {
    // B-roll dominates (moment selection + vision scoring + fetching); hook is
    // quick. They run in parallel, so the total ≈ the slower one.
    return document.getElementById('autoBroll')?.checked
      ? t('est.autoBrollTime') : t('est.autoHookTime');
  }
  function _syncCaptionChildren() {
    const wrap = document.getElementById('captionChildren');
    if (!wrap) return;
    const on = !!document.getElementById('burnCaptions')?.checked && !isAudioInput;
    wrap.classList.toggle('disabled', !on);
    const ab = document.getElementById('autoBroll');
    const ah = document.getElementById('autoHook');
    [ab, ah].forEach(el => { if (el) el.disabled = !on; });
    if (!on) { if (ab) ab.checked = false; if (ah) ah.checked = false; }
    const note = document.getElementById('autoExtraTimeNote');
    if (note) {
      const anyOn = on && ((ab && ab.checked) || (ah && ah.checked));
      note.textContent = anyOn ? t('opt.autochild.extra', { t: _autoExtraTimeEstimate() }) : '';
      note.style.display = anyOn ? 'flex' : 'none';
    }
  }
  _syncCaptionChildren();
  const aggrVal = document.getElementById('aggrVal');
  aggrSlider.addEventListener('input', () => {
    const a = AGGR_MAP[aggrSlider.value - 1];
    aggrDesc.textContent = t(a.label);
    aggrVal.textContent  = a.silence + ' s';
  });

  // The aggressiveness slider only matters while Cut silences is on
  function updateAggrVisibility() {
    document.getElementById('aggrPanel').classList.toggle('hidden', !document.getElementById('cutSilences').checked);
  }
  document.getElementById('cutSilences').addEventListener('change', updateAggrVisibility);
  updateAggrVisibility();

  // Pre-run estimate, calibrated on the L4 (2026-07 measurements):
  // Whisper turbo ≈10× realtime, DeepFilterNet ≈2-4× realtime, render ≈4× realtime,
  // ESRGAN ≈0.5 s/frame → 15 s (30fps) to 30 s (60fps) per second of video.
  // Fixed overhead spans warm vs cold GPU container. Shown as a labelled range;
  // the progress card still reports only real measured times.
  function estimatedTime(secs) {
    const useEnhance    = document.getElementById('enhanceAudio').checked;
    const useTranscribe = document.getElementById('cutSilences').checked || document.getElementById('burnCaptions').checked;
    const evMode        = _enhanceVideoMode();
    let lo = 30, hi = 75;
    if (useEnhance)    { lo += secs * 0.4;  hi += secs * 0.8; }
    if (useTranscribe) { lo += secs * 0.25; hi += secs * 0.5; }
    if (evMode === 'filters' && !useTranscribe) { lo += secs * 0.1; hi += secs * 0.3; }
    if (evMode === 'esrgan')  { lo += secs * 15; hi += secs * 30; }
    const lom = Math.max(1, Math.floor(lo / 60));
    const him = Math.max(lom + 1, Math.ceil(hi / 60));
    return t('est.simple', {lo: lom, hi: him});
  }

  function updateTimeEstimate() {
    const box = document.getElementById('timeEstimate');
    if (!videoDuration || !selectedFile) { box.style.display = 'none'; return; }
    document.getElementById('timeEstimateText').textContent = estimatedTime(videoDuration);
    // Upload estimate - needs a measured uplink (probed at selection). ~85%
    // efficiency accounts for TLS/HTTP/TCP overhead.
    const upEl = document.getElementById('uploadEstimateText');
    if (upEl) {
      if (_uplinkMbps && selectedFile) {
        const secs = (selectedFile.size * 8 / 1e6) / (_uplinkMbps * 0.85);
        // Show a range, not a false-precision single number - a probe can't
        // predict a fluctuating connection exactly.
        const lo = Math.max(1, Math.floor(secs * 0.8 / 60));
        const hi = Math.max(lo + 1, Math.ceil(secs * 1.25 / 60));
        const slow = secs > 480;   // only warn when genuinely long (> ~8 min)
        upEl.textContent = t(slow ? 'est.uploadSlow' : 'est.upload', { lo, hi });
        upEl.style.color      = slow ? 'var(--terracotta)' : '';
        upEl.style.fontWeight = slow ? '600' : '';
        upEl.style.display    = '';
      } else {
        upEl.style.display = 'none';
      }
    }
    box.style.display = 'flex';
  }

  // Best-effort uplink probe: measure AGGREGATE throughput across several
  // concurrent streams so the estimate matches the real upload (which runs
  // UPLOAD_CONCURRENCY streams). A single-stream probe badly under-measures on
  // APs that shape per-connection, doubling the estimate. Resolves to Mbps or
  // null (never throws). Writes tiny scratch files the server prunes.
  // Probes the R2 path first (that's where the real upload goes; the Modal
  // ingress caps at ~3-4 Mbps over h2, which would wildly over-estimate the
  // time) and falls back to the Modal probe when R2 isn't reachable.
  function _probeUplinkMbps() {
    return _probeR2Mbps().then(m => m || _probeModalMbps()).catch(() => null);
  }

  function _probeR2Mbps() {
    const SIZE = 384 * 1024;
    return (async () => {
      let urls;
      try {
        // Plain fetch, NOT apiFetch: the probe is telemetry - a 401 here
        // (expired session, older backend) must never tear the app down.
        const resp = await fetch(`${API_BASE}/upload_r2/probe/`,
          { headers: authToken ? { 'Authorization': 'Bearer ' + authToken } : {} });
        if (!resp.ok) return null;
        urls = await resp.json();
        if (!Array.isArray(urls.puts) || !urls.puts.length) return null;
      } catch (_) { return null; }
      const body = new Uint8Array(SIZE);
      const t0 = performance.now();
      const results = await Promise.all(urls.puts.map(u => new Promise(res => {
        const xhr = new XMLHttpRequest();
        xhr.open('PUT', u);
        xhr.timeout = 20000;
        xhr.onload    = () => res(xhr.status >= 200 && xhr.status < 300);
        xhr.onerror   = () => res(false);
        xhr.ontimeout = () => res(false);
        xhr.send(body);
      })));
      const secs = (performance.now() - t0) / 1000;
      // Clean up the scratch objects; fire-and-forget.
      (urls.deletes || []).forEach(u => { try { fetch(u, { method: 'DELETE' }).catch(() => {}); } catch (_) {} });
      if (!results.every(Boolean) || secs <= 0) return null;   // CORS unset / blocked → not the real path
      return (urls.puts.length * SIZE * 8 / 1e6) / secs;
    })();
  }

  function _probeModalMbps() {
    const STREAMS = 4, SIZE = 384 * 1024;   // ~1.5 MB total, mirrors multi-stream upload
    return new Promise(resolve => {
      try {
        const body = new Uint8Array(SIZE);
        const t0 = performance.now();
        let done = 0, ok = 0;
        const finish = () => {
          if (++done < STREAMS) return;
          const secs = (performance.now() - t0) / 1000;
          resolve((ok === STREAMS && secs > 0) ? (STREAMS * SIZE * 8 / 1e6) / secs : null);
        };
        for (let s = 0; s < STREAMS; s++) {
          const key = ('probe' + crypto.randomUUID().replace(/-/g, '')).slice(0, 60);
          const xhr = new XMLHttpRequest();
          xhr.open('POST', `${API_BASE}/upload_chunk/`);
          xhr.setRequestHeader('Content-Type', 'application/octet-stream');
          xhr.setRequestHeader('X-Upload-Key', key);
          xhr.setRequestHeader('X-Upload-Index', '9999');
          if (authToken) xhr.setRequestHeader('Authorization', 'Bearer ' + authToken);
          xhr.timeout = 20000;
          xhr.onload    = () => { if (xhr.status >= 200 && xhr.status < 300) ok++; finish(); };
          xhr.onerror   = finish;
          xhr.ontimeout = finish;
          xhr.send(body);
        }
      } catch (_) { resolve(null); }
    });
  }

  function _enhanceVideoMode() {
    return document.querySelector('input[name="enhanceVideo"]:checked')?.value || 'none';
  }

  const _EV_DESCS = { none: 'ev.desc.none', filters: 'ev.desc.filters', esrgan: 'ev.desc.esrgan' };
  document.querySelectorAll('input[name="enhanceVideo"]').forEach(r =>
    r.addEventListener('change', () => {
      document.getElementById('enhanceVideoDesc').innerHTML = t(_EV_DESCS[_enhanceVideoMode()]);
      checkToolsEnabled();
      updateTimeEstimate();
      _syncUpscaleLimit();
    }));

  // The 4K upscale runs at 15-30x realtime, so past ~3 minutes of source it
  // would outrun the server's job timeout and fail AFTER burning the GPU time.
  // Disable the option rather than let the user pick a run that cannot finish.
  function _syncUpscaleLimit() {
    const radio = document.querySelector('input[name="enhanceVideo"][value="esrgan"]');
    if (!radio) return;
    const tooLong = !!videoDuration &&
      videoDuration > UPSCALE_MAX_SECONDS + CREDIT_DURATION_TOLERANCE;
    if (tooLong && radio.checked) {
      const fallback = document.querySelector('input[name="enhanceVideo"][value="none"]');
      if (fallback) fallback.checked = true;
      const desc = document.getElementById('enhanceVideoDesc');
      if (desc) desc.innerHTML = t(_EV_DESCS[_enhanceVideoMode()]);
    }
    radio.disabled = tooLong;
    // The radios are visually hidden; the sibling <label for> IS the control.
    const row = document.querySelector('label[for="' + radio.id + '"]');
    if (row) {
      row.classList.toggle('opt-disabled', tooLong);
      row.title = tooLong ? t('ev.upscaleTooLong', {max: UPSCALE_MAX_SECONDS / 60}) : '';
    }
    const note = document.getElementById('upscaleLimitNote');
    if (note) {
      note.textContent = tooLong ? t('ev.upscaleTooLong', {max: UPSCALE_MAX_SECONDS / 60}) : '';
      note.style.display = tooLong ? 'block' : 'none';
    }
  }

  // ── Run ──
  runBtn.addEventListener('click', () => { if (burnMode) doBurn(); else run(); });
  retryBtn.addEventListener('click', run);

  // Set before an intentional in-app reload (Start over, logout) so the
  // beforeunload guard below doesn't pop the browser's "leave page?" prompt -
  // the user already confirmed in the app's own modal.
  let _intentionalNav = false;
  window.addEventListener('beforeunload', (e) => {
    if (_intentionalNav) return;
    const editing = document.getElementById('captionEditorCard').style.display !== 'none';
    const hasBrollWork = Object.keys(stockBrollSelections).length > 0;
    // NOTE: a finished download URL is NOT "unsaved work" - the file lives on
    // the server (30-day retention) and in History, so leaving loses nothing.
    // (It also must NOT be a trigger: the download itself must never provoke a
    // beforeunload prompt.)
    if (isUploading || pollController || editing || resultBlob || hasBrollWork) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  async function run(isRetry = false) {
    if (!selectedFile || blocked) return;
    if (_quotaExhausted()) {
      showQuotaExhausted();
      return;
    }
    if (!(await _confirmQuotaUse())) return;

    resultBlob = null;
    resultDownloadUrl = null;
    _webShareFile = null;   // free the previous video's cached share file
    showUploadProgress();

    const aggr = AGGR_MAP[aggrSlider.value - 1];
    // Audio: no video processing / no burn - captions are always produced for
    // the SRT, output is a clean .m4a. Only cut-silences + enhance-audio apply.
    const params = new URLSearchParams({
      filename:             selectedFile.name,
      cut_silences:         document.getElementById('cutSilences').checked  ? 'true' : 'false',
      burn_captions:        (!isAudioInput && document.getElementById('burnCaptions').checked) ? 'true' : 'false',
      enhance_audio:        document.getElementById('enhanceAudio').checked ? 'true' : 'false',
      enhance_video:        isAudioInput ? 'none' : _enhanceVideoMode(),
      transcribe_for_broll: 'false',
      is_audio:             isAudioInput ? 'true' : 'false',
      min_silence:          aggr.silence,
      padding:              aggr.padding,
      // Prices the run (long source / 4K upscale cost extra credits). The
      // server re-checks it against its own probe, so a wrong value here only
      // ever fails the job - it can never buy a cheaper run.
      duration:             videoDuration || 0,
    });

    try {
      // Phase 1: upload video in chunks
      _setStage('upload');
      const _upT0 = performance.now();
      const _onUpProgress = (pct) => {
        document.getElementById('uploadBarFill').style.width = (pct * 100).toFixed(0) + '%';
        document.getElementById('uploadBarPct').textContent  = (pct * 100).toFixed(0) + '%';
        // Live ETA from measured throughput so the wait isn't a black box.
        const etaEl = document.getElementById('uploadEta');
        const etaMinMs = (window.__UPLOAD_ETA_MIN_MS != null) ? window.__UPLOAD_ETA_MIN_MS : 1500; // test seam
        if (etaEl && pct > 0.02 && pct < 0.999) {
          const elapsedMs = performance.now() - _upT0;
          if (elapsedMs > etaMinMs) {
            const remaining = (elapsedMs / 1000) * (1 - pct) / pct;
            etaEl.textContent = t('upload.remaining', { t: formatTime(Math.round(remaining)) });
          }
        }
      };
      // Resolve the upload PATH + KEY up front so processing can be
      // PRE-REGISTERED (deferred spawn): the server starts the job the moment
      // the last byte lands - even if the app is closed by then (its frozen JS
      // used to be the thing doing the spawn, so processing silently waited
      // for a reopen). Every native pick uses Android's foreground-service
      // uploader so the upload survives minimize/lock and owns a visible
      // status-bar notification. Web uploads remain resumable and chunked.
      let _upMode = 'chunked', _upMeta = selectedFile, _upBlob = stableBlob;
      if (nativeUploadDesc) _upMode = 'stream';
      const uploadKey = _upMode === 'stream'
        ? crypto.randomUUID().replace(/-/g, '')
        : _resumeUploadKey(_upMeta);
      params.set('key', uploadKey);

      // Pre-register the job (defer=true): quota is checked NOW (fail fast,
      // before any bytes fly), the spawn happens server-side at upload
      // completion. Any registration failure other than the quota gate falls
      // back to the classic post-upload spawn (older backend, network blip).
      const _totalChunks = _upMode === 'stream' ? 1
        : Math.max(1, Math.ceil(((_upBlob && _upBlob.size) || _upMeta.size || 1) / CHUNK_SIZE));
      let deferred = false;
      {
        let dr = null;
        try {
          dr = await apiFetch(`${API_BASE}/process/?${params}&defer=true&total_chunks=${_totalChunks}`,
                              { method: 'POST' });
        } catch (_) {}
        if (dr && dr.status === 202) deferred = true;
        else if (dr && dr.status === 402) {
          const body = await dr.json().catch(() => ({}));
          throw new Error(body.error || t('err.spawn', { status: 402 }));
        }
      }
      if (deferred) saveJob('pending', null, { filename: selectedFile.name, key: uploadKey });

      if (_upMode === 'stream') {
        await nativeUpload(nativeUploadDesc, _onUpProgress, uploadKey);
      } else {
        // Fast path: presigned parallel PUTs to R2 (~uplink speed). Falls back
        // to the chunked Modal path on any R2-specific failure (old backend,
        // missing creds, bucket CORS unset) - flagged r2Fallback so a real
        // terminal error (unreadable file, dead network) still surfaces.
        let usedR2 = false;
        try {
          await r2Upload(_upMeta, _upBlob, _onUpProgress, uploadKey);
          usedR2 = true;
        } catch (e) {
          if (!e || !e.r2Fallback) throw e;
          console.warn('R2 upload unavailable - falling back to chunked:', e.message);
        }
        if (!usedR2) await chunkedUpload(_upMeta, _upBlob, _onUpProgress, uploadKey);
      }
      _stepDone('upload');
      document.getElementById('uploadBarRow').style.display = 'none';
      { const n = document.getElementById('uploadNote'); if (n) n.style.display = 'none'; }
      // (upload throughput is still recorded to window.__lastUploadStats +
      // console for diagnostics, but no longer shown in the UI)

      currentUploadKey = uploadKey;

      // Phase 2: the job's call id. Deferred: the server spawned it inside the
      // final upload request - just fetch the id. Fallback: classic spawn.
      runStartTime = Date.now();
      showProcessing();
      _setStage('spawn');
      let call_id;
      if (deferred) {
        call_id = await _awaitPendingSpawn(uploadKey);
      } else {
        const spawnResp = await apiFetch(`${API_BASE}/process/?${params}`, { method: 'POST' });
        if (spawnResp.status !== 202) {
          const body = await spawnResp.json().catch(() => ({}));
          throw new Error(body.error || t('err.spawn', {status: spawnResp.status}));
        }
        ({ call_id } = await spawnResp.json());
      }
      refreshQuota();

      // Poll until processing is done - returns JSON {captions, video_key}
      currentCallId = call_id;
      saveJob('process', call_id, { filename: selectedFile.name, key: uploadKey });
      _setStage('processing');
      const pollUrl = `${API_BASE}/process_poll/${call_id}/?key=${encodeURIComponent(uploadKey)}`;
      const result = await pollForJSON(pollUrl, 900_000, call_id, _applyProgress);
      _stepsDoneProcessing(result.step_times);
      clearSavedJob();

      captionsData = result.captions;
      videoKey     = result.video_key;
      _prefetchPreviewBlob(videoKey);
      // Trust the output key: the backend auto-detects audio even if the
      // frontend guessed wrong, so mode follows the real result.
      isAudioInput = (videoKey || '').endsWith('.m4a');
      applyAudioMode(isAudioInput);
      const _stem  = (selectedFile.name || 'video').replace(/\.[^/.]+$/, '');
      cutFilename  = _stem + (isAudioInput ? '_clean.m4a' : '_cut.mp4');

      if (isAudioInput) {
        // Audio: always land on the reduced editor (audio player + captions +
        // SRT + download-audio). No burn, no B-roll.
        showCaptionEditor();
      } else {
        if (captionsData.length > 0) {
          // Keep checklist visible (steps 1-3 done) while user edits captions.
          // await so the editor is built before the auto-generations fire.
          await showCaptionEditor();
          _startAutoGenerations();
        } else {
          // No captions, no B-roll - download directly
          await _finalizeAndDownload(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(cutFilename)}`, cutFilename);
        }
      }
    } catch (err) {
      // Android's upload service may report a user cancellation as either a
      // `cancelled` event or a generic `failed` event. Start Over owns this
      // cancellation, so never render that transient failure before reload.
      if (err.name === 'AbortError' || intentionalStartOver) return;
      console.error('Process error:', err.message);
      // A network drop doesn't kill the server-side job - keep the saved job
      // so the Resume banner can reconnect to it on the next visit.
      if (!_isNetErr(err)) clearSavedJob();
      if (!isRetry && err.message.includes('Network error')) {
        // GPU warmup retry - keep checklist, just update upload step label
        checkItems.upload.className = 'check-item done';
        _stepActivate('enhance');
        await new Promise(r => setTimeout(r, 8000));
        run(true);
      } else {
        showError(err.message);
      }
    }
  }

  // Reprocess overwrites the whole editing session, so EVERYONE confirms -
  // not just quota-charged users (admins used to jump straight in). One
  // modal: the discard warning, plus the credit line when it applies.
  async function _confirmRerun() {
    const charged = quotaInfo && quotaInfo.role !== 'admin' &&
                    quotaInfo.video_limit != null && quotaInfo.video_limit >= 0;
    const cost = _creditCost();
    const body = t('confirm.rerunBody')
      + (charged ? ' ' + t('confirm.rerunCredit', { n: cost }) : '');
    return showConfirmModal(t('confirm.rerunTitle'), body, t('confirm.rerunOk'));
  }

  async function rerun() {
    if (!currentUploadKey || !selectedFile) return;
    if (_quotaExhausted()) {
      showQuotaExhausted();
      return;
    }
    if (!(await _confirmRerun())) return;
    // Kill in-flight background tools (auto B-roll / hook) - their detached
    // poll loops would otherwise keep painting rows on the NEW run's
    // checklist ("stuck on looking for B-roll", field report).
    _cancelActiveTools();

    // Hide editor cards and reset to pre-caption state
    burnMode = false;
    hasBurnedOnce = false;
    captionsData = [];
    videoKey = null;
    resultBlob = null;
    resultDownloadUrl = null;
    _webShareFile = null;
    _resetExactPreview();
    { const el = document.getElementById('captionEditorCard'); if (el) el.style.display = 'none'; }
    document.getElementById('reprocessBtn').style.display = 'none';
    document.getElementById('burnSuccessBanner').style.display = 'none';
    { const _osb = document.getElementById('openScheduleBtn'); if (_osb) _osb.style.display = 'none';
      const _so = document.getElementById('scheduleOverlay'); if (_so) _so.style.display = 'none'; }
    setSetupLocked(true);

    const aggr = AGGR_MAP[aggrSlider.value - 1];
    const params = new URLSearchParams({
      filename:             selectedFile.name,
      cut_silences:         document.getElementById('cutSilences').checked  ? 'true' : 'false',
      burn_captions:        document.getElementById('burnCaptions').checked ? 'true' : 'false',
      enhance_audio:        document.getElementById('enhanceAudio').checked ? 'true' : 'false',
      enhance_video:        _enhanceVideoMode(),
      transcribe_for_broll: 'false',
      min_silence:          aggr.silence,
      padding:              aggr.padding,
      duration:             videoDuration || 0,
      key:                  currentUploadKey,
    });

    lockPipelineActions();
    runStartTime = Date.now();
    runBtn.style.display = 'none';

    // Show checklist: upload already done (cached), enhance + cut pending
    statusCard.classList.add('visible');
      expandCard('statusBody');
    checklistEl.style.display = 'block';
    statusDone.classList.remove('visible');
    statusError.classList.remove('visible');
    _resetChecklist();
    checkItems.upload.className = 'check-item done';
    checkTimeEls.upload.textContent = t('err.cached');
    showProcessing();

    try {
      _setStage('spawn');
      const spawnResp = await apiFetch(`${API_BASE}/process/?${params}`, { method: 'POST' });
      if (spawnResp.status !== 202) {
        const body = await spawnResp.json().catch(() => ({}));
        throw new Error(body.error || t('err.spawn', {status: spawnResp.status}));
      }
      const { call_id } = await spawnResp.json();
      refreshQuota();

      currentCallId = call_id;
      saveJob('process', call_id, { filename: selectedFile.name, key: currentUploadKey });
      _setStage('processing');
      const result = await pollForJSON(`${API_BASE}/process_poll/${call_id}/?key=${encodeURIComponent(currentUploadKey)}`, 900_000, call_id, _applyProgress);
      _stepsDoneProcessing(result.step_times);
      clearSavedJob();

      captionsData = result.captions;
      videoKey     = result.video_key;
      _prefetchPreviewBlob(videoKey);
      cutFilename  = (selectedFile.name || 'video').replace(/\.[^/.]+$/, '') + '_cut.mp4';

      unlockPipelineActions();
      if (captionsData.length > 0) {
        await showCaptionEditor();
        _startAutoGenerations();
      } else {
        await _finalizeAndDownload(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(cutFilename)}`, cutFilename);
      }
    } catch (err) {
      unlockPipelineActions();
      if (err.name === 'AbortError') return;
      console.error('Re-process error:', err.message);
      if (!_isNetErr(err)) clearSavedJob();
      showError(err.message);
    }
  }

  document.getElementById('reprocessBtn').addEventListener('click', rerun);

  // Upload file in chunks to the Modal ASGI endpoint (streaming body, no 303 redirect issue).
  // Returns the upload key to pass to /process/?key=...
  // 2 MB chunks (was 5): small enough that completed chunks "stick" quickly on
  // a slow uplink and a killed in-flight chunk re-sends ≤2 MB, not ≤5 MB - so
  // backgrounding barely dents progress. Throughput is uplink-bound, so smaller
  // chunks don't slow a healthy connection (connection reuse absorbs the extra
  // request overhead); on a flaky one they're faster (less re-send waste).
  // 1 MB chunks (was 2 MB): on a congested slow uplink, 6 concurrent 2 MB
  // chunks each crawled ~20 s and flirted with the stall watchdog; if one
  // aborted, a full 2 MB re-sent. Smaller chunks finish ~2× faster (clearer
  // liveness, less wasted on an abort) and stick faster on a weak link.
  // Overhead is negligible now that all chunks share ONE cached CORS preflight.
  const CHUNK_SIZE = 1 * 1024 * 1024;
  // Parallelism: chunk POSTs share ONE cached CORS preflight (fixed URL +
  // Access-Control-Max-Age), so the old preflight-doubling that pushed 16
  // in-flight past the container's input cap is gone. 6 fills the bandwidth-
  // delay product on healthy uplinks, stays well under the API's max_inputs=50
  // (chunks + polls), and caps in-flight (losable-on-background) bytes at
  // 6×1 MB = 6 MB. Kept modest so a background-kill loses little.
  const UPLOAD_CONCURRENCY = 6;
  const UPLOAD_RESUME_TTL = 24 * 60 * 60 * 1000;   // server prunes scratch chunks after 48h; stay well under
  // POST one chunk via XHR (not fetch): fetch reports no upload progress and
  // has no timeout, so on a slow mobile uplink the bar sits at 0% until a whole
  // 5 MB chunk finishes and a stalled connection hangs forever. XHR gives
  // byte-level progress AND lets us abort a connection that has STALLED (no
  // bytes moved for STALL_MS) - distinct from merely slow, which we let run.
  // Rejects with a flagged error: isTerminal (don't retry), isServer (bounded),
  // else network/stall (bounded, but survives backgrounding since a frozen page
  // fires no progress and its timers are paused).
  // Resolves immediately if the page is visible, else when it next becomes
  // visible. Used to park upload retries while the phone is backgrounded so
  // an interruption doesn't burn the retry budget.
  function _whenVisible() {
    if (!document.hidden) return Promise.resolve();
    return new Promise(res => {
      const h = () => { if (!document.hidden) { document.removeEventListener('visibilitychange', h); res(); } };
      document.addEventListener('visibilitychange', h);
    });
  }

  // Bumped every time the page goes hidden. An upload attempt captures this
  // before sending; if it changed by the time the request fails, the failure
  // coincided with backgrounding (the OS killed the in-flight request) and
  // must NOT count against the dead-connection budget - only failures that
  // happen while the page stays visible are genuine network problems.
  let _hiddenEpoch = 0;
  document.addEventListener('visibilitychange', () => { if (document.hidden) _hiddenEpoch++; });

  // 35 s (was 20 s): on congested café Wi-Fi a live-but-slow chunk can go >20 s
  // between byte-progress events; aborting it then re-sending wasted bandwidth
  // and dropped effective throughput. Only abort when a connection is truly
  // dead — a slow-but-moving upload is never killed. A genuinely dead socket is
  // still bounded by MAX_STUCK × this + MAX_TOTAL.
  const CHUNK_STALL_MS = 35_000;
  // POST one chunk to a FIXED url with key/index in headers (not the query
  // string). A chunk POST always triggers a CORS preflight (Authorization +
  // octet-stream are non-safelisted); keeping the URL constant lets the browser
  // reuse ONE cached preflight (Access-Control-Max-Age) for the whole upload
  // instead of re-flying before every 2 MB chunk (a per-chunk RTT tax).
  function _postChunkBytes(key, index, body, onLoaded) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let watchdog;
      const stallMs = window.__CHUNK_STALL_MS || CHUNK_STALL_MS;   // test seam
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => { try { xhr.abort(); } catch (_) {}
          reject(Object.assign(new Error('upload stalled'), { isNetwork: true })); }, stallMs);
      };
      xhr.open('POST', `${API_BASE}/upload_chunk/`);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-Upload-Key', key);
      xhr.setRequestHeader('X-Upload-Index', String(index));
      if (authToken) xhr.setRequestHeader('Authorization', 'Bearer ' + authToken);
      xhr.upload.onprogress = e => { arm(); if (e.lengthComputable) onLoaded(e.loaded); };
      xhr.onload = () => {
        clearTimeout(watchdog);
        if (xhr.status >= 200 && xhr.status < 300) { resolve(); return; }
        if (xhr.status === 401) { _sessionExpired();
          reject(Object.assign(new Error(t('auth.sessionExpired')), { isTerminal: true })); return; }
        if (xhr.status >= 400 && xhr.status < 500 && xhr.status !== 408 && xhr.status !== 429) {
          let msg; try { msg = JSON.parse(xhr.responseText).error; } catch (_) {}
          reject(Object.assign(new Error(msg || ''), { isTerminal: true, httpStatus: xhr.status })); return;
        }
        reject(Object.assign(new Error(`server ${xhr.status}`), { isServer: true, httpStatus: xhr.status }));
      };
      xhr.onerror   = () => { clearTimeout(watchdog); reject(Object.assign(new Error('Failed to fetch'), { isNetwork: true })); };
      xhr.ontimeout = () => { clearTimeout(watchdog); reject(Object.assign(new Error('upload stalled'), { isNetwork: true })); };
      arm();
      xhr.send(body);
    });
  }

  // Resume state: which chunks the server already has, keyed by a stable file
  // signature so a "try again" (same File) or a reload + re-pick (same file)
  // reuses the upload key and skips chunks already sent. Server chunk files are
  // keyed by (key, index) and re-POSTing an index just overwrites, so skipping
  // completed indices is safe as long as every index's file still exists.
  function _fileSig(file) { return `hebpipe_up_${file.name}_${file.size}_${file.lastModified}`; }
  function _loadResume(file) {
    try {
      const raw = localStorage.getItem(_fileSig(file));
      if (!raw) return null;
      const s = JSON.parse(raw);
      if (!s.key || Date.now() - (s.ts || 0) > UPLOAD_RESUME_TTL) { localStorage.removeItem(_fileSig(file)); return null; }
      return s;
    } catch (_) { return null; }
  }
  function _saveResume(file, key, completed) {
    try { localStorage.setItem(_fileSig(file), JSON.stringify({ key, completed: [...completed], ts: Date.now() })); } catch (_) {}
  }
  function _clearResume(file) { try { localStorage.removeItem(_fileSig(file)); } catch (_) {} }

  // ── File byte snapshot (Google Photos / cloud-sync resilience) ──
  // A file picked on Android can be cloud-only (Google Photos): its bytes live
  // in the cloud, not on the device. Reading it lazily per chunk DURING a long
  // upload fails partway through when background sync moves/re-syncs it
  // (ERR_UPLOAD_FILE_CHANGED). snapshotFile() copies the bytes ONCE at
  // selection time - while the reference is still valid - into a stable Blob the
  // upload slices from instead. That copy can't go stale. It also forces the
  // read to happen now, so a truly cloud-only (unreadable) file is caught
  // immediately, before any upload or credit is spent.
  const SNAPSHOT_MEM_LIMIT = 150 * 1024 * 1024; // hold in memory below this; larger -> IndexedDB (disk, off-heap)

  function _idbOpen() {
    return new Promise((res, rej) => {
      const r = indexedDB.open('hebpipe-uploads', 1);
      r.onupgradeneeded = () => { if (!r.result.objectStoreNames.contains('blobs')) r.result.createObjectStore('blobs'); };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error || new Error('idb open failed'));
    });
  }
  async function _idbPut(id, blob) {
    const db = await _idbOpen();
    try {
      await new Promise((res, rej) => {
        const tx = db.transaction('blobs', 'readwrite');
        tx.objectStore('blobs').put(blob, id);
        tx.oncomplete = res;
        tx.onerror = () => rej(tx.error || new Error('idb write failed'));
        tx.onabort = () => rej(tx.error || new Error('idb write aborted'));
      });
    } finally { db.close(); }
  }
  async function _idbGet(id) {
    const db = await _idbOpen();
    try {
      return await new Promise((res, rej) => {
        const tx = db.transaction('blobs', 'readonly');
        const rq = tx.objectStore('blobs').get(id);
        rq.onsuccess = () => res(rq.result);
        rq.onerror = () => rej(rq.error || new Error('idb read failed'));
      });
    } finally { db.close(); }
  }
  async function _idbDel(id) {
    try {
      const db = await _idbOpen();
      try {
        await new Promise(res => {
          const tx = db.transaction('blobs', 'readwrite');
          tx.objectStore('blobs').delete(id);
          tx.oncomplete = res; tx.onerror = res; tx.onabort = res;
        });
      } finally { db.close(); }
    } catch (_) {}
  }
  // Wipe any snapshot left behind by a prior session (e.g. after "start over"
  // reloads the page). A reload can't resume an in-memory File anyway - the
  // user re-picks and re-snapshots - so nothing on disk is worth keeping.
  async function _idbClearAll() {
    try {
      const db = await _idbOpen();
      try {
        await new Promise(res => {
          const tx = db.transaction('blobs', 'readwrite');
          tx.objectStore('blobs').clear();
          tx.oncomplete = res; tx.onerror = res; tx.onabort = res;
        });
      } finally { db.close(); }
    } catch (_) {}
  }
  _idbClearAll();

  // Returns a stable Blob of the file's bytes, or throws if the file can't be
  // read (almost always a cloud-only Google Photos file not on the device).
  async function snapshotFile(file) {
    _disposeSnapshot();
    if (file.size <= SNAPSHOT_MEM_LIMIT) {
      const buf = await file.arrayBuffer();                  // throws if unreadable
      return new Blob([buf], { type: file.type || 'video/mp4' });
    }
    // Too big to hold in RAM: stash on disk via IndexedDB, read back a stable,
    // disk-backed copy. The put reads every byte, so it also detects a bad file.
    const id = 'up_' + ((crypto.randomUUID && crypto.randomUUID()) || String(file.size) + '_' + file.lastModified);
    await _idbPut(id, file);                                 // throws if unreadable
    const stable = await _idbGet(id);
    if (!stable) throw new Error('snapshot readback empty');
    _snapshotId = id;
    return stable;
  }
  function _disposeSnapshot() {
    stableBlob = null;
    if (_snapshotId) { _idbDel(_snapshotId); _snapshotId = null; }
  }

  // The upload key this file WILL use: a prior in-progress upload's key (so
  // its chunks resume) or a fresh one. Needed before the upload starts so the
  // deferred processing job can be registered against it.
  function _resumeUploadKey(file) {
    const saved = _loadResume(file);
    return (saved && saved.key) || crypto.randomUUID().replace(/-/g, '');
  }

  async function chunkedUpload(file, source, onProgress, presetKey) {
    // `source` is the stable byte snapshot (see snapshotFile); slice bytes from
    // it, never from the raw File, so a cloud-synced original can't go stale
    // mid-upload. Falls back to the File itself if no snapshot was taken.
    source = source || file;
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    // Reuse a prior in-progress upload for this exact file if one exists.
    const saved = _loadResume(file);
    const key = presetKey || (saved && saved.key) || crypto.randomUUID().replace(/-/g, '');
    // Saved chunk indices only apply when they belong to THIS key.
    const completed = new Set((saved && saved.key === key && saved.completed || []).filter(i => i < totalChunks));
    if (completed.size) console.info(`Upload resume: ${completed.size}/${totalChunks} chunks already sent`);
    // Per-chunk loaded bytes - summed for accurate progress across concurrent
    // chunks (each in-flight chunk contributes its own live byte count).
    const loadedByChunk = new Array(totalChunks).fill(0);
    const report = () => onProgress(loadedByChunk.reduce((a, b) => a + b, 0) / file.size);

    // Telemetry: measure achieved throughput + churn so a slow upload can be
    // diagnosed (link-bound vs. our overhead) instead of guessed at. Surfaced
    // in the UI (#uploadStats) and on window.__lastUploadStats.
    const _t0 = performance.now();
    let _stalls = 0, _netRetries = 0, _serverRetries = 0, _resentBytes = 0;

    async function uploadChunk(i) {
      const start = i * CHUNK_SIZE;
      const end   = Math.min(start + CHUNK_SIZE, file.size);
      const chunkLen = end - start;
      // Already on the server from a prior attempt - count it done, skip it.
      if (completed.has(i)) { loadedByChunk[i] = chunkLen; report(); return; }
      const slice = source.slice(start, end);
      const MAX_SERVER_ATTEMPTS = 4;    // 408/429/5xx responses
      // A chunk gives up only after MAX_STUCK consecutive failures that happen
      // WHILE THE PAGE IS VISIBLE - a genuinely dead connection. Failures that
      // coincide with the page being backgrounded (the OS killing in-flight
      // requests) don't count, so an upload interrupted by minimizing the
      // browser repeatedly never exhausts its budget. MAX_TOTAL is an absolute
      // backstop against a pathological retry loop.
      const MAX_STUCK = 6, MAX_TOTAL = 60;
      let serverAttempts = 0, stuckTries = 0, totalTries = 0;
      const pctEl = document.getElementById('uploadBarPct');
      while (true) {
        if (totalTries++ > 0) {
          // Park (and don't count tries) while the page is hidden - wait until
          // it's foregrounded again, so backgrounding costs no attempts.
          await _whenVisible();
          await new Promise(r => setTimeout(r, 2000));
        }
        if (totalTries > MAX_TOTAL) {
          console.error(`Chunk ${i}: giving up after ${totalTries} total attempts`);
          throw Object.assign(new Error(t('err.chunkRetries', { i: i, n: MAX_TOTAL, status: 0 })), { isTerminal: true });
        }
        loadedByChunk[i] = 0; report();
        // Read the chunk bytes explicitly before sending. On Android a picked
        // file can silently become unreadable (moved, changed, or cloud-synced
        // via Google Photos); reading first surfaces that as a terminal error
        // (only re-selecting fixes it) instead of a generic transport failure.
        let body;
        try {
          body = await slice.arrayBuffer();
        } catch (readErr) {
          console.error(`Chunk ${i}: file unreadable - ${readErr.message}`);
          throw Object.assign(new Error(t('err.fileUnreadable')), { isTerminal: true });
        }
        const epoch0 = _hiddenEpoch;
        try {
          await _postChunkBytes(
            key, i, body,
            loaded => { loadedByChunk[i] = Math.min(loaded, chunkLen); report(); }
          );
          loadedByChunk[i] = chunkLen; report();
          completed.add(i); _saveResume(file, key, completed);
          return;
        } catch (e) {
          if (e.isTerminal) {
            if (!e.message) e.message = t('err.chunk', { i: i, status: e.httpStatus || 0 });
            throw e;
          }
          _resentBytes += loadedByChunk[i];   // bytes already pushed for this attempt are now wasted
          if (e.isServer) {
            _serverRetries++;
            if (++serverAttempts >= MAX_SERVER_ATTEMPTS)
              throw Object.assign(new Error(t('err.chunkRetries', { i: i, n: MAX_SERVER_ATTEMPTS, status: e.httpStatus || 0 })), { isTerminal: true });
            console.warn(`Chunk ${i}: server ${e.httpStatus}, retry ${serverAttempts}/${MAX_SERVER_ATTEMPTS}`);
          } else {
            if (/stall/i.test(e.message || '')) _stalls++; else _netRetries++;
            // Network error or stall. If the page was backgrounded during this
            // attempt, the OS killed the request - don't count it, just retry.
            const backgrounded = document.hidden || _hiddenEpoch !== epoch0;
            if (backgrounded) {
              console.warn(`Chunk ${i}: ${e.message} during backgrounding - retrying (not counted)`);
            } else if (++stuckTries >= MAX_STUCK) {
              console.error(`Chunk ${i}: giving up after ${stuckTries} network failures`);
              throw Object.assign(e, { isTerminal: true });
            } else {
              console.warn(`Chunk ${i}: ${e.message} - retry ${stuckTries}/${MAX_STUCK}`);
            }
            if (pctEl) pctEl.textContent = t('upload.retrying');
          }
        }
      }
    }

    // Sliding window - always keep UPLOAD_CONCURRENCY requests in-flight.
    // Avoids the batch barrier where the slowest chunk of a batch of N
    // blocks all subsequent chunks from starting.
    let nextChunk = 0;
    const inFlight = new Map(); // index -> promise

    function launchNext() {
      if (nextChunk >= totalChunks) return;
      const i = nextChunk++;
      const p = uploadChunk(i).then(() => inFlight.delete(i), err => { inFlight.delete(i); throw err; });
      inFlight.set(i, p);
    }

    // Fill the window
    while (nextChunk < totalChunks && inFlight.size < UPLOAD_CONCURRENCY) launchNext();

    while (inFlight.size > 0) {
      await Promise.race(inFlight.values());
      while (nextChunk < totalChunks && inFlight.size < UPLOAD_CONCURRENCY) launchNext();
    }

    _clearResume(file);   // fully uploaded - no stale resume state to leave behind
    _disposeSnapshot();   // bytes are on the server now - free the local copy

    const secs = (performance.now() - _t0) / 1000;
    const mbps = secs > 0 ? (file.size * 8 / 1e6) / secs : 0;
    window.__lastUploadStats = {
      bytes: file.size, mb: +(file.size / 1048576).toFixed(1), secs: +secs.toFixed(1),
      mbps: +mbps.toFixed(2), chunks: totalChunks, concurrency: UPLOAD_CONCURRENCY,
      stalls: _stalls, netRetries: _netRetries, serverRetries: _serverRetries,
      resentMB: +(_resentBytes / 1048576).toFixed(1),
    };
    console.info(`Upload done: ${window.__lastUploadStats.mb} MB in ${secs.toFixed(0)} s = `
      + `${mbps.toFixed(2)} Mbps effective; stalls=${_stalls} netRetries=${_netRetries} `
      + `serverRetries=${_serverRetries} resent=${window.__lastUploadStats.resentMB} MB`);
    return key;
  }

  // ── R2 direct upload (fast path) ──
  // Chunk POSTs to the Modal ingress are capped at ~3-4 Mbps: the browser
  // coalesces every concurrent request onto ONE HTTP/2 connection, and the h2
  // upload flow-control window on the ~120 ms path to us-east-1 is the ceiling
  // (measured 2026-08-06; no chunk-size/concurrency knob moves it). The R2 S3
  // endpoint speaks HTTP/1.1, so PARTS_CONCURRENCY XHRs get real parallel TCP
  // connections - the same uplink measured 21 Mbps (uplink-bound). Flow:
  // /upload_r2/init presigns multipart part URLs, parts PUT straight to R2
  // (no auth header - the signature is in the URL), /upload_r2/complete
  // assembles + copies to the volume server-side. Falls back to chunkedUpload
  // on ANY early failure (old backend, missing creds, bucket CORS not set) -
  // the throw carries r2Fallback so the caller can tell "use the slow path"
  // from a real terminal error.
  const R2_RESUME_TTL = 3 * 60 * 60 * 1000;   // presigned URLs live 4 h; stay under

  // PUT one part; resolves with the ETag (bucket CORS must expose it).
  function _putPartR2(url, body, onLoaded) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let watchdog;
      const stallMs = window.__CHUNK_STALL_MS || CHUNK_STALL_MS;
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => { try { xhr.abort(); } catch (_) {}
          reject(Object.assign(new Error('upload stalled'), { isNetwork: true })); }, stallMs);
      };
      xhr.open('PUT', url);
      xhr.upload.onprogress = e => { arm(); if (e.lengthComputable) onLoaded(e.loaded); };
      xhr.onload = () => {
        clearTimeout(watchdog);
        const etag = xhr.getResponseHeader('ETag');
        if (xhr.status >= 200 && xhr.status < 300 && etag) { resolve(etag); return; }
        if (xhr.status >= 200 && xhr.status < 300) {
          // 2xx but no readable ETag = bucket CORS lacks ExposeHeaders - the
          // multipart can never be completed. Not retryable.
          reject(Object.assign(new Error('no etag'), { isTerminal: true, r2Fallback: true })); return;
        }
        if (xhr.status >= 400 && xhr.status < 500 && xhr.status !== 408 && xhr.status !== 429) {
          // 403 = expired/invalid presign - retrying the same URL can't help.
          reject(Object.assign(new Error(`part ${xhr.status}`), { isTerminal: true, r2Fallback: true, httpStatus: xhr.status })); return;
        }
        reject(Object.assign(new Error(`server ${xhr.status}`), { isServer: true, httpStatus: xhr.status }));
      };
      xhr.onerror   = () => { clearTimeout(watchdog); reject(Object.assign(new Error('Failed to fetch'), { isNetwork: true })); };
      xhr.ontimeout = () => { clearTimeout(watchdog); reject(Object.assign(new Error('upload stalled'), { isNetwork: true })); };
      arm();
      xhr.send(body);
    });
  }

  function _r2Sig(file)      { return _fileSig(file) + '_r2'; }
  function _r2LoadState(file, key) {
    try {
      const s = JSON.parse(localStorage.getItem(_r2Sig(file)) || 'null');
      if (!s || s.key !== key || Date.now() - (s.ts || 0) > R2_RESUME_TTL) return null;
      return s;
    } catch (_) { return null; }
  }
  function _r2SaveState(file, st) {
    try { localStorage.setItem(_r2Sig(file), JSON.stringify(st)); } catch (_) {}
  }
  function _r2ClearState(file) { try { localStorage.removeItem(_r2Sig(file)); } catch (_) {} }

  // Active multipart (for Start Over cleanup). {key, upload_id} or null.
  let _r2Active = null;

  async function r2Upload(file, source, onProgress, key) {
    source = source || file;
    const size = source.size || file.size;
    // Resume a prior multipart for this exact file if the URLs are still
    // fresh; otherwise init a new one.
    let st = _r2LoadState(file, key);
    if (!st) {
      let resp;
      try {
        resp = await apiFetch(`${API_BASE}/upload_r2/init/`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, size }),
        });
      } catch (e) {
        // apiFetch clears the token on a real 401 teardown - don't mask that
        // by falling back (the chunked path would just 401 chunk by chunk).
        if (!authToken) throw e;
        throw Object.assign(new Error('r2 init unreachable'), { r2Fallback: true });
      }
      if (!resp.ok) throw Object.assign(new Error(`r2 init ${resp.status}`), { r2Fallback: true });
      const d = await resp.json();
      if (!d.upload_id || !Array.isArray(d.urls) || !d.urls.length)
        throw Object.assign(new Error('r2 init malformed'), { r2Fallback: true });
      st = { key, upload_id: d.upload_id, part_size: d.part_size, urls: d.urls,
             etags: {}, ts: Date.now() };
      _r2SaveState(file, st);
    }
    _r2Active = { key, upload_id: st.upload_id };
    const partSize   = st.part_size;
    const totalParts = st.urls.length;
    const loadedByPart = new Array(totalParts).fill(0);
    const report = () => onProgress(loadedByPart.reduce((a, b) => a + b, 0) / size);
    const resumedParts = Object.keys(st.etags).length;
    if (resumedParts) console.info(`R2 resume: ${resumedParts}/${totalParts} parts already sent`);

    const _t0 = performance.now();
    let _stalls = 0, _netRetries = 0, _serverRetries = 0, _resentBytes = 0;
    let anySuccess = resumedParts > 0;

    async function uploadPart(i) {
      const start = i * partSize;
      const end   = Math.min(start + partSize, size);
      const partLen = end - start;
      if (st.etags[i]) { loadedByPart[i] = partLen; report(); return; }
      const slice = source.slice(start, end);
      const MAX_SERVER_ATTEMPTS = 4;
      const MAX_STUCK = 6, MAX_TOTAL = 60;
      let serverAttempts = 0, stuckTries = 0, totalTries = 0;
      const pctEl = document.getElementById('uploadBarPct');
      while (true) {
        if (totalTries++ > 0) {
          await _whenVisible();
          await new Promise(r => setTimeout(r, 2000));
        }
        if (totalTries > MAX_TOTAL)
          throw Object.assign(new Error(t('err.chunkRetries', { i: i, n: MAX_TOTAL, status: 0 })), { isTerminal: true });
        loadedByPart[i] = 0; report();
        let body;
        try {
          body = await slice.arrayBuffer();
        } catch (readErr) {
          throw Object.assign(new Error(t('err.fileUnreadable')), { isTerminal: true });
        }
        const epoch0 = _hiddenEpoch;
        try {
          const etag = await _putPartR2(
            st.urls[i], body,
            loaded => { loadedByPart[i] = Math.min(loaded, partLen); report(); }
          );
          loadedByPart[i] = partLen; report();
          st.etags[i] = etag; anySuccess = true; _r2SaveState(file, st);
          return;
        } catch (e) {
          if (e.isTerminal) throw e;
          _resentBytes += loadedByPart[i];
          if (e.isServer) {
            _serverRetries++;
            if (++serverAttempts >= MAX_SERVER_ATTEMPTS)
              throw Object.assign(new Error(t('err.chunkRetries', { i: i, n: MAX_SERVER_ATTEMPTS, status: e.httpStatus || 0 })), { isTerminal: true });
          } else {
            if (/stall/i.test(e.message || '')) _stalls++; else _netRetries++;
            const backgrounded = document.hidden || _hiddenEpoch !== epoch0;
            if (!backgrounded) {
              // No part has EVER gone through and the first tries die while
              // visible: almost certainly bucket CORS not set / R2 blocked.
              // Bail to the chunked path fast instead of burning 6 retries.
              if (!anySuccess && stuckTries >= 1)
                throw Object.assign(e, { isTerminal: true, r2Fallback: true });
              if (++stuckTries >= MAX_STUCK)
                throw Object.assign(e, { isTerminal: true });
            }
            if (pctEl) pctEl.textContent = t('upload.retrying');
          }
        }
      }
    }

    let nextPart = 0;
    const inFlight = new Map();
    function launchNext() {
      if (nextPart >= totalParts) return;
      const i = nextPart++;
      const p = uploadPart(i).then(() => inFlight.delete(i), err => { inFlight.delete(i); throw err; });
      inFlight.set(i, p);
    }
    try {
      while (nextPart < totalParts && inFlight.size < UPLOAD_CONCURRENCY) launchNext();
      while (inFlight.size > 0) {
        await Promise.race(inFlight.values());
        while (nextPart < totalParts && inFlight.size < UPLOAD_CONCURRENCY) launchNext();
      }
    } catch (e) {
      if (e && e.r2Fallback) {
        // Hand the multipart back before falling to the chunked path.
        _r2AbortActive();
        _r2ClearState(file);
      }
      throw e;
    }

    // All parts are in R2 - ask the server to assemble + copy to the volume.
    // Retryable: /complete is idempotent (a landed chunk_0000 short-circuits).
    const etags = Array.from({ length: totalParts }, (_, i) => st.etags[i]);
    let completed = false, lastErr = null;
    for (let attempt = 0; attempt < 3 && !completed; attempt++) {
      if (attempt) await new Promise(r => setTimeout(r, 2000 * attempt));
      try {
        const resp = await apiFetch(`${API_BASE}/upload_r2/complete/`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key, upload_id: st.upload_id, etags }),
        });
        if (resp.ok) { completed = true; break; }
        lastErr = new Error(`r2 complete ${resp.status}`);
      } catch (e) { lastErr = e; if (!authToken) break; }
    }
    if (!completed) throw lastErr || new Error('r2 complete failed');

    _r2Active = null;
    _r2ClearState(file);
    _clearResume(file);
    _disposeSnapshot();

    const secs = (performance.now() - _t0) / 1000;
    const mbps = secs > 0 ? (size * 8 / 1e6) / secs : 0;
    window.__lastUploadStats = {
      bytes: size, mb: +(size / 1048576).toFixed(1), secs: +secs.toFixed(1),
      mbps: +mbps.toFixed(2), chunks: totalParts, concurrency: UPLOAD_CONCURRENCY,
      stalls: _stalls, netRetries: _netRetries, serverRetries: _serverRetries,
      resentMB: +(_resentBytes / 1048576).toFixed(1), path: 'r2',
    };
    console.info(`R2 upload done: ${window.__lastUploadStats.mb} MB in ${secs.toFixed(0)} s = `
      + `${mbps.toFixed(2)} Mbps effective; stalls=${_stalls} netRetries=${_netRetries}`);
    return key;
  }

  // Best-effort multipart abort (Start Over / fallback) - keepalive so it
  // survives the page reload that follows.
  function _r2AbortActive() {
    if (!_r2Active) return;
    const { key, upload_id } = _r2Active;
    _r2Active = null;
    try {
      fetch(`${API_BASE}/upload_r2/abort/`, {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json',
                   ...(authToken ? { 'Authorization': 'Bearer ' + authToken } : {}) },
        body: JSON.stringify({ key, upload_id }),
      }).catch(() => {});
    } catch (_) {}
  }

  // Upload file via XHR (for progress tracking); returns {callId} from 202 response
  function xhrUpload(url, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.responseType = 'text';

      xhr.upload.onprogress = e => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
      xhr.upload.onload = () => onProgress(1);

      xhr.onload = () => {
        if (xhr.status === 202) {
          const json = JSON.parse(xhr.responseText);
          resolve({ callId: json.call_id });
        } else {
          reject(new Error(t('err.server', {status: xhr.status, text: xhr.responseText.slice(0, 200)})));
        }
      };
      xhr.onerror = () => reject(new Error(t('err.network')));
      xhr.ontimeout = () => reject(new Error(t('err.uploadTimeout')));
      xhr.timeout = 10 * 60 * 1000; // 10 min for upload only
      xhr.send(file);
    });
  }

  // Poll a URL every 3s until it returns 200 (binary) or throws on 5xx.
  // Retries up to 3 times on transient network errors (e.g. ERR_CONNECTION_RESET).
  async function pollForBinary(pollUrl) {
    pollController = new AbortController();
    const signal = pollController.signal;
    let networkRetries = 0;
    const MAX_RETRIES = 3;
    while (true) {
      try {
        const resp = await fetch(pollUrl, { signal });
        networkRetries = 0;
        if (resp.status === 200) {
          const buffer = await resp.arrayBuffer();
          const cd = resp.headers.get('content-disposition') || '';
          return { buffer, cd };
        }
        if (resp.status === 202) {
          await new Promise((res, rej) => {
            const t = setTimeout(res, 3000);
            signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
          });
          continue;
        }
        const text = await resp.text();
        throw new Error(t('err.server', {status: resp.status, text: text.slice(0, 200)}));
      } catch (e) {
        if (e.name === 'AbortError') throw e;
        if (++networkRetries <= MAX_RETRIES) {
          console.warn(`Poll network error (retry ${networkRetries}/${MAX_RETRIES}): ${e.message}`);
          await new Promise((res, rej) => {
            const t = setTimeout(res, 2000);
            signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
          });
          continue;
        }
        console.error('Poll failed after retries:', e.message);
        throw e;
      }
    }
  }

  async function pollForJSON(pollUrl, timeoutMs = 900_000, callId = null, onProgress = null) {
    pollUrl = _withToken(pollUrl);   // SW polls the same URL - token travels with it
    pollController = new AbortController();
    const signal = pollController.signal;
    const deadline = Date.now() + timeoutMs;

    // Single promise that can be settled by either the page poll loop or the SW
    let _resolve, _reject;
    const done = new Promise((res, rej) => { _resolve = res; _reject = rej; });

    if (callId) {
      swResolvers.set(callId, _resolve);
      // Record poll info so the visibilitychange handler can hand off to SW when hidden
      currentPollInfo = { callId, pollUrl, deadline };
    }

    // Page-side poll - calls _resolve/_reject directly, never leaves a dangling promise
    (async () => {
      let serverRetries = 0;
      const MAX_SERVER_RETRIES = 3;
      while (Date.now() < deadline) {
        try {
          const resp = await fetch(pollUrl, { signal });
          if (resp.status === 200) { _resolve(await resp.json()); return; }
          if (resp.status === 202) {
            serverRetries = 0;
            if (onProgress) {
              try { onProgress((await resp.json()).progress); } catch {}
            }
            await new Promise((res, rej) => {
              const t = setTimeout(res, Math.min(3000, deadline - Date.now()));
              signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
            });
            continue;
          }
          const text = await resp.text();
          const err = new Error(t('err.server', {status: resp.status, text: text.slice(0, 200)}));
          err.isServer = true;
          throw err;
        } catch (e) {
          if (e.name === 'AbortError') { _reject(e); return; }
          if (e.isServer && ++serverRetries > MAX_SERVER_RETRIES) {
            console.error('Poll failed after server retries:', e.message);
            _reject(e); return;
          }
          // Pure network failures ("Failed to fetch") are never fatal before
          // the deadline: mobile OSes kill in-flight fetches on background/
          // foreground transitions while the job keeps running server-side.
          console.warn(`Poll error, retrying: ${e.message}`);
          await new Promise((res, rej) => {
            const t = setTimeout(res, Math.min(2000, deadline - Date.now()));
            signal.addEventListener('abort', () => { clearTimeout(t); rej(new DOMException('aborted', 'AbortError')); });
          });
          continue;
        }
      }
      _reject(new Error(t('err.resultTimeout')));
    })();

    try {
      const result = await done;
      if (callId) {
        swResolvers.delete(callId);
        currentPollInfo = null;
        navigator.serviceWorker?.controller?.postMessage({ type: 'POLL_CANCEL', callId });
      }
      return result;
    } catch (e) {
      if (callId) { swResolvers.delete(callId); currentPollInfo = null; }
      throw e;
    }
  }

  downloadBtn.addEventListener('click', triggerDownload);
  document.getElementById('burnDownloadBtn').addEventListener('click', triggerDownload);
  const nativeDownloaderPlugin = _isNative() && _capPlugin('NativeDownloader');
  const openDownloadsButtons = [
    document.getElementById('openDownloadsBtn'),
    document.getElementById('burnOpenDownloadsBtn'),
  ].filter(Boolean);
  if (nativeDownloaderPlugin && nativeDownloaderPlugin.openDownloads) {
    openDownloadsButtons.forEach(button => {
      button.style.display = '';
      button.addEventListener('click', async () => {
        try {
          await nativeDownloaderPlugin.openDownloads();
        } catch (error) {
          console.error('open downloads failed', error);
          celebrateToast(t('download.openFailed'), { kind: 'error', duration: 7000 });
        }
      });
    });
  }
  // Shared by the bottom "Start over" button AND the file card's X - both
  // mean "drop everything and reset", so both confirm with the same modal
  // and run the same cleanup + reload.
  async function startOverFlow() {
    const confirmed = await showConfirmModal(
      t('confirm.startTitle'),
      t('confirm.startBody'),
      t('confirm.startOk')
    );
    if (!confirmed) return;
    intentionalStartOver = true;
    try {
      await _cancelActiveNativeUpload();
    } catch (error) {
      intentionalStartOver = false;
      console.error('native upload cancellation failed', error);
      celebrateToast(t('upload.cancelFailed'), { kind: 'error', duration: 7000 });
      return;
    }
    if (currentCallId) apiFetch(`${API_BASE}/cancel/${currentCallId}/`, { keepalive: true }).catch(() => {});
    _r2AbortActive();   // keepalive - survives the reload below
    clearSavedJob();
    _intentionalNav = true;   // already confirmed in-app; skip the browser prompt
    location.reload();
  }
  document.getElementById('startOverBtn').addEventListener('click', startOverFlow);

  // Find B-Roll Moments button
  const findBrollBtn = document.getElementById('findBrollBtn');
  findBrollBtn.addEventListener('click', () => triggerStockBroll());

  // Caption preview / burn font size dropdown (burn px; preview scales it).
  const fontSizeSlider = document.getElementById('captionFontSizeSlider');

  // Snap an arbitrary px value (legacy slider persistence, tests) to the
  // nearest dropdown option so select.value never silently becomes ''.
  function _snapSizeOption(sel, px) {
    const vals = Array.from(sel.options).map(o => parseInt(o.value, 10));
    return vals.reduce((a, b) => Math.abs(b - px) < Math.abs(a - px) ? b : a, vals[0]);
  }

  fontSizeSlider.addEventListener('input', () => {
    captionFontSize = parseInt(fontSizeSlider.value, 10) || 48;
    localStorage.setItem('captionFontSize', captionFontSize);
    updatePreviewCaption();
  });
  fontSizeSlider.addEventListener('change', () => {
    captionFontSize = parseInt(fontSizeSlider.value, 10) || 48;
    localStorage.setItem('captionFontSize', captionFontSize);
    updatePreviewCaption();
  });

  (function initPreviewFontSize() {
    const saved = parseInt(localStorage.getItem('captionFontSize') || '48', 10);
    captionFontSize = _snapSizeOption(fontSizeSlider, saved);
    fontSizeSlider.value = captionFontSize;
  })();

  // ── Caption styling (hook-parity): text/outline colours + background box ──
  // Flows through /preview_frame + /burn (build_caption_ass) for WYSIWYG.
  function _captionStylePayload() {
    return {
      font_color:   document.getElementById('capFontColor')?.value || '#FFFFFF',
      border_color: document.getElementById('capBorderColor')?.value || '#000000',
      border_size:  parseInt(document.getElementById('capBorderSize')?.value || '2', 10),
      bg_color:     document.getElementById('capBgColor')?.value || '#000000',
      bg_opacity:   parseInt(document.getElementById('capBgOpacity')?.value || '0', 10) / 100,
      mode:         document.getElementById('capStyleMode')?.value || 'classic',
      highlight_color: document.getElementById('capHighlightColor')?.value || '#FFD400',
      progress_bar:    !!document.getElementById('capProgressBar')?.checked,
      progress_color:  document.getElementById('capProgressColor')?.value || '#C26D4B',
      auto_zoom:       !!document.getElementById('fxAutoZoom')?.checked,
      zoom_strength:   document.getElementById('fxZoomStrength')?.value || 'medium',
    };
  }
  // The highlight color only means something in karaoke mode - show it there.
  function _syncStyleModeUI() {
    const row = document.getElementById('capHighlightRow');
    if (row) row.style.display = _capMode() === 'karaoke' ? 'flex' : 'none';
    _syncProgressBarPreview();
    _syncZoomPreview();
    _renderZoomList();
    _refreshColorSwatches();
  }

  // ── Auto punch-in zoom (Effects tab) ──────────────────────────────────────
  // Windows are computed HERE and ride the burn body as caption_style.zooms -
  // explicit [start, end] pairs the server renders verbatim (the hook.lines
  // contract), so preview and burn can never disagree on WHERE the punches
  // land. Heuristic, deliberately conservative: punch in at captions that
  // start after a real speech pause (sentence starts), at most one per
  // ZOOM_MIN_GAP seconds (≤4/min), snap in and out (the trendy cut, not a
  // smooth ramp).
  // The punch holds for the WHOLE caption it lands on (user request,
  // 2026-08-09): zoom in with the sentence, ease back out when it ends.
  // MAX_DUR is only a safety cap for unusually long lines. ZOOM_LEAD starts
  // the move half a second BEFORE the sentence (user request, 2026-08-09) -
  // the camera anticipates the emphasis instead of reacting to it, and the
  // total zoomed span grows by the same 0.5s.
  const ZOOM_MIN_GAP = 15, ZOOM_MIN_DUR = 1.0, ZOOM_MAX_DUR = 6.0, ZOOM_LEAD = 0.5;
  // Factors raised 2026-08-09 (the old 1.06-1.16 read as "nothing happened"
  // even on strong - field report). Keep in sync with _zoom_filters
  // (pipeline_fns.py). ZOOM_FOCUS_Y biases the punch toward the FACE: crop /
  // transform-origin center sits at 30% height, where a talking head lives -
  // mirrored by the server crop y=(ih-oh)*0.30 and /preview_frame.
  const ZOOM_FACTORS = { subtle: 1.10, medium: 1.18, strong: 1.28 };
  const ZOOM_FOCUS_Y = '50% 30%';
  // Smooth camera ramp (2026-08-09): ease in/out with a smoothstep, mirroring
  // _zoom_filters' zoompan expression - the same constants on both sides keep
  // the CSS preview, the exact still (which receives the RAMPED factor at the
  // paused t) and the burn moving in step. The burn additionally centers on
  // the DETECTED face; the instant CSS preview keeps the fixed upper-third
  // origin (no face detector in the browser) - the exact frame corrects it.
  const ZOOM_RAMP_IN = 0.45, ZOOM_RAMP_OUT = 0.35;
  function _computeZoomWindows(caps) {
    const wins = [];
    let lastStart = -Infinity, prevEnd = null;
    for (const c of (caps || [])) {
      const s = Number(c.start), e = Number(c.end);
      if (!isFinite(s) || !isFinite(e) || e <= s) continue;
      const sentenceStart = prevEnd == null || s - prevEnd >= 0.35;
      prevEnd = e;
      // Continuous speech (no pauses) still gets an occasional punch, just at
      // a longer interval, so flat monologues aren't zoomless.
      const minGap = sentenceStart ? ZOOM_MIN_GAP : ZOOM_MIN_GAP * 1.5;
      if (s - lastStart < minGap) continue;
      const dur = Math.max(ZOOM_MIN_DUR, Math.min(ZOOM_MAX_DUR, e - s));
      wins.push([+Math.max(0, s - ZOOM_LEAD).toFixed(3), +(s + dur).toFixed(3)]);
      lastStart = s;
    }
    return wins;
  }
  let _zoomWinCache = { sig: null, wins: [] };
  // Moments the user tapped OFF in the Effects list (keys = window start,
  // 3-decimal string). Session-scoped: windows recompute deterministically,
  // so a re-edit simply starts with everything on again.
  const _zoomOff = new Set();
  function _zoomWindows() {
    if (!document.getElementById('fxAutoZoom')?.checked) return [];
    const caps = (typeof captionsData !== 'undefined' && captionsData) || [];
    const sig = caps.map(c => (+c.start).toFixed(2) + '-' + (+c.end).toFixed(2)).join(',');
    if (sig !== _zoomWinCache.sig) {
      _zoomWinCache = { sig, wins: _computeZoomWindows(caps) };
      _renderZoomList();
    }
    return _zoomWinCache.wins.filter(w => !_zoomOff.has(w[0].toFixed(3)));
  }
  // The Effects-tab moment list: every computed punch-in as a tappable chip
  // (time + the caption it lands on); tapping toggles that moment off/on.
  function _renderZoomList() {
    const el = document.getElementById('zoomList');
    if (!el) return;
    const on = !!document.getElementById('fxAutoZoom')?.checked;
    const wins = on ? _zoomWinCache.wins : [];
    if (!wins.length) { el.style.display = 'none'; el.innerHTML = ''; return; }
    el.style.display = 'flex';
    el.innerHTML = '';
    const caps = (typeof captionsData !== 'undefined' && captionsData) || [];
    wins.forEach(([s]) => {
      const key = s.toFixed(3);
      // The window starts ZOOM_LEAD before its anchor caption - look the
      // caption up at the anchor, tolerating the t=0 clamp.
      const cap = caps.find(c => +c.start >= s - 0.05 && +c.start <= s + ZOOM_LEAD + 0.05);
      const words = String((cap && cap.text) || '').replace(/\\N/g, ' ').split(/\s+/).filter(Boolean);
      const snippet = words.slice(0, 4).join(' ') + (words.length > 4 ? '…' : '');
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'kw-chip zoom-chip' + (_zoomOff.has(key) ? ' off' : '');
      chip.textContent = formatTime(Math.round(s)) + (snippet ? ' · ' + snippet : '');
      chip.title = t('fx.zoomChipTip');
      chip.addEventListener('click', () => {
        if (_zoomOff.has(key)) _zoomOff.delete(key); else _zoomOff.add(key);
        chip.classList.toggle('off', _zoomOff.has(key));
        _syncZoomPreview();
      });
      el.appendChild(chip);
    });
  }
  function _zoomFactorAt(t) {
    const wins = _zoomWindows();
    for (const [s, e] of wins) {
      if (t >= s && t <= e) {
        const F = ZOOM_FACTORS[document.getElementById('fxZoomStrength')?.value] || 1.18;
        const ri = Math.min(ZOOM_RAMP_IN, (e - s) / 2), ro = Math.min(ZOOM_RAMP_OUT, (e - s) / 2);
        const u = Math.max(0, Math.min(1, (t - s) / ri, (e - t) / ro));
        const p = u * u * (3 - 2 * u);   // smoothstep
        return 1 + (F - 1) * p;
      }
    }
    return 1;
  }
  // Instant CSS punch-in on the paused/playing video (clipped by playerWrap's
  // overflow:hidden). The exact still gets the same zoom server-side.
  function _syncZoomPreview(t) {
    const vid = document.getElementById('cutVideo');
    if (!vid) return;
    const z = _zoomFactorAt(t != null ? t : (vid.currentTime || 0));
    const tr = z > 1.001 ? 'scale(' + (+z.toFixed(4)) + ')' : '';
    if (vid.style.transformOrigin !== ZOOM_FOCUS_Y) vid.style.transformOrigin = ZOOM_FOCUS_Y;
    if (vid.style.transform !== tr) vid.style.transform = tr;
  }

  // Mirror of the burned ASS progress bar on the moving preview.
  function _syncProgressBarPreview() {
    const pb = document.getElementById('playerProgressBurn');
    const vid = document.getElementById('cutVideo');
    if (!pb) return;
    const on = !!document.getElementById('capProgressBar')?.checked;
    const dur = vid && vid.duration;
    if (on && dur > 0) {
      pb.style.display = 'block';
      pb.style.width = ((vid.currentTime || 0) / dur * 100) + '%';
      pb.style.background = document.getElementById('capProgressColor')?.value || '#C26D4B';
    } else {
      pb.style.display = 'none';
    }
  }

  // ── One-tap caption style presets ─────────────────────────────────────────
  // A preset is a full caption_style payload (+ font). Applying one drives the
  // SAME controls the user can then tweak - no second styling pathway.
  const CAPTION_PRESETS = [
    { id: 'clean',   nameKey: 'preset.clean',   font: 'Heebo',
      style: { font_color: '#FFFFFF', border_color: '#000000', border_size: 2,
               bg_color: '#000000', bg_opacity: 0, mode: 'classic', highlight_color: '#FFD400' } },
    { id: 'karaoke', nameKey: 'preset.karaoke', font: 'Heebo',
      style: { font_color: '#FFFFFF', border_color: '#000000', border_size: 2,
               bg_color: '#000000', bg_opacity: 0, mode: 'karaoke', highlight_color: '#FFD400' } },
    { id: 'wordpop', nameKey: 'preset.wordpop', font: 'Secular One',
      style: { font_color: '#FFFFFF', border_color: '#000000', border_size: 4,
               bg_color: '#000000', bg_opacity: 0, mode: 'word', highlight_color: '#FFD400' } },
    { id: 'news',    nameKey: 'preset.news',    font: 'Frank Ruhl Libre',
      style: { font_color: '#FFFFFF', border_color: '#000000', border_size: 0,
               bg_color: '#000000', bg_opacity: 0.6, mode: 'classic', highlight_color: '#FFD400' } },
    { id: 'viral',   nameKey: 'preset.viral',   font: 'Rubik',
      style: { font_color: '#FFE45C', border_color: '#000000', border_size: 3,
               bg_color: '#000000', bg_opacity: 0, mode: 'classic', highlight_color: '#FFFFFF' } },
    { id: 'invert',  nameKey: 'preset.invert',  font: 'Assistant',
      style: { font_color: '#1A1A1A', border_color: '#FFFFFF', border_size: 0,
               bg_color: '#F4ECE0', bg_opacity: 0.85, mode: 'classic', highlight_color: '#C26D4B' } },
  ];
  function applyCaptionPreset(p) {
    _applyCaptionStyleValues(p.style);
    if (p.font) {
      captionFont = p.font;
      const fs = document.getElementById('fontSelect'); if (fs) fs.value = p.font;
      _ensureCaptionFont(p.font);
    }
    if (p.size) {
      const ss = document.getElementById('captionFontSizeSlider');
      if (ss) {
        captionFontSize = _snapSizeOption(ss, parseInt(p.size, 10) || 48);
        ss.value = String(captionFontSize);
        try { localStorage.setItem('captionFontSize', String(captionFontSize)); } catch (_) {}
      }
    }
    if (p.margin_v != null && isFinite(p.margin_v)) captionMarginPct = p.margin_v;
    try { localStorage.setItem('captionStyle', JSON.stringify(_captionStylePayload())); } catch (_) {}
    updatePreviewCaption();
    _markActivePreset(p.id);
  }
  // ── App-styled color picker ───────────────────────────────────────────────
  // The native <input type=color> opens the OS dialog, which clashes with the
  // app's look. Each registered input gets a compact swatch button that opens
  // a shared popover of curated tiles (brand palette + caption-pop colors);
  // "custom" falls through to the native picker. Tiles set the input's value
  // and dispatch 'input', so every existing handler keeps working unchanged.
  const COLOR_SWATCHES = [
    // boho brand palette
    '#C26D4B', '#7E8E6A', '#A8B694', '#E8DFD3', '#F4ECE0', '#5C5346',
    // essentials
    '#FFFFFF', '#DDDDDD', '#888888', '#333333', '#1A1A1A', '#000000',
    // caption-pop suggestions
    '#FFD400', '#FFE45C', '#FF9500', '#FF3B30', '#FF2D95', '#A78BFA',
    '#00C2FF', '#7FDBFF', '#34D399', '#00E676', '#F5C518', '#FF6B6B',
  ];
  const _COLOR_INPUT_IDS = [
    'capFontColor', 'capBorderColor', 'capBgColor', 'capHighlightColor',
    'capProgressColor', 'hookFontColor', 'hookBgColor', 'hookBorderColor',
  ];
  let _colorPopTarget = null;
  function _colorPopover() {
    let pop = document.getElementById('colorPopover');
    if (pop) return pop;
    pop = document.createElement('div');
    pop.id = 'colorPopover';
    pop.className = 'color-popover';
    const grid = document.createElement('div');
    grid.className = 'color-popover-grid';
    COLOR_SWATCHES.forEach(hex => {
      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'color-tile';
      tile.dataset.hex = hex;
      tile.style.background = hex;
      tile.addEventListener('click', () => {
        if (!_colorPopTarget) return;
        _colorPopTarget.value = hex;
        _colorPopTarget.dispatchEvent(new Event('input',  { bubbles: true }));
        _colorPopTarget.dispatchEvent(new Event('change', { bubbles: true }));
        _refreshColorSwatches();
        _closeColorPopover();
      });
      grid.appendChild(tile);
    });
    const custom = document.createElement('button');
    custom.type = 'button';
    custom.className = 'color-tile color-tile-custom';
    custom.title = 'custom';
    custom.addEventListener('click', (e) => {
      // In-app custom creator, NOT the OS dialog (the native picker clashed
      // with the app design - removed 2026-08-09).
      e.stopPropagation();
      const panel = document.getElementById('colorCustomPanel');
      const opening = panel.style.display === 'none';
      panel.style.display = opening ? 'block' : 'none';
      if (opening) _customFromHex(_colorPopTarget ? _colorPopTarget.value : '#FFFFFF');
      _positionColorPopover();   // the popover just changed height - re-clamp to the screen
    });
    grid.appendChild(custom);
    pop.appendChild(grid);
    pop.appendChild(_buildCustomPanel());
    document.body.appendChild(pop);
    document.addEventListener('click', (e) => {
      if (pop.classList.contains('open') && !pop.contains(e.target)
          && !(e.target.closest && e.target.closest('.color-swatch-btn'))) _closeColorPopover();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') _closeColorPopover(); });
    return pop;
  }
  function _closeColorPopover() {
    const pop = document.getElementById('colorPopover');
    if (pop) pop.classList.remove('open');
    _colorPopTarget = null;
  }
  // ── App-styled custom color creator ───────────────────────────────────────
  // Lives inside the color popover (revealed by the "custom" tile): an HSV
  // saturation/brightness canvas + hue slider + hex field. Applies LIVE to
  // the target input through the SAME 'input'/'change' event path the tiles
  // use, so every existing handler keeps working unchanged.
  let _hsvH = 0, _hsvS = 1, _hsvV = 1;
  function _hsvToHex(h, s, v) {
    const f = (n) => {
      const k = (n + h / 60) % 6;
      const c = v - v * s * Math.max(0, Math.min(k, 4 - k, 1));
      return Math.round(c * 255).toString(16).padStart(2, '0');
    };
    return '#' + f(5) + f(3) + f(1);
  }
  function _hexToHsv(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim());
    if (!m) return null;
    const n = parseInt(m[1], 16);
    const r = (n >> 16) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
    let h = 0;
    if (d) {
      if (max === r) h = 60 * (((g - b) / d) % 6);
      else if (max === g) h = 60 * ((b - r) / d + 2);
      else h = 60 * ((r - g) / d + 4);
    }
    if (h < 0) h += 360;
    return { h, s: max ? d / max : 0, v: max };
  }
  function _buildCustomPanel() {
    const panel = document.createElement('div');
    panel.id = 'colorCustomPanel';
    panel.className = 'color-custom-panel';
    panel.style.display = 'none';
    const sv = document.createElement('canvas');
    sv.className = 'color-sv';
    sv.width = 220; sv.height = 130;
    const hue = document.createElement('input');
    hue.type = 'range'; hue.min = '0'; hue.max = '360'; hue.step = '1';
    hue.className = 'color-hue';
    hue.setAttribute('aria-label', 'hue');
    const row = document.createElement('div');
    row.className = 'color-hex-row';
    const chip = document.createElement('span');
    chip.className = 'color-hex-chip';
    const hex = document.createElement('input');
    hex.type = 'text'; hex.className = 'color-hex'; hex.maxLength = 7;
    hex.id = 'colorCustomHex';
    hex.setAttribute('spellcheck', 'false');
    row.append(chip, hex);
    panel.append(sv, hue, row);

    const ctx = sv.getContext('2d');
    const paint = () => {
      const w = sv.width, h = sv.height;
      ctx.fillStyle = `hsl(${_hsvH}, 100%, 50%)`;
      ctx.fillRect(0, 0, w, h);
      let g = ctx.createLinearGradient(0, 0, w, 0);
      g.addColorStop(0, '#fff'); g.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, '#000');
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
      const x = _hsvS * w, y = (1 - _hsvV) * h;
      ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 7.5, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(0,0,0,0.55)'; ctx.lineWidth = 1; ctx.stroke();
    };
    const sync = (apply, keepHexText) => {
      const hexVal = _hsvToHex(_hsvH, _hsvS, _hsvV);
      if (!keepHexText) hex.value = hexVal;
      chip.style.background = hexVal;
      hue.value = String(Math.round(_hsvH));
      paint();
      if (apply && _colorPopTarget) {
        _colorPopTarget.value = hexVal;
        _colorPopTarget.dispatchEvent(new Event('input',  { bubbles: true }));
        _colorPopTarget.dispatchEvent(new Event('change', { bubbles: true }));
        _refreshColorSwatches();
        document.querySelectorAll('#colorPopover .color-tile').forEach(tl =>
          tl.classList.toggle('selected', (tl.dataset.hex || '').toUpperCase() === hexVal.toUpperCase()));
      }
    };
    const pick = (ev) => {
      const r = sv.getBoundingClientRect();
      _hsvS = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
      _hsvV = Math.max(0, Math.min(1, 1 - (ev.clientY - r.top) / r.height));
      sync(true);
    };
    sv.addEventListener('pointerdown', (ev) => {
      ev.preventDefault();
      try { sv.setPointerCapture(ev.pointerId); } catch (_) {}
      pick(ev);
    });
    sv.addEventListener('pointermove', (ev) => { if (ev.buttons) pick(ev); });
    hue.addEventListener('input', () => { _hsvH = parseFloat(hue.value) || 0; sync(true); });
    hex.addEventListener('input', () => {
      const p = _hexToHsv(hex.value);
      if (!p) return;   // partial typing - wait for a full #rrggbb
      _hsvH = p.h; _hsvS = p.s; _hsvV = p.v;
      sync(true, true);
    });
    panel._sync = sync;   // used by _customFromHex when the panel opens
    return panel;
  }
  function _customFromHex(hexVal) {
    const p = _hexToHsv(hexVal) || { h: 0, s: 0, v: 1 };
    _hsvH = p.h; _hsvS = p.s; _hsvV = p.v;
    const panel = document.getElementById('colorCustomPanel');
    if (panel && panel._sync) panel._sync(false);
  }
  let _colorPopAnchor = null;
  // Positioning is its own function because the popover CHANGES SIZE while
  // open (the custom panel expands it): re-run after any resize or the
  // bottom half ends up past the screen edge (field report, 2026-08-09).
  function _positionColorPopover() {
    const pop = document.getElementById('colorPopover');
    const anchorBtn = _colorPopAnchor;
    if (!pop || !anchorBtn || !pop.classList.contains('open')) return;
    const r = anchorBtn.getBoundingClientRect();
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    let left = Math.min(Math.max(8, r.left), window.innerWidth - pw - 8);
    let top = r.bottom + 6;
    if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 6);
    // Fits on neither side of the anchor? Pin to the bottom edge; the
    // popover's own max-height/scroll takes it from there.
    if (top + ph > window.innerHeight - 8) top = Math.max(8, window.innerHeight - ph - 8);
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }
  function _openColorPopover(input, anchorBtn) {
    const pop = _colorPopover();
    _colorPopTarget = input;
    _colorPopAnchor = anchorBtn;
    // Fresh open starts on the tile grid; the custom panel re-opens on demand.
    { const cp = document.getElementById('colorCustomPanel'); if (cp) cp.style.display = 'none'; }
    const cur = (input.value || '').toUpperCase();
    pop.querySelectorAll('.color-tile').forEach(tl =>
      tl.classList.toggle('selected', (tl.dataset.hex || '').toUpperCase() === cur));
    pop.classList.add('open');
    _positionColorPopover();
  }
  function _refreshColorSwatches() {
    document.querySelectorAll('.color-swatch-btn').forEach(btn => {
      const inp = document.getElementById(btn.dataset.for || '');
      const chip = btn.querySelector('.csb-chip');
      if (inp && chip) chip.style.background = inp.value;
    });
  }
  (function initColorSwatches() {
    _COLOR_INPUT_IDS.forEach(id => {
      const inp = document.getElementById(id);
      if (!inp) return;
      inp.classList.add('color-input-hidden');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'color-swatch-btn';
      btn.dataset.for = id;
      btn.id = id + 'Swatch';
      const chip = document.createElement('span');
      chip.className = 'csb-chip';
      chip.style.background = inp.value;
      btn.appendChild(chip);
      // The hidden input's flex-layout margin (e.g. the progress row's
      // margin-inline-start:auto end-pin) dies with display:none - carry it
      // over so the swatch sits where the input did.
      if (inp.style.marginInlineStart) btn.style.marginInlineStart = inp.style.marginInlineStart;
      btn.addEventListener('click', (e) => {
        // Inside a <label>: without preventDefault the label forwards the
        // click to its input and the OS dialog opens anyway.
        e.preventDefault();
        e.stopPropagation();
        _openColorPopover(inp, btn);
      });
      inp.insertAdjacentElement('afterend', btn);
      inp.addEventListener('input', _refreshColorSwatches);
    });
  })();

  // Collapsible caption-design card. ALWAYS opens by default (user directive,
  // 2026-08-10 - the font selector is a primary control): the collapse is
  // session-scoped only, and every fresh editor open re-expands it
  // (showCaptionEditor calls toggleCapDesign(true)).
  function toggleCapDesign(force) {
    const head = document.getElementById('capDesignHead');
    const body = document.getElementById('capDesignBody');
    if (!head || !body) return;
    const open = force != null ? !!force : body.style.display === 'none';
    body.style.display = open ? 'block' : 'none';
    head.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  window.toggleCapDesign = toggleCapDesign;
  toggleCapDesign(true);

  // ── Settings profiles (2026-08-09) ────────────────────────────────────────
  // One named snapshot of EVERYTHING configurable: options toggles, caption
  // look (font/size/style), effects (progress bar, auto-zoom) and hook design.
  // Stored on the ACCOUNT (/profiles, [{name, data}] + default name, bounded
  // server-side) so profiles sync web ↔ Android and ride the daily backup.
  // The starred default auto-applies once per boot - never mid-editor.
  let _profiles = [], _profileDefault = null, _profilesLoaded = false, _activeProfile = null;
  async function loadProfiles() {
    try {
      const resp = await apiFetch(`${API_BASE}/profiles`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const d = await resp.json();
      _profiles = Array.isArray(d.profiles) ? d.profiles : [];
      _profileDefault = d.default || null;
      _renderProfiles();
      if (!_profilesLoaded && _profileDefault && !videoKey) {
        const p = _profiles.find(x => x.name === _profileDefault);
        if (p) applyProfile(p, { silent: true });
      }
      _profilesLoaded = true;
    } catch (_) { /* best-effort - profiles are a convenience */ }
  }
  let _profileSaveTimer = null;
  function _saveProfiles() {
    clearTimeout(_profileSaveTimer);
    _profileSaveTimer = setTimeout(async () => {
      try {
        await apiFetch(`${API_BASE}/profiles`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ profiles: _profiles, default: _profileDefault }),
        });
      } catch (_) {}
    }, 400);
  }
  function _profileData() {
    const cs = _captionStylePayload();
    const val = id => document.getElementById(id)?.value;
    return {
      options: {
        cut: !!document.getElementById('cutSilences')?.checked,
        aggr: parseInt(aggrSlider.value, 10) || 3,
        captions: !!document.getElementById('burnCaptions')?.checked,
        enhance_audio: !!document.getElementById('enhanceAudio')?.checked,
        enhance_video: _enhanceVideoMode(),
        auto_broll: !!document.getElementById('autoBroll')?.checked,
        auto_hook: !!document.getElementById('autoHook')?.checked,
      },
      font: captionFont,
      font_size: captionFontSize,
      margin_v: captionMarginPct,
      caption_style: cs,
      hook: {
        font: val('hookFont'), font_color: val('hookFontColor'),
        bg_color: val('hookBgColor'), bg_opacity: val('hookBgOpacity'),
        border_color: val('hookBorderColor'), border_size: val('hookBorderSize'),
        size: val('hookFontSize'),
      },
    };
  }
  function applyProfile(p, opts = {}) {
    const d = p.data || {};
    const o = d.options || {};
    const setChk = (id, v) => { const e = document.getElementById(id); if (e && v != null) e.checked = !!v; };
    setChk('cutSilences', o.cut);
    setChk('burnCaptions', o.captions);
    setChk('enhanceAudio', o.enhance_audio);
    setChk('autoBroll', o.auto_broll);
    setChk('autoHook', o.auto_hook);
    if (o.aggr) { aggrSlider.value = o.aggr; aggrSlider.dispatchEvent(new Event('input', { bubbles: true })); }
    if (o.enhance_video) {
      const r = document.querySelector(`input[name="enhanceVideo"][value="${o.enhance_video}"]`);
      if (r && !r.checked) { r.checked = true; r.dispatchEvent(new Event('change', { bubbles: true })); }
    }
    _syncCaptionChildren();
    updateTimeEstimate();
    // Caption look drives the SAME controls the user would ('in' guards keep
    // any field an older profile doesn't carry).
    if (d.caption_style) _applyCaptionStyleValues(d.caption_style);
    if (d.font) {
      captionFont = d.font;
      const fs = document.getElementById('fontSelect'); if (fs) fs.value = d.font;
      _ensureCaptionFont(d.font);
    }
    if (d.font_size) {
      const ss = document.getElementById('captionFontSizeSlider');
      if (ss) { captionFontSize = _snapSizeOption(ss, parseInt(d.font_size, 10) || 48); ss.value = String(captionFontSize); }
      try { localStorage.setItem('captionFontSize', String(d.font_size)); } catch (_) {}
    }
    if (d.margin_v != null && isFinite(d.margin_v)) captionMarginPct = d.margin_v;
    try { localStorage.setItem('captionStyle', JSON.stringify(_captionStylePayload())); } catch (_) {}
    // Hook design: set values only (no events - the hook preview redraws when
    // its UI is next active; firing draw handlers with no hook selected is
    // the editor's business, not the profile's).
    const hk = d.hook || {};
    const setVal = (id, v) => { const e = document.getElementById(id); if (e && v != null) e.value = v; };
    setVal('hookFont', hk.font);
    setVal('hookFontColor', hk.font_color);
    setVal('hookBgColor', hk.bg_color);
    setVal('hookBgOpacity', hk.bg_opacity);
    setVal('hookBorderColor', hk.border_color);
    setVal('hookBorderSize', hk.border_size);
    setVal('hookFontSize', hk.size);
    { const e = document.getElementById('hookBgOpacityVal'); if (e && hk.bg_opacity != null) e.textContent = hk.bg_opacity + '%'; }
    { const e = document.getElementById('hookBorderSizeVal'); if (e && hk.border_size != null) e.textContent = hk.border_size + 'px'; }
    _refreshColorSwatches();
    if (videoKey) { try { updatePreviewCaption(); } catch (_) {} }
    _activeProfile = p.name;
    _renderProfiles();
    if (!opts.silent) celebrateToast(t('profile.applied', { name: p.name }));
  }
  function _renderProfiles() {
    const row = document.getElementById('profileRow');
    const box = document.getElementById('profileChips');
    if (!row || !box) return;
    row.style.display = '';
    box.innerHTML = '';
    _profiles.forEach(p => {
      const chip = document.createElement('div');
      chip.className = 'profile-chip' + (p.name === _activeProfile ? ' active' : '');
      const name = document.createElement('button');
      name.type = 'button';
      name.className = 'profile-chip-name';
      name.textContent = p.name;
      name.addEventListener('click', () => applyProfile(p));
      const star = document.createElement('button');
      star.type = 'button';
      star.className = 'profile-chip-star' + (p.name === _profileDefault ? ' on' : '');
      star.innerHTML = ICON.star;
      star.title = t('profile.defaultTip');
      star.addEventListener('click', () => {
        _profileDefault = _profileDefault === p.name ? null : p.name;
        _saveProfiles();
        _renderProfiles();
      });
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'profile-chip-del';
      del.setAttribute('aria-label', 'delete');
      del.innerHTML = ICON.x;
      del.addEventListener('click', async () => {
        const ok = await showConfirmModal(t('profile.deleteTitle'), p.name, t('confirm.delete'));
        if (!ok) return;
        _profiles = _profiles.filter(x => x !== p);
        if (_profileDefault === p.name) _profileDefault = null;
        if (_activeProfile === p.name) _activeProfile = null;
        _saveProfiles();
        _renderProfiles();
      });
      chip.append(name, star, del);
      box.appendChild(chip);
    });
    if (_profiles.length < 6) {
      const add = document.createElement('button');
      add.type = 'button';
      add.className = 'profile-chip-add';
      add.id = 'profileAdd';
      add.textContent = '+ ' + t('profile.save');
      add.addEventListener('click', () => {
        const sr = document.getElementById('profileSaveRow');
        if (sr) { sr.style.display = 'flex'; document.getElementById('profileName')?.focus(); }
      });
      box.appendChild(add);
    } else {
      // At the cap the save affordance disappears entirely - the hint says why.
      const hint = document.createElement('p');
      hint.className = 'profile-hint';
      hint.textContent = t('profile.maxHint');
      box.appendChild(hint);
      const sr = document.getElementById('profileSaveRow');
      if (sr) sr.style.display = 'none';
    }
  }
  document.getElementById('profileSaveBtn')?.addEventListener('click', () => {
    const nameEl = document.getElementById('profileName');
    const name = (nameEl?.value || '').trim().slice(0, 40);
    if (!name) { nameEl?.focus(); return; }
    if (_profiles.some(p => p.name === name)) {
      celebrateToast(t('profile.nameExists', { name }), { kind: 'error' });
      nameEl?.focus(); nameEl?.select();
      return;
    }
    _profiles.unshift({ name, data: _profileData() });
    _profiles = _profiles.slice(0, 6);
    _activeProfile = name;
    if (nameEl) nameEl.value = '';
    { const sr = document.getElementById('profileSaveRow'); if (sr) sr.style.display = 'none'; }
    _saveProfiles();
    _renderProfiles();
    celebrateToast(t('profile.saved', { name }));
  });

  function _markActivePreset(id) {
    // Guard on a real id: an undefined id would match every chip missing
    // dataset.preset (undefined === undefined) and light ALL of them.
    document.querySelectorAll('.cap-preset-chip').forEach(ch =>
      ch.classList.toggle('active', !!id && ch.dataset.preset === id));
  }
  // User-saved caption styles (localStorage), shown on the "My styles" view.
  function _getUserPresets() {
    try {
      const list = JSON.parse(localStorage.getItem('captionUserPresets') || '[]');
      // Pre-2026-08-09 saves had no id - selecting one then marked every
      // saved chip active. Backfill once and persist.
      let migrated = false;
      list.forEach((p, i) => { if (!p.id) { p.id = 'u' + Date.now() + '_' + i; migrated = true; } });
      if (migrated) _saveUserPresets(list);
      return list;
    } catch { return []; }
  }
  function _saveUserPresets(list) {
    try { localStorage.setItem('captionUserPresets', JSON.stringify(list)); } catch (_) {}
  }
  let _presetView = 'system';   // 'system' | 'mine'

  function _presetChip(p, label, onApply) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'cap-preset-chip';
    if (p.id) chip.dataset.preset = p.id;
    const sample = document.createElement('span');
    sample.className = 'cap-preset-sample';
    sample.textContent = 'אבג';
    sample.style.fontFamily = `'${p.font}', sans-serif`;
    sample.style.color = p.style.font_color;
    if (p.style.bg_opacity > 0) sample.style.background = _hexToRgba(p.style.bg_color, p.style.bg_opacity);
    if (p.style.border_size > 0) sample.style.textShadow = _outlineShadows(1.2, p.style.border_color);
    const lbl = document.createElement('span');
    lbl.className = 'cap-preset-name';
    lbl.textContent = label;
    chip.append(sample, lbl);
    chip.addEventListener('click', onApply);
    return chip;
  }

  function _buildPresetRow() {
    const row = document.getElementById('capPresetRow');
    if (!row) return;
    row.innerHTML = '';
    const saveRow = document.getElementById('capPresetSaveRow');
    if (saveRow) saveRow.style.display = 'none';
    if (_presetView === 'system') {
      CAPTION_PRESETS.forEach(p =>
        row.appendChild(_presetChip(p, t(p.nameKey), () => applyCaptionPreset(p))));
      return;
    }
    // "My styles": saved chips (deletable) + a save-current chip.
    const mine = _getUserPresets();
    mine.forEach((p, i) => {
      const chip = _presetChip(p, p.name, () => applyCaptionPreset(p));
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'cap-preset-del';
      del.setAttribute('aria-label', 'delete');
      del.innerHTML = ICON.x;
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        const ok = await showConfirmModal(t('preset.tabMine'), p.name, t('confirm.delete'));
        if (!ok) return;
        const list = _getUserPresets();
        list.splice(i, 1);
        _saveUserPresets(list);
        _buildPresetRow();
      });
      chip.appendChild(del);
      row.appendChild(chip);
    });
    if (!mine.length) {
      const none = document.createElement('p');
      none.style.cssText = 'font-size:0.78rem;color:var(--muted);margin:auto 0;align-self:center';
      none.textContent = t('preset.noneSaved');
      row.appendChild(none);
    }
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'cap-preset-chip';
    add.id = 'capPresetAdd';
    const plus = document.createElement('span');
    plus.className = 'cap-preset-sample';
    plus.textContent = '+';
    const albl = document.createElement('span');
    albl.className = 'cap-preset-name';
    albl.textContent = t('preset.save');
    add.append(plus, albl);
    add.addEventListener('click', () => {
      const sr = document.getElementById('capPresetSaveRow');
      if (sr) { sr.style.display = 'flex'; document.getElementById('capPresetName')?.focus(); }
    });
    row.appendChild(add);
  }
  function _switchPresetView(view) {
    _presetView = view;
    document.getElementById('capPresetTabSystem')?.classList.toggle('active', view === 'system');
    document.getElementById('capPresetTabMine')?.classList.toggle('active', view === 'mine');
    _buildPresetRow();
  }
  document.getElementById('capPresetTabSystem')?.addEventListener('click', () => _switchPresetView('system'));
  document.getElementById('capPresetTabMine')?.addEventListener('click', () => _switchPresetView('mine'));
  document.getElementById('capPresetSaveBtn')?.addEventListener('click', () => {
    const nameEl = document.getElementById('capPresetName');
    const name = (nameEl?.value || '').trim();
    if (!name) { nameEl?.focus(); return; }
    const cs = _captionStylePayload();
    const list = _getUserPresets();
    list.unshift({
      id: 'u' + Date.now(),
      name,
      font: captionFont,
      size: captionFontSize,
      margin_v: captionMarginPct,   // caption position rides the saved style
      // LOOK fields only - orthogonal toggles (progress bar, zoom) are
      // deliberately not part of a saved style.
      style: { font_color: cs.font_color, border_color: cs.border_color,
               border_size: cs.border_size, bg_color: cs.bg_color,
               bg_opacity: cs.bg_opacity, mode: cs.mode,
               highlight_color: cs.highlight_color },
    });
    _saveUserPresets(list.slice(0, 24));
    if (nameEl) nameEl.value = '';
    _buildPresetRow();
  });
  _buildPresetRow();
  document.addEventListener('langchange', _buildPresetRow);
  function _capMode() { return document.getElementById('capStyleMode')?.value || 'classic'; }
  // Word-by-word mode: per-word display cues for one caption line. MIRRORS
  // _word_events in build_caption_ass (pipeline_fns.py) - same proportional
  // redistribution fallback and the same 0.15s anti-strobe merge, so the
  // moving preview groups words exactly like the burn will.
  function _wordCuesFor(cap) {
    const text = String(cap.text || '').replace(/\\N|\n/g, ' ');
    if (cap.__cueText === text && cap.__cues) return cap.__cues;
    const toks = text.split(/\s+/).filter(Boolean);
    const start = +cap.start || 0, end = +cap.end || 0;
    let times = null;
    if (Array.isArray(cap.words) && cap.words.length === toks.length) {
      times = cap.words.map(w => [+w[0], +w[1]]);
      if (times.some(w => !isFinite(w[0]) || !isFinite(w[1]))) times = null;
    }
    if (!times) {
      const span = Math.max(0.001, end - start);
      const total = toks.reduce((a, w) => a + w.length, 0) || 1;
      let acc = start;
      times = toks.map(w => { const d = span * w.length / total; const r = [acc, acc + d]; acc += d; return r; });
    }
    const MIN = 0.15, cues = [];
    let i = 0;
    while (i < toks.length) {
      const s0 = i > 0 ? times[i][0] : start;
      let j = i, e0;
      for (;;) {
        e0 = (j + 1 < toks.length) ? times[j + 1][0] : end;
        if (e0 - s0 >= MIN || j + 1 >= toks.length) break;
        j++;
      }
      cues.push({ start: s0, end: Math.max(e0, s0 + 0.05), i0: i, i1: j,
                  text: toks.slice(i, j + 1).join(' ') });
      i = j + 1;
    }
    cap.__cueText = text; cap.__cues = cues;
    return cues;
  }
  // Karaoke moving preview: the classic-wrapped line with the current cue's
  // tokens wrapped in a colored span (color only - metric-neutral, like the
  // burn's inline \c tags).
  function _karaokeHTML(cap, q, displayW) {
    const wrapped = rewrapCaption(cap.text, displayW, _capBurnPx());
    const hl = document.getElementById('capHighlightColor')?.value || '#FFD400';
    let k = 0;
    return wrapped.split('\n').map(line =>
      line.split(/\s+/).filter(Boolean).map(w => {
        const html = (k >= q.i0 && k <= q.i1)
          ? '<span style="color:' + hl + '">' + _escapeHtml(w) + '</span>'
          : _escapeHtml(w);
        k++;
        return html;
      }).join(' ')
    ).join('\n');
  }
  function _wordCueAt(cap, t) {
    const cues = _wordCuesFor(cap);
    return cues.find(q => t >= q.start && t < q.end)
      || (t <= cap.start + 0.05 ? cues[0] : null)
      || (t >= cap.end - 0.05 ? cues[cues.length - 1] : null);
  }
  function _hexToRgba(hex, alpha) {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }
  // Build an outward text outline as a ring of hard text-shadows. Chosen over
  // -webkit-text-stroke (even with paint-order:stroke fill, which DOES work in
  // Chromium - verified by pixel probe): a text stroke is drawn per glyph with
  // MITER joins, so sharp Hebrew corners grow needle spikes and a fat stroke
  // from one letter bites into its neighbour's fill at tight spacing - the
  // "glitchy border" field report. Shadows paint behind ALL glyphs together
  // and the union of offset copies gives round joins, matching libass's
  // rounded outward border.
  function _outlineShadows(w, color) {
    if (!(w > 0)) return 'none';
    const s = [];
    const ring = (radius, count, phase) => {
      for (let i = 0; i < count; i++) {
        const a = (2 * Math.PI * i) / count + phase;
        s.push(`${(Math.cos(a) * radius).toFixed(2)}px ${(Math.sin(a) * radius).toFixed(2)}px 0 ${color}`);
      }
    };
    ring(w, Math.max(8, Math.ceil(w) * 4), 0);   // outer ring - more directions for fatter borders
    if (w > 2) ring(w * 0.6, 8, 0.39);           // inner ring closes coverage gaps
    return s.join(', ');
  }
  // Apply the style to the live caption overlay (scale = player px per burn px).
  function _applyCapStyleColors(el, scale) {
    const cs = _captionStylePayload();
    el.style.color = cs.font_color;
    el.style.paintOrder = '';
    el.style.webkitTextStroke = '';
    el.style.textShadow = cs.border_size > 0
      ? _outlineShadows(Math.max(1, cs.border_size * scale), cs.border_color) : 'none';
    if (cs.bg_opacity > 0) {
      el.style.background = _hexToRgba(cs.bg_color, cs.bg_opacity);
      el.style.padding = '0.14em 0.3em';
    } else {
      el.style.background = ''; el.style.padding = '';
    }
  }
  // ── Restore-to-default: caption styling + hook styling ──────────────────
  // Small reset buttons in both editors; confirmation first (a stray tap
  // shouldn't wipe a carefully tuned style). Text/selection are untouched.
  async function resetCaptionStyle() {
    const ok = await showConfirmModal(t('style.resetTitle'), t('style.resetCapBody'), t('style.resetOk'));
    if (!ok) return;
    captionFont = 'Heebo';
    { const fs = document.getElementById('fontSelect'); if (fs) fs.value = 'Heebo'; }
    captionFontSize = 48;
    fontSizeSlider.value = '48';
    captionMarginPct = 0.08;
    const defs = { capFontColor: '#FFFFFF', capBorderColor: '#000000', capBorderSize: 2,
                   capBgColor: '#000000', capBgOpacity: 0, capStyleMode: 'classic',
                   capHighlightColor: '#FFD400', capProgressColor: '#C26D4B' };
    for (const [id, v] of Object.entries(defs)) {
      const el = document.getElementById(id); if (el) el.value = v;
    }
    { const e = document.getElementById('capBorderSizeVal'); if (e) e.textContent = '2px'; }
    { const e = document.getElementById('capBgOpacityVal'); if (e) e.textContent = '0%'; }
    { const e = document.getElementById('capProgressBar'); if (e) e.checked = false; }
    { const e = document.getElementById('fxAutoZoom'); if (e) e.checked = false; }
    { const e = document.getElementById('fxZoomStrength'); if (e) e.value = 'medium'; }
    _syncStyleModeUI();
    try {
      localStorage.setItem('captionFontSize', '48');
      localStorage.removeItem('captionStyle');
    } catch (_) {}
    updatePreviewCaption();
  }
  async function resetHookStyle() {
    const ok = await showConfirmModal(t('style.resetTitle'), t('style.resetHookBody'), t('style.resetOk'));
    if (!ok) return;
    const defs = { hookFont: 'Heebo', hookFontColor: '#FFFFFF', hookBgColor: '#000000',
                   hookBgOpacity: 60, hookBorderColor: '#000000', hookBorderSize: 0,
                   hookFontSize: 100, hookPosition: 10, hookStartSec: 0, hookDurationSec: 3 };
    for (const [id, v] of Object.entries(defs)) {
      const el = document.getElementById(id); if (el) el.value = v;
    }
    { const e = document.getElementById('hookBgOpacityVal'); if (e) e.textContent = '60%'; }
    { const e = document.getElementById('hookBorderSizeVal'); if (e) e.textContent = '0px'; }
    _refreshColorSwatches();
    drawHookPreview();
    updatePlayerHook();
    _hideExactHook(); scheduleExactHook();
  }

  (function initCaptionStyle() {
    // Persist per device, like font + size.
    try {
      const saved = JSON.parse(localStorage.getItem('captionStyle') || 'null');
      if (saved) {
        const map = { capFontColor: saved.font_color, capBorderColor: saved.border_color,
                      capBorderSize: saved.border_size, capBgColor: saved.bg_color,
                      capBgOpacity: Math.round((saved.bg_opacity || 0) * 100),
                      capStyleMode: saved.mode || 'classic',
                      capHighlightColor: saved.highlight_color || '#FFD400',
                      capProgressColor: saved.progress_color || '#C26D4B' };
        for (const [id, v] of Object.entries(map)) {
          const el = document.getElementById(id);
          if (el && v != null) el.value = v;
        }
        const bsv = document.getElementById('capBorderSizeVal');
        if (bsv) bsv.textContent = (saved.border_size != null ? saved.border_size : 2) + 'px';
        const bov = document.getElementById('capBgOpacityVal');
        if (bov) bov.textContent = Math.round((saved.bg_opacity || 0) * 100) + '%';
        { const e = document.getElementById('capProgressBar'); if (e) e.checked = !!saved.progress_bar; }
        // auto_zoom is deliberately NOT restored (user directive, 2026-08-10):
        // the punch-in starts OFF for every video, so it is never applied
        // without a deliberate tap. Only the STRENGTH is remembered (a
        // preference, like a color). Explicit paths still set the toggle:
        // profiles and History re-edits go through _applyCaptionStyleValues.
        { const e = document.getElementById('fxZoomStrength'); if (e && saved.zoom_strength) e.value = saved.zoom_strength; }
      }
    } catch (_) {}
    const onEdit = () => {
      _markActivePreset(null);   // manual tweak - no longer exactly a preset
      _syncStyleModeUI();
      try { localStorage.setItem('captionStyle', JSON.stringify(_captionStylePayload())); } catch (_) {}
      updatePreviewCaption();
    };
    _syncStyleModeUI();
    ['capFontColor', 'capBorderColor', 'capBorderSize', 'capBgColor', 'capBgOpacity', 'capStyleMode', 'capHighlightColor', 'capProgressBar', 'capProgressColor', 'fxAutoZoom', 'fxZoomStrength'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input', onEdit);
      el.addEventListener('change', onEdit);
    });
  })();

  // Save a finished file through Android's system DownloadManager. It streams
  // directly into public Downloads and owns the familiar progress/completion
  // notification. The Filesystem path below remains as compatibility for
  // installed native shells that predate the NativeDownloader bridge.
  let _nativeDownloadBusy = false;
  let _nativeDownloadUrl = null;
  let _nativeDownloadTask = null;
  function _safeDownloadName(name) {
    const cleaned = String(name || 'video.mp4')
      .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_')
      .replace(/^\.+/, '')
      .trim();
    return (cleaned || 'video.mp4').slice(0, 180);
  }
  async function _nativeSystemDownload(Downloader, url, name) {
    if (_nativeDownloadBusy) return;
    _nativeDownloadBusy = true;
    const safe = _safeDownloadName(name);
    const buttons = [downloadBtn, document.getElementById('burnDownloadBtn')].filter(Boolean);
    const labels = buttons.map(button => button.innerHTML);
    buttons.forEach(button => {
      button.disabled = true;
      button.textContent = t('download.starting');
    });
    try {
      const res = await Downloader.download({
        url: _withToken(url),
        filename: safe,
        mimeType: 'video/mp4',
        description: t('download.notification'),
      });
      // Prefer opening the VIDEO itself (openFile, next binary onward - it
      // opens the finished file, or the Downloads UI while still in flight).
      // Older shells with only openDownloads keep the folder action.
      const dlId = res && res.id;
      const action = (Downloader.openFile && dlId != null)
        ? { label: t('download.openVideo'),
            onClick: () => { Downloader.openFile({ id: dlId, mimeType: 'video/mp4' })
              .catch(() => { if (Downloader.openDownloads) Downloader.openDownloads().catch(() => {}); }); } }
        : (Downloader.openDownloads
          ? { label: t('download.openFolder'), onClick: () => { Downloader.openDownloads().catch(() => {}); } }
          : null);
      celebrateToast(t('download.started'), { duration: 8000, action });
    } catch (error) {
      console.error('native system download failed', error);
      _reportError('download', (error && error.message) || t('err.downloadFailed'));
      celebrateToast(t('err.downloadFailed'), { kind: 'error', duration: 7000 });
    } finally {
      buttons.forEach((button, index) => {
        button.disabled = false;
        button.innerHTML = labels[index];
      });
      _nativeDownloadBusy = false;
    }
  }

  // True when the share warm-up already holds (or is currently fetching) this
  // exact file in the app cache, so it can be saved without a second fetch.
  function _warmCacheCovers(url) {
    return (_sharePrep && _sharePrep.url === url && _sharePrep.uri)
        || (_sharePrepPromise && _sharePrepUrl === url);
  }

  // Save the warm-cached copy into public Downloads through the native bridge
  // (MediaStore-registered, so it is visible and openable). Falls back to a
  // plain DownloadManager fetch on ANY failure - a re-download is a slow
  // success, while a swallowed error would be a missing file.
  async function _nativeCachedSave(Downloader, url, name) {
    if (_nativeDownloadBusy) return;
    _nativeDownloadBusy = true;
    const safe = _safeDownloadName(name);
    const buttons = [downloadBtn, document.getElementById('burnDownloadBtn')].filter(Boolean);
    const labels = buttons.map(button => button.innerHTML);
    buttons.forEach(button => { button.disabled = true; button.textContent = t('download.saving'); });
    let saved = null;
    try {
      // The warm-up may still be in flight; awaiting it shares its stream
      // rather than starting a competing download.
      if (!(_sharePrep && _sharePrep.url === url && _sharePrep.uri) && _sharePrepPromise) {
        try { await _sharePrepPromise; } catch (_) {}
      }
      if (_sharePrep && _sharePrep.url === url && _sharePrep.uri) {
        const res = await Downloader.saveToDownloads({
          path: _sharePrep.uri,
          filename: safe,
          mimeType: 'video/mp4',
          title: safe,
          text: t('download.readyWatch'),
        });
        saved = (res && res.uri) || null;
      }
    } catch (error) {
      console.warn('cached save failed, falling back to a download', error);
    } finally {
      buttons.forEach((button, index) => {
        button.disabled = false;
        button.innerHTML = labels[index];
      });
      _nativeDownloadBusy = false;
    }
    if (saved) {
      celebrateToast(t('download.readyWatch'), { duration: 0 });
      return;
    }
    await _nativeSystemDownload(Downloader, url, name);
  }

  async function _nativeDocumentDownload(Filesystem, url, name) {
    if (_nativeDownloadBusy) return;
    _nativeDownloadBusy = true;
    const tokenUrl = _withToken(url);
    const safe = _safeDownloadName(name);
    const path = safe;
    const buttons = [downloadBtn, document.getElementById('burnDownloadBtn')].filter(Boolean);
    const labels = buttons.map(b => b.innerHTML);
    buttons.forEach(b => { b.disabled = true; b.textContent = t('download.saving'); });
    let progressHandle = null;
    try {
      // This used to shortcut through Filesystem.copy from the share warm-up's
      // cached bytes. It was removed on 2026-08-11: `copy` is the one
      // Filesystem write that never runs a MediaScan, so the saved file was
      // invisible to Files and Gallery - the user's "not saved to device".
      // downloadFile costs a second fetch but DOES index the result, and a
      // slower save the user can actually find beats a fast one they cannot.
      if (Filesystem.addListener) {
        progressHandle = await Filesystem.addListener('progress', p => {
          if (p.url && p.url !== tokenUrl) return;
          const pct = p.contentLength > 0 ? Math.min(100, Math.round(p.bytes * 100 / p.contentLength)) : 0;
          buttons.forEach(b => { b.textContent = pct ? t('download.savingPct', { pct }) : t('download.saving'); });
        });
      }
      await Filesystem.downloadFile({
        url: tokenUrl,
        path,
        directory: 'DOCUMENTS',
        progress: true,
      });
      let savedUri = null;
      if (Filesystem.getUri) {
        try {
          const { uri } = await Filesystem.getUri({ directory: 'DOCUMENTS', path });
          savedUri = uri || null;
        } catch (_) { /* save succeeded; sharing can still build a cache copy */ }
      }
      // This path has no launcher bridge (that is what makes it the legacy
      // path), so the action can only hand the file to the share sheet. Say
      // so: labelling it "Open video" is what produced the "it only opens the
      // share window" report - the button did exactly what it said it would
      // not do.
      celebrateToast(t('download.readyWatch'), {
        duration: 0,
        action: savedUri
          ? { label: t('download.shareVideo'), onClick: () => _openSavedVideo(savedUri) }
          : { label: safe },
      });
      // No system notification here: this path only runs in a shell with no
      // NativeDownloader bridge, which by definition has no way to post one
      // either. (A `notifySaved` call used to sit here; it was unreachable,
      // because reaching this function at all means the bridge is missing.)
    } catch (e) {
      console.error('native download failed', e);
      _reportError('download', (e && e.message) || t('err.downloadFailed'));
      celebrateToast(t('err.downloadFailed'), { kind: 'error', duration: 7000 });
    } finally {
      try {
        const h = await Promise.resolve(progressHandle);
        if (h && h.remove) await h.remove();
      } catch (_) {}
      buttons.forEach((b, i) => { b.disabled = false; b.innerHTML = labels[i]; });
      _nativeDownloadBusy = false;
    }
  }

  // Kick off a download of a server URL. Native Android uses DownloadManager
  // (or the legacy Filesystem fallback). Browsers use Content-Disposition
  // through a hidden iframe, so
  // the API can stream to disk without buffering a large Blob in page memory.
  // The media token rides in the query (`_withToken`), computed at call time so
  // it's always fresh. We load it in a hidden IFRAME rather than clicking an
  // <a>: the API is cross-origin (modal.run), so the anchor's `download` attr is
  // ignored and a plain click/location would be treated as a TOP-LEVEL
  // navigation - firing the beforeunload "leave page?" prompt before the server's
  // Content-Disposition can turn it into a download (and cancelling it if the
  // user dismisses the prompt). An iframe navigation never unloads the page, so
  // the file downloads with no prompt. The server sets the filename from the
  // `filename` query param, so `name` isn't needed on the client.
  function _browserDownload(url, name) {
    // DownloadManager FIRST whenever the bridge exists.
    //
    // There used to be a local-first shortcut here: when the share warm-up had
    // already cached the file, saving was a Filesystem.copy into public
    // Documents instead of a second network fetch (field report: "download
    // takes 30s after the video is ready"). It was fast and it was wrong -
    // `copy` is the ONE Filesystem write that never runs a MediaScan, so the
    // file landed on disk but was never indexed, and neither Files nor Gallery
    // could see it. The copy path's toast then offered "open video", which is
    // a Share sheet in a shell without a launcher bridge. Field report,
    // 2026-08-11: "downloads from History only open in the share window, but
    // are not saved to device" - both halves are that branch.
    //
    // The URL a History row builds is byte-identical to the one the fresh burn
    // builds (same output key, same `<stem>_edited.mp4`), so the newest row hit
    // the warm cache and took it too.
    //
    // DownloadManager writes to public Downloads, registers the file with the
    // system so it is visible everywhere, and posts its own completion
    // notification that opens the video on tap.
    //
    // The fast path is BACK, but native now: `saveToDownloads` copies the
    // warm-cached bytes straight into the MediaStore Downloads collection, so
    // the result is indexed by construction - the property Filesystem.copy
    // lacked. It ships from Android 1.4.0 onward; every older shell simply
    // does not expose the method and keeps re-downloading, and any failure
    // falls through to DownloadManager below, so the worst case is a wasted
    // call rather than a lost file.
    const Downloader = _isNative() && _capPlugin('NativeDownloader');
    if (Downloader && Downloader.saveToDownloads && Downloader.download
        && _warmCacheCovers(url)) {
      if (_nativeDownloadBusy) return;
      _nativeDownloadUrl = url;
      const task = _nativeCachedSave(Downloader, url, name);
      _nativeDownloadTask = task;
      task.finally(() => {
        if (_nativeDownloadTask === task) {
          _nativeDownloadTask = null;
          _nativeDownloadUrl = null;
        }
      });
      return;
    }
    if (Downloader && Downloader.download) {
      if (_nativeDownloadBusy) return;
      _nativeDownloadUrl = url;
      const task = _nativeSystemDownload(Downloader, url, name);
      _nativeDownloadTask = task;
      task.finally(() => {
        if (_nativeDownloadTask === task) {
          _nativeDownloadTask = null;
          _nativeDownloadUrl = null;
        }
      });
      return;
    }
    const Filesystem = _isNative() && _capPlugin('Filesystem');
    if (Filesystem) {
      if (_nativeDownloadBusy) return;
      _nativeDownloadUrl = url;
      const task = _nativeDocumentDownload(Filesystem, url, name);
      _nativeDownloadTask = task;
      task.finally(() => {
        if (_nativeDownloadTask === task) {
          _nativeDownloadTask = null;
          _nativeDownloadUrl = null;
        }
      });
      return;
    }
    let ifr = document.getElementById('_dlFrame');
    if (!ifr) {
      ifr = document.createElement('iframe');
      ifr.id = '_dlFrame';
      ifr.style.display = 'none';
      document.body.appendChild(ifr);
    }
    ifr.src = _withToken(url);
  }

  function triggerDownload() {
    // Preferred: stream straight from the server to disk (no RAM buffering).
    if (resultDownloadUrl) { _browserDownload(resultDownloadUrl, resultName); return; }
    // Legacy fallback: a pre-fetched Blob (kept for any path still using one).
    if (!resultBlob) return;
    const url = URL.createObjectURL(resultBlob);
    const a = document.createElement('a');
    a.href = url; a.download = resultName; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  // ── State helpers ──
  // ── Checklist helpers ──────────────────────────────────────────────────────
  function _stepActivate(name) {
    const item = checkItems[name];
    if (!item || item.classList.contains('done')) return;
    if (item.classList.contains('active')) return;   // already running - keep its timer
    item.style.display = '';   // show row if it was hidden (e.g. burn starts hidden)
    item.className = 'check-item active';
    const timeEl = checkTimeEls[name];
    if (timeEl) timeEl.textContent = '0:00';
    const start = Date.now();
    if (stepTimers[name]) clearInterval(stepTimers[name].id);
    stepTimers[name] = {
      start,
      id: setInterval(() => {
        const s = Math.floor((Date.now() - start) / 1000);
        if (timeEl) timeEl.textContent = formatTime(s);
      }, 500)
    };
  }

  function _stepDone(name) {
    if (stepTimers[name]) { clearInterval(stepTimers[name].id); }
    const elapsed = stepTimers[name] ? Math.round((Date.now() - stepTimers[name].start) / 1000) : null;
    // How long each step took is often the whole story in a failure report.
    _crumb('done:' + name, elapsed === null ? '' : elapsed + 's');
    stepTimers[name] = null;
    const item = checkItems[name];
    if (!item) return;
    item.className = 'check-item done';
    const timeEl = checkTimeEls[name];
    if (timeEl && elapsed !== null) {
      timeEl.textContent = formatTime(elapsed);
      stepEndSecs[name] = elapsed;
    } else if (timeEl && stepEndSecs[name] != null) {
      timeEl.textContent = formatTime(stepEndSecs[name]);
    }
  }

  function _stepSkip(name) {
    const item = checkItems[name];
    if (item) item.style.display = 'none';
    if (stepTimers[name]) { clearInterval(stepTimers[name].id); stepTimers[name] = null; }
  }

  const HIDDEN_BY_DEFAULT = new Set(['upscale', 'broll', 'hook', 'burn', 'finalize']); // rows hidden until triggered

  // Cut-only jobs (no captions/B-roll) go straight to the download. The file is
  // handed to the browser to stream to disk (see _browserDownload) instead of
  // being pulled into a Blob first - so there's no multi-second silent gap on
  // big files; showDone() fires the download immediately.
  async function _finalizeAndDownload(url, name) {
    _setStage('download');
    resultDownloadUrl = url;
    resultName = name;
    showDone();
  }

  function _resetChecklist() {
    Object.keys(checkItems).forEach(name => {
      if (stepTimers[name]) { clearInterval(stepTimers[name].id); stepTimers[name] = null; }
      const item = checkItems[name];
      if (item) {
        item.className = 'check-item pending';
        item.style.display = HIDDEN_BY_DEFAULT.has(name) ? 'none' : '';
      }
      const t = checkTimeEls[name];
      if (t) t.textContent = '';
    });
    stepEndSecs = {};
    _syncCutStepLabel();
    // Only list the steps this run will actually execute - a row for a
    // disabled tool reads as that tool running (user request, v1.10.5).
    // Safe for resumed jobs too: _stepActivate re-reveals any row the
    // backend's real progress reports.
    const _cutOn  = !!document.getElementById('cutSilences')?.checked;
    const _capsOn = !!document.getElementById('burnCaptions')?.checked;
    if (!(_cutOn || _capsOn || isAudioInput) && checkItems.cut)
      checkItems.cut.style.display = 'none';
    if (!document.getElementById('enhanceAudio')?.checked && checkItems.enhance)
      checkItems.enhance.style.display = 'none';
    // ...and conversely, every tool this run WILL execute is listed as pending
    // from the very start, in execution order - rows popping into existence
    // mid-run read as surprises (field report). Burn stays hidden: it belongs
    // to the export step, not processing.
    if (_enhanceVideoMode() === 'esrgan' && checkItems.upscale) {
      checkItems.upscale.style.display = '';
      const l = document.querySelector('#checkUpscale .check-label');
      if (l) l.textContent = t('prog.upscale');
    }
    const _editorRun = _capsOn && !isAudioInput;
    // The save row is always listed (every run publishes its result to the
    // volume) - only its LABEL depends on where the user lands next: a
    // terminal run (no editor) is literally preparing the download.
    {
      const sl = checkItems.save && checkItems.save.querySelector('.check-label');
      if (sl) {
        const key = _editorRun ? 'prog.save' : 'prog.saveDl';
        sl.setAttribute('data-i18n', key);
        sl.textContent = t(key);
      }
    }
    if (_editorRun && checkItems.finalize) checkItems.finalize.style.display = '';
    if (_editorRun && document.getElementById('autoBroll')?.checked && checkItems.broll)
      checkItems.broll.style.display = '';
    if (_editorRun && document.getElementById('autoHook')?.checked && checkItems.hook)
      checkItems.hook.style.display = '';
  }

  // The worker's 'cut' stage covers transcription TOO (captions need it), so
  // with "Cut silences" toggled OFF the step still runs - labeled "Cut
  // silences" it read as the toggle being ignored (field report, v1.10.4:
  // the audit log proved cut_silences=false end-to-end while the user watched
  // a step called 'removing silences'). Label it by what it actually does.
  // Sets data-i18n too so a language switch re-translates the right key.
  function _syncCutStepLabel() {
    const el = checkItems.cut && checkItems.cut.querySelector('.check-label');
    if (!el) return;
    const cutOn = !!document.getElementById('cutSilences')?.checked;
    const key = cutOn ? 'prog.cut' : 'prog.transcribe';
    el.setAttribute('data-i18n', key);
    el.textContent = t(key);
  }

  // ── State display ──────────────────────────────────────────────────────────
  function showUploadProgress() {
    isUploading = true;
    setSetupLocked(true);
    statusCard.classList.add('visible');
    expandCard('statusBody');
    checklistEl.style.display = 'block';
    statusDone.classList.remove('visible');
    statusError.classList.remove('visible');
    _resetChecklist();

    // Upload step: active + inline progress bar
    _stepActivate('upload');
    document.getElementById('uploadBarRow').style.display = 'flex';
    document.getElementById('uploadBarFill').style.width = '0%';
    document.getElementById('uploadBarPct').textContent = '0%';
    { const e = document.getElementById('uploadEta'); if (e) e.textContent = ''; }
    { const n = document.getElementById('uploadNote'); if (n) n.style.display = _isNative() ? 'none' : 'block'; }

    runBtn.disabled = true;
    runBtn.style.display = 'none';
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  let _procStartMs = null;

  function showProcessing() {
    isUploading = false;
    statusCard.classList.add('visible');
    expandCard('statusBody');
    clearInterval(uploadTimer);
    _procStartMs = Date.now();
    // Real backend progress (via /process_poll) drives the transitions and the
    // final numbers. Activate the first applicable step so the checklist isn't
    // idle until the first poll; skip steps whose toggle is off.
    const enhanceOn = document.getElementById('enhanceAudio').checked;
    if (!enhanceOn) _stepSkip('enhance');
    // The enhancement row reports the selected mode (backend stage: 'upscale')
    const _upLabel = document.querySelector('#checkUpscale .check-label');
    if (_upLabel) _upLabel.textContent = _enhanceVideoMode() === 'esrgan' ? t('prog.upscale') : t('prog.enhanceVideo');
    _stepActivate(enhanceOn ? 'enhance' : 'cut');
  }

  // Force a step done with an explicit elapsed-seconds value (real, backend-reported).
  function _forceDone(name, secs) {
    if (stepTimers[name]) { clearInterval(stepTimers[name].id); stepTimers[name] = null; }
    const item = checkItems[name];
    if (!item) return;
    item.style.display = '';
    item.className = 'check-item done';
    const timeEl = checkTimeEls[name];
    if (timeEl) { timeEl.textContent = formatTime(secs); stepEndSecs[name] = secs; }
  }

  // Live progress from /process_poll: {stage, done:{step: secs}} - all real.
  function _applyProgress(progress) {
    if (!progress) return;
    Object.entries(progress.done || {}).forEach(([name, secs]) => {
      const item = checkItems[name];
      if (item && !item.classList.contains('done')) _forceDone(name, Math.max(1, Math.round(secs)));
    });
    const cur = progress.stage;
    if (cur && checkItems[cur] && !checkItems[cur].classList.contains('done')) _stepActivate(cur);
  }

  // Called when process_poll returns. Closes each step with the backend's real
  // duration; steps the backend never ran are hidden, never estimated.
  function _stepsDoneProcessing(stepTimes) {
    const st = stepTimes || {};
    ['enhance', 'cut', 'upscale', 'save'].forEach(name => {
      const item = checkItems[name];
      if (!item) return;
      if (st[name] != null) _forceDone(name, Math.max(1, Math.round(st[name])));
      else if (item.classList.contains('active')) _stepDone(name);  // frontend-measured wall time
      else if (!item.classList.contains('done')) _stepSkip(name);   // step never ran
    });
  }

  // ── Celebration micro-interactions ─────────────────────────────────────────
  // Restrained, on-brand payoff at key checkpoints: a springy check + a warm
  // one-shot glow + a count-up stat. The global prefers-reduced-motion CSS rule
  // makes the CSS animations instant; the count-up guards itself in JS.
  function _prefersReducedMotion() {
    return window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  function _pulse(el, cls) {
    if (!el) return;
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
    setTimeout(() => el.classList.remove(cls), 1300);
  }
  // Floating status toast. Errors use a distinct red treatment and remain
  // visible longer so a transient failure cannot look like success.
  let _toastTimer = null;
  function celebrateToast(text, { kind = 'success', duration = 2600, action = null } = {}) {
    const el = document.getElementById('celebrateToast');
    if (!el) return;
    // Reparent to <body> so position:fixed is viewport-relative and z-index
    // wins outright — inside the in-flow container it was trapped in a lower
    // stacking context and rendered behind the sticky topbar.
    if (el.parentElement !== document.body) document.body.appendChild(el);
    clearTimeout(_toastTimer);
    el.classList.remove('show', 'error');
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
    if (kind === 'error') el.classList.add('error');
    document.getElementById('celebrateToastText').textContent = text;
    // Optional tappable action ("Your video is saved to: <name>"). Created
    // lazily; tapping the toast body itself dismisses the toast.
    let act = document.getElementById('celebrateToastAction');
    if (action && action.label) {
      if (!act) {
        act = document.createElement('button');
        act.id = 'celebrateToastAction';
        act.className = 'celebrate-toast-action';
        el.appendChild(act);
      }
      act.textContent = action.label;
      act.onclick = (e) => {
        e.stopPropagation();
        try { action.onClick && action.onClick(); } catch (_) {}
        el.classList.remove('show');
      };
      act.style.display = '';
    } else if (act) {
      act.style.display = 'none';
      act.onclick = null;
    }
    // duration: 0 = sticky - no auto-hide, an explicit small X dismisses it
    // (user request 2026-08-13 for the "video ready" toast: it carries the
    // follow-up action, so it should wait for the user, not race a timer).
    let close = document.getElementById('celebrateToastClose');
    if (!duration) {
      if (!close) {
        close = document.createElement('button');
        close.id = 'celebrateToastClose';
        close.className = 'celebrate-toast-close';
        close.setAttribute('aria-label', t('toast.dismiss'));
        close.innerHTML = ICON.x;
        el.appendChild(close);
      }
      close.onclick = (e) => { e.stopPropagation(); el.classList.remove('show'); };
      close.style.display = '';
    } else if (close) {
      close.style.display = 'none';
      close.onclick = null;
    }
    el.onclick = () => el.classList.remove('show');
    _pulse(el.querySelector('.celebrate-toast-check'), 'celebrate-check');
    // Restart the entrance transition even if another toast was just visible.
    void el.offsetWidth;
    el.classList.add('show');
    if (duration) _toastTimer = setTimeout(() => el.classList.remove('show'), duration);
  }

  // Legacy shells have no ACTION_VIEW bridge - the Share sheet is the
  // practical "open" for a saved file (Files/Gallery/player targets included).
  function _openSavedVideo(uri) {
    const Share = _capPlugin('Share');
    if (Share && uri) { try { Share.share({ url: uri }); } catch (_) {} }
  }
  // Export complete: animate the success banner in place + count up the payoff.
  function celebrateExport() {
    const banner = document.getElementById('burnSuccessBanner');
    if (banner) {
      _pulse(banner, 'celebrate-glow');
      _pulse(banner.querySelector('.burn-success-icon'), 'celebrate-check');
      // Bring the finished-video banner (with the Download button) into view -
      // after a burn the page is scrolled down on the checklist, so surface the
      // payoff + download instead of leaving the user staring at the progress.
      setTimeout(() => _scrollToBelowTopbar(banner), 60);
    }
    // The "seconds trimmed" payoff stat was removed — keep the banner clean.
    const statEl = document.getElementById('burnSuccessStat');
    if (statEl) statEl.style.display = 'none';
  }

  function showDone() {
    checklistEl.style.display = 'none';
    statusDone.classList.add('visible');
    _maybeShowShare();
    if (!burnMode) runBtn.style.display = 'none';
    // A cut-only video (no captions/hook/B-roll to burn) is still a finished
    // deliverable - let it be scheduled straight from the done card. The cut
    // video key IS the output; scheduling posts its /media/ URL to Metricool.
    const dsb = document.getElementById('doneScheduleBtn');
    if (dsb) {
      if (videoKey && !burnMode) {
        window._schedCtx = {
          outputKey: videoKey,
          filename:  resultName || cutFilename || 'video.mp4',
          videoKey:  videoKey,
          hasTranscript: _hasTranscript(),
        };
        dsb.style.display = 'block';
      } else {
        dsb.style.display = 'none';
      }
    }
    if (videoKey && !burnMode) {
      // A cut-only result is not a dead end: the source is still on the
      // server, so unlock the tool toggles and offer a re-process - the same
      // affordance the editor flow has (field report: "can't reselect tools").
      document.getElementById('optionsCard')?.classList.remove('setup-locked');
      // selectedFile too: after a reload-resume the picked File is gone and
      // rerun() couldn't start - don't show a dead button.
      if (currentUploadKey && selectedFile) {
        const rb = document.getElementById('reprocessBtn');
        if (rb) rb.style.display = 'block';
      }
    }
    triggerDownload();
  }

  // Fire-and-forget error telemetry: every user-facing error surface reports
  // here, the backend stores it and pushes an IMMEDIATE FCM alert to the
  // admin's devices - the owner knows about field failures as they happen.
  // Deliberately plain fetch, NOT apiFetch: apiFetch turns a 401 into
  // _sessionExpired(), which would tear the app back to the login view mid
  // error-card - so merely REPORTING a failure could log the user out (an
  // expired session, or an older backend without this route, both 401).
  // Telemetry must never touch session state.
  // ── Error-report context ──────────────────────────────────────────────────
  // A push notification can only ever be a headline, and "something failed for
  // someone" is not debuggable. Everything below travels with the report and is
  // readable ONLY through /admin/errors (403 for everyone else) and the
  // admin-gated Errors panel. The user still sees the same friendly card - none
  // of this is surfaced to them.
  const _breadcrumbs = [];
  function _crumb(event, detail) {
    try {
      _breadcrumbs.push({
        t: Math.round(performance.now() / 100) / 10,   // seconds since load, 0.1s
        ev: String(event || '').slice(0, 40),
        ...(detail ? { d: String(detail).slice(0, 80) } : {}),
      });
      if (_breadcrumbs.length > 14) _breadcrumbs.shift();
    } catch (_) {}
  }

  function _errorContext() {
    const ctx = {};
    try {
      ctx.platform = _platform();
      ctx.native = _isNative();
      ctx.lang = (document.documentElement.lang || '').slice(0, 5);
      ctx.online = navigator.onLine;
      ctx.screen = `${window.innerWidth}x${window.innerHeight}`;
      // Which job. This is what turns "a user hit an error" into a row in the
      // volume and the History list that can actually be inspected.
      if (videoKey) ctx.video_key = String(videoKey).slice(0, 140);
      if (currentCallId) ctx.call_id = String(currentCallId).slice(0, 60);
      if (videoDuration) ctx.duration = Math.round(videoDuration);
      if (selectedFile) {
        ctx.file = { name: String(selectedFile.name || '').slice(0, 80),
                     size: selectedFile.size || 0,
                     type: String(selectedFile.type || '').slice(0, 40) };
      }
      // The options actually chosen - most "it failed" reports turn out to be
      // one specific combination.
      const on = id => { const el = document.getElementById(id); return el ? !!el.checked : null; };
      ctx.options = {
        cut: on('cutSilences'), captions: on('burnCaptions'), broll: on('autoBroll'),
        hook: on('autoHook'), enhance_audio: on('enhanceAudio'),
        enhance_video: _enhanceVideoMode(),
      };
      if (typeof navigator.connection === 'object' && navigator.connection) {
        ctx.net = String(navigator.connection.effectiveType || '').slice(0, 12);
      }
      if (_breadcrumbs.length) ctx.trail = _breadcrumbs.slice(-14);
    } catch (_) { /* context is a nicety - never let it break the report */ }
    return ctx;
  }

  function _reportError(stage, msg) {
    try {
      if (!authToken) return;
      fetch(`${API_BASE}/error-report/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
        body: JSON.stringify({
          stage: stage || flowStage || '?',
          message: String(msg || '').slice(0, 400),
          version: 'v' + APP_VERSION,
          ua: navigator.userAgent.slice(0, 160),
          context: _errorContext(),
        }),
      }).catch(() => {});
    } catch (_) {}
  }

  function showError(msg) {
    const isQuota = /limit_reached/.test(msg);
    if (!isQuota) _reportError(flowStage, msg);   // quota-exhausted isn't a malfunction
    if (isQuota) {
      msg = _billingAvailable() ? _tStore('billing.exhausted') : _playStoreQuotaCopy();
    }
    if (/no_audio/.test(msg)) msg = t('err.noAudio');
    // The server priced the run from its own probe and it came out higher than
    // what was charged, or the upscale was asked for on too long a source.
    if (/upscale_too_long/.test(msg)) msg = t('ev.upscaleTooLong', {max: UPSCALE_MAX_SECONDS / 60});
    if (/credit_mismatch/.test(msg)) msg = t('err.creditMismatch');
    // Quota exhaustion isn't a malfunction. Current Android shells purchase
    // in-app; web and old shells go only to the app's Play Store listing.
    const playCta = document.getElementById('errorPlayCta');
    const buyCta = document.getElementById('errorBuyCta');
    if (playCta) {
      _configurePlayStoreCta(playCta);
      playCta.style.display =
        isQuota && !_billingAvailable() && _playStoreCtaAllowed() ? 'inline-flex' : 'none';
    }
    if (buyCta) buyCta.style.display =
      isQuota && _billingAvailable() ? 'inline-flex' : 'none';
    // A failed job refunds its credit server-side; re-pull usage so the pill
    // reflects the refund (harmless no-op when nothing was charged).
    refreshQuota();
    isUploading = false;
    setSetupLocked(false);
    clearInterval(uploadTimer);
    Object.keys(stepTimers).forEach(k => { if (stepTimers[k]) { clearInterval(stepTimers[k].id); stepTimers[k] = null; } });
    checklistEl.style.display = 'none';
    statusError.classList.add('visible');
    const isNet = _isNetErr({ message: msg });
    const friendly = isNet
      ? t('err.netDropped', { stage: flowStage ? t('err.stage.' + flowStage) : '' }).replace('  ', ' ')
      : msg;
    errorMsg.textContent = friendly.length > 200 ? friendly.slice(0, 200) + '…' : friendly;
    // Technical detail line - locale-neutral, for screenshots/bug reports.
    const detailEl = document.getElementById('errorDetail');
    if (detailEl) {
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, '0'), mm = String(now.getMinutes()).padStart(2, '0');
      detailEl.textContent = [
        flowStage || 'unknown stage',
        msg.slice(0, 120),
        navigator.onLine ? 'online' : 'offline',
        `${hh}:${mm}`,
      ].join(' · ');
      detailEl.style.display = 'block';
    }
    // Expandable console log - the last few warn/error lines leading up here
    const logWrap = document.getElementById('errorLogWrap');
    const logEl   = document.getElementById('errorLog');
    if (logWrap && logEl) {
      logEl.textContent = consoleLog.slice(-10).join('\n');
      logWrap.style.display = consoleLog.length ? 'block' : 'none';
      logWrap.open = false;
    }
    runBtn.disabled = false;
    runBtn.style.display = 'block';
  }

  function resetStatus() {
    isUploading = false;
    if (pollController) { pollController.abort(); pollController = null; }
    currentCallId = null;
    clearInterval(uploadTimer);
    Object.keys(stepTimers).forEach(k => { if (stepTimers[k]) { clearInterval(stepTimers[k].id); stepTimers[k] = null; } });
    runStartTime = null;
    statusCard.classList.remove('visible');
    checklistEl.style.display = 'none';
    _resetChecklist();
    statusDone.classList.remove('visible');
    statusError.classList.remove('visible');
    document.getElementById('captionEditorCard').style.display = 'none';
    document.getElementById('captionsList').innerHTML = '';
    { const _heh = document.getElementById('historyEditHint'); if (_heh) _heh.style.display = 'none'; }
    historyEditName = '';
    document.getElementById('stockBrollList').innerHTML = '';
    stockBrollSelections = {};
    syncBrollPreviewPool();
    { const ph = document.getElementById('playerHook'); if (ph) ph.style.display = 'none'; }
    document.getElementById('hookOptions').style.display = 'none';
    document.getElementById('hookControls').style.display = 'none';
    document.getElementById('hookError').style.display = 'none';
    document.getElementById('generateHookBtn').disabled = true;
    document.getElementById('hookStatus').style.display = 'none';
    { const _hrb = document.getElementById('hookRerunBanner'); if (_hrb) _hrb.style.display = 'none'; }
    selectedHookIdx = -1;
    hookGenAborted = false;
    hookThumbnail = null;
    pendingAnalyses       = 0;
    stockBrollAnalyzed    = false;
    lastAnalyzedSignature = '';
    _hookGenSignature     = '';
    burnMode = false;
    hasBurnedOnce = false;
    currentUploadKey = null;
    document.getElementById('reprocessBtn').style.display = 'none';
    setSetupLocked(false);
    document.getElementById('burnSuccessBanner').style.display = 'none';
    { const _osb = document.getElementById('openScheduleBtn'); if (_osb) _osb.style.display = 'none';
      const _so = document.getElementById('scheduleOverlay'); if (_so) _so.style.display = 'none'; }
    document.getElementById('stockBrollRerunBanner').style.display = 'none';
    document.getElementById('stockCostLimitBanner').style.display  = 'none';
    runBtn.textContent = t('run.pipelinePlain');
    runBtn.style.display = 'block';
    const burnErrorEl = document.getElementById('burnError');
    if (burnErrorEl) burnErrorEl.style.display = 'none';
    captionsData = [];
    videoKey    = null;
    cutFilename = '';
    captionFont      = 'Heebo';
    captionMarginPct = 0.08;
    captionFontSize  = 48;
    const _vid = document.getElementById('cutVideo');
    if (_vid) { _vid.pause(); _vid.removeAttribute('src'); _vid.load(); }
    if (_previewObjURL) { try { URL.revokeObjectURL(_previewObjURL); } catch (_) {} _previewObjURL = null; }
    _playerSetupDone = false;
    _playerDispW = 0;
    const _cp = document.getElementById('captionPlayer');
    if (_cp) _cp.style.display = 'none';
    const fs = document.getElementById('fontSelect');
    if (fs) fs.value = 'Heebo';
  }

  // ── Network check ──
  const _conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;

  function checkNetwork() {
    if (!_conn) return;
    const type = _conn.type;
    const eff  = _conn.effectiveType;
    const isCellular = type === 'cellular';
    const isWired    = type === 'wifi' || type === 'ethernet';
    const isSlow     = !isCellular && !isWired && ['slow-2g', '2g', '3g'].includes(eff);
    if (isCellular || isSlow) {
      document.getElementById('noticeNetTitle').textContent =
        isCellular ? t('net.cellTitle') : t('net.slowTitle');
      document.getElementById('noticeNetBody').textContent =
        isCellular ? t('net.cellBody') : t('net.slowBody', {eff: eff});
      noticeNet.classList.add('visible');
    } else {
      noticeNet.classList.remove('visible');
    }
  }

  if (_conn) _conn.addEventListener('change', checkNetwork);

  // ── Notice helpers ──
  function clearNotices() {
    noticeBlock.classList.remove('visible');
    noticeWarn.classList.remove('visible');
    noticeNet.classList.remove('visible');
    if (noticeAudio) noticeAudio.classList.remove('visible');
  }
  function showBlockNotice(title, body) {
    document.getElementById('noticeBlockTitle').textContent = title;
    document.getElementById('noticeBlockBody').textContent  = body;
    const _play = document.getElementById('noticePlayCta');
    if (_play) _play.style.display = 'none';   // only showQuotaExhausted reveals it
    const _buy = document.getElementById('noticeBuyCta');
    if (_buy) _buy.style.display = 'none';
    noticeBlock.classList.add('visible');
  }
  // Quota exhausted = a warm lead, not a dead end. Purchase in the current
  // Android shell; web and old shells route to Google Play to install/update.
  function showQuotaExhausted() {
    showBlockNotice(t('quota.pillZero'),
      _billingAvailable() ? _tStore('billing.exhausted') : _playStoreQuotaCopy());
    const play = document.getElementById('noticePlayCta');
    if (play && !_billingAvailable() && _playStoreCtaAllowed()) {
      _configurePlayStoreCta(play);
      play.style.display = 'inline-flex';
    }
    const buy = document.getElementById('noticeBuyCta');
    if (buy && _billingAvailable()) buy.style.display = 'inline-flex';
  }
  function showWarnNotice(title, body) {
    document.getElementById('noticeWarnTitle').textContent = title;
    document.getElementById('noticeWarnBody').textContent  = body;
    noticeWarn.classList.add('visible');
  }

  // ── Utils ──
  function formatSize(bytes) {
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }
  function formatTime(s) {
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }
  function formatDuration(s) {
    const m = Math.floor(s / 60);
    const sec = Math.round(s % 60);
    return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
  }

  // ── Caption editor ──
  function fmtCapTime(secs) {
    const m = Math.floor(secs / 60);
    const s = String(Math.floor(secs % 60)).padStart(2, '0');
    return `${m}:${s}`;
  }

  // ── Caption line-wrapping - mirrors Python _rewrap_cap (char_w = fontSize * 0.60) ──
  // Caption size is chosen in REFERENCE px - what it would measure on a
  // 1080-short-side export. The burn scales it to the real frame so one
  // choice looks identical on 720p, 1080p and 4K; everything the user picks,
  // saves or SENDS stays in reference px (the server does the same scaling),
  // and only display/wrap math below converts to real frame px.
  // MUST mirror the _CAPTION_REF_SHORT block in build_caption_ass.
  const CAPTION_REF_SHORT = 1080;
  function _capResScale() {
    // min(w,h) is rotation-invariant, so an auto-rotated video needs no
    // special case. Unknown dimensions (metadata not in yet) => 1, i.e. the
    // pre-2026-08-10 behavior; the next redraw corrects it.
    const vid = document.getElementById('cutVideo');
    const w = (vid && vid.videoWidth) || 0, h = (vid && vid.videoHeight) || 0;
    if (!w || !h) return 1;
    return Math.max(0.25, Math.min(4, Math.min(w, h) / CAPTION_REF_SHORT));
  }
  // The caption's size in REAL frame px - what the burn will use.
  function _capBurnPx() { return captionFontSize * _capResScale(); }

  function rewrapCaption(text, videoWidth, fontSize) {
    const marginH = Math.max(25, Math.floor(videoWidth / 14));
    const avail   = videoWidth - 2 * marginH;
    const charW   = fontSize * 0.50;
    // Typed newlines (and \N markers) are HARD breaks: each segment wraps
    // independently. A segment that already fits keeps its EXACT text, so
    // deliberate multi-spaces survive; only overflowing segments re-wrap
    // (which collapses whitespace runs - the greedy estimate needs tokens).
    // MUST mirror _rewrap_cap in pipeline_fns.py.
    const lines = [];
    for (const seg of text.split(/\\N|\n/)) {
      const words = seg.split(' ').filter(w => w.length);
      if (!words.length) { lines.push(''); continue; }   // blank line kept
      if (seg.trim().length * charW <= avail) { lines.push(seg.trim()); continue; }
      let cur = [], curW = 0;
      for (const word of words) {
        const ww  = word.length * charW;
        const gap = cur.length ? charW : 0;
        if (cur.length && curW + gap + ww > avail) {
          lines.push(cur.join(' '));
          cur = [word]; curW = ww;
        } else {
          cur.push(word); curW += gap + ww;
        }
      }
      if (cur.length) lines.push(cur.join(' '));
    }
    return lines.length ? lines.join('\n') : text;
  }

  // ── Caption preview & positioning ──
  // Line-advance factors - MUST mirror _LIBASS_FONT_SCALE in pipeline_fns.py.
  // libass renders the ASS Fontsize scaled by the font's line metrics; the
  // burn compensates glyph SIZE with this factor, which makes its multi-line
  // advance = factor × nominal size - so the DOM overlay's line-height uses
  // the same factor to keep 2-line captions identical to the burn.
  const LIBASS_FONT_SCALE = {
    'Heebo': 1.469, 'Assistant': 1.308, 'Frank Ruhl Libre': 1.291,
    'Secular One': 1.455, 'Rubik': 1.185, 'Suez One': 1.306,
    'Karantina': 1.012, 'Playpen Sans Hebrew': 1.530, 'Miriam Libre': 1.313,
  };

  function updatePreviewCaption() {
    // Called by font/size/position sliders - refreshes player caption overlay immediately
    const capEl = document.getElementById('playerCap');
    const vid   = document.getElementById('cutVideo');
    if (!capEl) return;
    capEl.style.fontFamily = `'${captionFont}', sans-serif`;
    capEl.style.lineHeight = String(LIBASS_FONT_SCALE[captionFont] || 1.35);
    capEl.style.bottom     = (captionMarginPct * 100) + '%';
    // Colours apply regardless of whether the video's metadata is ready yet
    // (fallback scale until the real display width is known).
    _applyCapStyleColors(capEl, (vid && vid.videoWidth) ? vid.clientWidth / (_playerDispW || vid.videoWidth) : 0.24);
    if (vid && vid.videoWidth) {
      // _playerDispW = detected display width (handles browser-auto-rotated videos where
      // videoWidth reports stream width, not display width)
      const displayW = _playerDispW || vid.videoWidth;
      const scale = vid.clientWidth / displayW;
      capEl.style.fontSize = Math.max(7, _capBurnPx() * scale) + 'px';
      const t = vid.currentTime;
      const cap = captionsData && captionsData.find(c => t >= c.start && t <= c.end + 0.05);
      if (cap) {
        const q = (_capMode() === 'word' || _capMode() === 'karaoke') ? _wordCueAt(cap, t) : null;
        if (_capMode() === 'word') capEl.textContent = q ? q.text : '';
        else if (_capMode() === 'karaoke' && q) capEl.innerHTML = _karaokeHTML(cap, q, displayW);
        else capEl.textContent = rewrapCaption(cap.text, displayW, _capBurnPx());
      }
    }
    // Keep the hook wrap (hidden measurement canvas) + on-player overlay in sync.
    drawHookPreview();
    updatePlayerHook();
    // Font/size/position changed → the exact still is stale: show the instant
    // approximate now, re-render the exact frame once edits settle.
    _hideExactCap();
    scheduleExactCap();
  }

  // Every family offered by #fontSelect (must match the server-installed set in
  // pipeline_core.py and the Google Fonts <link> in index.html).
  const CAPTION_FONTS = [
    'Heebo', 'Assistant', 'Frank Ruhl Libre', 'Secular One', 'Rubik',
    'Suez One', 'Karantina', 'Playpen Sans Hebrew', 'Miriam Libre',
  ];
  // Google Fonts ships the @font-face rules but fetches each binary lazily on
  // first use, and the browser does NOT repaint already-drawn text when a font
  // arrives - so switching to a not-yet-used face would stay on the fallback
  // until some later repaint (the "font didn't change the preview" bug).
  function _loadFontFaces(family) {
    if (!family || !document.fonts || !document.fonts.load) return Promise.resolve();
    // The preview text renders bold (700); load 400 too so both are ready.
    return Promise.all([
      document.fonts.load(`700 24px '${family}'`).catch(() => {}),
      document.fonts.load(`400 24px '${family}'`).catch(() => {}),
    ]);
  }
  // Warm every caption face when the editor opens so the first switch to any of
  // them is instant, not a flash of fallback.
  function _preloadCaptionFonts() { CAPTION_FONTS.forEach(_loadFontFaces); }
  // On a font switch: repaint immediately (size/position stay responsive and the
  // fallback shows) and again once the real face is guaranteed loaded.
  function _ensureCaptionFont(family) {
    _loadFontFaces(family).then(() => updatePreviewCaption());
  }

  // ── Exact (libass) WYSIWYG preview ──────────────────────────────────────
  // The Canvas/CSS preview is a fast APPROXIMATION; a browser and libass will
  // never rasterise identically (font metrics, outline model, variable-font
  // bold). For true parity we render the current frame through the SAME
  // build_caption_ass + ffmpeg/libass path as the burn (/preview_frame) and
  // overlay that still. It shows while the video is PAUSED / after edits settle;
  // during motion or dragging we fall back to the instant approximate preview.
  let _exactCapURL = null, _exactHookURL = null;
  let _exactCapTimer = null, _exactHookTimer = null;
  let _exactCapSeq = 0, _exactHookSeq = 0;

  function _hookPreviewPayload() {
    if (selectedHookIdx < 0) return null;
    const el = document.getElementById(`hookText${selectedHookIdx}`);
    const txt = el ? el.value.trim() : '';
    if (!txt) return null;
    drawHookPreview();   // refresh _hookLines against current text/font/size
    return {
      text: txt, lines: _hookLines,
      font:              document.getElementById('hookFont')?.value || 'Heebo',
      font_color:        document.getElementById('hookFontColor')?.value || '#FFFFFF',
      bg_color:          document.getElementById('hookBgColor')?.value || '#000000',
      bg_opacity:        parseInt(document.getElementById('hookBgOpacity')?.value || '60') / 100,
      font_size_pct:     parseInt(document.getElementById('hookFontSize')?.value || '100'),
      border_color:      document.getElementById('hookBorderColor')?.value || '#000000',
      border_size:       parseInt(document.getElementById('hookBorderSize')?.value || '0'),
      start_seconds:     parseFloat(document.getElementById('hookStartSec')?.value || '0'),
      duration_seconds:  parseFloat(document.getElementById('hookDurationSec')?.value || '3'),
      vertical_position: parseInt(document.getElementById('hookPosition')?.value || '10'),
    };
  }

  async function _fetchExactFrame(t, hookPayload) {
    if (!videoKey || isAudioInput) return null;
    const mode = _capMode();
    const body = {
      video_key: videoKey, t,
      font: captionFont, font_size: captionFontSize, margin_v: captionMarginPct,
      // Word mode sends PRE-CUED single-word captions (grouping already
      // applied by the shared mirror), with mode stripped so the builder
      // renders them as-is - re-splitting a merged pair would diverge.
      // Karaoke sends per-cue FULL-LINE entries carrying the highlight token
      // range (hl) - absolute word timings can't survive the still-frame
      // retime, the explicit range can.
      captions: (captionsData || []).flatMap(c => {
        if (mode === 'word')
          return _wordCuesFor(c).map(q => ({ start: q.start, end: q.end, text: q.text }));
        if (mode === 'karaoke')
          return _wordCuesFor(c).map(q => ({ start: q.start, end: q.end, text: c.text, hl: [q.i0, q.i1] }));
        return [{ start: c.start, end: c.end, text: c.text }];
      }),
      hook: hookPayload || null,
      caption_style: Object.assign(
        _captionStylePayload(),
        mode === 'word' ? { mode: 'classic' } : {},
        (() => {
          const vid = document.getElementById('cutVideo');
          return (document.getElementById('capProgressBar')?.checked && vid && vid.duration > 0)
            ? { progress_frac: t / vid.duration } : {};
        })()),
      // Punch-in zoom at the paused instant: send the FACTOR, not the windows -
      // absolute window times mean nothing to a retimed still, the factor does.
      ...(() => { const z = _zoomFactorAt(t); return z > 1 ? { zoom: z } : {}; })(),
    };
    const resp = await apiFetch(`${API_BASE}/preview_frame`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error('preview_frame ' + resp.status);
    return URL.createObjectURL(await resp.blob());
  }

  // Hide the exact still → reveal the instant approximate preview (during motion/edits).
  // While the exact still is displayed, the DOM caption/hook overlays must NOT
  // draw on top of it - the still already contains them (libass render), and
  // the two text engines never align pixel-perfectly, so both visible at once
  // reads as glitchy doubled text. Opacity (not visibility) keeps the overlays
  // touchable: grabbing one to drag immediately hides the still and restores
  // them for live feedback.
  function _setExactShowing(on) {
    const wrap = document.getElementById('playerWrap');
    if (wrap) wrap.classList.toggle('exact-showing', !!on);
  }
  function _hideExactCap() {
    const i = document.getElementById('exactCap');
    if (i) i.style.display = 'none';
    _setExactShowing(false);
  }

  // Fetch + show the exact caption frame for the current (paused) time.
  function scheduleExactCap() {
    const vid = document.getElementById('cutVideo');
    const img = document.getElementById('exactCap');
    if (!img || !vid || isAudioInput || !videoKey) return;
    clearTimeout(_exactCapTimer);
    _exactCapTimer = setTimeout(async () => {
      if (!vid.paused) return;               // only meaningful while paused
      const t = vid.currentTime;
      if (_activeBrollAt(t)) return;          // b-roll overlay showing - keep the DOM preview
      const seq = ++_exactCapSeq;
      try {
        const hw = _hookWindow();
        const hp = (hw && t >= hw[0] && t <= hw[1]) ? _hookPreviewPayload() : null;
        const url = await _fetchExactFrame(t, hp);
        if (seq !== _exactCapSeq || !url) { if (url) URL.revokeObjectURL(url); return; }
        if (!vid.paused) { URL.revokeObjectURL(url); return; }   // started playing meanwhile
        if (_exactCapURL) URL.revokeObjectURL(_exactCapURL);
        _exactCapURL = url; img.src = url; img.style.display = 'block';
        _setExactShowing(true);
      } catch (e) { /* keep the approximate preview */ }
    }, 400);
  }

  // The hook previews on the MAIN player now: refresh its DOM overlay and let
  // the player's exact-frame path (which includes the hook payload when the
  // playhead is inside the hook window) provide libass parity.
  function _hideExactHook() { _hideExactCap(); }
  function scheduleExactHook() { updatePlayerHook(); scheduleExactCap(); }

  // Drop any pending/loaded exact frames (new video, re-run, mode switch).
  function _resetExactPreview() {
    _exactCapSeq++; _exactHookSeq++;
    clearTimeout(_exactCapTimer); clearTimeout(_exactHookTimer);
    if (_exactCapURL)  { URL.revokeObjectURL(_exactCapURL);  _exactCapURL = null; }
    if (_exactHookURL) { URL.revokeObjectURL(_exactHookURL); _exactHookURL = null; }
    { const el = document.getElementById('exactCap');
      if (el) { el.style.display = 'none'; el.removeAttribute('src'); } }
    _setExactShowing(false);
  }

  function _safePlay(vid) {
    const p = vid.play();
    if (p) p.catch(e => { if (e.name !== 'AbortError') console.error(e); });
  }

  function setupCaptionPlayer() {
    if (_playerSetupDone) return;
    _playerSetupDone = true;

    const vid     = document.getElementById('cutVideo');
    const capEl   = document.getElementById('playerCap');
    const bigPlay = document.getElementById('playerBigPlay');
    const playBtn = document.getElementById('playerPlayBtn');
    const progFill = document.getElementById('playerProgFill');
    const progWrap = document.getElementById('playerProgWrap');
    const timeLbl  = document.getElementById('playerTimeLbl');
    if (!vid) return;

    // Fetch the cut file ONCE into a blob and play from an object URL, rather
    // than streaming live from /download/ via HTTP range requests. Each range
    // request to that route reloads the Modal volume before serving bytes, so a
    // playing/seeking <video> kept re-buffering and stuttering. These are short
    // edited clips, so a single download plays instantly and scrubs with zero
    // per-seek latency. Falls back to live streaming if the blob fetch fails.
    vid.preload = 'auto';
    document.getElementById('captionPlayer').style.display = 'block';
    const loadingEl = document.getElementById('playerLoading');
    if (loadingEl) loadingEl.style.display = 'flex';
    if (bigPlay) bigPlay.style.opacity = '0';   // hide play affordance until the clip is ready
    // Hide the spinner once the first frame is decodable (fires for both the
    // blob src and the streaming fallback). bigPlay is re-shown by the 'pause'
    // handler's normal opacity toggle on first render.
    vid.addEventListener('loadeddata', () => {
      if (loadingEl) loadingEl.style.display = 'none';
      if (bigPlay && vid.paused) bigPlay.style.opacity = '1';
    }, { once: true });
    (async () => {
      // STREAM-FIRST (2026-07-16). Waiting for the whole cut video to download
      // into a blob kept the spinner up for MINUTES on long videos; the server
      // supports byte-range streaming, so the remote URL plays within seconds.
      // A short bounded wait still catches an already-landed blob (small clip,
      // resume) so those keep the instant zero-latency player from the start;
      // otherwise stream now and hot-swap to the blob when the background
      // prefetch finishes.
      const bounded = (p, ms) => Promise.race([p, new Promise(r => setTimeout(() => r(null), ms))]);
      const waitMs = (window.__PREVIEW_BLOB_WAIT_MS != null) ? window.__PREVIEW_BLOB_WAIT_MS : 1500;
      let objURL = null;
      try { objURL = _previewBlobPromise ? await bounded(_previewBlobPromise, waitMs) : null; }
      catch (_) {}
      if (!_playerSetupDone) return;   // player was reset while fetching
      if (objURL) {
        if (_previewObjURL) { try { URL.revokeObjectURL(_previewObjURL); } catch (_) {} }
        _previewObjURL = objURL;
        vid.src = _previewObjURL;
      } else {
        console.info('preview: streaming now, blob upgrade armed');
        vid.src = _withToken(`${API_BASE}/download/${videoKey}`);
        _armPreviewBlobUpgrade(vid);
      }
      _finishPreviewStepWhenPlayable(vid);
    })();

    // Buffering must be VISIBLE: on the streaming source the buffer can run
    // dry mid-play and the video froze with no hint (field report: "plays and
    // stops abruptly"). Show the loading overlay whenever the element reports
    // it is waiting for data, hide it the moment playback resumes.
    if (!vid._bufWired) {
      vid._bufWired = true;
      const _showBuf = () => {
        // onclick set = the failure-retry overlay owns the element - don't
        // repurpose it as a buffering spinner.
        if (!_playerSetupDone || !loadingEl || loadingEl.onclick) return;
        loadingEl.style.display = 'flex';
      };
      const _hideBuf = () => { if (loadingEl && !loadingEl.onclick) loadingEl.style.display = 'none'; };
      vid.addEventListener('waiting', _showBuf);
      vid.addEventListener('stalled', () => { if (!vid.paused) _showBuf(); });
      ['playing', 'seeked', 'loadeddata'].forEach(ev => vid.addEventListener(ev, _hideBuf));
    }

    // A failed source (stalled network, corrupt blob, expired token) must
    // never strand the spinner: first failure retries as a live stream, a
    // second shows a tappable retry instead of spinning forever.
    if (!vid._errWired) {
      vid._errWired = true;
      let srcErrors = 0;
      vid.addEventListener('error', () => {
        if (!_playerSetupDone || !videoKey) return;
        srcErrors++;
        if (srcErrors <= 1) {
          console.warn('player source failed - falling back to streaming');
          vid.src = _withToken(`${API_BASE}/download/${videoKey}`);
          try { vid.load(); } catch (_) {}
          return;
        }
        if (loadingEl) {
          loadingEl.style.display = 'flex';
          loadingEl.textContent = t('capedit.previewFailed');
          loadingEl.style.cursor = 'pointer';
          loadingEl.onclick = () => {
            srcErrors = 0;
            loadingEl.onclick = null;
            loadingEl.style.cursor = '';
            loadingEl.innerHTML = '<span class="player-spinner" aria-hidden="true"></span><span>' +
                                  _escapeHtml(t('capedit.previewLoading')) + '</span>';
            vid.src = _withToken(`${API_BASE}/download/${videoKey}`);
            try { vid.load(); } catch (_) {}
          };
        }
      });
    }

    vid.addEventListener('loadedmetadata', () => {
      const wrap = document.getElementById('playerWrap');
      if (!wrap) { updatePreviewCaption(); return; }
      // Match the player box to the ACTUAL video orientation so landscape
      // (16:9), square and portrait (9:16) inputs all preview true-to-source
      // instead of being force-cropped into a portrait frame.
      const vw = vid.videoWidth  || 1080;
      const vh = vid.videoHeight || 1920;
      wrap.style.aspectRatio = vw + ' / ' + vh;
      // Fit within a sensible box: portrait constrained by width (tall phone
      // frame), landscape/square constrained by a height budget so a wide clip
      // doesn't render as a tiny strip.
      if (vh >= vw) {
        wrap.style.width = Math.min(260, window.innerWidth * 0.72) + 'px';
      } else {
        const maxW = Math.min(440, window.innerWidth * 0.88);
        wrap.style.width = Math.min(maxW, 300 * (vw / vh)) + 'px';
      }
      _playerDispW = vw;
      videoOrientation = _orientationFor(vw, vh);
      _syncHookSizeLabels();
      // Seek to the first caption so a subtitle is visible immediately. The
      // frame at t=0 almost always sits in the lead-in silence (no caption),
      // which made the overlay look broken until the user scrubbed/played.
      if (captionsData && captionsData.length && isFinite(vid.duration)) {
        try { vid.currentTime = Math.min(vid.duration, captionsData[0].start + 0.05); } catch (_) {}
      }
      renderPlayerFrame();
      updatePreviewCaption();
    }, { once: true });

    function fmtT(s) {
      if (!isFinite(s)) return '0:00';
      const m = Math.floor(s / 60), sec = Math.floor(s % 60);
      return `${m}:${sec.toString().padStart(2, '0')}`;
    }

    function togglePlay() {
      if (vid.paused || vid.ended) _safePlay(vid); else vid.pause();
    }

    // ── Playback rendering ──
    // Progress + captions are driven by a requestAnimationFrame loop WHILE
    // PLAYING (frame-accurate and smooth, unlike the browser's ~4 Hz
    // 'timeupdate'), and by 'seeked' when scrubbing paused. The caption text,
    // its styling, and the active-row highlight are only rewritten when the
    // active SEGMENT changes - so the per-frame cost is a single progress-bar
    // width write and nothing janks, even on long transcripts.
    const thumb = document.getElementById('playerProgThumb');
    let scrubbing = false;
    let _rafId    = 0;
    let _lastCap  = undefined;   // last-rendered caption (undefined = never rendered yet)
    let _lastCapCue = undefined; // cue identity: the line (classic) or line@word (word mode)

    function _applyCaptionStyles() {
      capEl.style.fontFamily = `'${captionFont}', sans-serif`;
      capEl.style.bottom     = (captionMarginPct * 100) + '%';
      const scale = vid.videoWidth ? vid.clientWidth / vid.videoWidth
                                   : vid.clientHeight / (vid.videoHeight || 1920);
      capEl.style.fontSize = Math.max(7, _capBurnPx() * scale) + 'px';
      _applyCapStyleColors(capEl, scale);
      if (vid.videoWidth) {
        const marginH = Math.max(25, Math.floor(vid.videoWidth / 14));
        capEl.style.maxWidth = ((vid.videoWidth - 2 * marginH) / vid.videoWidth * 100).toFixed(2) + '%';
      }
    }

    // Highlight the caption row for the current playhead. Only called on a
    // segment change, so re-reading timestamps from the DOM (kept live for
    // edits) here is cheap.
    function _highlightRow() {
      const t = vid.currentTime;
      document.querySelectorAll('.caption-row').forEach(row => {
        const rs = parseFloat(row.querySelector('.caption-start')?.value) || 0;
        const re = parseFloat(row.querySelector('.caption-end')?.value)   || 0;
        row.classList.toggle('caption-row-active', t >= rs && t <= re + 0.05);
      });
    }

    function renderPlayerFrame() {
      const t = vid.currentTime, dur = vid.duration || 0;
      if (dur > 0 && !scrubbing) {
        const pct = (t / dur * 100) + '%';
        progFill.style.width = pct;
        if (thumb) thumb.style.left = pct;
      }
      timeLbl.textContent = fmtT(t) + ' / ' + fmtT(dur);

      // Touch the caption DOM only when the active CUE changes - the whole
      // line in classic mode, the current word in word-by-word mode.
      const cap = captionsData.find(c => t >= c.start && t <= c.end + 0.05) || null;
      const mode = _capMode();
      let cueKey = cap, cueText = cap ? cap.text : '', cue = null;
      if (cap && (mode === 'word' || mode === 'karaoke')) {
        cue = _wordCueAt(cap, t);
        cueKey = cue ? cap.start + '@' + cue.start : (mode === 'karaoke' ? cap : null);
        if (mode === 'word') cueText = cue ? cue.text : '';
      }
      if (cueKey !== _lastCapCue) {
        _lastCapCue = cueKey;
        _lastCap = cap;
        if (!cap || (!cueText && mode === 'word')) capEl.textContent = '';
        else if (mode === 'word') capEl.textContent = cueText;
        else if (mode === 'karaoke' && cue) capEl.innerHTML = _karaokeHTML(cap, cue, vid.videoWidth || 1080);
        else capEl.textContent = rewrapCaption(cap.text, vid.videoWidth || 1080, _capBurnPx());
        if (cap) _applyCaptionStyles();
        _highlightRow();
      }

      // Hook overlay: visible only inside its time window.
      { const hw = _hookWindow();
        const ph = document.getElementById('playerHook');
        if (ph) {
          const show = !!(hw && t >= hw[0] && t <= hw[1] && ph.dataset.ready === '1');
          if ((ph.style.display !== 'none') !== show) ph.style.display = show ? 'block' : 'none';
        } }

      // B-roll overlay: swap in the selected clip during its moment window.
      _updateBrollOverlay(t);

      _syncProgressBarPreview();
      _syncZoomPreview(t);
    }

    function _playerLoop() {
      renderPlayerFrame();
      _rafId = (!vid.paused && !vid.ended) ? requestAnimationFrame(_playerLoop) : 0;
    }
    function _startLoop() { if (!_rafId) _rafId = requestAnimationFrame(_playerLoop); }
    function _stopLoop()  { if (_rafId) { cancelAnimationFrame(_rafId); _rafId = 0; } renderPlayerFrame(); }

    const playerWrap = document.getElementById('playerWrap');
    if (playerWrap) playerWrap.addEventListener('click', togglePlay);
    if (playBtn) playBtn.addEventListener('click', togglePlay);
    vid.addEventListener('play',  () => { bigPlay.style.opacity = '0'; playBtn.classList.add('is-playing'); _startLoop(); _hideExactCap(); _brollSetPlaying(true); });
    vid.addEventListener('pause', () => { bigPlay.style.opacity = '1'; playBtn.classList.remove('is-playing'); _stopLoop(); scheduleExactCap(); _brollSetPlaying(false); });
    vid.addEventListener('ended', () => { bigPlay.style.opacity = '1'; playBtn.classList.remove('is-playing'); _stopLoop(); _brollSetPlaying(false); });
    // Paused scrubs + programmatic seeks (e.g. seek-to-first-caption on load).
    vid.addEventListener('seeked', () => { _brollResync(); renderPlayerFrame(); _hideExactCap(); if (vid.paused) scheduleExactCap(); });
    // Safety net if the rAF loop isn't running (e.g. a background tab resumes).
    vid.addEventListener('timeupdate', () => { if (vid.paused) renderPlayerFrame(); });

    // Scrub: click or drag progress bar to seek
    function seekToX(clientX) {
      if (!isFinite(vid.duration)) return;
      const r = progWrap.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      vid.currentTime = pct * vid.duration;
      progFill.style.width = (pct * 100) + '%';
      if (thumb) thumb.style.left = (pct * 100) + '%';
    }
    progWrap.addEventListener('mousedown', e => {
      scrubbing = true;
      seekToX(e.clientX);
    });
    document.addEventListener('mousemove', e => { if (scrubbing) seekToX(e.clientX); });
    document.addEventListener('mouseup',   () => { scrubbing = false; });
    progWrap.addEventListener('touchstart', e => { scrubbing = true; seekToX(e.touches[0].clientX); }, { passive: true });
    document.addEventListener('touchmove',  e => { if (scrubbing) seekToX(e.touches[0].clientX); }, { passive: true });
    document.addEventListener('touchend',   () => { scrubbing = false; });
  }

  // ── Drag-to-position: grab the caption (or hook) ON the player ──────────
  function _dragVertical(el, onPct) {
    if (!el) return;
    el.addEventListener('pointerdown', (e) => {
      e.preventDefault(); e.stopPropagation();   // don't toggle play
      const wrap = document.getElementById('playerWrap');
      if (!wrap) return;
      try { el.setPointerCapture(e.pointerId); } catch (_) {}
      const move = (ev) => {
        const r = wrap.getBoundingClientRect();
        onPct(Math.max(0, Math.min(1, (ev.clientY - r.top) / r.height)));
      };
      const stop = (ev) => {
        el.removeEventListener('pointermove', move);
        el.removeEventListener('pointerup', stop);
        el.removeEventListener('pointercancel', stop);
        _hideExactCap(); scheduleExactCap();
      };
      el.addEventListener('pointermove', move);
      el.addEventListener('pointerup', stop);
      el.addEventListener('pointercancel', stop);
    });
    // A bare tap on the overlay shouldn't toggle playback underneath it.
    el.addEventListener('click', (e) => e.stopPropagation());
  }

  function initCaptionDrag() {
    const capEl = document.getElementById('playerCap');
    _dragVertical(capEl, (ratio) => {
      // ratio = pointer distance from the TOP of the frame; caption anchor is
      // its bottom margin as a fraction of height (same as the burn).
      const MIN_PCT = 0.03, MAX_PCT = 0.80;
      captionMarginPct = Math.max(MIN_PCT, Math.min(MAX_PCT, 1 - ratio - 0.02));
      updatePreviewCaption();
    });
    const hookEl = document.getElementById('playerHook');
    _dragVertical(hookEl, (ratio) => {
      const inp = document.getElementById('hookPosition');
      if (!inp) return;
      inp.value = Math.round(Math.max(0, Math.min(1, ratio)) * 100);
      drawHookPreview();
      updatePlayerHook();
    });
  }

  // ── Hook overlay on the main player (DOM approximation; the paused exact
  //    frame provides libass parity). Geometry mirrors drawHookPreview(). ──
  function _hookWindow() {
    if (selectedHookIdx < 0) return null;
    const el = document.getElementById(`hookText${selectedHookIdx}`);
    if (!el || !el.value.trim()) return null;
    const s = parseFloat(document.getElementById('hookStartSec')?.value || '0') || 0;
    const d = parseFloat(document.getElementById('hookDurationSec')?.value || '3') || 3;
    return [s, s + d];
  }

  // Relabel the hook-size options with the real BURN pixel size for the
  // loaded video (fs = min(w,h) * 0.075 * pct). Values stay % for templates.
  function _syncHookSizeLabels() {
    const sel = document.getElementById('hookFontSize');
    const vid = document.getElementById('cutVideo');
    if (!sel) return;
    const vw = (vid && vid.videoWidth)  || 1080;
    const vh = (vid && vid.videoHeight) || 1920;
    const base = Math.min(vw, vh) * 0.075;
    Array.from(sel.options).forEach(o => {
      const pct = parseInt(o.value, 10);
      o.textContent = Math.round(base * pct / 100) + ' px';
    });
  }

  function updatePlayerHook() {
    const ph   = document.getElementById('playerHook');
    const wrap = document.getElementById('playerWrap');
    const vid  = document.getElementById('cutVideo');
    if (!ph || !wrap) return;
    const hw = _hookWindow();
    if (!hw) { ph.dataset.ready = '0'; ph.style.display = 'none'; return; }
    drawHookPreview();   // refresh _hookLines (wrap authority)
    const W = wrap.clientWidth, H = wrap.clientHeight;
    if (!W || !H || !_hookLines.length) { ph.dataset.ready = '0'; ph.style.display = 'none'; return; }
    const sizePct  = parseInt(document.getElementById('hookFontSize')?.value || '100') / 100;
    const fs       = Math.max(8, Math.min(W, H) * 0.075 * sizePct);
    const padH     = fs * 0.55, padV = fs * 0.35, lineH = fs * 1.10, edgePad = fs * 0.7;
    const vPos     = parseInt(document.getElementById('hookPosition')?.value || '10') / 100;
    const blockH   = _hookLines.length * lineH;
    const rawCY    = edgePad + (H - 2 * edgePad) * vPos;
    const centerY  = Math.max(blockH / 2 + padV + 4, Math.min(H - blockH / 2 - padV - 4, rawCY));
    const bg       = document.getElementById('hookBgColor')?.value || '#000000';
    const bgOp     = parseInt(document.getElementById('hookBgOpacity')?.value || '60') / 100;
    const fontCol  = document.getElementById('hookFontColor')?.value || '#FFFFFF';
    const fontName = document.getElementById('hookFont')?.value || 'Heebo';
    const bSize    = parseInt(document.getElementById('hookBorderSize')?.value || '0');
    const bColor   = document.getElementById('hookBorderColor')?.value || '#000000';
    const r = parseInt(bg.slice(1, 3), 16), g = parseInt(bg.slice(3, 5), 16), b = parseInt(bg.slice(5, 7), 16);
    ph.style.background   = `rgba(${r},${g},${b},${bgOp})`;
    ph.style.width        = (W - 2 * edgePad + 2 * padH) + 'px';
    ph.style.top          = (centerY - blockH / 2 - padV) + 'px';
    ph.style.padding      = padV + 'px 0';
    ph.style.fontFamily   = `'${fontName.replace(/([a-z])([A-Z])/g, '$1 $2')}', 'Heebo', sans-serif`;
    ph.style.fontWeight   = '700';
    ph.style.fontSize     = fs + 'px';
    ph.style.lineHeight   = lineH + 'px';
    ph.style.color        = fontCol;
    // Shadow ring, not text-stroke - same rounded-outline rationale as the
    // caption overlay (_outlineShadows): no miter spikes, no glyph overlap.
    ph.style.webkitTextStroke = '';
    ph.style.textShadow = bSize > 0
      ? _outlineShadows(Math.max(0.5, bSize * (W / 270)), bColor) : 'none';
    ph.innerHTML = _hookLines.map(l => '<div class="hook-line">' + _escapeHtml(l) + '</div>').join('');
    ph.dataset.ready = '1';
    // Visibility itself follows the playhead (renderPlayerFrame); when paused
    // inside the window show immediately.
    const vid2 = document.getElementById('cutVideo');
    const t = vid2 ? vid2.currentTime : 0;
    ph.style.display = (t >= hw[0] && t <= hw[1]) ? 'block' : 'none';
  }

  // ── B-roll live preview: one muted <video> per selected clip, shown during
  //    its moment window over the main video (audio keeps playing under it,
  //    exactly like the burn). ──
  let _brollActiveIdx = null;
  function syncBrollPreviewPool() {
    const layer = document.getElementById('brollLayer');
    if (!layer) return;
    const want = {};
    for (const [idx, sel] of Object.entries(stockBrollSelections)) {
      if (sel && sel.download_url) want[idx] = sel;
    }
    // Remove stale pool entries.
    Array.from(layer.children).forEach(v => {
      if (!(v.dataset.momentIdx in want) || v.dataset.url !== want[v.dataset.momentIdx].download_url) v.remove();
    });
    // Add missing ones (preloaded + muted so window entry is seamless).
    for (const [idx, sel] of Object.entries(want)) {
      if (layer.querySelector(`video[data-moment-idx="${idx}"]`)) continue;
      const v = document.createElement('video');
      v.muted = true; v.playsInline = true; v.preload = 'auto'; v.loop = false;
      v.dataset.momentIdx = idx; v.dataset.url = sel.download_url;
      v.src = sel.download_url;
      layer.appendChild(v);
    }
    _brollActiveIdx = null;   // force re-evaluation on the next frame
    const vid = document.getElementById('cutVideo');
    if (vid) _updateBrollOverlay(vid.currentTime);
  }

  function _activeBrollAt(t) {
    for (const [idx, sel] of Object.entries(stockBrollSelections)) {
      if (sel && sel.download_url && t >= sel.start && t < sel.end) return { idx, sel };
    }
    return null;
  }

  function _brollClipTime(sel, t) {
    const clipStart = sel.clip_use_start_seconds || 0;
    const clipEnd   = sel.clip_use_end_seconds;
    let ct = clipStart + (t - sel.start);
    if (clipEnd != null && ct > clipEnd) ct = clipEnd;
    return ct;
  }

  function _updateBrollOverlay(t) {
    const layer = document.getElementById('brollLayer');
    if (!layer) return;
    const vid = document.getElementById('cutVideo');
    const active = _activeBrollAt(t);
    const key = active ? active.idx : null;
    if (key !== _brollActiveIdx) {
      _brollActiveIdx = key;
      Array.from(layer.children).forEach(v => {
        const on = v.dataset.momentIdx === key;
        v.style.display = on ? 'block' : 'none';
        if (!on) { try { v.pause(); } catch (_) {} }
      });
      if (active) {
        const v = layer.querySelector(`video[data-moment-idx="${key}"]`);
        if (v) {
          try { v.currentTime = _brollClipTime(active.sel, t); } catch (_) {}
          if (vid && !vid.paused) { const pr = v.play(); if (pr && pr.catch) pr.catch(() => {}); }
        }
      }
      // Entering/leaving a b-roll window invalidates the exact still.
      _hideExactCap(); if (vid && vid.paused) scheduleExactCap();
    }
  }

  function _brollResync() {
    const vid = document.getElementById('cutVideo');
    if (!vid) return;
    const active = _activeBrollAt(vid.currentTime);
    if (!active) return;
    const layer = document.getElementById('brollLayer');
    const v = layer && layer.querySelector(`video[data-moment-idx="${active.idx}"]`);
    if (v) { try { v.currentTime = _brollClipTime(active.sel, vid.currentTime); } catch (_) {} }
  }

  function _brollSetPlaying(playing) {
    const layer = document.getElementById('brollLayer');
    if (!layer) return;
    Array.from(layer.children).forEach(v => {
      if (v.style.display !== 'none') {
        if (playing) { _brollResync(); const pr = v.play(); if (pr && pr.catch) pr.catch(() => {}); }
        else { try { v.pause(); } catch (_) {} }
      }
    });
  }

  // ── Editor tabs ──
  function switchEditorTab(name) {
    const map = { captions: 'tabCaptions', hook: 'tabHook', broll: 'tabBroll', effects: 'tabEffects' };
    for (const [key, id] of Object.entries(map)) {
      const panel = document.getElementById(id);
      const btn   = document.getElementById('tabBtn' + key.charAt(0).toUpperCase() + key.slice(1));
      if (panel) panel.style.display = key === name ? 'block' : 'none';
      if (btn)   btn.classList.toggle('active', key === name);
    }
    // Opening the hook tab with a hook selected: land the playhead inside the
    // hook window so the user immediately SEES what they're editing.
    if (name === 'hook') {
      // Options rendered while this tab was hidden measured scrollHeight 0 -
      // now that they're visible, size each text field to its real content.
      document.querySelectorAll('.hook-text-input').forEach(_autosizeHookText);
      const hw = _hookWindow();
      const vid = document.getElementById('cutVideo');
      if (hw && vid && (vid.currentTime < hw[0] || vid.currentTime > hw[1])) {
        try { vid.currentTime = hw[0] + 0.1; } catch (_) {}
      }
      updatePlayerHook();
    }
  }

  // ── Sticky preview: dock + shrink the player once its sentinel scrolls out ──
  let _stickyObs = null, _stickyCardObs = null, _stickyReleaseObs = null;
  let _sentinelAboveTop = false, _editorInView = true;
  // FLIP the wrap between its two layouts: snapshot where it WAS, apply the
  // layout change, then transition a transform from the old spot to the new
  // one - so docking/undocking flies smoothly across the screen instead of
  // teleporting (the old keyframe just scaled in place at the corner).
  function _flipPlayerWrap(wrap, mutate) {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !wrap.getBoundingClientRect) { mutate(); return; }
    const from = wrap.getBoundingClientRect();
    mutate();
    const to = wrap.getBoundingClientRect();
    if (!from.width || !to.width) return;
    const dx = from.left - to.left, dy = from.top - to.top, s = from.width / to.width;
    if (Math.abs(dx) < 2 && Math.abs(dy) < 2 && Math.abs(s - 1) < 0.02) return;
    wrap.style.transition = 'none';
    wrap.style.transformOrigin = 'top left';
    wrap.style.transform = `translate(${dx}px, ${dy}px) scale(${s})`;
    // A mid-flight wrap sweeps ACROSS the page and would swallow taps on
    // whatever it passes over (field: hook options unclickable while the
    // dock animated in a loop of scroll adjustments).
    wrap.style.pointerEvents = 'none';
    requestAnimationFrame(() => requestAnimationFrame(() => {
      wrap.style.transition = 'transform 0.35s cubic-bezier(0.2, 0.8, 0.3, 1)';
      wrap.style.transform = '';
    }));
    const done = () => {
      wrap.style.transition = ''; wrap.style.transformOrigin = '';
      wrap.style.pointerEvents = '';
      wrap.removeEventListener('transitionend', done);
    };
    wrap.addEventListener('transitionend', done);
    setTimeout(done, 500);   // backstop: transitionend can be swallowed by a mid-flight retoggle
  }

  function _applyStickyState(player) {
    // Float ONLY while the user is inside the editor area: the sentinel has
    // scrolled past the top AND part of the editor card is still on screen.
    // Scrolling above the card, or past it (burn button / status), un-floats.
    // While ANY editor text field is being EDITED the mini-player also gets
    // out of the way - it covered the very text being typed (field report).
    // It returns on blur (tapping outside) or on scrolling back up.
    const stick = _sentinelAboveTop && _editorInView && !_captionEditFocus;
    if (stick === player.classList.contains('is-stuck')) return;
    const wrap = player.querySelector('.player-wrap');
    _flipPlayerWrap(wrap || player, () => {
      if (stick) {
        // Reserve the full-size slot BEFORE the wrap goes position:fixed, so
        // the layout doesn't shift (a shift would re-trigger the observer and
        // oscillate the docked state forever).
        player.style.minHeight = player.getBoundingClientRect().height + 'px';
        player.classList.add('is-stuck');
      } else {
        player.classList.remove('is-stuck');
        player.style.minHeight = '';
      }
    });
    // The wrap resizes → rescale the caption + hook overlays.
    requestAnimationFrame(() => { updatePreviewCaption(); });
  }
  let _captionEditFocus = false;
  (() => {
    // ANY text edit in the editor (caption rows, hook text, preset name, ...)
    // hides the floating mini-player - it covered the very field being typed
    // into. Scoped to the whole editor card, but TEXT-entry fields only:
    // sliders/checkboxes/selects stay out, since while adjusting those you
    // WANT the mini-player visible to see the effect.
    const card = document.getElementById('captionEditorCard');
    if (!card) return;
    const isEditField = el => {
      if (!el || !el.matches) return false;
      if (el.matches('textarea, [contenteditable="true"]')) return true;
      if (!el.matches('input')) return false;
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      return ['text', 'search', 'email', 'url', 'tel', 'number', 'password'].includes(type);
    };
    card.addEventListener('focusin', e => {
      if (!isEditField(e.target)) return;
      _captionEditFocus = true;
      const player = document.getElementById('captionPlayer');
      if (player) _applyStickyState(player);
    });
    card.addEventListener('focusout', () => {
      // Hopping between two text fields fires focusout before the next
      // focusin - re-check on the next tick so the player doesn't flash back.
      setTimeout(() => {
        const a = document.activeElement;
        const next = !!(a && card.contains(a) && isEditField(a));
        if (next === _captionEditFocus) return;
        _captionEditFocus = next;
        const player = document.getElementById('captionPlayer');
        if (player) _applyStickyState(player);
      }, 80);
    });
  })();

  function initStickyPlayer() {
    const sentinel = document.getElementById('playerStickySentinel');
    const player   = document.getElementById('captionPlayer');
    const card     = document.getElementById('captionEditorCard');
    if (!sentinel || !player || _stickyObs) return;
    const topbar = document.querySelector('.app-topbar');
    const tbH = topbar ? topbar.offsetHeight : 52;
    document.documentElement.style.setProperty('--topbar-h', tbH + 'px');
    // HYSTERESIS (2026-08-07): stick and release act at DIFFERENT lines, and
    // each observer only sets its own direction. A single shared threshold
    // let a scroll position sitting exactly on the line flip stick/unstick
    // forever (layout nudges re-crossed it) - the page visibly oscillated and
    // Playwright clicks on mobile timed out with ever-changing interceptors.
    const _setStick = (on) => {
      if (on === _sentinelAboveTop) return;
      _sentinelAboveTop = on;
      _applyStickyState(player);
    };
    _stickyObs = new IntersectionObserver(([entry]) => {
      // "Past the top" specifically - a sentinel below the viewport (page
      // scrolled ABOVE the editor) must not float the player.
      if (!entry.isIntersecting && entry.boundingClientRect.top < tbH + 6) _setStick(true);
    }, { rootMargin: `-${tbH + 6}px 0px 0px 0px`, threshold: 0 });
    _stickyObs.observe(sentinel);
    _stickyReleaseObs = new IntersectionObserver(([entry]) => {
      // Release only once the sentinel is comfortably back below the bar
      // (40px band) - never at the stick line itself.
      if (entry.isIntersecting) _setStick(false);
    }, { rootMargin: `-${tbH + 46}px 0px 0px 0px`, threshold: 0 });
    _stickyReleaseObs.observe(sentinel);
    if (card) {
      _stickyCardObs = new IntersectionObserver(([entry]) => {
        _editorInView = entry.isIntersecting;
        _applyStickyState(player);
      }, { rootMargin: `-${tbH + 6}px 0px 0px 0px`, threshold: 0 });
      _stickyCardObs.observe(card);
    }
  }

  document.getElementById('fontSelect')?.addEventListener('change', e => {
    captionFont = e.target.value;
    updatePreviewCaption();       // instant: size/position + fallback face
    _ensureCaptionFont(captionFont); // repaint once the chosen face is loaded
  });

  // ── Collapsible cards ──
  function toggleCard(bodyId, headerEl) {
    const body = document.getElementById(bodyId);
    const isCollapsed = headerEl.classList.contains('collapsed');
    headerEl.classList.toggle('collapsed');
    body.style.display = isCollapsed ? 'block' : 'none';
  }

  // Re-expand a collapsible card body when its card is programmatically revealed
  function expandCard(bodyId) {
    const body = document.getElementById(bodyId);
    if (!body) return;
    const header = body.parentElement.querySelector('.card-header');
    if (header) header.classList.remove('collapsed');
    body.style.display = 'block';
  }

  // ── B-roll ──
  let stockBrollSelections = {};

  function openLightbox(url) {
    const lb  = document.getElementById('brollLightbox');
    const vid = document.getElementById('brollLightboxVideo');
    vid.src = url;
    vid.play().catch(() => {});
    lb.classList.add('open');
  }
  function closeLightbox() {
    const lb  = document.getElementById('brollLightbox');
    const vid = document.getElementById('brollLightboxVideo');
    lb.classList.remove('open');
    vid.pause();
    vid.src = '';
  }
  document.getElementById('brollLightbox').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeLightbox();
  });

  function updateBurnBtn() {
    if (!burnMode) return;
    const n = Object.keys(stockBrollSelections).length;
    // Once the video has been burned + downloaded once, subsequent burns are re-burns.
    if (hasBurnedOnce) {
      runBtn.textContent = n > 0
        ? t('run.reburnBrolls', {n: n, s: n > 1 ? 's' : ''})
        : t('run.reburnPlain');
    } else {
      runBtn.textContent = n > 0
        ? t('run.burnBrolls', {n: n, s: n > 1 ? 's' : ''})
        : t('run.burnPlain');
    }
  }
  function validateCaptionTimes() {
    if (!burnMode) return;
    const rows = Array.from(document.querySelectorAll('#captionsList .caption-row'));
    const vid  = document.getElementById('cutVideo');
    const videoDur = (vid && isFinite(vid.duration) && vid.duration > 0) ? vid.duration : Infinity;
    let hasError = false;

    rows.forEach((row, i) => {
      const startInp  = row.querySelector('.caption-start');
      const endInp    = row.querySelector('.caption-end');
      const start     = parseFloat(startInp.value) || 0;
      const end       = parseFloat(endInp.value)   || 0;
      const prevEnd   = i > 0
        ? (parseFloat(rows[i - 1].querySelector('.caption-end').value)   || 0) : 0;
      const nextStart = i < rows.length - 1
        ? (parseFloat(rows[i + 1].querySelector('.caption-start').value) || 0) : Infinity;

      const startBad = start < 0 || start < prevEnd;
      const endBad   = end <= start || end > videoDur || end > nextStart;

      startInp.classList.toggle('time-invalid', startBad);
      endInp.classList.toggle('time-invalid',   endBad);
      startInp.title = startBad
        ? (start < 0 ? t('cap.negStart')
                     : t('cap.overlapPrev', {t: prevEnd.toFixed(2)}))
        : t('cap.seek');
      endInp.title = endBad
        ? (end <= start   ? t('cap.endAfter')
         : end > videoDur ? t('cap.pastEnd', {t: videoDur.toFixed(2)})
                          : t('cap.overlapNext', {t: nextStart.toFixed(2)}))
        : t('cap.seek');

      if (startBad || endBad) hasError = true;
    });

    if (burnMode) runBtn.disabled = hasError || pendingAnalyses > 0;
  }


  // Saved background jobs auto-resume from showApp() - no banner tap needed.
  // The #reconnectBanner markup stays as the manual fallback surface (shown
  // only if an auto-resume attempt errors out mid-flight).

  // Service Worker - takes over polling only when the tab goes to the background
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js')
      .then(() => console.info('[SW] registered'))
      .catch(e => console.warn('[SW] registration failed:', e.message));

    navigator.serviceWorker.addEventListener('message', evt => {
      const { type, callId, result } = evt.data || {};
      console.info(`[SW] message: ${type} callId=${callId}`);
      if (type === 'SW_DONE') {
        const resolve = swResolvers.get(callId);
        if (resolve) { swResolvers.delete(callId); currentPollInfo = null; resolve(result); }
      }
    });

    // Hand off to SW when page is minimized; reclaim when page comes back
    document.addEventListener('visibilitychange', () => {
      const sw = navigator.serviceWorker.controller;
      if (!sw || !currentPollInfo) return;
      if (document.hidden) {
        console.info('[SW] page hidden - handing poll to SW');
        sw.postMessage({ type: 'POLL_START', ...currentPollInfo });
      } else {
        console.info('[SW] page visible - reclaiming poll from SW');
        sw.postMessage({ type: 'POLL_CANCEL', callId: currentPollInfo.callId });
      }
    });
  }

  function triggerStockBroll() {
    document.getElementById('stockBrollRerunBanner').style.display = 'none';
    startStockBrollAnalysis(getEditedCaptions());
  }

  // One-click extras: after captions are ready, auto-run B-roll + hook generation
  // in the BACKGROUND (in parallel, no pipeline lock) if their toggles are on, so
  // the user gets suggestions + hook options without extra clicks while they edit.
  function _startAutoGenerations() {
    if (isAudioInput || !captionsData.length) return;
    const wantBroll = !!document.getElementById('autoBroll')?.checked;
    const wantHook  = !!document.getElementById('autoHook')?.checked;
    if (!wantBroll && !wantHook) return;
    // Keep the processing card + checklist visible so the timers show.
    statusCard.classList.add('visible');
    checklistEl.style.display = 'block';
    if (wantBroll) startStockBrollAnalysis(getEditedCaptions(), { background: true });
    if (wantHook)  triggerGenerateHook({ background: true });
  }

  // Nudge to regenerate hooks when captions changed since they were generated
  // (parallels the stock-B-roll rerun banner).
  function _maybeShowHookRerun() {
    const banner = document.getElementById('hookRerunBanner');
    if (!banner) return;
    const hooksExist = _hookGenSignature && (document.getElementById('hookOptions')?.children.length || 0) > 0;
    banner.style.display = (hooksExist && getCaptionsSignature() !== _hookGenSignature) ? 'flex' : 'none';
  }

  // ── Hook Generator ──────────────────────────────────────────────────────

  function cancelGenerateHook() {
    hookGenAborted = true;
  }

  async function fetchHookThumbnail() {
    if (!videoKey) return;
    await document.fonts.ready;
    try {
      const resp = await apiFetch(`${API_BASE}/thumbnail/${videoKey}/`);
      if (!resp.ok) return;
      const blob = await resp.blob();
      const img  = new Image();
      img.onload = () => { hookThumbnail = img; drawHookPreview(); };
      img.src = URL.createObjectURL(blob);
    } catch (_) {}
  }

  // Draw a representative caption onto the hook preview canvas at the caption's
  // current position/font/size (mirrors the burned look: white text, dark
  // stroke + shadow). Lets the user see hook vs caption placement at a glance.
  function _drawHookPreviewCaption(ctx, W, H) {
    if (!captionsData || !captionsData.length) return;
    const sample = captionsData.find(c => (c.text || '').trim()) || captionsData[0];
    const text = ((sample && sample.text) || '').trim();
    if (!text) return;
    // Scale by the REAL video dimensions, not the thumbnail's: the /thumbnail
    // is downscaled to 400px wide, so scaling by it oversized the caption.
    // Matches the caption editor, which renders at captionFontSize / videoHeight
    // of the frame height.
    const _vid   = document.getElementById('cutVideo');
    const realVW = (_vid && _vid.videoWidth)  || _playerDispW || (hookThumbnail && hookThumbnail.naturalWidth)  || 1080;
    // Scale by WIDTH, not height: the canvas width W (=270) always represents the
    // full video width, and realVW resolves to the TRUE width via _playerDispW
    // even before the <video>'s own metadata is ready. The height path had no
    // reliable source and fell back to 1920, which made the caption tiny. Aspects
    // match, so W/realVW == H/realVH whenever both are known - this just removes
    // the bad fallback.
    const capFs = Math.max(7, _capBurnPx() * (W / realVW));
    window.__hookCapFs = capFs;   // exposed for tests (size must track the editor, not the thumbnail)
    const lines = rewrapCaption(text, realVW, _capBurnPx()).split('\n');
    const lineH = capFs * 1.35;
    // captionMarginPct = the caption block's bottom edge distance from the
    // video bottom, as a fraction of height (same as the player + burn).
    const blockBottom = H - captionMarginPct * H;
    ctx.save();
    ctx.direction    = 'rtl';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'alphabetic';
    ctx.font         = `700 ${capFs}px '${captionFont}', 'Heebo', sans-serif`;
    ctx.lineJoin     = 'round';
    ctx.strokeStyle  = 'rgba(0,0,0,0.85)';
    ctx.lineWidth    = Math.max(1, capFs * 0.16);
    ctx.shadowColor  = 'rgba(0,0,0,0.75)';
    ctx.shadowOffsetX = 2; ctx.shadowOffsetY = 2;
    ctx.fillStyle    = '#FFFFFF';
    lines.forEach((line, i) => {
      const y = blockBottom - (lines.length - 1 - i) * lineH;
      ctx.strokeText(line, W / 2, y);
      ctx.fillText(line, W / 2, y);
    });
    ctx.restore();
  }

  function drawHookPreview() {
    const canvas = document.getElementById('hookPreviewCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    if (hookThumbnail) {
      const newH = Math.round(W * hookThumbnail.naturalHeight / hookThumbnail.naturalWidth);
      if (canvas.height !== newH) canvas.height = newH;
    }
    const H = canvas.height;

    ctx.clearRect(0, 0, W, H);
    if (hookThumbnail) {
      // Cover-fit: fill canvas, crop excess
      const scale = Math.max(W / hookThumbnail.naturalWidth, H / hookThumbnail.naturalHeight);
      const sw = hookThumbnail.naturalWidth * scale, sh = hookThumbnail.naturalHeight * scale;
      ctx.drawImage(hookThumbnail, (W - sw) / 2, (H - sh) / 2, sw, sh);
    } else {
      ctx.fillStyle = '#1a1a2e';
      ctx.fillRect(0, 0, W, H);
    }

    // Overlay the caption at its configured position (#4) so hook and captions
    // are visible together and can be placed without overlapping. Drawn BEFORE
    // the hook (and before the no-hook early-return) so it always shows.
    _drawHookPreviewCaption(ctx, W, H);

    // Retrieve current settings
    const text = selectedHookIdx >= 0
      ? (document.getElementById(`hookText${selectedHookIdx}`)?.value?.trim() || '')
      : '';
    if (!text) return;

    const fontName   = document.getElementById('hookFont')?.value || 'Heebo';
    const fontColor  = document.getElementById('hookFontColor')?.value || '#FFFFFF';
    const bgColor    = document.getElementById('hookBgColor')?.value || '#000000';
    const bgOpacity  = parseInt(document.getElementById('hookBgOpacity')?.value || '60') / 100;
    const vPos       = parseInt(document.getElementById('hookPosition')?.value || '10') / 100;
    const sizePct    = parseInt(document.getElementById('hookFontSize')?.value || '100') / 100;

    const fontSize = Math.max(12, Math.round(Math.min(W, H) * 0.075 * sizePct));
    ctx.direction   = 'rtl';
    ctx.font        = `bold ${fontSize}px '${fontName}', 'Heebo', sans-serif`;
    ctx.textAlign   = 'center';
    ctx.textBaseline = 'middle';

    // Side margin 0.7·fs (was 1.0·fs) so the hook fills more of the width. MUST
    // match build_caption_ass `edge` so the lines wrapped here fit the burned box.
    const edgePad = fontSize * 0.7;
    const maxW    = W - 2 * edgePad;          // max text width
    const padH    = Math.round(fontSize * 0.55); // horizontal box padding
    const padV    = Math.round(fontSize * 0.35); // vertical box padding
    const lineH   = Math.round(fontSize * 1.10); // line height - matches ASS renderer (~1.0-1.1×)

    // Word-wrap text into lines that fit within maxW. Typed newlines are
    // HARD breaks; a paragraph that already fits keeps its exact spacing.
    const lines = [];
    for (const para of text.split('\n')) {
      if (!para.trim()) { lines.push(''); continue; }
      if (ctx.measureText(para.trim()).width <= maxW) { lines.push(para.trim()); continue; }
      const words = para.split(' ').filter(Boolean);
      let cur = '';
      for (const w of words) {
        const test = cur ? cur + ' ' + w : w;
        if (ctx.measureText(test).width > maxW && cur) { lines.push(cur); cur = w; }
        else cur = test;
      }
      if (cur) lines.push(cur);
    }
    // Remember the exact wrap so the burn can reproduce it (WYSIWYG).
    _hookLines = lines.slice();

    const blockH  = lines.length * lineH;
    // Center of block, clamped so the box stays inside the canvas
    const rawCY   = edgePad + (H - 2 * edgePad) * vPos;
    const centerY = Math.max(blockH / 2 + padV + 4,
                    Math.min(H - blockH / 2 - padV - 4, rawCY));
    const boxW = maxW + 2 * padH;
    const boxH = blockH + 2 * padV;
    const bx   = W / 2 - boxW / 2;
    const by   = centerY - boxH / 2;

    // Background box (square - matches ASS BorderStyle=3 which has no rounded corners)
    ctx.save();
    ctx.globalAlpha = bgOpacity;
    ctx.fillStyle   = bgColor;
    ctx.fillRect(bx, by, boxW, boxH);
    ctx.restore();

    // Draw each wrapped line - re-assert all text props after restore
    ctx.direction    = 'rtl';
    ctx.font         = `bold ${fontSize}px '${fontName}', 'Heebo', sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';

    const borderSize  = parseInt(document.getElementById('hookBorderSize')?.value || '0');
    const borderColor = document.getElementById('hookBorderColor')?.value || '#000000';

    if (borderSize > 0) {
      ctx.strokeStyle = borderColor;
      ctx.lineWidth   = borderSize * 2;
      ctx.lineJoin    = 'round';
      lines.forEach((line, i) => {
        ctx.strokeText(line, W / 2, by + padV + (i + 0.5) * lineH);
      });
    }

    ctx.fillStyle = fontColor;
    lines.forEach((line, i) => {
      ctx.fillText(line, W / 2, by + padV + (i + 0.5) * lineH);
    });
  }

  function _hookSettingListeners() {
    const onEdit = () => { drawHookPreview(); _hideExactHook(); scheduleExactHook(); };
    ['hookFont','hookFontColor','hookBgColor','hookBgOpacity','hookPosition','hookFontSize','hookBorderColor','hookBorderSize','hookStartSec','hookDurationSec'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input',  onEdit);
      el.addEventListener('change', onEdit); // color pickers fire 'change' on Safari after picker closes
    });
  }

  // ── Hook design templates (localStorage) ──────────────────────────────────
  function _getHookTemplates() {
    try { return JSON.parse(localStorage.getItem('hookTemplates') || '[]'); } catch { return []; }
  }
  function _saveHookTemplates(list) {
    localStorage.setItem('hookTemplates', JSON.stringify(list));
  }
  function _currentHookDesign() {
    return {
      font:        document.getElementById('hookFont')?.value || 'Heebo',
      fontColor:   document.getElementById('hookFontColor')?.value || '#FFFFFF',
      bgColor:     document.getElementById('hookBgColor')?.value || '#000000',
      bgOpacity:   document.getElementById('hookBgOpacity')?.value || '60',
      fontSize:    document.getElementById('hookFontSize')?.value || '100',
      borderColor: document.getElementById('hookBorderColor')?.value || '#000000',
      borderSize:  document.getElementById('hookBorderSize')?.value || '0',
      position:    document.getElementById('hookPosition')?.value || '10',
    };
  }
  function _applyHookDesign(d) {
    { const sel = document.getElementById('hookFontSize');
      if (sel && d.fontSize != null) d = { ...d, fontSize: String(_snapSizeOption(sel, parseInt(d.fontSize, 10) || 100)) }; }
    const map = {
      hookFont: d.font, hookFontColor: d.fontColor, hookBgColor: d.bgColor,
      hookBgOpacity: d.bgOpacity, hookFontSize: d.fontSize,
      hookBorderColor: d.borderColor, hookBorderSize: d.borderSize,
      hookPosition: d.position,
    };
    for (const [id, val] of Object.entries(map)) {
      const el = document.getElementById(id);
      if (el) el.value = val;
    }
    const opacityValEl = document.getElementById('hookBgOpacityVal');
    const sizeValEl    = document.getElementById('hookFontSizeVal');
    const borderValEl  = document.getElementById('hookBorderSizeVal');
    if (opacityValEl) opacityValEl.textContent = d.bgOpacity + '%';
    if (sizeValEl)    sizeValEl.textContent    = d.fontSize + '%';
    if (borderValEl)  borderValEl.textContent  = d.borderSize + 'px';
    _refreshColorSwatches();
    drawHookPreview();
    // The on-player overlay (same element docked or floating) + the exact
    // still don't watch input events - refresh them so a loaded template
    // (incl. its saved POSITION) shows immediately (field report).
    _hideExactHook();
    scheduleExactHook();
  }
  function _renderTemplateList() {
    const list = _getHookTemplates();
    const el   = document.getElementById('hookTemplateList');
    if (!el) return;
    if (!list.length) {
      el.innerHTML = '<p style="font-size:0.82rem;color:var(--muted);text-align:center">' + t('tpl.none') + '</p>';
      return;
    }
    el.innerHTML = list.map((tpl, i) => `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1.5px solid var(--purple-100);border-radius:10px;background:var(--purple-50)">
        <span style="flex:1;font-size:0.85rem;font-weight:600;color:var(--purple-800);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${tpl.name}</span>
        <button data-tidx="${i}" class="hook-tpl-apply"
          style="padding:4px 10px;border-radius:7px;border:none;background:var(--purple-600);color:#fff;font-size:0.78rem;font-weight:700;cursor:pointer;flex-shrink:0">
          ${t('tpl.apply')}
        </button>
        <button data-tidx="${i}" class="hook-tpl-del"
          style="padding:4px 8px;border-radius:7px;border:1.5px solid #FECACA;background:#FEF2F2;color:var(--red);font-size:0.9rem;font-weight:700;cursor:pointer;flex-shrink:0;display:flex;align-items:center">
          ${ICON.x}
        </button>
      </div>`).join('');
    el.querySelectorAll('.hook-tpl-apply').forEach(btn => {
      btn.addEventListener('click', () => {
        const i = parseInt(btn.dataset.tidx);
        _applyHookDesign(_getHookTemplates()[i]);
        closeHookTemplateModal();
      });
    });
    el.querySelectorAll('.hook-tpl-del').forEach(btn => {
      btn.addEventListener('click', () => _deleteHookTemplate(parseInt(btn.dataset.tidx)));
    });
  }
  function _deleteHookTemplate(idx) {
    const list = _getHookTemplates();
    list.splice(idx, 1);
    _saveHookTemplates(list);
    _renderTemplateList();
  }
  function saveHookTemplate() {
    const nameEl = document.getElementById('hookTemplateName');
    const name   = nameEl?.value.trim();
    if (!name) { nameEl?.focus(); return; }
    const list = _getHookTemplates();
    list.unshift({ name, ..._currentHookDesign() });
    _saveHookTemplates(list);
    if (nameEl) nameEl.value = '';
    _renderTemplateList();
  }
  function openHookTemplateModal() {
    _renderTemplateList();
    const ov = document.getElementById('hookTemplateOverlay');
    if (ov) ov.style.display = 'flex';
    setTimeout(() => document.getElementById('hookTemplateName')?.focus(), 50);
  }
  function closeHookTemplateModal() {
    const ov = document.getElementById('hookTemplateOverlay');
    if (ov) ov.style.display = 'none';
  }

  // Privacy & Terms + Contact open ON-PAGE modals (never navigate away) so a
  // user mid-edit doesn't lose progress just to read the policy or the email.
  function openLegalModal(src, titleKey) {
    const f = document.getElementById('legalFrame');
    // Lazy-load the requested page (legal.html by default, or the account
    // deletion page). Both share hebpipe_lang via localStorage so they render in
    // the user's language, and hide their own "back to pipeline" link when
    // embedded. Reset src when switching pages so the iframe shows the right one.
    const target = src || '/legal.html';
    if (f && f.getAttribute('src') !== target) f.setAttribute('src', target);
    const titleEl = document.getElementById('legalModalTitle');
    if (titleEl) {
      const key = titleKey || 'footer.legal';
      titleEl.setAttribute('data-i18n', key);
      titleEl.textContent = t(key);
    }
    const ov = document.getElementById('legalOverlay');
    if (ov) ov.style.display = 'flex';
  }
  function closeLegalModal() {
    const ov = document.getElementById('legalOverlay');
    if (ov) ov.style.display = 'none';
  }
  function openContactModal() {
    const ov = document.getElementById('contactOverlay');
    if (ov) ov.style.display = 'flex';
  }
  function closeContactModal() {
    const ov = document.getElementById('contactOverlay');
    if (ov) ov.style.display = 'none';
  }
  function copyContactEmail(btn) {
    const email = 'hebrewpipeline@gmail.com';
    const done = () => {
      if (!btn) return;
      const prev = btn.textContent;
      btn.textContent = t('contact.copied');
      setTimeout(() => { btn.textContent = prev; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(email).then(done).catch(() => {});
    } else {
      // Fallback for browsers without the async clipboard API.
      const ta = document.createElement('textarea');
      ta.value = email; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (_) {}
      ta.remove();
    }
  }
  // Esc closes whichever info modal is open.
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const legal = document.getElementById('legalOverlay');
    const contact = document.getElementById('contactOverlay');
    const billing = document.getElementById('billingOverlay');
    if (legal && legal.style.display !== 'none') closeLegalModal();
    if (contact && contact.style.display !== 'none') closeContactModal();
    if (billing && billing.style.display !== 'none') closeBillingModal();
  });

  // Transient AI-service failures (Anthropic 529 "Overloaded", 429, 5xx) reach
  // the client as "ai_busy:<code>" (workers re-raise them as plain errors -
  // the raw SDK exceptions aren't picklable across Modal and used to surface
  // as "Could not deserialize remote exception..."). Show a human message.
  function _friendlyGenError(msg) {
    return /ai_busy|overloaded|deserialize remote exception/i.test(msg || '')
      ? t('err.aiBusy') : (msg || '');
  }

  // ── Background-tool cancellation (reprocess) ──
  // The auto B-roll / hook generators poll in detached async loops. A
  // reprocess resets the editor AROUND them, so their checklist rows sat
  // stuck on "searching…" forever (field report). Bumping the epoch makes
  // every in-flight loop exit at its next tick without touching the fresh
  // run's UI, and the spawned server jobs are cancelled to stop burning
  // compute on results nobody will see.
  let _toolsEpoch = 0;
  let _brollCallId = null, _hookCallId = null;
  function _cancelActiveTools() {
    _toolsEpoch++;
    hookGenAborted = true;   // the hook loop also checks this flag between polls
    for (const id of [_brollCallId, _hookCallId]) {
      if (id) apiFetch(`${API_BASE}/cancel/${id}/`, { keepalive: true }).catch(() => {});
    }
    _brollCallId = _hookCallId = null;
  }

  // ── AI-cost budget (2026-08-09) ───────────────────────────────────────────
  // Each processed video gets GEN_LIMIT hook generations and GEN_LIMIT B-roll
  // searches - these are the two priciest per-tap AI calls. The buttons show
  // what's left in parentheses and disable at 0; budgets reset with every
  // fresh editor (new process / re-process / History re-edit).
  const GEN_LIMIT = 3;
  let _hookGenLeft = GEN_LIMIT, _brollGenLeft = GEN_LIMIT, _suggestGenLeft = GEN_LIMIT;
  function _resetGenBudgets() {
    _hookGenLeft = GEN_LIMIT; _brollGenLeft = GEN_LIMIT; _suggestGenLeft = GEN_LIMIT;
  }
  function _syncGenButtons() {
    const hb = document.getElementById('generateHookBtn');
    if (hb) {
      hb.textContent = t('hook.generate') + ' ' + t('gen.left', { n: _hookGenLeft });
      if (_hookGenLeft <= 0) hb.disabled = true;
    }
    const fb = document.getElementById('findBrollBtn');
    if (fb) {
      fb.textContent = t('stock.find') + ' ' + t('gen.left', { n: _brollGenLeft });
      if (_brollGenLeft <= 0) fb.disabled = true;
    }
    const sb = document.getElementById('suggestCaptionBtn');
    if (sb) {
      sb.textContent = t('sched.suggest') + ' ' + t('gen.left', { n: _suggestGenLeft });
      if (_suggestGenLeft <= 0) sb.disabled = true;
    }
  }

  async function triggerGenerateHook(opts = {}) {
    const background = !!opts.background;   // auto-run: no confirm, no pipeline lock
    const myEpoch = _toolsEpoch;
    if (!videoKey || !captionsData.length) return;
    if (_hookGenLeft <= 0) { _syncGenButtons(); return; }
    // Options already on screen → regenerating discards them (and any edits).
    // Confirm first so a stray tap doesn't wipe a hook the user was refining.
    const optsEl = document.getElementById('hookOptions');
    if (!background && optsEl.style.display !== 'none' && optsEl.children.length) {
      const ok = await showConfirmModal(t('hook.regenTitle'), t('hook.regenBody'), t('hook.regenOk'));
      if (!ok) return;
    }
    const captions = getEditedCaptions();

    const btn        = document.getElementById('generateHookBtn');
    const status     = document.getElementById('hookStatus');
    const optionsEl  = document.getElementById('hookOptions');
    const controlsEl = document.getElementById('hookControls');
    const errEl      = document.getElementById('hookError');

    hookGenAborted        = false;
    if (!background) {
      lockPipelineActions({ activeBtn: 'generateHookBtn', activeCard: 'captionEditorCard', exempt: ['findBrollBtn'] });
      btn.disabled        = true;
    } else {
      _stepActivate('hook');
    }
    document.getElementById('hookRerunBanner') && (document.getElementById('hookRerunBanner').style.display = 'none');
    status.style.display  = 'flex';
    optionsEl.style.display   = 'none';
    controlsEl.style.display  = 'none';
    errEl.style.display   = 'none';
    selectedHookIdx       = -1;

    try {
      const resp = await apiFetchRetry(`${API_BASE}/generate-hook/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ captions_json: JSON.stringify(captions), video_key: videoKey }),
      });
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${resp.status}`);
      }
      const { call_id } = await resp.json();
      _hookCallId = call_id;
      _hookGenLeft = Math.max(0, _hookGenLeft - 1);   // a real spawn = one use

      const pollDeadline = Date.now() + 600_000;
      while (true) {
        if (hookGenAborted || myEpoch !== _toolsEpoch) break;
        try {
          const poll = await apiFetch(`${API_BASE}/generate-hook-poll/${call_id}/`);
          if (poll.status === 200) {
            const result = await poll.json();
            if (!hookGenAborted) { renderHookOptions(result.hooks || []); _hookGenSignature = getCaptionsSignature(); }
            break;
          }
          if (poll.status === 202) { await new Promise(r => setTimeout(r, 3000)); continue; }
          const errBody = await poll.json().catch(() => ({}));
          const err = new Error(errBody.error || `Server error ${poll.status}`);
          err.isServer = true;   // a real job failure, not a connection blip
          throw err;
        } catch (e) {
          // Same backgrounding story as the B-roll poll: a killed in-flight
          // fetch is not a failed job - park while hidden and keep polling.
          if (!e.isServer && Date.now() < pollDeadline) {
            await _whenVisible();
            await new Promise(r => setTimeout(r, 2000)); continue;
          }
          throw e;
        }
      }
    } catch (e) {
      if (!hookGenAborted && myEpoch === _toolsEpoch) {
        _reportError('hook', e.message);
        errEl.textContent   = t('hook.failed', {msg: _friendlyGenError(e.message).slice(0, 120)});
        errEl.style.display = 'block';
      }
    } finally {
      _hookCallId = null;
      const cancelledByReset = myEpoch !== _toolsEpoch;
      _syncGenButtons();   // reflect the spent use (and disable at 0)
      if (!background) {
        unlockPipelineActions();
        btn.disabled       = _hookGenLeft <= 0;
      } else if (!cancelledByReset) {
        // A reprocess already reset the checklist - marking the row done here
        // would stamp the NEW run's pending row with a stale result.
        _stepDone('hook');
      }
      if (!cancelledByReset) status.style.display = 'none';
      hookGenAborted       = false;
    }
  }

  // Grow a hook textarea to fit its text. Guard scrollHeight 0: options are
  // often rendered while the hook TAB is hidden (auto-generation runs in the
  // background) and a hidden element measures 0 - writing that would collapse
  // the field to an empty strip. The CSS min-height keeps one line visible
  // regardless; the real measure runs again when the tab opens.
  function _autosizeHookText(ta) {
    ta.style.height = 'auto';
    if (ta.scrollHeight > 0) ta.style.height = ta.scrollHeight + 'px';
  }

  function renderHookOptions(hooks) {
    selectedHookIdx = -1;
    const optionsEl  = document.getElementById('hookOptions');
    const controlsEl = document.getElementById('hookControls');

    if (!hooks.length) {
      optionsEl.innerHTML = '<p style="color:var(--muted);font-size:0.82rem">' + t('hook.none') + '</p>';
      optionsEl.style.display = 'block';
      return;
    }

    optionsEl.innerHTML = '';
    hooks.forEach((h, i) => {
      const card = document.createElement('div');
      card.id = `hookOption${i}`;
      card.className = 'hook-option';

      const check = document.createElement('span');
      check.className = 'hook-option-check';
      check.innerHTML = ICON.check;
      card.appendChild(check);

      // Editable hook text: rendered as an OBVIOUS input (bordered field +
      // pencil icon) - the old dashed underline read as decoration and testers
      // didn't realize the text was editable at all.
      const wrap = document.createElement('div');
      wrap.className = 'hook-edit-wrap';
      wrap.dir = 'rtl';   // hook text is Hebrew - pencil pins to the LEFT (away
                          // from the text start) in both UI languages
      const ta = document.createElement('textarea');
      ta.id = `hookText${i}`;
      ta.className = 'hook-text-input';
      ta.value = h.text;
      ta.rows = 1;
      ta.title = t('hook.clickEdit');
      ta.setAttribute('aria-label', t('hook.clickEdit'));
      ta.addEventListener('input', () => {
        _autosizeHookText(ta);
        drawHookPreview();
        _hideExactHook(); scheduleExactHook();
      });
      const pencil = document.createElement('span');
      pencil.className = 'hook-edit-pencil';
      pencil.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3.5l3.5 3.5L8 19.5 4 20l.5-4z"/></svg>';
      wrap.appendChild(ta);
      wrap.appendChild(pencil);
      card.appendChild(wrap);
      if (h.rationale) {
        const rationale = document.createElement('p');
        rationale.className = 'hook-tip';
        const lbl = document.createElement('span');
        lbl.className = 'hook-tip-label';
        lbl.textContent = t('hook.tipLabel');
        rationale.appendChild(lbl);
        rationale.appendChild(document.createTextNode(' ' + h.rationale));
        card.appendChild(rationale);
      }

      card.onclick = () => {
        if (selectedHookIdx === i) return; // already selected - don't interrupt editing
        document.querySelectorAll('.hook-option').forEach(el => el.classList.remove('selected'));
        card.classList.add('selected');
        selectedHookIdx = i;
        drawHookPreview();
        // Land the playhead inside the hook window so the pick is instantly visible.
        { const hw = _hookWindow();
          const v = document.getElementById('cutVideo');
          if (hw && v && (v.currentTime < hw[0] || v.currentTime > hw[1])) {
            try { v.currentTime = hw[0] + 0.1; } catch (_) {}
          } }
        _hideExactHook(); scheduleExactHook();
      };

      optionsEl.appendChild(card);

      // Auto-size textarea once it's in the DOM
      requestAnimationFrame(() => _autosizeHookText(ta));
    });

    optionsEl.style.display  = 'block';
    const firstShow = controlsEl.style.display === 'none';
    controlsEl.style.display = 'block';
    _syncHookSizeLabels();
    if (firstShow) _hookSettingListeners();
    drawHookPreview();

    // Hooks now exist → the button re-runs generation. Relabel it and switch its
    // data-i18n key so it stays "Regenerate…" across language toggles too.
    const ghb = document.getElementById('generateHookBtn');
    ghb.setAttribute('data-i18n', 'hook.regenerate');
    ghb.textContent = t('hook.regenerate');
  }

  // ── End Hook Generator ──────────────────────────────────────────────────

  async function startStockBrollAnalysis(captionsOverride, opts = {}) {
    const background = !!opts.background;   // auto-run: no lock, no scroll, no editor freeze
    const myEpoch = _toolsEpoch;
    const _tCancelled = () => myEpoch !== _toolsEpoch;
    const captions = captionsOverride || captionsData;
    if (!captions.length) return;
    if (_brollGenLeft <= 0) { _syncGenButtons(); return; }

    const status = document.getElementById('stockBrollStatus');
    const list   = document.getElementById('stockBrollList');

    status.style.display = 'flex';
    list.innerHTML       = '';
    if (!background) {
      switchEditorTab('broll');
      document.getElementById('tabBroll')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      lockPipelineActions({ activeBtn: 'findBrollBtn', activeCard: 'captionEditorCard', exempt: ['generateHookBtn'] });
      findBrollBtn.disabled = true;
      findBrollBtn.textContent = t('stock.searching');
      document.querySelectorAll('#captionsList .caption-input, #captionsList .caption-time-input, #captionsList .cap-btn').forEach(el => { el.disabled = true; });
    } else {
      _stepActivate('broll');
    }

    bumpPending(+1);
    const stockElapsedEl = document.getElementById('stockBrollElapsed');
    stockElapsedEl.textContent = '0:00';
    let stockSecs = 0;
    const stockTimer = setInterval(() => { stockElapsedEl.textContent = formatTime(++stockSecs); }, 1000);

    try {
      const resp = await apiFetchRetry(`${API_BASE}/stock-broll/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ captions_json: JSON.stringify(captions), video_key: videoKey || '', orientation: videoOrientation }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `Spawn failed: ${resp.status}`);
      }
      const { call_id } = await resp.json();
      _brollCallId = call_id;
      _brollGenLeft = Math.max(0, _brollGenLeft - 1);   // a real spawn = one use

      const pollDeadline = Date.now() + 900_000;
      while (true) {
        if (_tCancelled()) return;
        try {
          const pollResp = await apiFetch(`${API_BASE}/stock-broll-poll/${call_id}/`);
          if (_tCancelled()) return;
          if (pollResp.status === 200) {
            const result = await pollResp.json();
            clearInterval(stockTimer);
            status.style.display = 'none';
            const moments = result.moments || [];
            stockBrollAnalyzed    = true;
            lastAnalyzedSignature = getCaptionsSignature();
            // Cost-limit banner
            const costBanner = document.getElementById('stockCostLimitBanner');
            if (result.cost_limit_hit) {
              costBanner.textContent = t('stock.costLimit', {p: result.moments_processed, t: result.total_moments_identified});
              costBanner.style.display = 'block';
            } else {
              costBanner.style.display = 'none';
            }
            if (!moments.length) {
              const fs = result.filter_stats || {};
              let emptyMsg;
              if (!fs.sonnet_moments_raw) {
                emptyMsg = t('stock.noMoments');
              } else {
                const dropParts = [];
                if (fs.buf_drops)     dropParts.push(t('stock.edgeDrops', {n: fs.buf_drops, them: fs.buf_drops === 1 ? 'it' : 'them'}));
                if (fs.spacing_drops) dropParts.push(t('stock.spacingDrops', {n: fs.spacing_drops}));
                const detail = dropParts.length ? ` (${dropParts.join('; ')})` : '';
                const n = fs.sonnet_moments_raw;
                emptyMsg = t('stock.dropped', {n: n, s: n === 1 ? '' : 's',
                                               them: n === 1 ? 'it' : 'them', detail: detail});
              }
              list.innerHTML = `<p style="color:var(--muted);font-size:0.85rem;padding:8px 0">${emptyMsg}</p>`;
              return;
            }
            renderStockMoments(moments, result.video_context || null);
            return;
          }
          if (pollResp.status === 202) {
            await new Promise(r => setTimeout(r, 5000));
            continue;
          }
          const body = await pollResp.json().catch(() => ({}));
          const err = new Error(body.error || `Server error ${pollResp.status}`);
          err.isServer = true;   // a real job failure, not a connection blip
          throw err;
        } catch (e) {
          // Network failures are never fatal before the deadline: minimizing
          // the app freezes the WebView and kills the in-flight poll while
          // the job keeps running server-side (field report: B-roll "failed"
          // mid-process when backgrounded). Park while hidden, then resume.
          if (!e.isServer && Date.now() < pollDeadline) {
            await _whenVisible();
            await new Promise(r => setTimeout(r, 2000));
            continue;
          }
          throw e;
        }
      }
    } catch (e) {
      clearInterval(stockTimer);
      if (_tCancelled()) return;   // a reprocess reset the UI - leave it alone
      console.error('Stock B-roll error:', e.message);
      _reportError('broll', e.message);
      status.style.display = 'none';
      list.innerHTML = `<p style="color:var(--red);font-size:0.85rem;padding:8px 0">${t('stock.failedRetry', {msg: _friendlyGenError(e.message).slice(0, 160)})} <button onclick="triggerStockBroll()" style="margin-left:8px;font-size:0.8rem;padding:3px 10px;border-radius:6px;border:1px solid var(--red);background:none;color:var(--red);cursor:pointer">${t('stock.retry')}</button></p>`;
    } finally {
      clearInterval(stockTimer);
      _brollCallId = null;
      bumpPending(-1);
      if (!_tCancelled()) {
        if (!background) {
          unlockPipelineActions();
          // Restore caption editor and button
          document.querySelectorAll('#captionsList .caption-input, #captionsList .caption-time-input, #captionsList .cap-btn').forEach(el => { el.disabled = false; });
          _updateDeleteButtons();
          findBrollBtn.disabled = false;
          _syncGenButtons();   // restore the label WITH the remaining count
        } else {
          // A reprocess already reset the checklist - marking the row done
          // would stamp the NEW run's pending row with a stale result.
          _stepDone('broll');
        }
      }
    }
  }

  // Inline stock-clip preview: at most one plays at a time.
  function _stopInlineClip(thumbDiv) {
    const v = thumbDiv.querySelector('video.clip-inline-video');
    if (v) { try { v.pause(); } catch (_) {} v.remove(); }
    thumbDiv.classList.remove('playing');
  }
  function _stopAllInlineClips() {
    document.querySelectorAll('.clip-thumb.playing').forEach(_stopInlineClip);
  }

  function renderStockMoments(moments, videoCtx) {
    const list = document.getElementById('stockBrollList');
    list.innerHTML = '';

    if (moments.length > 0) {
      const summary = document.createElement('div');
      summary.className = 'broll-summary';
      summary.textContent = t('stock.momentsFound', {n: moments.length, s: moments.length !== 1 ? 's' : ''});
      list.appendChild(summary);
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'broll-moments-wrapper';
    if (moments.length > 6) {
      wrapper.style.cssText = 'max-height:520px;overflow-y:auto;padding-right:4px';
    }
    list.appendChild(wrapper);

    moments.forEach((m, momentIdx) => {
      const isCoverage = m.moment_type === 'coverage';
      const card = document.createElement('div');
      card.className = isCoverage ? 'moment-card coverage' : 'moment-card';

      const momentCtx = {
        momentIdx,
        start: m.start,
        end: m.end,
        broll_duration_seconds: m.broll_duration_seconds || 3.0,
        label: m.label || '',
        reasoning: m.reasoning || '',
        broad_search_prompt: m.broad_search_prompt || m.search_query || '',
        strict_eval_prompt: m.strict_eval_prompt || '',
      };

      // Header: time badge + label + dismiss X
      const header = document.createElement('div');
      header.className = 'moment-header';

      const badge = document.createElement('span');
      badge.className = 'moment-time-badge';
      badge.textContent = fmtCapTime(m.start) + ' - ' + fmtCapTime(m.end);

      const label = document.createElement('span');
      label.className = 'moment-label';
      label.textContent = m.label || '';

      // Dismiss button - for coverage: removes card entirely; for emphasis: skip with restore
      const dismissBtn = document.createElement('button');
      dismissBtn.className = 'moment-dismiss-btn';
      dismissBtn.innerHTML = ICON.x;
      dismissBtn.title = isCoverage ? t('stock.skipRhythm') : t('stock.skip');
      dismissBtn.addEventListener('click', () => {
        delete stockBrollSelections[momentIdx];
        syncBrollPreviewPool();
        if (isCoverage) {
          card.remove();
        } else {
          // Collapse card: hide all children except header, show restore button
          Array.from(card.children).forEach(el => { if (el !== header) el.style.display = 'none'; });
          card.classList.add('skipped-emphasis');
          dismissBtn.remove();
          const restoreBtn = document.createElement('button');
          restoreBtn.className = 'moment-restore-btn';
          restoreBtn.textContent = t('stock.restore');
          restoreBtn.addEventListener('click', () => {
            Array.from(card.children).forEach(el => { el.style.display = ''; });
            card.classList.remove('skipped-emphasis');
            restoreBtn.remove();
            header.appendChild(dismissBtn);
          });
          header.appendChild(restoreBtn);
        }
      });

      header.appendChild(badge);
      header.appendChild(label);
      header.appendChild(dismissBtn);
      card.appendChild(header);

      // Rationale (Hebrew) - WHY this moment deserves B-roll. Shown in full, not
      // truncated, so users understand the reasoning behind each suggestion.
      if (m.reasoning) {
        const reasoning = document.createElement('div');
        reasoning.className = 'moment-reasoning';
        reasoning.textContent = m.reasoning;
        card.appendChild(reasoning);
      }

      // Relevant transcript quote for this moment. Prefer the ACTUAL caption
      // text overlapping the moment window (verbatim from the transcript, so it
      // always matches what is said when the B-roll appears); fall back to the
      // backend's excerpt only if no caption overlaps the window.
      const overlapQuote = getEditedCaptions()
        .filter(c => c.end >= m.start - 0.5 && c.start <= m.end + 0.5)
        .map(c => c.text).join(' ').trim();
      const excerptText = overlapQuote || m.transcript_excerpt || m.verbatim_quote || '';
      if (excerptText) {
        const excerpt = document.createElement('blockquote');
        excerpt.className = 'moment-excerpt';
        excerpt.dir = 'rtl';
        excerpt.textContent = '"' + excerptText + '"';
        card.appendChild(excerpt);
      }

      // Clips row
      const clipsContainer = document.createElement('div');
      renderClips(clipsContainer, m.clips || [], m.broad_search_prompt || m.search_query, momentCtx);
      if (m.weak_match) {
        const wm = document.createElement('p');
        wm.className = 'no-clips-msg';
        wm.style.fontStyle = 'italic';
        wm.textContent = t('stock.noMatch');
        clipsContainer.appendChild(wm);
      }
      card.appendChild(clipsContainer);

      wrapper.appendChild(card);
    });
  }

  function renderClips(container, clips, searchQuery, momentCtx) {
    container.innerHTML = '';
    if (!clips || !clips.length) {
      const msg = document.createElement('p');
      msg.className = 'no-clips-msg';
      msg.textContent = t('stock.noClips', {q: searchQuery || t('stock.thisMoment')});
      container.appendChild(msg);
      return;
    }

    const radioGroup = momentCtx ? `stock-broll-${momentCtx.momentIdx}` : `stock-broll-x`;
    const row = document.createElement('div');
    row.className = 'clips-row';

    clips.forEach(clip => {
      const clipCard = document.createElement('div');
      clipCard.className = 'clip-card';

      const thumbDiv = document.createElement('div');
      thumbDiv.className = 'clip-thumb';
      thumbDiv.addEventListener('click', () => {
        // In-app preview: play the actual clip inline (muted, looping)
        // instead of bouncing to the stock site. Tap again to stop. The
        // attribution/view links below still open the source page (license).
        if (thumbDiv.classList.contains('playing')) { _stopInlineClip(thumbDiv); return; }
        if (!clip.preview_url) {
          if (clip.page_url) window.open(clip.page_url, '_blank', 'noopener,noreferrer');
          return;
        }
        _stopAllInlineClips();
        const v = document.createElement('video');
        v.className = 'clip-inline-video';
        v.src = clip.preview_url;
        v.muted = true; v.loop = true; v.autoplay = true; v.playsInline = true;
        v.setAttribute('muted', ''); v.setAttribute('playsinline', '');
        thumbDiv.classList.add('playing');
        // Insert under the badges (absolute-positioned later siblings) so the
        // source/score chips stay visible over the running clip.
        thumbDiv.insertBefore(v, playOverlay);
        v.play().catch(() => {
          _stopInlineClip(thumbDiv);
          if (clip.page_url) window.open(clip.page_url, '_blank', 'noopener,noreferrer');
        });
      });

      if (clip.thumbnail) {
        const img = document.createElement('img');
        img.src = clip.thumbnail;
        img.alt = clip.author || t('stock.clipAlt');
        img.loading = 'lazy';
        thumbDiv.appendChild(img);
      }

      const playOverlay = document.createElement('div');
      playOverlay.className = 'clip-thumb-play';
      const playIcon = document.createElement('div');
      playIcon.className = 'clip-thumb-play-icon';
      playIcon.innerHTML = ICON.play;
      playOverlay.appendChild(playIcon);
      thumbDiv.appendChild(playOverlay);

      const srcBadge = document.createElement('span');
      srcBadge.className = 'clip-source-badge ' + (clip.source || 'pexels');
      srcBadge.textContent = clip.source === 'pixabay' ? 'Pixabay' : 'Pexels';
      thumbDiv.appendChild(srcBadge);

      // Match score (Haiku vision, 0-10) on the thumbnail, with the reason as a
      // tooltip so users can see WHY a clip scored the way it did.
      if (clip.score !== undefined && clip.score !== null) {
        const level = clip.score >= 8 ? 'high' : clip.score >= 5 ? 'mid' : 'low';
        const scoreBadge = document.createElement('span');
        scoreBadge.className = 'clip-score-badge ' + level;
        scoreBadge.textContent = clip.score + '/10';
        const tip = [];
        if (clip.score_reason)    tip.push(clip.score_reason);
        if (clip.frames_observed) tip.push(clip.frames_observed);
        if (tip.length) scoreBadge.title = tip.join('\n');
        thumbDiv.appendChild(scoreBadge);
      }

      const meta = document.createElement('div');
      meta.className = 'clip-meta';

      const authorLink = document.createElement('a');
      authorLink.className = 'clip-author';
      authorLink.href   = clip.author_url || clip.page_url || '#';
      authorLink.target = '_blank';
      authorLink.rel    = 'noopener noreferrer';
      authorLink.textContent = clip.author ? t('stock.by', {author: clip.author}) : clip.source;

      // Toggle button - "Use for video" (acts like radio but can be deselected)
      const selLabel = document.createElement('label');
      selLabel.className = 'clip-select-label';
      const radio = document.createElement('input');
      radio.type = 'checkbox';
      radio.dataset.group = radioGroup;
      radio.addEventListener('change', () => {
        if (radio.checked) {
          // Deselect all others in this group, then select this one
          row.querySelectorAll(`input[data-group="${radioGroup}"]`).forEach(cb => {
            if (cb !== radio) {
              cb.checked = false;
              cb.closest('.clip-card')?.classList.remove('selected');
              cb.closest('label')?.classList.remove('checked');
            }
          });
          clipCard.classList.add('selected');
          selLabel.classList.add('checked');
          if (momentCtx) {
            stockBrollSelections[momentCtx.momentIdx] = {
              start:        momentCtx.start,
              end:          momentCtx.end,
              download_url: clip.preview_url || '',
              source:       clip.source || 'pexels',
              page_url:     clip.page_url || '',
              clip_use_start_seconds: clip.clip_use_start_seconds != null ? clip.clip_use_start_seconds : 0,
              clip_use_end_seconds:   clip.clip_use_end_seconds   != null ? clip.clip_use_end_seconds   : null,
            };
            // Show it in the unified preview right away: jump to the moment.
            syncBrollPreviewPool();
            const _v = document.getElementById('cutVideo');
            if (_v && momentCtx.start != null) { try { _v.currentTime = momentCtx.start + 0.05; } catch (_) {} }
          }
        } else {
          // Toggled off - remove selection
          clipCard.classList.remove('selected');
          selLabel.classList.remove('checked');
          if (momentCtx) { delete stockBrollSelections[momentCtx.momentIdx]; syncBrollPreviewPool(); }
        }
      });
      selLabel.appendChild(radio);
      selLabel.appendChild(document.createTextNode(t('stock.useForVideo')));

      // Small view link
      const viewLink = document.createElement('a');
      viewLink.className = 'clip-view-link';
      viewLink.href   = clip.page_url || '#';
      viewLink.target = '_blank';
      viewLink.rel    = 'noopener noreferrer';
      viewLink.textContent = t('stock.view');

      // Clip window hint (middle/padded strategy)
      if (clip.clip_window_strategy === 'middle' || clip.clip_window_strategy === 'padded') {
        const wn = document.createElement('div');
        wn.className = 'clip-window-note' + (clip.clip_window_strategy === 'padded' ? ' padded' : '');
        const inS  = (clip.clip_use_start_seconds != null ? clip.clip_use_start_seconds : 0).toFixed(1);
        const outS = (clip.clip_use_end_seconds   != null ? clip.clip_use_end_seconds   : 0).toFixed(1);
        wn.textContent = clip.clip_window_strategy === 'padded'
          ? t('stock.tooShort', {s: outS})
          : t('stock.useRange', {'in': inS, 'out': outS});
        meta.appendChild(wn);
      }

      meta.appendChild(authorLink);
      meta.appendChild(selLabel);
      meta.appendChild(viewLink);

      clipCard.appendChild(thumbDiv);
      clipCard.appendChild(meta);
      row.appendChild(clipCard);
    });

    container.appendChild(row);
  }

  function _selectCaption(row, seekSecs) {
    document.querySelectorAll('#captionsList .caption-row-selected')
      .forEach(r => r.classList.remove('caption-row-selected'));
    row.classList.add('caption-row-selected');
    // Audio mode: seek the audio player instead of the (absent) video player.
    const media = isAudioInput ? document.getElementById('cutAudio')
                               : document.getElementById('cutVideo');
    if (media && media.src && isFinite(media.duration)) {
      try { media.currentTime = seekSecs; } catch (_) {}
      media.pause();
      // removed scrollIntoView - don't auto-scroll to preview when selecting a caption
    }
  }

  function _updateDeleteButtons() {
    const rows = document.querySelectorAll('#captionsList .caption-row');
    const onlyOne = rows.length === 1;
    rows.forEach(r => { r.querySelector('.cap-btn-del').disabled = onlyOne; });
  }

  function _createCaptionRow(cap) {
    const row = document.createElement('div');
    row.className = 'caption-row';
    // Word timings ride on the row: getCaptionsFromEditor rebuilds captions
    // from the DOM, and without this they'd be dropped on the first edit sync
    // (word-by-word mode falls back to redistribution when the edited token
    // count stops matching - see _wordCuesFor).
    if (Array.isArray(cap.words) && cap.words.length) {
      try { row.dataset.words = JSON.stringify(cap.words); } catch (_) {}
    }
    if (Array.isArray(cap.kw) && cap.kw.length) {
      try { row.dataset.kw = JSON.stringify(cap.kw); } catch (_) {}
    }

    // Time controls
    const timeWrap = document.createElement('div');
    timeWrap.className = 'caption-time caption-time-wrap';

    function makeTimeRow(labelText, cls, val) {
      const tr = document.createElement('div');
      tr.className = 'caption-time-row';
      const lbl = document.createElement('label');
      lbl.innerHTML = labelText;
      const inp = document.createElement('input');
      inp.type      = 'number';
      inp.className = 'caption-time-input ' + cls;
      inp.value     = val.toFixed(2);
      inp.step      = '0.1';
      inp.min       = '0';
      inp.title     = t('cap.seek');
      inp.dataset.seek = '1';
      inp.addEventListener('click', e => {
        e.stopPropagation();
        _selectCaption(row, parseFloat(inp.value) || 0);
      });
      let _preEdit = null;
      inp.addEventListener('focus', () => { _preEdit = getCaptionsFromEditor(); });
      inp.addEventListener('input', () => {
        if (_preEdit) { _pushCaptionUndo(_preEdit); _preEdit = null; }  // one undo point per edit session
        captionsData = getCaptionsFromEditor();
        updatePreviewCaption();
        validateCaptionTimes();
      });
      tr.appendChild(lbl);
      tr.appendChild(inp);
      return { tr, inp };
    }
    const { tr: startRow, inp: startInp } = makeTimeRow(ICON.play, 'caption-start', cap.start);
    const { tr: endRow,   inp: endInp   } = makeTimeRow(ICON.stop, 'caption-end',   cap.end);
    timeWrap.appendChild(startRow);
    timeWrap.appendChild(endRow);

    // Text field - a TEXTAREA so Enter inserts a real line break (a manual
    // hard break the preview + burn both honor; see rewrapCaption).
    const textInp = document.createElement('textarea');
    textInp.rows      = 1;
    textInp.className = 'caption-input';
    textInp.value     = cap.text;
    textInp.dir       = 'rtl';
    const _growText = () => { textInp.style.height = 'auto'; textInp.style.height = textInp.scrollHeight + 'px'; };
    setTimeout(_growText, 0);   // initial fit once it's in the DOM
    let _preTextEdit = null;
    textInp.addEventListener('focus', () => { _preTextEdit = getCaptionsFromEditor(); });
    textInp.addEventListener('input', () => {
      _growText();
      if (_preTextEdit) { _pushCaptionUndo(_preTextEdit); _preTextEdit = null; }  // one undo point per edit session
      captionsData = getCaptionsFromEditor();
      updatePreviewCaption();
      if (stockBrollAnalyzed && getCaptionsSignature() !== lastAnalyzedSignature) {
        document.getElementById('stockBrollRerunBanner').style.display = 'flex';
      }
      _maybeShowHookRerun();
    });
    textInp.addEventListener('click', e => {
      e.stopPropagation();
      _selectCaption(row, parseFloat(startInp.value) || 0);
    });

    // Action buttons
    const actions = document.createElement('div');
    actions.className = 'caption-actions';

    // Split button ✂
    const splitBtn = document.createElement('button');
    splitBtn.className  = 'cap-btn cap-btn-split';
    splitBtn.innerHTML = ICON.scissors;
    splitBtn.title      = t('cap.split');
    splitBtn.addEventListener('click', () => {
      _pushCaptionUndo();
      const pos   = textInp.selectionStart ?? textInp.value.length;
      const left  = textInp.value.slice(0, pos).trim();
      const right = textInp.value.slice(pos).trim();
      const s = parseFloat(startInp.value) || 0;
      const e = parseFloat(endInp.value)   || 0;
      const mid = parseFloat(((s + e) / 2).toFixed(2));
      textInp.value = left;
      endInp.value  = mid.toFixed(2);
      const newRow = _createCaptionRow({ start: mid, end: e, text: right });
      row.insertAdjacentElement('afterend', newRow);
      _updateDeleteButtons();
      captionsData = getCaptionsFromEditor();
      updatePreviewCaption();
      validateCaptionTimes();
      newRow.querySelector('.caption-input').focus();
    });

    // Add button +
    const addBtn = document.createElement('button');
    addBtn.className  = 'cap-btn cap-btn-add';
    addBtn.textContent = '+';
    addBtn.title      = t('cap.addLine');
    addBtn.addEventListener('click', () => {
      _pushCaptionUndo();
      const e = parseFloat(endInp.value) || 0;
      const nextStart = e;
      const nextRow = row.nextElementSibling;
      const nextEnd = nextRow
        ? (parseFloat(nextRow.querySelector('.caption-start')?.value) || e + 2)
        : e + 2;
      const newRow = _createCaptionRow({ start: nextStart, end: Math.min(nextEnd, nextStart + 2), text: '' });
      row.insertAdjacentElement('afterend', newRow);
      _updateDeleteButtons();
      validateCaptionTimes();
      newRow.querySelector('.caption-input').focus();
    });

    // Delete button −
    const delBtn = document.createElement('button');
    delBtn.className  = 'cap-btn cap-btn-del';
    delBtn.textContent = '−';
    delBtn.title      = t('cap.removeLine');
    delBtn.addEventListener('click', () => {
      _pushCaptionUndo();
      row.remove();
      _updateDeleteButtons();
      captionsData = getCaptionsFromEditor();
      updatePreviewCaption();
      validateCaptionTimes();
    });

    actions.appendChild(splitBtn);
    actions.appendChild(addBtn);
    actions.appendChild(delBtn);

    row.appendChild(timeWrap);
    row.appendChild(textInp);
    row.appendChild(actions);

    // Row click → select + seek to start (buttons handle themselves via stopPropagation)
    row.addEventListener('click', e => {
      if (e.target.closest('.cap-btn')) return;
      _selectCaption(row, parseFloat(startInp.value) || 0);
    });

    return row;
  }

  async function showCaptionEditor() {
    await _ensurePreviewReady();   // preview blob fully downloaded before the editor opens (instant Play)
    _resetExactPreview();   // clear any stale exact frame from a prior video
    // Ensure card body is expanded (may have been collapsed in a previous burn)
    const captionHeader = document.querySelector('#captionEditorCard .card-header');
    if (captionHeader) captionHeader.classList.remove('collapsed');
    const captionBody = document.getElementById('captionBody');
    if (captionBody) captionBody.style.display = 'block';
    toggleCapDesign(true);   // design card opens by default on every fresh editor

    const list = document.getElementById('captionsList');
    list.innerHTML = '';
    captionsData.forEach(cap => list.appendChild(_createCaptionRow(cap)));
    _updateDeleteButtons();
    _resetCaptionUndo();
    document.getElementById('captionEditorCard').style.display = 'block';

    if (isAudioInput) {
      // Audio mode: reduced editor - a native audio player, the editable
      // caption list, and Download SRT / Download audio. No video preview,
      // tabs, hooks, B-roll or burn.
      document.getElementById('editorTabs').style.display = 'none';
      switchEditorTab('captions');
      document.getElementById('captionPlayer').style.display = 'none';
      // Font + size only affect burned captions - irrelevant for audio.
      document.querySelectorAll('#captionEditorCard .caption-controls')
        .forEach(el => { el.style.display = 'none'; });
      document.getElementById('audioPlayer').style.display = 'block';
      const au = document.getElementById('cutAudio');
      if (au && videoKey) au.src = _withToken(`${API_BASE}/download/${videoKey}`);
      document.getElementById('downloadAudioBtn').style.display = 'block';
      const hintEl = document.querySelector('#captionEditorCard .cap-hint-row p');
      if (hintEl) { hintEl.setAttribute('data-i18n', 'capedit.hintAudio'); hintEl.textContent = t('capedit.hintAudio'); }
      burnMode = true;
      runBtn.style.display = 'none';
      document.getElementById('reprocessBtn').style.display = 'none';
      document.getElementById('uploadCard').classList.add('setup-locked');
      document.getElementById('optionsCard').classList.add('setup-locked');
      setTimeout(() => _scrollToBelowTopbar(document.getElementById('captionEditorCard')), 60);
      return;
    }

    // Video mode: undo any audio-mode UI (in case an audio file preceded this).
    document.getElementById('audioPlayer').style.display = 'none';
    document.getElementById('downloadAudioBtn').style.display = 'none';
    document.querySelectorAll('#captionEditorCard .caption-controls')
      .forEach(el => { el.style.display = ''; });
    const _au = document.getElementById('cutAudio'); if (_au) { _au.pause(); _au.removeAttribute('src'); _au.load(); }
    document.getElementById('editorTabs').style.display = 'flex';
    switchEditorTab('captions');
    // Fresh video → clear any prior hooks, reset the AI budgets, and label
    // the generator buttons with their remaining uses.
    _resetGenBudgets();
    const ghb = document.getElementById('generateHookBtn');
    ghb.disabled = false;
    ghb.removeAttribute('data-i18n');   // label is JS-owned (carries the count)
    _syncGenButtons();
    const hookOpts = document.getElementById('hookOptions');
    hookOpts.innerHTML = '';
    hookOpts.style.display = 'none';
    document.getElementById('hookControls').style.display = 'none';
    selectedHookIdx = -1;
    fetchHookThumbnail();
    burnMode = true;
    document.getElementById('uploadCard').classList.add('setup-locked');
    document.getElementById('optionsCard').classList.remove('setup-locked');
    if (currentUploadKey) {
      document.getElementById('reprocessBtn').style.display = 'block';
    }
    runBtn.disabled = false;
    runBtn.style.display = 'block';
    updateBurnBtn();
    setupCaptionPlayer();
    _preloadCaptionFonts();   // warm every face so font switches never flash fallback
    initStickyPlayer();
    syncBrollPreviewPool();
    setTimeout(() => { initCaptionDrag(); updatePreviewCaption(); validateCaptionTimes(); }, 50);
    // Land on the caption editor (its header just below the sticky topbar), not
    // at the bottom of the page - the user should see the editor they just
    // unlocked, not the finished progress card / burn button below it.
    setTimeout(() => _scrollToBelowTopbar(document.getElementById('captionEditorCard')), 60);
  }

  async function doBurn() {
    if (!videoKey) return;
    if (captionsData.length === 0 && Object.keys(stockBrollSelections).length === 0) return;
    const confirmed = await showConfirmModal(
      t('confirm.burnTitle'),
      t('confirm.burnBody'),
      t('confirm.burnOk').replace('&amp;', '&')
    );
    if (!confirmed) return;
    lockPipelineActions({ activeBtn: 'runBtn' });
    // Burn step in the checklist tracks the real burn operation (reset on re-burn)
    if (checkItems.burn) {
      checkItems.burn.className = 'check-item pending';
      if (checkTimeEls.burn) checkTimeEls.burn.textContent = '';
      _stepActivate('burn');
    }
    document.getElementById('burnSuccessBanner').style.display = 'none';
    { const _osb = document.getElementById('openScheduleBtn'); if (_osb) _osb.style.display = 'none';
      const _so = document.getElementById('scheduleOverlay'); if (_so) _so.style.display = 'none'; }

    const edited = getCaptionsFromEditor();

    // No File when the editor was reopened from History - keep the original
    // name so the re-burn is recognisable in the list instead of "video.mp4".
    const fname = selectedFile ? selectedFile.name : (historyEditName || 'video.mp4');
    const burnUrl = new URL(API_BASE + '/burn/');
    burnUrl.searchParams.set('video_key',  videoKey);
    burnUrl.searchParams.set('filename',   fname);
    burnUrl.searchParams.set('font',       captionFont);
    burnUrl.searchParams.set('margin_v',   captionMarginPct.toFixed(4));
    // Burn at the SAME size the preview shows (the old ×1.10 made burned
    // captions bigger than the preview). Preview is the source of truth.
    burnUrl.searchParams.set('font_size',  captionFontSize);

    const burnErrorEl   = document.getElementById('burnError');
    const reprocessBtn  = document.getElementById('reprocessBtn');
    runBtn.disabled     = true;
    runBtn.textContent  = t('run.burning');
    burnErrorEl.style.display    = 'none';
    if (reprocessBtn) reprocessBtn.disabled = true;
    let burnBtnTimer = null;

    // Lock the editor card and collapse it while burn is in progress
    const editorIds = ['captionEditorCard'];
    editorIds.forEach(id => document.getElementById(id)?.classList.add('burning'));
    { const h = document.querySelector('#captionEditorCard .card-header');
      const b = document.getElementById('captionBody');
      if (h && !h.classList.contains('collapsed')) { h.classList.add('collapsed'); if (b) b.style.display = 'none'; } }
    try {
      // POST captions + selected stock B-rolls → get call_id immediately
      const allBroll = Object.values(stockBrollSelections);

      // Collect hook settings if an option was selected
      let hookPayload = null;
      if (selectedHookIdx >= 0) {
        const hookTextEl = document.getElementById(`hookText${selectedHookIdx}`);
        const hookText   = hookTextEl ? hookTextEl.value.trim() : '';
        if (hookText) {
          drawHookPreview();  // refresh _hookLines against the current text/font/size
          hookPayload = {
            text:              hookText,
            lines:             _hookLines,   // exact preview wrap → burn matches WYSIWYG
            font:              document.getElementById('hookFont')?.value || 'Heebo',
            font_color:        document.getElementById('hookFontColor')?.value || '#FFFFFF',
            bg_color:          document.getElementById('hookBgColor')?.value || '#000000',
            bg_opacity:        parseInt(document.getElementById('hookBgOpacity')?.value || '60') / 100,
            font_size_pct:     parseInt(document.getElementById('hookFontSize')?.value || '100'),
            border_color:      document.getElementById('hookBorderColor')?.value || '#000000',
            border_size:       parseInt(document.getElementById('hookBorderSize')?.value || '0'),
            start_seconds:     parseFloat(document.getElementById('hookStartSec')?.value || '0'),
            duration_seconds:  parseFloat(document.getElementById('hookDurationSec')?.value || '3'),
            vertical_position: parseInt(document.getElementById('hookPosition')?.value || '10'),
          };
        }
      }

      pollController = new AbortController();
      _setStage('burn');
      const spawnResp = await apiFetchRetry(burnUrl.toString(), {
        method: 'POST',
        body: JSON.stringify({
          captions: edited, broll: allBroll,
          // Punch-in windows ride the style payload; the server renders these
          // exact spans (never recomputes), so preview == burn.
          caption_style: Object.assign(_captionStylePayload(),
            (() => { const w = _zoomWindows(); return w.length ? { zooms: w } : {}; })()),
          ...(hookPayload ? { hook: hookPayload } : {}),
        }),
        headers: { 'Content-Type': 'application/json' },
        signal: pollController.signal,
      });
      if (!spawnResp.ok) throw new Error(`Burn spawn failed: ${spawnResp.status}`);
      const { call_id } = await spawnResp.json();
      refreshQuota();

      // Poll until burn is done → returns {output_key}
      currentCallId = call_id;
      const outFilename = fname.replace(/\.[^/.]+$/, '') + '_edited.mp4';
      saveJob('burn', call_id, { outputFilename: outFilename });
      const burnResult = await pollForJSON(`${API_BASE}/burn_poll/${call_id}/`, 600_000, call_id);
      clearSavedJob();
      _stepDone('burn');

      // Video is ready on the server - reveal the schedule card NOW (scheduling
      // uses the server-side URL, it never waits on the device download). The
      // success banner appears only after the download below settles.
      window._schedCtx = { outputKey: burnResult.output_key, filename: outFilename, videoKey: videoKey, hasTranscript: _hasTranscript() };

      // Video is ready — reveal the Schedule button (opens the modal). Keep the
      // rest of the pipeline greyed until the device download settles; the
      // Schedule button stays usable throughout.
      unlockPipelineActions();
      lockPipelineActions({ activeBtn: 'openScheduleBtn' });
      const _osb = document.getElementById('openScheduleBtn');
      if (_osb) _osb.style.display = 'block';
      runBtn.disabled = true;
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

      // Hand the finished video to the browser to stream to disk (native
      // progress shelf, no RAM buffering). Non-blocking and does NOT gate
      // scheduling - the schedule path uses the server-side /media/ URL.
      resultDownloadUrl = `${API_BASE}/download/${burnResult.output_key}/?filename=${encodeURIComponent(outFilename)}`;
      resultName = outFilename;
      triggerDownload();
      // Success banner (with "Download again") is truthful and usable.
      hasBurnedOnce = true;   // subsequent burns are re-burns
      document.getElementById('burnSuccessBanner').style.display = 'flex';
      _maybeShowShare();
      celebrateExport();
      // Re-enable editors for another round of changes on the same video
      editorIds.forEach(id => document.getElementById(id).classList.remove('burning'));
      [
        { h: document.querySelector('#captionEditorCard .card-header'), b: document.getElementById('captionBody') },
      ].forEach(({ h, b }) => {
        if (h) { h.classList.remove('collapsed'); if (b) b.style.display = 'block'; }
      });
    } catch (err) {
      if (!_isNetErr(err)) clearSavedJob();
      // Burn didn't finish - stop and hide its checklist row
      if (stepTimers.burn) { clearInterval(stepTimers.burn.id); stepTimers.burn = null; }
      if (checkItems.burn) { checkItems.burn.className = 'check-item pending'; checkItems.burn.style.display = 'none'; }
      if (err.name !== 'AbortError') {
        console.error('Burn error:', err.message);
        editorIds.forEach(id => document.getElementById(id).classList.remove('burning'));
        // Expand editors back so user can retry
        [
          { h: document.querySelector('#captionEditorCard .card-header'), b: document.getElementById('captionBody') },
        ].forEach(({ h, b }) => {
          if (h) { h.classList.remove('collapsed'); if (b) b.style.display = 'block'; }
        });
        _reportError('burn', err.message);
        burnErrorEl.textContent = err.message.length > 200 ? err.message.slice(0, 200) + '…' : err.message;
        burnErrorEl.style.display = 'block';
      }
    } finally {
      unlockPipelineActions();
      runBtn.disabled = false;
      if (reprocessBtn) reprocessBtn.disabled = false;
      updateBurnBtn();
    }
  }

  function switchTab(which, keepGuideCtx) {
    const views = { pipeline: 'pipelineView', history: 'historyView', guide: 'guideView', admin: 'adminView', costs: 'costsView' };
    const tabs  = { pipeline: 'tabPipeline',  history: 'tabHistory',  guide: 'tabGuide',  admin: 'tabAdmin',  costs: 'tabCosts' };
    for (const k of Object.keys(views)) {
      document.getElementById(views[k]).style.display = (k === which) ? '' : 'none';
      document.getElementById(tabs[k]).classList.toggle('active', k === which);
      document.getElementById(tabs[k]).setAttribute('aria-selected', String(k === which));
    }
    if (which === 'history') loadHistory();
    if (which === 'admin') loadAdmin();
    if (which === 'costs') loadCosts();
    // Any tab change that isn't an "i"-icon jump into the guide clears the
    // "back to where you were" context.
    if (!keepGuideCtx) {
      _guideReturn = null;
      const bb = document.getElementById('guideBackBar');
      if (bb) bb.style.display = 'none';
    }
    // Remember the tab so a page refresh returns to it.
    try { localStorage.setItem('hebpipe_tab', which); } catch (_) {}
  }

  // Restore the last-open tab after the app is shown. History/Guide are always
  // available; Admin waits until the role is known (see updateQuotaUI).
  let _pendingTabRestore = null;
  function restoreTab() {
    let saved = null;
    try { saved = localStorage.getItem('hebpipe_tab'); } catch (_) {}
    if (!saved || saved === 'pipeline') return;
    if (saved === 'admin' || saved === 'costs') {
      if (quotaInfo && quotaInfo.role === 'admin') switchTab(saved);
      else _pendingTabRestore = saved;
      return;
    }
    if (saved === 'history' || saved === 'guide') switchTab(saved);
  }

  // ── Guide: accordion, search, deep-link from app "i" icons + back ──
  let _guideReturn = null;   // { tab, cardId } — where an "i" icon jumped from

  function toggleGuideSec(head) {
    const sec = head.closest('.guide-sec');
    const willOpen = !sec.classList.contains('open');
    // Single-open accordion: close every other section.
    document.querySelectorAll('.guide-sec.open').forEach(s => {
      if (s !== sec) { s.classList.remove('open'); s.querySelector('.guide-sec-head').setAttribute('aria-expanded', 'false'); }
    });
    sec.classList.toggle('open', willOpen);
    head.setAttribute('aria-expanded', String(willOpen));
    // On open, bring the section's start to the top (below the sticky bar).
    if (willOpen) requestAnimationFrame(() => _scrollToBelowTopbar(sec));
  }

  // Scroll an element to the top of the viewport, offset by the sticky top bar
  // so the element's own top edge is visible (plain scrollIntoView hides it
  // behind the pinned .app-topbar).
  function _scrollToBelowTopbar(el) {
    const bar = document.querySelector('.app-topbar');
    const offset = ((bar && getComputedStyle(bar).position === 'sticky') ? bar.getBoundingClientRect().height : 0) + 12;
    // A section near the bottom of the page can't be lifted to the top - there
    // isn't enough content below it, so the browser clamps at max scroll and the
    // header stays hidden above the fold. Add just enough bottom room so any
    // section can reach the top.
    const secs = document.querySelector('.guide-secs');
    if (secs) {
      const elTop = el.getBoundingClientRect().top + window.scrollY;
      const needed = elTop - offset + window.innerHeight;
      const deficit = needed - document.documentElement.scrollHeight;
      secs.style.paddingBottom = deficit > 0 ? Math.ceil(deficit + 8) + 'px' : '';
    }
    el.style.scrollMarginTop = Math.round(offset) + 'px';
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // Called by the small "i" icons in app card headers.
  function openGuideSection(key, originCardId) {
    const curTab = document.getElementById('tabHistory').classList.contains('active') ? 'history'
                 : document.getElementById('tabAdmin').classList.contains('active') ? 'admin' : 'pipeline';
    _guideReturn = { tab: curTab, cardId: originCardId || null };
    switchTab('guide', true);
    const bb = document.getElementById('guideBackBar');
    if (bb) {
      bb.style.display = _guideReturn.cardId ? 'block' : 'none';
      if (_guideReturn.cardId) {
        const bar = document.querySelector('.app-topbar');
        const h = (bar && getComputedStyle(bar).position === 'sticky') ? bar.getBoundingClientRect().height : 0;
        bb.style.top = Math.round(h + 8) + 'px';
      }
    }
    const gs = document.getElementById('guideSearch');
    if (gs && gs.value) { gs.value = ''; filterGuide(''); }
    const sec = document.getElementById('gsec-' + key);
    if (!sec) return;
    document.querySelectorAll('.guide-sec.open').forEach(s => {
      if (s !== sec) { s.classList.remove('open'); s.querySelector('.guide-sec-head').setAttribute('aria-expanded', 'false'); }
    });
    sec.classList.add('open');
    sec.querySelector('.guide-sec-head').setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => {
      _scrollToBelowTopbar(sec);
      sec.classList.add('gsec-flash');
      setTimeout(() => sec.classList.remove('gsec-flash'), 1600);
    });
    try { history.replaceState(null, '', '#guide/' + key); } catch (_) {}
  }

  // The editor card's info-dot is context-aware: it opens the guide section
  // for whichever editor tab is active (captions / hook / b-roll / effects).
  function openGuideEditorSection() {
    const key = document.getElementById('tabBtnHook')?.classList.contains('active') ? 'hook'
              : document.getElementById('tabBtnBroll')?.classList.contains('active') ? 'broll'
              : document.getElementById('tabBtnEffects')?.classList.contains('active') ? 'effects'
              : 'captions';
    openGuideSection(key, 'captionEditorCard');
  }

  function returnFromGuide() {
    const r = _guideReturn;   // switchTab() below clears _guideReturn
    switchTab(r && r.tab ? r.tab : 'pipeline');
    if (r && r.cardId) {
      const el = document.getElementById(r.cardId);
      if (el) requestAnimationFrame(() => _scrollToBelowTopbar(el));
    }
    try { history.replaceState(null, '', location.pathname); } catch (_) {}
  }

  // Filter sections by text (searches full content even while collapsed, via
  // textContent), auto-expanding matches and showing a no-results note.
  function filterGuide(q) {
    q = (q || '').trim().toLowerCase();
    let shown = 0;
    document.querySelectorAll('.guide-sec').forEach(sec => {
      const match = !q || (sec.textContent || '').toLowerCase().indexOf(q) !== -1;
      sec.style.display = match ? '' : 'none';
      if (match) shown++;
      const head = sec.querySelector('.guide-sec-head');
      if (q) { sec.classList.toggle('open', match); head.setAttribute('aria-expanded', String(match)); }
      else   { sec.classList.remove('open'); head.setAttribute('aria-expanded', 'false'); }
    });
    const nr = document.getElementById('guideNoResults');
    if (nr) nr.style.display = (q && shown === 0) ? 'block' : 'none';
  }

  // ── Admin: user limits ──
  // ── Admin: measured compute cost ──
  // The numbers behind credit pricing. `gpu_per_src` (GPU seconds burned per
  // second of source) is the one that matters: a plain run near 1 next to an
  // upscale near 20 is what justifies charging the upscale an extra credit.
  let _costDays = 7;

  function _fmtSecs(s) {
    const n = Number(s) || 0;
    if (n < 90) return Math.round(n) + 's';
    if (n < 5400) return Math.round(n / 60) + 'm';
    return (n / 3600).toFixed(1) + 'h';
  }

  // Reopening the Admin tab re-fetches in the background but only redraws when
  // the data actually changed - no loading flash over an already-rendered card.
  let _costSig = null;
  // ── Pricing plans (admin Costs tab, 2026-08-10) ───────────────────────────
  // The packs sold through Google Play. Prices are configured in Play Console
  // (the store is the source of truth - the app shows `formattedPrice` from
  // ProductDetails at purchase time), so this ladder exists ONLY to reason
  // about margin here. Keep it in sync when a price changes in the console.
  const PLAY_PACKS = [
    { credits: 10,  ils: 59 },
    { credits: 30,  ils: 149 },
    { credits: 100, ils: 399 },
  ];
  const PLAY_FEE = 0.15;   // Google Play commission, standard program (<$1M/yr)
  const USD_ILS  = 3.7;    // shown in the note so the assumption is auditable
  const MARGIN_TARGET = 0.90;
  const _ils = v => '₪' + (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));

  // Everything here is DERIVED from the measured cost per video, so the panel
  // re-prices itself as the pipeline gets cheaper or more expensive.
  function _renderPricingPlans(allPerVideoUsd) {
    const wrap = document.getElementById('costPlans');
    if (!wrap) return;
    const rows = document.getElementById('costPlanRows');
    const note = document.getElementById('costPlansNote');
    wrap.style.display = 'block';
    const costCredit = (allPerVideoUsd || 0) * USD_ILS;   // one credit ~ one average video
    if (!(costCredit > 0)) {
      // No measured cost in this window - say so instead of printing a
      // confident 100% margin derived from nothing.
      rows.innerHTML = '';
      note.textContent = t('cost.plansNeedData');
      ['costServe', 'costBreakEven', 'costFloor'].forEach(id => {
        const el = document.getElementById(id); if (el) el.textContent = '-';
      });
      return;
    }
    const keep      = 1 - PLAY_FEE;             // share of the price we actually receive
    const breakEven = costCredit / keep;        // price where profit is exactly zero
    const floor     = costCredit / (keep * (1 - MARGIN_TARGET));
    document.getElementById('costServe').textContent     = _ils(costCredit);
    document.getElementById('costBreakEven').textContent = _ils(breakEven);
    document.getElementById('costFloor').textContent     = _ils(floor);

    rows.innerHTML = '';
    PLAY_PACKS.forEach(pack => {
      const net    = pack.ils * keep;
      const cost   = pack.credits * costCredit;
      const profit = net - cost;
      const margin = net > 0 ? (profit / net) * 100 : 0;
      const tr = document.createElement('tr');

      const name = document.createElement('td');
      name.className = 'cost-mode-name';
      name.textContent = t('cost.planCredits', { n: pack.credits });
      const each = document.createElement('span');
      each.className = 'cost-plan-each';
      each.textContent = t('cost.planEach', { total: _ils(pack.ils),
                                              each: _ils(pack.ils / pack.credits) });
      name.appendChild(each);
      tr.appendChild(name);

      [_ils(profit), margin.toFixed(0) + '%'].forEach(v => {
        const td = document.createElement('td');
        td.textContent = v;
        tr.appendChild(td);
      });
      rows.appendChild(tr);
    });
    note.textContent = t('cost.plansNote', { fee: Math.round(PLAY_FEE * 100),
                                             fx: USD_ILS.toFixed(2) });
  }

  async function loadCosts(opts) {
    const force   = !!(opts && opts.force);
    const loading = document.getElementById('costLoading');
    const errBox  = document.getElementById('costError');
    const empty   = document.getElementById('costEmpty');
    const body    = document.getElementById('costBody');
    if (!loading) return;
    if (_costSig === null) {
      loading.style.display = '';
      errBox.style.display = 'none';
      empty.style.display = 'none';
      body.style.display = 'none';
    }
    const days = _costDays;
    try {
      const resp = await apiFetch(`${API_BASE}/admin/costs?days=${days}`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const c = await resp.json();
      if (days !== _costDays) return;   // a newer range request superseded this one
      const sig = `${days}:${JSON.stringify(c)}`;
      if (!force && _costSig !== null && sig === _costSig) return;   // nothing new
      _costSig = sig;
      loading.style.display = 'none';
      errBox.style.display = 'none';
      if (!c.videos && !c.ai_calls) { body.style.display = 'none'; empty.style.display = 'block'; return; }
      empty.style.display = 'none';
      // The pricing-decision hero: compute + AI, per delivered video.
      document.getElementById('costAllPerVideo').textContent = '$' + (c.all_per_video || 0).toFixed(3);
      document.getElementById('costVideos').textContent   = c.videos;
      document.getElementById('costTotal').textContent    = '$' + (c.usd || 0).toFixed(2);
      document.getElementById('costAi').textContent       = '$' + (c.ai_usd || 0).toFixed(2);
      document.getElementById('costGpu').textContent      = _fmtSecs(c.gpu_secs);
      const aiTable = document.getElementById('costAiTable');
      const aiBody  = document.getElementById('costAiKinds');
      const kinds = Object.keys(c.ai_by_kind || {}).sort(
        (a, b) => (c.ai_by_kind[b].usd || 0) - (c.ai_by_kind[a].usd || 0));
      aiBody.innerHTML = '';
      kinds.forEach(kind => {
        const kk = c.ai_by_kind[kind];
        const tr = document.createElement('tr');
        [kind, kk.n, '$' + (kk.usd || 0).toFixed(3)].forEach((v, i) => {
          const td = document.createElement('td');
          td.textContent = v;
          if (i === 0) td.className = 'cost-mode-name';
          tr.appendChild(td);
        });
        aiBody.appendChild(tr);
      });
      aiTable.style.display = kinds.length ? '' : 'none';
      const modes = document.getElementById('costModes');
      modes.innerHTML = '';
      Object.keys(c.by_mode || {}).sort().forEach(mode => {
        const m = c.by_mode[mode];
        const tr = document.createElement('tr');
        [mode, m.n, (m.gpu_per_src || 0).toFixed(1) + 'x'].forEach((v, i) => {
          const td = document.createElement('td');
          td.textContent = v;
          if (i === 0) td.className = 'cost-mode-name';
          tr.appendChild(td);
        });
        modes.appendChild(tr);
      });
      _renderPricingPlans(c.all_per_video);
      body.style.display = 'block';
    } catch (e) {
      loading.style.display = 'none';
      if (_costSig === null) {
        errBox.textContent = t('admin.loadFailed');
        errBox.style.display = 'block';
      }
    }
  }

  document.getElementById('costRange')?.addEventListener('click', e => {
    const btn = e.target.closest('.cost-range-btn');
    if (!btn) return;
    _costDays = parseInt(btn.dataset.days, 10) || 7;
    document.querySelectorAll('#costRange .cost-range-btn')
      .forEach(b => b.classList.toggle('is-on', b === btn));
    loadCosts();
  });

  let _adminSig = null;
  async function loadAdmin(opts) {
    const force = !!(opts && opts.force);
    // The errors panel loads with the tab; it has its own signature cache, so
    // a no-change refetch costs one request and rebuilds nothing.
    loadAdminErrors(opts).catch(() => {});
    const list = document.getElementById('adminList');
    const loading = document.getElementById('adminLoading');
    const errBox = document.getElementById('adminError');
    if (_adminSig === null) {
      loading.style.display = '';
      errBox.style.display = 'none';
      list.innerHTML = '';
    }
    try {
      const resp = await apiFetch(`${API_BASE}/admin/users`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const { users } = await resp.json();
      const sig = JSON.stringify(users);
      if (!force && _adminSig !== null && sig === _adminSig) return;   // nothing new
      _adminSig = sig;
      loading.style.display = 'none';
      errBox.style.display = 'none';
      list.innerHTML = '';
      users.forEach(u => list.appendChild(_adminRow(u)));
    } catch (e) {
      loading.style.display = 'none';
      if (_adminSig === null) {
        errBox.textContent = t('admin.loadFailed');
        errBox.style.display = 'block';
      }
    }
  }

  // ── Recent errors (admin-only) ────────────────────────────────────────────
  // The push is a headline; this is the report. Same tab-cache discipline as
  // the other admin surfaces: render only when the payload actually changed.
  let _adminErrSig = null;
  async function loadAdminErrors(opts) {
    const force = !!(opts && opts.force);
    const list = document.getElementById('adminErrList');
    const loading = document.getElementById('adminErrLoading');
    const empty = document.getElementById('adminErrEmpty');
    if (!list) return;
    if (_adminErrSig === null) { loading.style.display = ''; list.innerHTML = ''; }
    try {
      const resp = await apiFetch(`${API_BASE}/admin/errors`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const { errors } = await resp.json();
      const sig = JSON.stringify(errors);
      if (!force && _adminErrSig !== null && sig === _adminErrSig) return;
      _adminErrSig = sig;
      loading.style.display = 'none';
      list.innerHTML = '';
      (errors || []).forEach(err => list.appendChild(_adminErrRow(err)));
      empty.style.display = (errors || []).length ? 'none' : '';
    } catch (_) {
      loading.style.display = 'none';
      if (_adminErrSig === null) {
        empty.textContent = t('admin.loadFailed');
        empty.style.display = '';
      }
    }
  }

  function _errAgo(ts) {
    const secs = Math.max(0, Math.round(Date.now() / 1000 - (ts || 0)));
    if (secs < 90) return t('adminErr.now');
    if (secs < 5400) return t('adminErr.minutes', { n: Math.round(secs / 60) });
    if (secs < 172800) return t('adminErr.hours', { n: Math.round(secs / 3600) });
    return t('adminErr.days', { n: Math.round(secs / 86400) });
  }

  function _adminErrRow(err) {
    const ctx = err.context || {};
    const row = document.createElement('div');
    row.className = 'admin-row admin-err-row';

    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'admin-err-head';
    const who = document.createElement('span');
    who.className = 'admin-name';
    who.textContent = err.user || '?';
    const stage = document.createElement('span');
    stage.className = 'admin-src';           // reuse the existing badge chip
    stage.textContent = err.stage || '?';
    const when = document.createElement('span');
    when.className = 'admin-err-when';
    when.textContent = _errAgo(err.ts);
    head.append(who, stage, when);

    const msg = document.createElement('p');
    msg.className = 'admin-err-msg';
    msg.textContent = err.message || '';

    // Everything else is folded away - the list has to stay scannable when
    // twenty reports land at once.
    const details = document.createElement('div');
    details.className = 'admin-err-details';
    details.style.display = 'none';

    const facts = [
      ['adminErr.version', [ctx.platform, err.version].filter(Boolean).join(' ')],
      ['adminErr.job', ctx.video_key || ctx.call_id || ''],
      ['adminErr.duration', ctx.duration ? formatDuration(ctx.duration) : ''],
      ['adminErr.options', _errOptionSummary(ctx.options)],
      ['adminErr.network', [ctx.online === false ? 'offline' : '', ctx.net || '']
        .filter(Boolean).join(' ')],
      ['adminErr.exception', ctx.exception || ''],
      ['adminErr.device', err.ua || ''],
    ];
    facts.forEach(([key, value]) => {
      if (!value) return;
      const line = document.createElement('div');
      line.className = 'admin-err-fact';
      const label = document.createElement('span');
      label.className = 'admin-err-key';
      label.textContent = t(key);
      const val = document.createElement('span');
      val.className = 'admin-err-val';
      val.textContent = value;
      line.append(label, val);
      details.appendChild(line);
    });

    // The breadcrumb trail: what the user was doing, and how long each step
    // took, right up to the failure.
    if (Array.isArray(ctx.trail) && ctx.trail.length) {
      const trail = document.createElement('div');
      trail.className = 'admin-err-trail';
      trail.textContent = ctx.trail
        .map(c => `${c.t}s ${c.ev}${c.d ? ' ' + c.d : ''}`).join('  →  ');
      details.appendChild(trail);
    }
    if (ctx.traceback) {
      const tb = document.createElement('pre');
      tb.className = 'admin-err-trace';
      tb.textContent = ctx.traceback;
      details.appendChild(tb);
    }

    head.addEventListener('click', () => {
      const open = details.style.display !== 'none';
      details.style.display = open ? 'none' : '';
      row.classList.toggle('is-open', !open);
    });

    row.append(head, msg, details);
    return row;
  }

  // Deliberately untranslated technical tokens, identical to the ones the push
  // body uses - this panel is read by one person, and matching the two makes a
  // notification and its row obviously the same event.
  function _errOptionSummary(options) {
    if (!options || typeof options !== 'object') return '';
    const on = [];
    if (options.cut) on.push('cut');
    if (options.captions) on.push('captions');
    if (options.broll) on.push('broll');
    if (options.hook) on.push('hook');
    if (options.enhance_audio) on.push('audio');
    if (options.enhance_video && options.enhance_video !== 'none') on.push(options.enhance_video);
    return on.join(' · ');
  }

  function _adminRow(u) {
    const row = document.createElement('div');
    row.className = 'admin-row';
    const name = document.createElement('div');
    name.className = 'admin-name';
    name.textContent = u.username;
    if (u.role === 'admin') {
      const star = document.createElement('span');
      star.className = 'admin-star';
      star.title = 'admin';
      star.innerHTML = ICON.star;
      name.appendChild(star);
    }
    // Campaign attribution badge (?src= link the account signed up through).
    if (u.src) {
      const srcTag = document.createElement('span');
      srcTag.className = 'admin-src';
      srcTag.textContent = u.src;
      srcTag.title = t('admin.srcTip');
      name.appendChild(srcTag);
    }
    name.title = u.username;
    // Header line: email (grows, ellipsis-safe) + usage count pinned opposite.
    const header = document.createElement('div');
    header.className = 'admin-header';
    const used = document.createElement('div');
    used.className = 'admin-used';
    used.textContent = u.role === 'admin' ? t('admin.unlimited') : t('admin.used', {used: u.videos_used});
    header.append(name, used);
    // Controls sit on their own line so a long email never gets clipped or
    // broken vertically - the name always reads horizontally on the line above.
    const controls = document.createElement('div');
    controls.className = 'admin-controls';
    row.append(header, controls);
    if (u.role !== 'admin') {
      const inp = document.createElement('input');
      inp.type = 'number'; inp.min = -1; inp.max = 100000;
      inp.value = u.base_video_limit == null ? u.video_limit : u.base_video_limit;
      inp.className = 'admin-limit-input';
      const btn = document.createElement('button');
      btn.className = 'admin-save-btn';
      btn.textContent = t('admin.save');
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const resp = await apiFetch(`${API_BASE}/admin/limit`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username: u.username, limit: parseInt(inp.value, 10) }),
          });
          if (!resp.ok) throw new Error('HTTP ' + resp.status);
          btn.innerHTML = ICON.check;
        } catch {
          btn.textContent = t('admin.saveFailed');
        }
        setTimeout(() => { btn.textContent = t('admin.save'); btn.disabled = false; }, 1500);
      };
      controls.append(inp, btn);
    }
    // Reset-password control (available for every account, admins included).
    const pwBtn = document.createElement('button');
    pwBtn.className = 'admin-reset-btn';
    pwBtn.textContent = t('admin.resetPw');
    pwBtn.onclick = () => _startPwReset(row, u, pwBtn);
    controls.append(pwBtn);
    return row;
  }

  // Inline "set a new password" flow: swaps the reset button for a password
  // field + confirm/cancel, POSTs to /admin/reset-password.
  function _startPwReset(row, u, pwBtn) {
    pwBtn.style.display = 'none';
    const inp = document.createElement('input');
    inp.type = 'password';
    inp.autocomplete = 'new-password';
    inp.placeholder = t('admin.newPwPlaceholder');
    inp.className = 'admin-pw-input';
    const ok = document.createElement('button');
    ok.className = 'admin-save-btn admin-pw-ok';
    ok.textContent = t('admin.setPw');
    const cancel = document.createElement('button');
    cancel.className = 'admin-cancel-btn';
    cancel.textContent = t('admin.cancel');
    const cleanup = () => { inp.remove(); ok.remove(); cancel.remove(); pwBtn.style.display = ''; };
    cancel.onclick = cleanup;
    ok.onclick = async () => {
      const pw = inp.value || '';
      if (pw.length < 8) {
        ok.textContent = t('admin.pwTooShort');
        setTimeout(() => { ok.textContent = t('admin.setPw'); }, 1500);
        inp.focus();
        return;
      }
      ok.disabled = true;
      try {
        const resp = await apiFetch(`${API_BASE}/admin/reset-password`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: u.username, new_password: pw }),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        cleanup();
        pwBtn.innerHTML = ICON.check;
        setTimeout(() => { pwBtn.textContent = t('admin.resetPw'); }, 1500);
      } catch {
        ok.disabled = false;
        ok.textContent = t('admin.saveFailed');
        setTimeout(() => { ok.textContent = t('admin.setPw'); }, 1500);
      }
    };
    pwBtn.after(inp, ok, cancel);
    inp.focus();
  }

  // ── History tab ──
  // Reopening the tab renders instantly from the previous fetch and refetches
  // in the BACKGROUND; the list is only rebuilt when the server data changed.
  // A rebuild refetches every thumbnail (the media token rotates, so the URLs
  // never browser-cache) and resets scroll - skipping it is the whole point.
  let _historySig = null;
  async function loadHistory(opts) {
    const force   = !!(opts && opts.force);
    const list    = document.getElementById('historyList');
    const empty   = document.getElementById('historyEmpty');
    const loading = document.getElementById('historyLoading');
    if (_historySig === null) {
      loading.style.display = '';
      empty.style.display   = 'none';
      list.innerHTML = '';
    }
    try {
      const resp = await apiFetch(`${API_BASE}/jobs/`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const { jobs } = await resp.json();
      const sig = JSON.stringify(jobs || []);
      if (!force && _historySig !== null && sig === _historySig) return;   // nothing new
      _historySig = sig;
      loading.style.display = 'none';
      list.innerHTML = '';
      if (!jobs || !jobs.length) { empty.textContent = t('hist.empty'); empty.style.display = ''; return; }
      empty.style.display = 'none';
      jobs.forEach(job => list.appendChild(_historyCard(job)));
    } catch (e) {
      loading.style.display = 'none';
      if (_historySig === null) {
        empty.textContent = t('hist.loadError');
        empty.style.display = '';
      }
    }
  }

  function _fmtJobDate(ts) {
    if (!ts) return '';
    return new Date(ts * 1000).toLocaleString('en-GB',
      { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  }

  function _fmtJobDuration(secs) {
    if (!secs) return '';
    const m = Math.floor(secs / 60), sSec = Math.round(secs % 60);
    return `${m}:${String(sSec).padStart(2, '0')}`;
  }

  function _historyCard(job) {
    const card = document.createElement('div');
    card.className = 'history-card';
    const isAudio = job.key.endsWith('.m4a');

    let thumb;
    if (isAudio) {
      // No video frame to grab - show an audio glyph placeholder.
      thumb = document.createElement('div');
      thumb.className = 'history-thumb history-thumb-audio';
      thumb.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V6l10-2v12"/><circle cx="6" cy="18" r="3"/><circle cx="16" cy="16" r="3"/></svg>';
    } else {
      thumb = document.createElement('img');
      thumb.className = 'history-thumb';
      thumb.loading = 'lazy';
      thumb.alt = '';
      thumb.src = _withToken(`${API_BASE}/thumbnail/${job.key}/`);
      thumb.onerror = () => { thumb.style.visibility = 'hidden'; };
    }

    const info = document.createElement('div');
    info.className = 'history-info';
    const name = document.createElement('div');
    name.className = 'history-name';
    name.textContent = job.name || t('hist.videoFallback');
    const meta = document.createElement('div');
    meta.className = 'history-meta';
    const bits = [_fmtJobDate(job.ts)];
    if (job.duration) bits.push(_fmtJobDuration(job.duration));
    if (job.size) bits.push(`${(job.size / 1048576).toFixed(0)} MB`);
    meta.textContent = bits.filter(Boolean).join(' \u00B7 ');
    info.append(name, meta);

    const actions = document.createElement('div');
    actions.className = 'history-actions';
    const dl = document.createElement('button');
    dl.className = 'history-btn';
    dl.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11m0 0l-4-4m4 4l4-4M5 19h14"/></svg>';
    dl.title = t('hist.download');
    dl.onclick = () => {
      const stem = (job.name || 'video').replace(/\.[^/.]+$/, '');
      const fname = isAudio ? stem + '_clean.m4a' : stem + '_edited.mp4';
      _browserDownload(`${API_BASE}/download/${job.key}/?filename=${encodeURIComponent(fname)}`, fname);
      // Native saves run through a tracked task - spin THIS row's button
      // until it settles (the generic download buttons don't exist here).
      if (_nativeDownloadTask) {
        _btnBusy(dl, true);
        _nativeDownloadTask.finally(() => { if (dl.isConnected) _btnBusy(dl, false); });
      }
    };
    const sch = document.createElement('button');
    sch.className = 'history-btn';
    sch.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="16" rx="2.5"/><path d="M4 9.5h16M8 3v4M16 3v4"/></svg>';
    sch.title = t('hist.schedule');
    sch.onclick = () => openScheduleModal({
      outputKey: job.key, filename: job.name || 'video', videoKey: '', hasTranscript: false,
    });

    const del = document.createElement('button');
    del.className = 'history-btn history-btn-danger';
    del.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 12.5h9l1-12.5"/></svg>';
    del.title = t('hist.delete');
    del.onclick = async () => {
      const ok = await showConfirmModal(t('hist.deleteTitle'),
        t('hist.deleteBody', {name: job.name}), t('confirm.delete'));
      if (!ok) return;
      // Remove the card in place - no full-tab reload (a reload refetched the
      // whole list + every thumbnail and reset the scroll position).
      _btnBusy(del, true);
      try {
        // Retrying fetch: a backgrounding blip (user switches away mid-tap)
        // parks and resumes instead of failing the delete.
        const resp = await apiFetchRetry(`${API_BASE}/jobs/${job.key}/`, { method: 'DELETE' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        // Keep the tab cache in step with the in-place removal, so the next
        // tab open doesn't see a "changed" list and rebuild everything.
        try { _historySig = JSON.stringify(JSON.parse(_historySig || '[]').filter(j => j.key !== job.key)); } catch (_) {}
        card.style.overflow = 'hidden';
        card.style.transition = 'opacity 0.18s, max-height 0.25s 0.1s, margin 0.25s 0.1s, padding 0.25s 0.1s';
        card.style.maxHeight = card.offsetHeight + 'px';
        requestAnimationFrame(() => {
          card.style.opacity = '0';
          card.style.maxHeight = '0'; card.style.marginBottom = '0';
          card.style.paddingTop = '0'; card.style.paddingBottom = '0';
        });
        setTimeout(() => {
          card.remove();
          const list = document.getElementById('historyList');
          if (list && !list.children.length) {
            const empty = document.getElementById('historyEmpty');
            if (empty) { empty.textContent = t('hist.empty'); empty.style.display = ''; }
          }
        }, 400);
      } catch (_) {
        _btnBusy(del, false);
        celebrateToast(t('hist.deleteFailed'));
      }
    };
    // Re-open a burned export in the editor. Only offered when the server says
    // the job carries its editor state AND its cut source is still on the
    // volume (jobs burned before this feature shipped never will).
    let edit = null;
    if (job.editable && !isAudio) {
      edit = document.createElement('button');
      edit.className = 'history-btn';
      edit.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3.5l3.5 3.5L8 19.5 4 20l.5-4z"/></svg>';
      edit.title = t('hist.edit');
      edit.onclick = () => editFromHistory(job, edit);
    }

    // Scheduling posts a video to social platforms - not applicable to audio.
    if (isAudio) actions.append(dl, del);
    else         actions.append(...[edit, sch, dl, del].filter(Boolean));

    card.append(thumb, info, actions);
    return card;
  }

/* ── Re-edit a previous export ──────────────────────────────────────────────
   Reopens the editor on a finished burn: the SAME cut source plus the captions,
   hook, B-roll picks and styling that produced it. Captions are baked into the
   output permanently, so the re-burn targets the stored `src_key`, which the
   backend pins for the job's lifetime. Re-burning spends no credit (quota is
   consumed at /process spawn), which is the point of this path over
   re-uploading to fix a typo. */
  let historyEditName = '';   // names the re-burn's History entry (no File here)

  function _applyCaptionStyleValues(style) {
    const map = { capFontColor:  style.font_color,
                  capBorderColor: style.border_color,
                  capBorderSize:  style.border_size,
                  capBgColor:     style.bg_color,
                  capBgOpacity:   style.bg_opacity != null ? Math.round(style.bg_opacity * 100) : null,
                  capStyleMode:   style.mode || 'classic',
                  capHighlightColor: style.highlight_color || null,
                  capProgressColor:  style.progress_color || null };
    for (const [id, v] of Object.entries(map)) {
      const el = document.getElementById(id);
      if (el && v != null) el.value = v;
    }
    const bsv = document.getElementById('capBorderSizeVal');
    if (bsv && style.border_size != null) bsv.textContent = style.border_size + 'px';
    const bov = document.getElementById('capBgOpacityVal');
    if (bov && style.bg_opacity != null) bov.textContent = Math.round(style.bg_opacity * 100) + '%';
    // Only write toggles the payload actually CARRIES: presets are looks and
    // deliberately omit progress_bar - forcing it false here was the
    // "progress bar resets when I change a setting" bug.
    if ('progress_bar' in style) {
      const e = document.getElementById('capProgressBar'); if (e) e.checked = !!style.progress_bar;
    }
    if ('auto_zoom' in style) {
      const e = document.getElementById('fxAutoZoom'); if (e) e.checked = !!style.auto_zoom;
    }
    if (style.zoom_strength) {
      const e = document.getElementById('fxZoomStrength'); if (e) e.value = style.zoom_strength;
    }
    _syncStyleModeUI();
  }

  // Only the CHOSEN hook is stored, so rebuild a one-option list and select it.
  // Must run AFTER showCaptionEditor(), which clears any previous hook UI.
  function _restoreHookState(hook) {
    if (!hook || !hook.text) return;
    renderHookOptions([{ text: hook.text, rationale: '' }]);
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (!el || v == null) return;
      el.value = v;
      // A <select> silently rejects a value with no matching <option>, leaving
      // it blank and out of sync with what will burn. Snap to the nearest.
      if (el.value === '' && el.tagName === 'SELECT' && el.options.length) {
        const n = parseFloat(v);
        el.value = String(isFinite(n) ? _snapSizeOption(el, n) : el.options[0].value);
      }
    };
    set('hookFont',        hook.font);
    set('hookFontColor',   hook.font_color);
    set('hookBgColor',     hook.bg_color);
    set('hookBgOpacity',   hook.bg_opacity != null ? Math.round(hook.bg_opacity * 100) : null);
    set('hookFontSize',    hook.font_size_pct);
    set('hookBorderColor', hook.border_color);
    set('hookBorderSize',  hook.border_size);
    set('hookStartSec',    hook.start_seconds);
    set('hookDurationSec', hook.duration_seconds);
    set('hookPosition',    hook.vertical_position);
    _syncHookSizeLabels();
    const card = document.getElementById('hookOption0');
    if (card) card.classList.add('selected');
    selectedHookIdx = 0;
    drawHookPreview();
    updatePlayerHook();
  }

  // The candidate cards came from an analysis we don't store, so show what was
  // kept rather than a misleadingly empty B-roll tab. A fresh search replaces it.
  function _renderKeptBroll(items) {
    const list = document.getElementById('stockBrollList');
    if (!list) return;
    list.innerHTML = '';
    if (!items.length) return;
    const p = document.createElement('p');
    p.className = 'broll-kept-note';
    p.textContent = t('stock.keptFromExport', {n: items.length});
    list.appendChild(p);
  }

  async function editFromHistory(job, btn) {
    // Visible feedback: the state fetch + rehydration can take a few seconds
    // on mobile, and a silently-disabled pencil reads as a dead button.
    if (btn) { _btnBusy(btn, true); btn.title = t('hist.editLoading'); }
    try {
      // Retrying fetch: parks over a backgrounding blip instead of failing.
      const resp = await apiFetchRetry(`${API_BASE}/jobs/${job.key}/edit/`);
      // 410 = the cut source was cleaned up; 404 = burned before this shipped.
      if (resp.status === 410 || resp.status === 404) {
        celebrateToast(t('hist.editGone'));
        if (btn) btn.remove();
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const st = await resp.json();

      resetStatus();            // drop any stale checklist / editor / hook / B-roll
      switchTab('pipeline');

      historyEditName  = st.name || 'video';
      isAudioInput     = false;   // only video burns are re-editable
      applyAudioMode(false);
      captionsData     = st.captions || [];
      videoKey         = st.src_key;
      cutFilename      = historyEditName.replace(/\.[^/.]+$/, '') + '_cut.mp4';
      captionFont      = st.font || 'Heebo';
      captionFontSize  = st.font_size || 48;
      captionMarginPct = (st.margin_v_pct != null) ? st.margin_v_pct : 0.08;
      { const fs = document.getElementById('fontSelect');  if (fs) fs.value = captionFont; }
      // The size control is a <select> of discrete steps: snap so the UI and the
      // value we burn with can never disagree.
      { const ss = document.getElementById('captionFontSizeSlider');
        if (ss) { captionFontSize = _snapSizeOption(ss, captionFontSize); ss.value = String(captionFontSize); } }
      _applyCaptionStyleValues(st.caption_style || {});

      // Picks ride along so the re-burn keeps them without re-running analysis.
      stockBrollSelections = {};
      (st.broll || []).forEach((it, i) => { stockBrollSelections[i] = it; });
      syncBrollPreviewPool();

      hasBurnedOnce = true;     // the action button reads "Re-burn & Download"
      _prefetchPreviewBlob(videoKey);
      await showCaptionEditor();
      _renderKeptBroll(st.broll || []);
      _restoreHookState(st.hook || {});
      updateBurnBtn();
      { const h = document.getElementById('historyEditHint'); if (h) h.style.display = 'block'; }
    } catch (e) {
      console.error('History edit failed:', e && e.message);
      _reportError('history-edit', e && e.message);
      celebrateToast(t('hist.editFailed'));
    } finally {
      if (btn && btn.isConnected) { _btnBusy(btn, false); btn.title = t('hist.edit'); }
    }
  }

/* ── Schedule this video (Metricool handoff helper) ── */
  const SCHED_PLATFORM_LABELS = { instagram: 'Instagram', facebook: 'Facebook', tiktok: 'TikTok', youtube: 'YouTube' };

  function _hasTranscript() {
    return typeof captionsData !== 'undefined' && Array.isArray(captionsData) && captionsData.length > 0;
  }

  // Open the shared scheduling modal for one video.
  // video = { outputKey, filename, videoKey, hasTranscript }
  function openScheduleModal(video) {
    window._schedCtx = video || {};
    window._schedSubmitted = false;   // fresh open - close-guard armed
    document.getElementById('schedVideoName').textContent = (video && video.filename) || '';
    const d = new Date();
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const dateEl = document.getElementById('schedDate');
    if (dateEl && !dateEl.value) { dateEl.value = iso; dateEl.min = iso; }
    document.getElementById('schedStatus').style.display = 'none';
    document.getElementById('schedError').style.display = 'none';
    // Suggest-caption needs the in-memory transcript — fresh videos only.
    const sb = document.getElementById('suggestCaptionBtn');
    if (sb) {
      const has = !!(video && video.hasTranscript);
      sb.style.display = has ? 'block' : 'none';
      sb.disabled = !has || _suggestGenLeft <= 0;
      _syncGenButtons();   // label carries the remaining-uses count
    }
    checkMetricoolStatus();
    document.getElementById('scheduleOverlay').style.display = 'flex';
  }
  function openScheduleFresh() { openScheduleModal(window._schedCtx || {}); }

  // True if the user has entered anything worth warning about before closing.
  function _schedFormDirty() {
    if (window._schedSubmitted) return false;   // already scheduled - nothing to lose
    const cap = (document.getElementById('schedCaption').value || '').trim();
    if (cap) return true;
    if (document.getElementById('schedAutoPublish').checked) return true;
    const yt = document.getElementById('ytTitle');
    if (yt && yt.value.trim()) return true;
    const checked = [...document.querySelectorAll('.sched-platform')].filter(c => c.checked).map(c => c.value).sort().join(',');
    if (checked !== 'instagram') return true;   // default is Instagram only
    const time = document.getElementById('schedTime');
    if (time && time.value && time.value !== '20:00') return true;
    return false;
  }

  async function closeScheduleModal(force) {
    if (!force && _schedFormDirty()) {
      const ok = await showConfirmModal(t('sched.discardTitle'), t('sched.discardBody'), t('sched.discardOk'));
      if (!ok) return;
    }
    document.getElementById('scheduleOverlay').style.display = 'none';
  }

  async function checkMetricoolStatus() {
    const connectEl = document.getElementById('schedConnect');
    const schedBtn = document.getElementById('scheduleBtn');
    try {
      const r = await apiFetch(`${API_BASE}/oauth/status`, { cache: 'no-store' });
      metricoolConnected = (await r.json()).connected;
    } catch {
      metricoolConnected = false;
    }
    if (connectEl) connectEl.style.display = metricoolConnected ? 'none' : 'block';
    if (schedBtn)  schedBtn.style.display  = metricoolConnected ? 'block' : 'none';
    renderMetricoolChip();
  }

  function connectMetricool() {
    window.open(_withToken(`${API_BASE}/oauth/start`), '_blank', 'noopener');
  }

  // ── Account-level Metricool connection chip (topbar) ──
  let metricoolConnected = null;
  function renderMetricoolChip() {
    const chip = document.getElementById('metricoolChip');
    if (!chip) return;
    chip.style.display = 'inline-block';
    chip.textContent = metricoolConnected ? t('mc.connected') : t('mc.connect');
    chip.classList.toggle('connected', !!metricoolConnected);
    chip.title = metricoolConnected ? t('mc.disconnectHint') : '';
  }
  async function refreshMetricoolChip() {
    try {
      const r = await apiFetch(`${API_BASE}/oauth/status`, { cache: 'no-store' });
      metricoolConnected = (await r.json()).connected;
    } catch { metricoolConnected = false; }
    renderMetricoolChip();
  }
  async function onMetricoolChip() {
    if (!metricoolConnected) { connectMetricool(); return; }
    // Connected → offer to disconnect.
    const ok = await showConfirmModal(t('mc.disconnectTitle'), t('mc.disconnectBody'), t('mc.disconnectOk'));
    if (!ok) return;
    try {
      await apiFetch(`${API_BASE}/oauth/disconnect`, { method: 'POST' });
    } catch { /* fall through — refresh reflects real state */ }
    await refreshMetricoolChip();
  }

  // Show YouTube-only required fields when YouTube is selected
  document.addEventListener('change', (e) => {
    if (e.target && e.target.classList && e.target.classList.contains('sched-platform')) {
      const ytOn = !!document.querySelector('.sched-platform[value="youtube"]:checked');
      const yt = document.getElementById('ytFields');
      if (yt) yt.style.display = ytOn ? 'block' : 'none';
    }
  });

  function _schedPlatforms() {
    return [...document.querySelectorAll('.sched-platform:checked')].map(c => c.value);
  }

  document.getElementById('schedAutoPublish').addEventListener('change', (e) => {
    document.getElementById('autoPublishDesc').textContent = e.target.checked
      ? t('sched.apOn')
      : t('sched.apOff');
  });

  async function suggestCaption() {
    if (!_hasTranscript()) return;
    if (_suggestGenLeft <= 0) { _syncGenButtons(); return; }
    const btn = document.getElementById('suggestCaptionBtn');
    const ta = document.getElementById('schedCaption');
    const errEl = document.getElementById('schedError');
    btn.disabled = true; btn.textContent = t('sched.generating');
    errEl.style.display = 'none';
    try {
      const resp = await apiFetch(`${API_BASE}/generate-caption/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          captions_json: JSON.stringify(captionsData),
          video_key: (window._schedCtx && window._schedCtx.videoKey) || '',
          platforms: _schedPlatforms().map(p => SCHED_PLATFORM_LABELS[p]).join(', '),
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const { call_id } = await resp.json();
      _suggestGenLeft = Math.max(0, _suggestGenLeft - 1);   // a real spawn = one use
      while (true) {
        const poll = await apiFetch(`${API_BASE}/generate-caption-poll/${call_id}/`);
        if (poll.status === 200) {
          const result = await poll.json();
          if (result.caption) ta.value = result.caption; else throw new Error('empty caption');
          break;
        }
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 3000)); continue; }
        throw new Error(`Server error ${poll.status}`);
      }
    } catch (e) {
      errEl.textContent = t('sched.captionFailed', {msg: String(e.message).slice(0, 80)});
      errEl.style.display = 'block';
    } finally {
      btn.disabled = _suggestGenLeft <= 0;
      _syncGenButtons();   // restore the label WITH the remaining count
    }
  }

  async function scheduleNow() {
    const errEl = document.getElementById('schedError');
    const statusEl = document.getElementById('schedStatus');
    const btn = document.getElementById('scheduleBtn');
    errEl.style.display = 'none'; statusEl.style.display = 'none';

    const platforms = _schedPlatforms();
    const caption = document.getElementById('schedCaption').value.trim();
    const date = document.getElementById('schedDate').value;
    const time = document.getElementById('schedTime').value;
    const ctx = window._schedCtx || {};
    const ytOn = platforms.includes('youtube');
    const ytTitle = document.getElementById('ytTitle').value.trim();

    const problems = [];
    if (!platforms.length) problems.push(t('sched.fix.platform'));
    if (!caption) problems.push(t('sched.fix.caption'));
    if (!date || !time) problems.push(t('sched.fix.datetime'));
    if (ytOn && !ytTitle) problems.push(t('sched.fix.ytTitle'));
    if (!ctx.outputKey) problems.push(t('sched.fix.burn'));
    if (problems.length) {
      errEl.textContent = t('sched.fixPrefix', {list: problems.join(', ')});
      errEl.style.display = 'block';
      return;
    }

    const payload = {
      platforms,
      caption,
      videoUrl: `${API_BASE}/media/${ctx.outputKey}`,
      dateTime: `${date}T${time}:00`,
      timezone: 'Asia/Jerusalem',
      autoPublish: document.getElementById('schedAutoPublish').checked,
      ytTitle,
      ytPrivacy: document.getElementById('ytPrivacy').value,
      ytKids: document.getElementById('ytKids').checked,
    };

    const orig = btn.textContent;
    btn.disabled = true; btn.textContent = t('sched.scheduling');
    statusEl.className = 'sched-status busy'; statusEl.textContent = t('sched.sending'); statusEl.style.display = 'block';
    try {
      const spawn = await apiFetch(`${API_BASE}/schedule/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (spawn.status === 400) { checkMetricoolStatus(); throw new Error(t('sched.notConnected')); }
      if (!spawn.ok) throw new Error(`HTTP ${spawn.status}`);
      const { call_id } = await spawn.json();

      let result = null;
      while (true) {
        const poll = await apiFetch(`${API_BASE}/schedule-poll/${call_id}/`);
        if (poll.status === 200) { result = await poll.json(); break; }
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 2500)); continue; }
        throw new Error(`Server error ${poll.status}`);
      }
      if (result.error) {
        throw new Error(result.error === 'no_brand' ? t('sched.noBrand') : result.error);
      }

      const plannerUrl = result.post && result.post.plannerUrl;
      statusEl.className = 'sched-status ok';
      const publishNote = payload.autoPublish
        ? t('sched.autoNote')
        : t('sched.approveNote');
      statusEl.innerHTML = t('sched.okLine', {date: date, time: time}) +
        (plannerUrl ? ` <a href="${plannerUrl}" target="_blank" rel="noopener">${t('sched.openLink')}</a>` : publishNote);
      btn.textContent = t('sched.scheduled');
      celebrateToast(t('celebrate.scheduled'));
      window._schedSubmitted = true;   // scheduled - don't warn on close
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 4000);
    } catch (e) {
      statusEl.style.display = 'none';
      errEl.textContent = t('sched.cantSchedule', {msg: String(e.message).slice(0, 160)});
      errEl.style.display = 'block';
      btn.textContent = orig; btn.disabled = false;
    }
  }

  // ── Session boot ──
  (async function initAuth() {
    if (!authToken) { showAuthView(); return; }
    try {
      const r = await fetch(`${API_BASE}/auth/me`, { headers: { 'Authorization': 'Bearer ' + authToken } });
      // ONLY a real 401 means the session is invalid/expired - log out then.
      if (r.status === 401) { _sessionExpired(); return; }
      if (r.ok) quotaInfo = await r.json();   // reuse for the quota pill
      // Any transient failure (network blip, cold-start timeout, 5xx) must NOT
      // wipe a valid 30-day session - stay signed in and show the app; showApp()
      // refreshes quota on its own, and a later genuine 401 still bounces to login.
      showApp();
    } catch {
      // Network error reaching /auth/me at launch - keep the session, show the app.
      showApp();
    }
  })();

  // ── Language switch: refresh state-driven labels that data-i18n can't cover ──
  document.addEventListener('langchange', () => {
    const a = AGGR_MAP[aggrSlider.value - 1];
    aggrDesc.textContent = t(a.label);
    _syncStoreCopy();   // applyI18n just reset the store-named strings
    document.getElementById('enhanceVideoDesc').innerHTML = t(_EV_DESCS[_enhanceVideoMode()]);
    updateTimeEstimate();
    if (burnMode && !runBtn.disabled) updateBurnBtn();
    // Auth buttons (both steps) re-label on language switch (flow-aware).
    _syncAuthSubmitLabel();
    { const b = document.getElementById('authVerifyBtn'); if (b) b.textContent = t('auth.verify'); }
    { const c = document.getElementById('authCodeHint');
      if (c && _authEmail && document.getElementById('authStepCode').style.display !== 'none')
        c.textContent = t('auth.codeHint', { email: _authEmail }); }
    const ap = document.getElementById('schedAutoPublish');
    if (ap && ap.checked) document.getElementById('autoPublishDesc').textContent = t('sched.apOn');
    _syncGenButtons();   // generator labels carry the remaining-uses count
    const upLbl = document.querySelector('#checkUpscale .check-label');
    if (upLbl) upLbl.textContent = _enhanceVideoMode() === 'esrgan' ? t('prog.upscale') : t('prog.enhanceVideo');
    updateQuotaUI();
    // Dynamic lists are built with t() at render time - re-render the open one
    // so its rows (used/unlimited/save, etc.) switch language too.
    if (authToken) {
      const av = document.getElementById('adminView');
      const hv = document.getElementById('historyView');
      // force: rows are built with t() at render time, so a language switch
      // must redraw them even when the server data is unchanged.
      if (av && av.style.display !== 'none') loadAdmin({ force: true });
      if (hv && hv.style.display !== 'none') loadHistory({ force: true });
    }
  });
