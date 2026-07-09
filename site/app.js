  const API_BASE = 'https://yotamjacob--hebrew-video-pipeline-api.modal.run';
  const API      = API_BASE + '/process/';

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

  // Show/hide password toggle (eye icon).
  const _EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const _EYE_OFF = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20C5 20 1 12 1 12a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';
  function togglePw(id, btn) {
    const el = document.getElementById(id);
    if (!el) return;
    const reveal = el.type === 'password';
    el.type = reveal ? 'text' : 'password';
    btn.innerHTML = reveal ? _EYE_OFF : _EYE;
    btn.setAttribute('aria-label', t(reveal ? 'auth.hidePw' : 'auth.showPw'));
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

  function _hideBootLoader() { const bl = document.getElementById('bootLoader'); if (bl) bl.style.display = 'none'; }

  function showAuthView() {
    _hideBootLoader();
    document.getElementById('authView').style.display = 'block';
    // Prefill the last-used email (saved on sign-in while "remember me" is
    // checked) so returning users only retype their password.
    const emailInput = document.getElementById('authEmail');
    if (emailInput && !emailInput.value) {
      emailInput.value = localStorage.getItem('hebpipe_email') || '';
    }
    document.getElementById('resetView').style.display = 'none';
    document.getElementById('tabsBar').style.display = 'none';
    const vb = document.getElementById('verifyBanner');
    if (vb) vb.style.display = 'none';
    const mc = document.getElementById('metricoolChip');
    if (mc) mc.style.display = 'none';
    const lo = document.getElementById('logoutTab');
    if (lo) lo.style.display = 'none';
    ['pipelineView', 'historyView', 'guideView', 'adminView'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    applyAuthMode();   // reflect EMAIL_UI_ENABLED (forgot link) on first paint
  }

  function showApp() {
    _hideBootLoader();
    document.getElementById('authView').style.display = 'none';
    document.getElementById('resetView').style.display = 'none';
    document.getElementById('tabsBar').style.display = 'flex';
    document.getElementById('logoutTab').style.display = 'inline-block';
    document.getElementById('pipelineView').style.display = 'block';
    refreshMediaToken();
    refreshMetricoolChip();
    if (quotaInfo) { updateQuotaUI(); updateVerifyBanner(); } else refreshQuota();
    restoreTab();
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

  // ── Password reset (opened via ?reset=token from the email) ──
  function showResetView(token) {
    _hideBootLoader();
    window._resetToken = token;
    document.getElementById('authView').style.display = 'none';
    document.getElementById('tabsBar').style.display = 'none';
    document.getElementById('logoutTab').style.display = 'none';
    ['pipelineView', 'historyView', 'guideView', 'adminView'].forEach(id => {
      const el = document.getElementById(id); if (el) el.style.display = 'none';
    });
    document.getElementById('resetView').style.display = 'block';
  }

  async function resetSubmit() {
    const btn = document.getElementById('resetSubmitBtn');
    const errEl = document.getElementById('resetError');
    const infoEl = document.getElementById('resetInfo');
    const pw = document.getElementById('resetPassword').value;
    errEl.style.display = 'none'; infoEl.style.display = 'none';
    if (pw.length < 8) { errEl.textContent = t('reset.tooShort'); errEl.style.display = 'block'; return; }
    _btnBusy(btn, true);
    try {
      const r = await fetch(`${API_BASE}/auth/reset`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: window._resetToken, password: pw }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || t('auth.errStatus', { status: r.status }));
      _btnBusy(btn, false);
      infoEl.textContent = t('reset.done');
      infoEl.style.display = 'block';
      // Drop the token from the URL and return to sign-in shortly.
      setTimeout(() => { history.replaceState(null, '', location.pathname); showAuthView(); }, 1800);
    } catch (e) {
      errEl.textContent = e.message || String(e);
      errEl.style.display = 'block';
      _btnBusy(btn, false);
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

  // Non-admin users confirm before spending a trial video
  async function _confirmQuotaUse() {
    if (!quotaInfo || quotaInfo.role === 'admin' ||
        quotaInfo.video_limit == null || quotaInfo.video_limit < 0) return true;
    const left = Math.max(0, quotaInfo.video_limit - quotaInfo.videos_used);
    return showConfirmModal(
      t('quota.confirmTitle'),
      t('quota.confirmBody', {left: left, limit: quotaInfo.video_limit}),
      t('quota.confirmOk'));
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
    // A saved Admin tab could only be restored once the role loaded.
    if (_pendingTabRestore === 'admin' && quotaInfo.role === 'admin') {
      _pendingTabRestore = null;
      switchTab('admin');
    }
    const pill = document.getElementById('quotaPill');
    if (!pill) return;
    if (quotaInfo.role === 'admin' || quotaInfo.video_limit == null || quotaInfo.video_limit < 0) {
      pill.style.display = 'none';
      return;
    }
    const left = Math.max(0, quotaInfo.video_limit - quotaInfo.videos_used);
    pill.textContent = left > 0
      ? t('quota.pill', {left: left, limit: quotaInfo.video_limit})
      : t('quota.pillZero');
    pill.classList.toggle('quota-pill-empty', left === 0);
    pill.style.display = '';
  }

  let authMode = 'login';   // 'login' | 'register' | 'forgot'

  function applyAuthMode() {
    const reg = authMode === 'register', forgot = authMode === 'forgot';
    document.getElementById('authPasswordRow').style.display = forgot ? 'none' : 'block';
    document.getElementById('authMarketingRow').style.display = (reg && EMAIL_UI_ENABLED) ? 'flex' : 'none';
    document.getElementById('authTermsRow').style.display    = reg ? 'flex' : 'none';
    document.getElementById('authInviteRow').style.display   = reg ? 'block' : 'none';
    document.getElementById('rememberRow').style.display     = forgot ? 'none' : 'flex';
    document.getElementById('authForgotLink').style.display  = (EMAIL_UI_ENABLED && !forgot) ? 'block' : 'none';
    document.getElementById('authEmailLabel').textContent = t('auth.email');
    document.getElementById('authSubmitBtn').textContent =
      reg ? t('auth.register') : forgot ? t('auth.sendReset') : t('auth.signin');
    document.getElementById('authModeBtn').textContent =
      reg ? t('auth.toSignin') : forgot ? t('auth.toSignin') : t('auth.toRegister');
    document.getElementById('authError').style.display = 'none';
    document.getElementById('authInfo').style.display = 'none';
    ['authEmailErr', 'authPasswordErr', 'authInviteErr'].forEach(id => _fieldErr(id, ''));
  }

  // ── Inline per-field validation (helps low-tech users fix inputs) ──
  const _EMAIL_JS_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  function _fieldErr(id, msg) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = msg || '';
    el.style.display = msg ? 'block' : 'none';
  }
  // onSubmit=true also flags empty required fields; live validation (false) only
  // warns about non-empty invalid input so users aren't nagged mid-typing.
  function validateEmail(onSubmit) {
    const v = document.getElementById('authEmail').value.trim();
    let msg = '';
    if (!v) { if (onSubmit) msg = t('valid.emailRequired'); }
    // Only registration enforces the email shape - login and forgot still
    // accept legacy username accounts from the pre-email era.
    else if (authMode === 'register' && !_EMAIL_JS_RE.test(v)) msg = t('valid.emailInvalid');
    _fieldErr('authEmailErr', msg);
    return !msg;
  }
  function validatePassword(onSubmit) {
    if (authMode === 'forgot') { _fieldErr('authPasswordErr', ''); return true; }
    const v = document.getElementById('authPassword').value;
    let msg = '';
    if (!v) { if (onSubmit) msg = t('valid.pwRequired'); }
    else if (authMode === 'register' && v.length < 8) msg = t('valid.pwShort');
    _fieldErr('authPasswordErr', msg);
    return !msg;
  }
  function validateInvite(onSubmit) {
    if (authMode !== 'register') { _fieldErr('authInviteErr', ''); return true; }
    const v = document.getElementById('authInvite').value.trim();
    const msg = (!v && onSubmit) ? t('valid.inviteRequired') : '';
    _fieldErr('authInviteErr', msg);
    return !msg;
  }
  document.getElementById('authEmail').addEventListener('input', () => validateEmail(false));
  document.getElementById('authPassword').addEventListener('input', () => validatePassword(false));
  document.getElementById('authInvite').addEventListener('input', () => validateInvite(false));

  function toggleAuthMode() {
    authMode = authMode === 'register' ? 'login' : (authMode === 'forgot' ? 'login' : 'register');
    applyAuthMode();
  }

  function showForgot() {
    authMode = 'forgot';
    applyAuthMode();
  }

  async function authSubmit() {
    const btn = document.getElementById('authSubmitBtn');
    const errEl = document.getElementById('authError');
    const infoEl = document.getElementById('authInfo');
    _btnBusy(btn, true);
    errEl.style.display = 'none';
    infoEl.style.display = 'none';

    // ── Forgot password: request a reset link, always report success ──
    if (authMode === 'forgot') {
      try {
        await fetch(`${API_BASE}/auth/forgot`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ identifier: document.getElementById('authEmail').value.trim() }),
        });
      } catch { /* never reveal existence */ }
      infoEl.textContent = t('auth.resetSent');
      infoEl.style.display = 'block';
      _btnBusy(btn, false);
      return;
    }

    // Inline field validation (login + register) - run all so every bad field
    // shows its own message, then stop if any is invalid.
    const uOk = validateEmail(true);
    const pOk = validatePassword(true);
    const iOk = validateInvite(true);
    if (!(uOk && pOk && iOk)) { _btnBusy(btn, false); return; }

    const payload = {
      email: document.getElementById('authEmail').value.trim(),
      password: document.getElementById('authPassword').value,
    };
    if (authMode === 'register') {
      if (!document.getElementById('authTermsCheck').checked) {
        errEl.textContent = t('auth.termsError');
        errEl.style.display = 'block';
        _btnBusy(btn, false);
        return;
      }
      payload.invite = document.getElementById('authInvite').value.trim();
      payload.terms_accepted = true;
      // Update/promo consent is deferred for now (EMAIL_UI_ENABLED).
      if (EMAIL_UI_ENABLED) {
        payload.marketing_consent = document.getElementById('authMarketing').checked;
      }
    }
    try {
      const resp = await fetch(`${API_BASE}/auth/${authMode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || t('auth.errStatus', {status: resp.status}));
      const remember = document.getElementById('rememberMe')?.checked ?? true;
      _storeToken(data.token, remember);
      // Remember the sign-in email for next time's prefill - but never on a
      // shared/incognito-style session where "remember me" was unchecked.
      if (remember) localStorage.setItem('hebpipe_email', payload.email);
      else          localStorage.removeItem('hebpipe_email');
      showApp();
      fetch(API_BASE + '/warmup/', { headers: { 'Authorization': 'Bearer ' + authToken } }).catch(() => {});
    } catch (e) {
      errEl.textContent = e.message || String(e);
      errEl.style.display = 'block';
    } finally {
      _btnBusy(btn, false);
    }
  }

  async function logout() {
    const ok = await showConfirmModal(t('logout.title'), t('logout.body'), t('logout.confirm'));
    if (!ok) return;
    _clearToken();
    location.reload();
  }

  const VEO_ENABLED = false;

  // Limits
  const MAX_BYTES    = 500 * 1024 * 1024; // 500 MB
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
  function _setStage(s) { flowStage = s; }
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
    burn:    document.getElementById('checkBurn'),
    finalize: document.getElementById('checkFinalize'),
  };
  const checkTimeEls = {
    upload:  document.getElementById('checkUploadTime'),
    enhance: document.getElementById('checkEnhanceTime'),
    cut:     document.getElementById('checkCutTime'),
    upscale: document.getElementById('checkUpscaleTime'),
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
  ];
  const noticeBlock  = document.getElementById('noticeBlock');
  const noticeWarn   = document.getElementById('noticeWarn');
  const noticeNet    = document.getElementById('noticeNet');

  let selectedFile = null;
  let videoDuration = null;
  // Stable in-memory/disk copy of the picked file's bytes, snapshotted at
  // selection time so a long upload can't be broken by the OS moving the file
  // (Google Photos cloud-sync). `_snapshotId` is the IndexedDB key when the
  // file was too big to hold in memory. See snapshotFile().
  let stableBlob = null;
  let _snapshotId = null;
  let resultBlob      = null;
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
  let selectedHookIdx        = -1;
  let hookGenAborted         = false;
  let hookThumbnail          = null;
  let _playerSetupDone       = false;
  let _playerDispW           = 0;   // detected display width (accounts for browser rotation)

  // ── Background-job persistence (survives tab close/reload on mobile) ──
  const JOB_KEY = 'hebpipe_job';
  const JOB_TTL = 45 * 60 * 1000; // 45 min - Modal jobs expire after that

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

  async function resumeSavedJob() {
    const job = loadSavedJob();
    document.getElementById('reconnectBanner').style.display = 'none';
    if (!job) return;
    document.getElementById('startOverBtn').style.display = '';

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
        cutFilename  = (job.filename || 'video').replace(/\.[^/.]+$/, '') + '_cut.mp4';
        if (captionsData.length > 0) {
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
        // Device download is optional and non-blocking
        try {
          const dlResp = await apiFetch(
            `${API_BASE}/download/${burnResult.output_key}/?filename=${encodeURIComponent(job.outputFilename)}`
          );
          if (!dlResp.ok) throw new Error(`Download failed (${dlResp.status})`);
          resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
          resultName = job.outputFilename;
          triggerDownload();
        } catch (dlErr) {
          if (dlErr.name !== 'AbortError')
            console.warn('Device download failed (video is still scheduled-ready):', dlErr.message);
        }
        document.getElementById('burnSuccessBanner').style.display = 'flex';
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
    return Array.from(document.querySelectorAll('#captionsList .caption-row')).map(row => ({
      start: parseFloat(row.querySelector('.caption-start').value) || 0,
      end:   parseFloat(row.querySelector('.caption-end').value)   || 0,
      text:  row.querySelector('.caption-input').value,
    }));
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

  function downloadSRT() {
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
    const blob = new Blob([srt], { type: 'text/plain;charset=utf-8' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    const base = selectedFile ? selectedFile.name.replace(/\.[^.]+$/, '') : 'captions';
    a.href = url; a.download = base + '.srt'; a.click();
    URL.revokeObjectURL(url);
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
  const LOCK_CARD_IDS = ['optionsCard', 'captionEditorCard', 'hookCard', 'brollCard',
                         'stockBrollCard'];
  let _actionLockDepth = 0;
  const _actionLockSaved = new Map();

  function lockPipelineActions({ activeBtn = null, activeCard = null } = {}) {
    if (++_actionLockDepth > 1) return;
    // Switching language mid-flow re-renders state-driven labels and can corrupt
    // an in-flight process/burn/download — lock the toggle for the duration.
    const _lt = document.getElementById('langToggle');
    if (_lt) _lt.disabled = true;
    LOCK_BTN_IDS.forEach(id => {
      if (id === activeBtn) return;               // the active flow manages its own button
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

  async function handleFile(file) {
    if (!file) return;
    if (!file.type.startsWith('video/') && !file.name.match(/\.(mp4|mov|mkv|avi|webm)$/i)) {
      showBlockNotice(t('file.badTypeTitle'), t('file.badType'));
      return;
    }

    selectedFile = file;
    fileName.textContent = file.name;
    fileInfo.classList.add('visible');
    document.getElementById('startOverBtn').style.display = '';
    clearNotices();
    resetStatus();

    // Size check (instant)
    if (file.size > MAX_BYTES) {
      fileDetail.textContent = t('file.tooLargeMeta', {size: formatSize(file.size)});
      showBlockNotice(t('file.tooLargeTitle'), t('file.tooLarge', {size: formatSize(file.size)}));
      blocked = true;
      runBtn.disabled = true;
      return;
    }

    // Read duration + resolution from the video element
    fileDetail.textContent = t('file.reading', {size: formatSize(file.size)});
    const meta = await getVideoMeta(file);
    videoDuration = meta.duration;
    // 4K if the long edge is ~3840 (landscape) or ~2160 tall portrait 4K -
    // i.e. the larger dimension reaches ~3000px. QHD (2560) is not flagged.
    const is4K = Math.max(meta.width || 0, meta.height || 0) >= 3000;

    if (videoDuration !== null) {
      fileDetail.textContent = formatSize(file.size) + ' · ' + formatDuration(videoDuration);
    } else {
      fileDetail.textContent = formatSize(file.size);
    }

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

    checkNetwork();

    // Fire-and-forget GPU warmup so the container is ready by the time the user clicks Run
    apiFetch(API_BASE + '/warmup/').catch(() => {});
  }

  clearFile.addEventListener('click', () => {
    if (!confirm(t('file.removeConfirm'))) return;
    selectedFile = null; videoDuration = null;
    _disposeSnapshot();
    document.getElementById('timeEstimate').style.display = 'none';
    fileInput.value = '';
    fileInfo.classList.remove('visible');
    clearNotices();
    runBtn.disabled = true;
    resetStatus();
  });

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

  function checkToolsEnabled() {
    if (!selectedFile || burnMode) return;
    const ids = ['cutSilences', 'burnCaptions', 'enhanceAudio'];
    if (VEO_ENABLED) ids.push('suggestBrolls');
    runBtn.disabled = !ids.some(id => document.getElementById(id)?.checked)
                      && _enhanceVideoMode() === 'none';
  }
  ['cutSilences', 'burnCaptions', 'enhanceAudio', 'suggestBrolls'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', () => { checkToolsEnabled(); updateTimeEstimate(); });
  });
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
    box.style.display = 'flex';
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
    }));

  // ── Run ──
  runBtn.addEventListener('click', () => { if (burnMode) doBurn(); else run(); });
  retryBtn.addEventListener('click', run);

  window.addEventListener('beforeunload', (e) => {
    const editing = document.getElementById('captionEditorCard').style.display !== 'none';
    const hasBrollWork = selectedBrolls.length > 0 || Object.keys(stockBrollSelections).length > 0;
    if (isUploading || pollController || editing || resultBlob || hasBrollWork) {
      e.preventDefault();
      e.returnValue = '';
    }
  });

  async function run(isRetry = false) {
    if (!selectedFile || blocked) return;
    if (_quotaExhausted()) {
      showBlockNotice(t('quota.pillZero'), t('quota.exhausted'));
      return;
    }
    if (!(await _confirmQuotaUse())) return;

    resultBlob = null;
    showUploadProgress();

    const aggr = AGGR_MAP[aggrSlider.value - 1];
    const brollOn      = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
    const needTranscript = brollOn && !document.getElementById('burnCaptions').checked;
    const params = new URLSearchParams({
      filename:             selectedFile.name,
      cut_silences:         document.getElementById('cutSilences').checked  ? 'true' : 'false',
      burn_captions:        document.getElementById('burnCaptions').checked ? 'true' : 'false',
      enhance_audio:        document.getElementById('enhanceAudio').checked ? 'true' : 'false',
      enhance_video:        _enhanceVideoMode(),
      transcribe_for_broll: needTranscript ? 'true' : 'false',
      min_silence:          aggr.silence,
      padding:              aggr.padding,
    });

    try {
      // Phase 1: upload video in chunks
      _setStage('upload');
      const uploadKey = await chunkedUpload(selectedFile, stableBlob, (pct) => {
        document.getElementById('uploadBarFill').style.width = (pct * 100).toFixed(0) + '%';
        document.getElementById('uploadBarPct').textContent  = (pct * 100).toFixed(0) + '%';
      });
      _stepDone('upload');
      document.getElementById('uploadBarRow').style.display = 'none';
      { const n = document.getElementById('uploadNote'); if (n) n.style.display = 'none'; }

      currentUploadKey = uploadKey;

      // Phase 2: spawn processing job (tiny request - just params, no body)
      runStartTime = Date.now();
      showProcessing();
      params.set('key', uploadKey);
      _setStage('spawn');
      const spawnResp = await apiFetch(`${API_BASE}/process/?${params}`, { method: 'POST' });
      if (spawnResp.status !== 202) {
        const body = await spawnResp.json().catch(() => ({}));
        throw new Error(body.error || t('err.spawn', {status: spawnResp.status}));
      }
      const { call_id } = await spawnResp.json();
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
      cutFilename  = (selectedFile.name || 'video').replace(/\.[^/.]+$/, '') + '_cut.mp4';

      const brollActive = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
      if (captionsData.length > 0 || brollActive) {
        // Keep checklist visible (steps 1-3 done) while user edits captions
        showCaptionEditor();
        startBrollAnalysis();
      } else {
        // No captions, no B-roll - download directly
        await _finalizeAndDownload(`${API_BASE}/download/${videoKey}/?filename=${encodeURIComponent(cutFilename)}`, cutFilename);
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
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

  async function rerun() {
    if (!currentUploadKey || !selectedFile) return;
    if (_quotaExhausted()) {
      showBlockNotice(t('quota.pillZero'), t('quota.exhausted'));
      return;
    }
    if (!(await _confirmQuotaUse())) return;

    // Hide editor cards and reset to pre-caption state
    burnMode = false;
    hasBurnedOnce = false;
    captionsData = [];
    videoKey = null;
    resultBlob = null;
    ['captionEditorCard', 'hookCard', 'brollCard', 'stockBrollCard'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    document.getElementById('reprocessBtn').style.display = 'none';
    document.getElementById('burnSuccessBanner').style.display = 'none';
    { const _osb = document.getElementById('openScheduleBtn'); if (_osb) _osb.style.display = 'none';
      const _so = document.getElementById('scheduleOverlay'); if (_so) _so.style.display = 'none'; }
    setSetupLocked(true);

    const aggr = AGGR_MAP[aggrSlider.value - 1];
    const brollOn        = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
    const needTranscript = brollOn && !document.getElementById('burnCaptions').checked;
    const params = new URLSearchParams({
      filename:             selectedFile.name,
      cut_silences:         document.getElementById('cutSilences').checked  ? 'true' : 'false',
      burn_captions:        document.getElementById('burnCaptions').checked ? 'true' : 'false',
      enhance_audio:        document.getElementById('enhanceAudio').checked ? 'true' : 'false',
      enhance_video:        _enhanceVideoMode(),
      transcribe_for_broll: needTranscript ? 'true' : 'false',
      min_silence:          aggr.silence,
      padding:              aggr.padding,
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
      cutFilename  = (selectedFile.name || 'video').replace(/\.[^/.]+$/, '') + '_cut.mp4';

      const brollActive = VEO_ENABLED && document.getElementById('suggestBrolls').checked;
      unlockPipelineActions();
      if (captionsData.length > 0 || brollActive) {
        showCaptionEditor();
        startBrollAnalysis();
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
  const CHUNK_SIZE = 2 * 1024 * 1024;
  // Modest parallelism: 16 in-flight chunks (each with a CORS preflight since
  // auth) exceeded the API container's concurrent-input cap, and Modal's
  // ingress reroutes the overflow with a 303 that browsers can't follow for
  // POSTs. 4 stays under the cap, saturates most uplinks, and caps in-flight
  // (losable-on-background) bytes at 4×2 MB = 8 MB.
  const UPLOAD_CONCURRENCY = 4;
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

  const CHUNK_STALL_MS = 20_000;
  function _postChunkBytes(url, body, onLoaded) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      let watchdog;
      const stallMs = window.__CHUNK_STALL_MS || CHUNK_STALL_MS;   // test seam
      const arm = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => { try { xhr.abort(); } catch (_) {}
          reject(Object.assign(new Error('upload stalled'), { isNetwork: true })); }, stallMs);
      };
      xhr.open('POST', url);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
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

  async function chunkedUpload(file, source, onProgress) {
    // `source` is the stable byte snapshot (see snapshotFile); slice bytes from
    // it, never from the raw File, so a cloud-synced original can't go stale
    // mid-upload. Falls back to the File itself if no snapshot was taken.
    source = source || file;
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    // Reuse a prior in-progress upload for this exact file if one exists.
    const saved = _loadResume(file);
    const key = (saved && saved.key) || crypto.randomUUID().replace(/-/g, '');
    const completed = new Set((saved && saved.completed || []).filter(i => i < totalChunks));
    if (completed.size) console.info(`Upload resume: ${completed.size}/${totalChunks} chunks already sent`);
    // Per-chunk loaded bytes - summed for accurate progress across concurrent
    // chunks (each in-flight chunk contributes its own live byte count).
    const loadedByChunk = new Array(totalChunks).fill(0);
    const report = () => onProgress(loadedByChunk.reduce((a, b) => a + b, 0) / file.size);

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
            `${API_BASE}/upload_chunk/?key=${key}&index=${i}`,
            body,
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
          if (e.isServer) {
            if (++serverAttempts >= MAX_SERVER_ATTEMPTS)
              throw Object.assign(new Error(t('err.chunkRetries', { i: i, n: MAX_SERVER_ATTEMPTS, status: e.httpStatus || 0 })), { isTerminal: true });
            console.warn(`Chunk ${i}: server ${e.httpStatus}, retry ${serverAttempts}/${MAX_SERVER_ATTEMPTS}`);
          } else {
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
    return key;
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
  document.getElementById('startOverBtn').addEventListener('click', async () => {
    const confirmed = await showConfirmModal(
      t('confirm.startTitle'),
      t('confirm.startBody'),
      t('confirm.startOk')
    );
    if (!confirmed) return;
    if (currentCallId) apiFetch(`${API_BASE}/cancel/${currentCallId}/`, { keepalive: true }).catch(() => {});
    clearSavedJob();
    location.reload();
  });

  // Find B-Roll Moments button
  const findBrollBtn = document.getElementById('findBrollBtn');
  findBrollBtn.addEventListener('click', () => triggerStockBroll());

  // Caption preview / burn font size slider
  const fontSizeSlider = document.getElementById('captionFontSizeSlider');

  fontSizeSlider.addEventListener('input', () => {
    captionFontSize = parseInt(fontSizeSlider.value, 10);
    localStorage.setItem('captionFontSize', captionFontSize);
    updatePreviewCaption();
  });

  (function initPreviewFontSize() {
    const saved = parseInt(localStorage.getItem('captionFontSize') || '48', 10);
    captionFontSize = saved;
    fontSizeSlider.value = saved;
  })();

  function triggerDownload() {
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

  const HIDDEN_BY_DEFAULT = new Set(['upscale', 'burn', 'finalize']); // rows hidden until triggered

  // Cut-only jobs (no captions/B-roll) go straight from "all steps green" to
  // pulling the full MP4 into a blob - a multi-second silent gap on big files.
  // Show a "Preparing download" spinner during that fetch so the UI never
  // looks frozen, then hand off to showDone().
  async function _finalizeAndDownload(url, name) {
    _setStage('download');
    _stepActivate('finalize');
    const dlResp = await apiFetch(url);
    resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
    resultName = name;
    _stepDone('finalize');
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
    { const n = document.getElementById('uploadNote'); if (n) n.style.display = 'block'; }

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
    ['enhance', 'cut', 'upscale'].forEach(name => {
      const item = checkItems[name];
      if (!item) return;
      if (st[name] != null) _forceDone(name, Math.max(1, Math.round(st[name])));
      else if (item.classList.contains('active')) _stepDone(name);  // frontend-measured wall time
      else if (!item.classList.contains('done')) _stepSkip(name);   // step never ran
    });
  }

  function showDone() {
    checklistEl.style.display = 'none';
    statusDone.classList.add('visible');
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
    triggerDownload();
  }

  function showError(msg) {
    if (/limit_reached/.test(msg)) msg = t('quota.exhausted');
    if (/no_audio/.test(msg)) msg = t('err.noAudio');
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
    document.getElementById('brollCard').style.display = 'none';
    document.getElementById('brollList').innerHTML = '';
    document.getElementById('stockBrollCard').style.display = 'none';
    document.getElementById('stockBrollList').innerHTML = '';
    stockBrollSelections = {};
    document.getElementById('hookCard').style.display = 'none';
    document.getElementById('hookOptions').style.display = 'none';
    document.getElementById('hookControls').style.display = 'none';
    document.getElementById('hookError').style.display = 'none';
    document.getElementById('generateHookBtn').disabled = true;
    document.getElementById('hookStatus').style.display = 'none';
    selectedHookIdx = -1;
    hookGenAborted = false;
    hookThumbnail = null;
    pendingAnalyses       = 0;
    stockBrollAnalyzed    = false;
    lastAnalyzedSignature = '';
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
    selectedBrolls = [];
    captionsData = [];
    videoKey    = null;
    cutFilename = '';
    captionFont      = 'Heebo';
    captionMarginPct = 0.08;
    captionFontSize  = 48;
    const _vid = document.getElementById('cutVideo');
    if (_vid) { _vid.pause(); _vid.removeAttribute('src'); _vid.load(); }
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
  }
  function showBlockNotice(title, body) {
    document.getElementById('noticeBlockTitle').textContent = title;
    document.getElementById('noticeBlockBody').textContent  = body;
    noticeBlock.classList.add('visible');
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
  function rewrapCaption(text, videoWidth, fontSize) {
    const words = text.replace(/\\N/g, ' ').split(' ').filter(w => w.length);
    if (!words.length) return text;
    const marginH = Math.max(25, Math.floor(videoWidth / 14));
    const avail   = videoWidth - 2 * marginH;
    const charW   = fontSize * 0.50;
    const lines   = [];
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
    return lines.join('\n');
  }

  // ── Caption preview & positioning ──
  function updatePreviewCaption() {
    // Called by font/size/position sliders - refreshes player caption overlay immediately
    const capEl = document.getElementById('playerCap');
    const vid   = document.getElementById('cutVideo');
    if (!capEl) return;
    capEl.style.fontFamily = `'${captionFont}', sans-serif`;
    capEl.style.bottom     = (captionMarginPct * 100) + '%';
    if (vid && vid.videoWidth) {
      // _playerDispW = detected display width (handles browser-auto-rotated videos where
      // videoWidth reports stream width, not display width)
      const displayW = _playerDispW || vid.videoWidth;
      const scale = vid.clientWidth / displayW;
      capEl.style.fontSize = Math.max(7, captionFontSize * scale) + 'px';
      const t = vid.currentTime;
      const cap = captionsData && captionsData.find(c => t >= c.start && t <= c.end + 0.05);
      if (cap) capEl.textContent = rewrapCaption(cap.text, displayW, captionFontSize);
    }
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

    // Set source - video streams from the already-processed cut file on Modal
    vid.src = _withToken(`${API_BASE}/download/${videoKey}`);
    document.getElementById('captionPlayer').style.display = 'block';

    vid.addEventListener('loadedmetadata', () => {
      const wrap = document.getElementById('playerWrap');
      if (!wrap) { updatePreviewCaption(); return; }
      const maxW = Math.min(260, window.innerWidth * 0.72);
      wrap.style.width = maxW + 'px';
      wrap.style.aspectRatio = '9 / 16';
      _playerDispW = vid.videoWidth || 1080;
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

    const playerWrap = document.getElementById('playerWrap');
    if (playerWrap) playerWrap.addEventListener('click', togglePlay);
    if (playBtn) playBtn.addEventListener('click', togglePlay);
    vid.addEventListener('play',  () => { bigPlay.style.opacity = '0'; playBtn.classList.add('is-playing'); });
    vid.addEventListener('pause', () => { bigPlay.style.opacity = '1'; playBtn.classList.remove('is-playing'); });
    vid.addEventListener('ended', () => { bigPlay.style.opacity = '1'; playBtn.classList.remove('is-playing'); });

    vid.addEventListener('timeupdate', () => {
      const t = vid.currentTime, dur = vid.duration || 0;
      // Progress bar + thumb
      if (dur > 0 && !scrubbing) {
        const pct = (t / dur * 100) + '%';
        progFill.style.width = pct;
        if (thumb) thumb.style.left = pct;
      }
      timeLbl.textContent = fmtT(t) + ' / ' + fmtT(dur);

      // Caption overlay - same font/position as the preview and the burned video
      const cap = captionsData.find(c => t >= c.start && t <= c.end + 0.05);
      capEl.textContent = cap ? rewrapCaption(cap.text, vid.videoWidth || 1080, captionFontSize) : '';
      if (cap) {
        capEl.style.fontFamily = `'${captionFont}', sans-serif`;
        capEl.style.bottom     = (captionMarginPct * 100) + '%';
        const scale = vid.videoWidth ? vid.clientWidth / vid.videoWidth : vid.clientHeight / (vid.videoHeight || 1920);
        capEl.style.fontSize   = Math.max(7, captionFontSize * scale) + 'px';
        if (vid.videoWidth) {
          const marginH = Math.max(25, Math.floor(vid.videoWidth / 14));
          capEl.style.maxWidth = ((vid.videoWidth - 2 * marginH) / vid.videoWidth * 100).toFixed(2) + '%';
        }
      }

      // Highlight active row - reads timestamps from DOM so it stays correct after edits
      document.querySelectorAll('.caption-row').forEach(row => {
        const rs = parseFloat(row.querySelector('.caption-start')?.value) || 0;
        const re = parseFloat(row.querySelector('.caption-end')?.value)   || 0;
        row.classList.toggle('caption-row-active', t >= rs && t <= re + 0.05);
      });
    });

    // Scrub: click or drag progress bar to seek
    const thumb = document.getElementById('playerProgThumb');
    function seekToX(clientX) {
      if (!isFinite(vid.duration)) return;
      const r = progWrap.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      vid.currentTime = pct * vid.duration;
      progFill.style.width = (pct * 100) + '%';
      if (thumb) thumb.style.left = (pct * 100) + '%';
    }
    let scrubbing = false;
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

  function initPositionTrack() {
    const track = document.getElementById('posTrack');
    const thumb = document.getElementById('posThumb');
    if (!track || !thumb) return;

    function setFromPct(pct) {
      const MIN_PCT = 0.03; const MAX_PCT = 0.80;
      const clamped = Math.max(MIN_PCT, Math.min(MAX_PCT, pct));
      captionMarginPct = clamped;
      const ratio = 1 - (clamped - MIN_PCT) / (MAX_PCT - MIN_PCT);
      thumb.style.top = (ratio * 100) + '%';
      updatePreviewCaption();
    }

    function onMove(clientY) {
      const rect = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));
      const MIN_PCT = 0.03; const MAX_PCT = 0.80;
      setFromPct(MIN_PCT + (1 - ratio) * (MAX_PCT - MIN_PCT));
    }

    thumb.addEventListener('mousedown', e => {
      e.preventDefault();
      const onMove2 = e2 => onMove(e2.clientY);
      const stop = () => { document.removeEventListener('mousemove', onMove2); document.removeEventListener('mouseup', stop); };
      document.addEventListener('mousemove', onMove2);
      document.addEventListener('mouseup', stop);
    });
    thumb.addEventListener('touchstart', e => {
      e.preventDefault();
      const onMove2 = e2 => { e2.preventDefault(); onMove(e2.touches[0].clientY); };
      const stop = () => { document.removeEventListener('touchmove', onMove2); document.removeEventListener('touchend', stop); };
      document.addEventListener('touchmove', onMove2, { passive: false });
      document.addEventListener('touchend', stop);
    }, { passive: false });
    track.addEventListener('click', e => onMove(e.clientY));

    setFromPct(captionMarginPct);
  }

  document.getElementById('fontSelect')?.addEventListener('change', e => {
    captionFont = e.target.value;
    updatePreviewCaption();
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
  let selectedBrolls = [];
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
    const n = selectedBrolls.length + Object.keys(stockBrollSelections).length;
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

  document.getElementById('suggestBrolls').addEventListener('change', e => {
    if (!VEO_ENABLED) return;
    const show = e.target.checked ? 'block' : 'none';
    document.getElementById('geminiKeyRow').style.display = show;
    document.getElementById('anthropicKeyRow').style.display = show;
    document.getElementById('brollAspectRow').style.display = show;
    if (e.target.checked) {
      const savedGemini = localStorage.getItem('geminiApiKey') || '';
      if (savedGemini) document.getElementById('geminiKey').value = savedGemini;
      const savedAnthropic = localStorage.getItem('anthropicApiKey') || '';
      if (savedAnthropic) document.getElementById('anthropicKey').value = savedAnthropic;
    }
  });
  document.getElementById('geminiKey').addEventListener('change', e => {
    localStorage.setItem('geminiApiKey', e.target.value.trim());
  });
  document.getElementById('anthropicKey').addEventListener('change', e => {
    localStorage.setItem('anthropicApiKey', e.target.value.trim());
  });

  // Init: check for a saved background job and show reconnect banner
  (function checkSavedJob() {
    const job = loadSavedJob();
    if (job) document.getElementById('reconnectBanner').style.display = 'flex';
  })();

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

  // Init: apply VEO_ENABLED and restore stock B-roll state from localStorage
  (function initToggles() {
    // Disable Veo toggle if not enabled
    if (!VEO_ENABLED) {
      const veoRow = document.getElementById('suggestBrollsRow');
      if (veoRow) {
        veoRow.classList.add('disabled-feature');
        veoRow.title = t('veo.later');
      }
      const veoChk = document.getElementById('suggestBrolls');
      if (veoChk) { veoChk.checked = false; veoChk.disabled = true; }
    }

  })();

  async function startBrollAnalysis() {
    if (!document.getElementById('suggestBrolls').checked) return;
    if (!videoKey) return;
    const geminiKey = document.getElementById('geminiKey').value.trim()
                   || localStorage.getItem('geminiApiKey') || '';
    if (!geminiKey) return;
    const anthropicKey = document.getElementById('anthropicKey').value.trim()
                      || localStorage.getItem('anthropicApiKey') || '';

    bumpPending(+1);
    const card   = document.getElementById('brollCard');
    const status = document.getElementById('brollStatus');
    const list   = document.getElementById('brollList');
    // Ensure broll card body is expanded when showing
    const brollHeader = document.querySelector('#brollCard .card-header');
    if (brollHeader) brollHeader.classList.remove('collapsed');
    const brollBody = document.getElementById('brollBody');
    if (brollBody) brollBody.style.display = 'block';
    card.style.display   = 'block';
    status.style.display = 'flex';
    list.innerHTML       = '';
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

    const aspectRatio = document.querySelector('input[name="brollAspect"]:checked')?.value || '9:16';

    try {
      const resp = await apiFetch(`${API_BASE}/broll/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_key: videoKey, captions: captionsData, gemini_key: geminiKey, aspect_ratio: aspectRatio, anthropic_key: anthropicKey }),
      });
      if (!resp.ok) throw new Error(`Spawn failed: ${resp.status}`);
      const { call_id } = await resp.json();

      const result = await pollBroll(`${API_BASE}/broll_poll/${call_id}/`);
      status.style.display = 'none';
      selectedBrolls = [];
      updateBurnBtn();
      const suggestions = result.suggestions || [];
      if (!suggestions.length) {
        list.innerHTML = '<p style="color:var(--muted);font-size:0.85rem;padding:8px 0">' + t('veo.none') + '</p>';
        return;
      }
      renderBrollList(suggestions);
    } catch (e) {
      console.error('B-roll error:', e.message);
      status.style.display = 'none';
      const isRetryable = /503|UNAVAILABLE|high demand|timeout|timed out/i.test(e.message);
      const msg = isRetryable
        ? t('veo.timeout')
        : t('veo.failed', {msg: e.message});
      list.innerHTML = `<p style="color:var(--red);font-size:0.85rem;padding:8px 0">${msg} <button onclick="startBrollAnalysis()" style="margin-left:8px;font-size:0.8rem;padding:3px 10px;border-radius:6px;border:1px solid var(--red);background:none;color:var(--red);cursor:pointer">${t('veo.retry')}</button></p>`;
    } finally {
      bumpPending(-1);
    }
  }

  async function pollBroll(url) {
    let retries = 0;
    while (true) {
      try {
        const resp = await apiFetch(url);
        if (resp.status === 200) return await resp.json();
        if (resp.status === 202) { await new Promise(r => setTimeout(r, 5000)); continue; }
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `Server error ${resp.status}`);
      } catch (e) {
        if (e.message && !e.message.startsWith('Server error') && ++retries <= 3) {
          await new Promise(r => setTimeout(r, 2000));
          continue;
        }
        throw e;
      }
    }
  }

  // ── Stock B-roll ──

  function triggerStockBroll() {
    document.getElementById('stockBrollRerunBanner').style.display = 'none';
    startStockBrollAnalysis(getEditedCaptions());
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

    const edgePad = fontSize;
    const maxW    = W - 2 * edgePad;          // max text width
    const padH    = Math.round(fontSize * 0.55); // horizontal box padding
    const padV    = Math.round(fontSize * 0.35); // vertical box padding
    const lineH   = Math.round(fontSize * 1.10); // line height - matches ASS renderer (~1.0-1.1×)

    // Word-wrap text into lines that fit within maxW
    const words = text.split(' ').filter(Boolean);
    const lines = [];
    let cur = '';
    for (const w of words) {
      const test = cur ? cur + ' ' + w : w;
      if (ctx.measureText(test).width > maxW && cur) { lines.push(cur); cur = w; }
      else cur = test;
    }
    if (cur) lines.push(cur);

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
    ['hookFont','hookFontColor','hookBgColor','hookBgOpacity','hookPosition','hookFontSize','hookBorderColor','hookBorderSize'].forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('input',  drawHookPreview);
      el.addEventListener('change', drawHookPreview); // color pickers fire 'change' on Safari after picker closes
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
    drawHookPreview();
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
          style="padding:4px 8px;border-radius:7px;border:1.5px solid #FECACA;background:#FEF2F2;color:var(--red);font-size:0.78rem;font-weight:700;cursor:pointer;flex-shrink:0">
          ✕
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

  async function triggerGenerateHook() {
    if (!videoKey || !captionsData.length) return;
    // Options already on screen → regenerating discards them (and any edits).
    // Confirm first so a stray tap doesn't wipe a hook the user was refining.
    const optsEl = document.getElementById('hookOptions');
    if (optsEl.style.display !== 'none' && optsEl.children.length) {
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
    lockPipelineActions({ activeBtn: 'generateHookBtn', activeCard: 'hookCard' });
    btn.disabled          = true;
    status.style.display  = 'flex';
    optionsEl.style.display   = 'none';
    controlsEl.style.display  = 'none';
    errEl.style.display   = 'none';
    selectedHookIdx       = -1;

    try {
      const resp = await apiFetch(`${API_BASE}/generate-hook/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ captions_json: JSON.stringify(captions), video_key: videoKey }),
      });
      if (!resp.ok) {
        const errBody = await resp.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${resp.status}`);
      }
      const { call_id } = await resp.json();

      let retries = 0;
      while (true) {
        if (hookGenAborted) break;
        try {
          const poll = await apiFetch(`${API_BASE}/generate-hook-poll/${call_id}/`);
          if (poll.status === 200) {
            const result = await poll.json();
            if (!hookGenAborted) renderHookOptions(result.hooks || []);
            break;
          }
          if (poll.status === 202) { await new Promise(r => setTimeout(r, 3000)); retries = 0; continue; }
          throw new Error(`Server error ${poll.status}`);
        } catch (e) {
          if (!e.message.startsWith('Server error') && ++retries <= 3) {
            await new Promise(r => setTimeout(r, 2000)); continue;
          }
          throw e;
        }
      }
    } catch (e) {
      if (!hookGenAborted) {
        errEl.textContent   = t('hook.failed', {msg: e.message.slice(0, 120)});
        errEl.style.display = 'block';
      }
    } finally {
      unlockPipelineActions();
      btn.disabled         = false;
      status.style.display = 'none';
      hookGenAborted       = false;
    }
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
      check.textContent = '✓';
      card.appendChild(check);

      // Editable textarea for hook text
      const ta = document.createElement('textarea');
      ta.id = `hookText${i}`;
      ta.value = h.text;
      ta.style.cssText = 'width:100%;border:none;border-bottom:1.5px dashed var(--purple-300);background:transparent;resize:none;overflow:hidden;font-family:inherit;font-size:1.05rem;font-weight:700;direction:rtl;text-align:right;color:var(--text);padding:0 0 2px;margin:0 0 6px;cursor:text;line-height:1.4;display:block;outline:none;transition:border-color 0.15s;';
      ta.rows = 1;
      ta.title = t('hook.clickEdit');
      ta.addEventListener('focus', () => { ta.style.borderBottomColor = 'var(--purple-500)'; });
      ta.addEventListener('blur',  () => { ta.style.borderBottomColor = 'var(--purple-300)'; });
      ta.addEventListener('input', () => {
        ta.style.height = 'auto';
        ta.style.height = ta.scrollHeight + 'px';
        drawHookPreview();
      });

      card.appendChild(ta);
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
      };

      optionsEl.appendChild(card);

      // Auto-size textarea once it's in the DOM
      requestAnimationFrame(() => {
        ta.style.height = 'auto';
        ta.style.height = ta.scrollHeight + 'px';
      });
    });

    optionsEl.style.display  = 'block';
    const firstShow = controlsEl.style.display === 'none';
    controlsEl.style.display = 'block';
    if (firstShow) _hookSettingListeners();
    drawHookPreview();

    // Hooks now exist → the button re-runs generation. Relabel it and switch its
    // data-i18n key so it stays "Regenerate…" across language toggles too.
    const ghb = document.getElementById('generateHookBtn');
    ghb.setAttribute('data-i18n', 'hook.regenerate');
    ghb.textContent = t('hook.regenerate');
  }

  // ── End Hook Generator ──────────────────────────────────────────────────

  async function startStockBrollAnalysis(captionsOverride) {
    const captions = captionsOverride || captionsData;
    if (!captions.length) return;

    const card   = document.getElementById('stockBrollCard');
    const status = document.getElementById('stockBrollStatus');
    const list   = document.getElementById('stockBrollList');

    const cardHeader = card.querySelector('.card-header');
    if (cardHeader) cardHeader.classList.remove('collapsed');
    const cardBody = document.getElementById('stockBrollBody');
    if (cardBody) cardBody.style.display = 'block';
    card.style.display   = 'block';
    status.style.display = 'flex';
    list.innerHTML       = '';
    // Bring the B-roll card into view - NOT the page bottom (jarring jump past
    // the card the user just triggered).
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });

    lockPipelineActions({ activeBtn: 'findBrollBtn', activeCard: 'stockBrollCard' });
    findBrollBtn.disabled = true;
    findBrollBtn.textContent = t('stock.searching');
    document.querySelectorAll('#captionsList .caption-input, #captionsList .caption-time-input, #captionsList .cap-btn').forEach(el => { el.disabled = true; });

    bumpPending(+1);
    const stockElapsedEl = document.getElementById('stockBrollElapsed');
    stockElapsedEl.textContent = '0:00';
    let stockSecs = 0;
    const stockTimer = setInterval(() => { stockElapsedEl.textContent = formatTime(++stockSecs); }, 1000);

    try {
      const resp = await apiFetch(`${API_BASE}/stock-broll/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ captions_json: JSON.stringify(captions), video_key: videoKey || '' }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `Spawn failed: ${resp.status}`);
      }
      const { call_id } = await resp.json();

      let netRetries = 0;
      while (true) {
        try {
          const pollResp = await apiFetch(`${API_BASE}/stock-broll-poll/${call_id}/`);
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
                if (fs.buf_drops)     dropParts.push(t('stock.edgeDrops', {n: fs.buf_drops}));
                if (fs.spacing_drops) dropParts.push(t('stock.spacingDrops', {n: fs.spacing_drops}));
                const detail = dropParts.length ? ` (${dropParts.join('; ')})` : '';
                const n = fs.sonnet_moments_raw;
                emptyMsg = t('stock.dropped', {n: n, s: n === 1 ? '' : 's', detail: detail});
              }
              list.innerHTML = `<p style="color:var(--muted);font-size:0.85rem;padding:8px 0">${emptyMsg}</p>`;
              return;
            }
            renderStockMoments(moments, result.video_context || null);
            return;
          }
          if (pollResp.status === 202) {
            await new Promise(r => setTimeout(r, 5000));
            netRetries = 0;
            continue;
          }
          const body = await pollResp.json().catch(() => ({}));
          throw new Error(body.error || `Server error ${pollResp.status}`);
        } catch (e) {
          if (!e.message.startsWith('Server error') && ++netRetries <= 3) {
            await new Promise(r => setTimeout(r, 2000));
            continue;
          }
          throw e;
        }
      }
    } catch (e) {
      clearInterval(stockTimer);
      console.error('Stock B-roll error:', e.message);
      status.style.display = 'none';
      list.innerHTML = `<p style="color:var(--red);font-size:0.85rem;padding:8px 0">${t('stock.failedRetry', {msg: e.message.slice(0, 160)})} <button onclick="triggerStockBroll()" style="margin-left:8px;font-size:0.8rem;padding:3px 10px;border-radius:6px;border:1px solid var(--red);background:none;color:var(--red);cursor:pointer">${t('veo.retry')}</button></p>`;
    } finally {
      bumpPending(-1);
      unlockPipelineActions();
      // Restore caption editor and button
      document.querySelectorAll('#captionsList .caption-input, #captionsList .caption-time-input, #captionsList .cap-btn').forEach(el => { el.disabled = false; });
      _updateDeleteButtons();
      findBrollBtn.textContent = t('stock.find');
      findBrollBtn.disabled = false;
    }
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
      dismissBtn.textContent = '✕';
      dismissBtn.title = isCoverage ? t('stock.skipRhythm') : t('stock.skip');
      dismissBtn.addEventListener('click', () => {
        delete stockBrollSelections[momentIdx];
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

      // Hebrew excerpt - prefer the backend's transcript_excerpt (built from edited captions)
      const excerptText = m.transcript_excerpt || m.verbatim_quote || (() => {
        const edited = getEditedCaptions();
        return edited.filter(c => c.end >= m.start - 0.5 && c.start <= m.end + 0.5).map(c => c.text).join(' ');
      })();
      if (excerptText) {
        const excerpt = document.createElement('div');
        excerpt.className = 'moment-excerpt';
        excerpt.textContent = excerptText;
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

      // Find different clips button
      let clipPage = 2;
      const findBtn = document.createElement('button');
      findBtn.className = 'find-clips-btn btn-refresh-icon';
      findBtn.textContent = t('stock.findDifferent');
      findBtn.addEventListener('click', async () => {
        findBtn.disabled = true;
        findBtn.textContent = t('stock.scoring');
        delete stockBrollSelections[momentIdx];
        await retryStockMomentClips(momentCtx.broad_search_prompt || m.search_query, clipsContainer, clipPage++, findBtn, momentCtx);
      });
      card.appendChild(findBtn);

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
        if (clip.page_url) window.open(clip.page_url, '_blank', 'noopener,noreferrer');
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
      playIcon.textContent = '▶';
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
          }
        } else {
          // Toggled off - remove selection
          clipCard.classList.remove('selected');
          selLabel.classList.remove('checked');
          if (momentCtx) delete stockBrollSelections[momentCtx.momentIdx];
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

  async function retryStockMomentClips(searchQuery, container, page, btn, momentCtx) {
    try {
      const ctxPayload = momentCtx
        ? JSON.stringify({
            label: momentCtx.label,
            reasoning: momentCtx.reasoning,
            strict_eval_prompt: momentCtx.strict_eval_prompt || '',
            broll_duration_seconds: momentCtx.broll_duration_seconds || 3.0,
          })
        : '';
      const resp = await apiFetch(`${API_BASE}/stock-broll-clips/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ search_query: searchQuery, page: page || 2, moment_context: ctxPayload }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `Spawn failed: ${resp.status}`);
      }
      const { call_id } = await resp.json();

      while (true) {
        const poll = await apiFetch(`${API_BASE}/stock-broll-clips-poll/${call_id}/`);
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 3000)); continue; }
        if (!poll.ok) throw new Error(`Poll error: ${poll.status}`);
        const result = await poll.json();
        renderClips(container, result.clips || [], searchQuery, momentCtx);
        break;
      }
    } catch (e) {
      console.error('Retry clips failed:', e.message);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = t('stock.findDifferent');
      }
    }
  }

  async function retryBrollVideo(s, thumbBox, retryBtn) {
    const geminiKey = document.getElementById('geminiKey').value.trim()
                   || localStorage.getItem('geminiApiKey') || '';
    const aspectRatio = document.querySelector('input[name="brollAspect"]:checked')?.value || '9:16';

    retryBtn.disabled = true;
    retryBtn.textContent = '…';

    try {
      const resp = await apiFetch(`${API_BASE}/broll_image/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: s.description, aspect_ratio: aspectRatio, gemini_key: geminiKey }),
      });
      if (!resp.ok) throw new Error(`Spawn failed: ${resp.status}`);
      const { call_id } = await resp.json();

      while (true) {
        const poll = await apiFetch(`${API_BASE}/broll_image_poll/${call_id}/`);
        if (poll.status === 202) { await new Promise(r => setTimeout(r, 5000)); continue; }
        if (!poll.ok) throw new Error(`Poll error: ${poll.status}`);
        const result = await poll.json();
        if (result.video_key) {
          const videoUrl = _withToken(`${API_BASE}/download/${result.video_key}/`);
          s.video_key = result.video_key;
          // Update in selectedBrolls if this card was checked
          const sel = selectedBrolls.find(b => b.start === s.start && b.end === s.end);
          if (sel) sel.video_key = result.video_key;
          // Replace thumb content with video
          thumbBox.innerHTML = '';
          thumbBox.classList.add('zoomable');
          const vid = document.createElement('video');
          vid.src = videoUrl;
          vid.autoplay = true;
          vid.loop = true;
          vid.muted = true;
          vid.playsInline = true;
          thumbBox.appendChild(vid);
          thumbBox.onclick = () => openLightbox(videoUrl);
        } else {
          throw new Error(result.video_error || 'Unknown error');
        }
        break;
      }
    } catch (e) {
      console.error('B-roll video retry failed:', e.message);
    } finally {
      retryBtn.disabled = false;
      retryBtn.textContent = t('veo.newVideo');
    }
  }

  function renderBrollList(suggestions) {
    const list = document.getElementById('brollList');
    list.innerHTML = '';
    suggestions.forEach((s, i) => {
      const row = document.createElement('div');
      row.className = 'broll-row';

      // Checkbox - select this B-roll to include in the final video
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.className = 'broll-checkbox';
      checkbox.title = t('veo.include');
      checkbox.addEventListener('change', () => {
        if (checkbox.checked) {
          selectedBrolls.push({ start: s.start, end: s.end, video_key: s.video_key });
        } else {
          selectedBrolls = selectedBrolls.filter(b => !(b.start === s.start && b.end === s.end));
        }
        updateBurnBtn();
      });

      // Thumbnail + retry button wrapped together
      const thumbWrap = document.createElement('div');
      thumbWrap.className = 'broll-thumb-wrap';

      const thumbBox = document.createElement('div');
      thumbBox.className = 'broll-thumb';
      if (s.video_key) {
        const videoUrl = _withToken(`${API_BASE}/download/${s.video_key}/`);
        const vid = document.createElement('video');
        vid.src = videoUrl;
        vid.autoplay = true;
        vid.loop = true;
        vid.muted = true;
        vid.playsInline = true;
        thumbBox.appendChild(vid);
        thumbBox.classList.add('zoomable');
        thumbBox.addEventListener('click', () => openLightbox(videoUrl));
      } else {
        const errMsg = s.video_error || t('veo.unavailable');
        thumbBox.title = errMsg;
        thumbBox.innerHTML = '<svg class="broll-thumb-placeholder" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" stroke-width="1.5"><path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>';
      }

      const retryBtn = document.createElement('button');
      retryBtn.className = 'broll-retry';
      retryBtn.textContent = t('veo.newVideo');
      retryBtn.title = t('veo.soon');
      retryBtn.disabled = true;

      thumbWrap.appendChild(thumbBox);
      thumbWrap.appendChild(retryBtn);

      const content = document.createElement('div');
      content.className = 'broll-content';

      const time = document.createElement('div');
      time.className = 'broll-time';
      time.textContent = fmtCapTime(s.start) + ' - ' + fmtCapTime(s.end);

      const label = document.createElement('div');
      label.className = 'broll-label';
      label.textContent = s.label;

      const desc = document.createElement('div');
      desc.className = 'broll-desc';
      desc.textContent = s.description;

      content.appendChild(time);
      content.appendChild(label);
      content.appendChild(desc);

      if (!s.video_key && s.video_error) {
        const vidErr = document.createElement('div');
        vidErr.style.cssText = 'font-size:0.72rem;color:var(--amber);margin-top:4px;';
        vidErr.textContent = t('stock.videoErr', {msg: s.video_error});
        content.appendChild(vidErr);
      }

      const dismiss = document.createElement('button');
      dismiss.className = 'broll-dismiss';
      dismiss.textContent = '✕';
      dismiss.addEventListener('click', () => {
        if (checkbox.checked) {
          selectedBrolls = selectedBrolls.filter(b => !(b.start === s.start && b.end === s.end));
          updateBurnBtn();
        }
        row.remove();
      });

      row.appendChild(checkbox);
      row.appendChild(thumbWrap);
      row.appendChild(content);
      row.appendChild(dismiss);
      list.appendChild(row);
    });
  }

  function _selectCaption(row, seekSecs) {
    document.querySelectorAll('#captionsList .caption-row-selected')
      .forEach(r => r.classList.remove('caption-row-selected'));
    row.classList.add('caption-row-selected');
    const vid = document.getElementById('cutVideo');
    if (vid && vid.src && isFinite(vid.duration)) {
      vid.currentTime = seekSecs;
      vid.pause();
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

    // Time controls
    const timeWrap = document.createElement('div');
    timeWrap.className = 'caption-time caption-time-wrap';

    function makeTimeRow(labelText, cls, val) {
      const tr = document.createElement('div');
      tr.className = 'caption-time-row';
      const lbl = document.createElement('label');
      lbl.textContent = labelText;
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
    const { tr: startRow, inp: startInp } = makeTimeRow('▶', 'caption-start', cap.start);
    const { tr: endRow,   inp: endInp   } = makeTimeRow('■', 'caption-end',   cap.end);
    timeWrap.appendChild(startRow);
    timeWrap.appendChild(endRow);

    // Text input
    const textInp = document.createElement('input');
    textInp.type      = 'text';
    textInp.className = 'caption-input';
    textInp.value     = cap.text;
    textInp.dir       = 'rtl';
    let _preTextEdit = null;
    textInp.addEventListener('focus', () => { _preTextEdit = getCaptionsFromEditor(); });
    textInp.addEventListener('input', () => {
      if (_preTextEdit) { _pushCaptionUndo(_preTextEdit); _preTextEdit = null; }  // one undo point per edit session
      captionsData = getCaptionsFromEditor();
      updatePreviewCaption();
      if (stockBrollAnalyzed && getCaptionsSignature() !== lastAnalyzedSignature) {
        document.getElementById('stockBrollRerunBanner').style.display = 'flex';
      }
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
    splitBtn.textContent = '✂';
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

  function showCaptionEditor() {
    // Ensure card body is expanded (may have been collapsed in a previous burn)
    const captionHeader = document.querySelector('#captionEditorCard .card-header');
    if (captionHeader) captionHeader.classList.remove('collapsed');
    const captionBody = document.getElementById('captionBody');
    if (captionBody) captionBody.style.display = 'block';

    const list = document.getElementById('captionsList');
    list.innerHTML = '';
    captionsData.forEach(cap => list.appendChild(_createCaptionRow(cap)));
    _updateDeleteButtons();
    _resetCaptionUndo();
    document.getElementById('captionEditorCard').style.display = 'block';
    document.getElementById('hookCard').style.display = 'block';
    // Stock B-roll card hosts the Find B-Roll Moments button
    document.getElementById('stockBrollCard').style.display = 'block';
    // Fresh video → clear any prior hooks and reset the button back to "Generate".
    const ghb = document.getElementById('generateHookBtn');
    ghb.disabled = false;
    ghb.setAttribute('data-i18n', 'hook.generate');
    ghb.textContent = t('hook.generate');
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
    setTimeout(() => { initPositionTrack(); updatePreviewCaption(); validateCaptionTimes(); }, 50);
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  }

  async function doBurn() {
    if (!videoKey) return;
    if (captionsData.length === 0 && selectedBrolls.length === 0 && Object.keys(stockBrollSelections).length === 0) return;
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

    const fname = selectedFile ? selectedFile.name : 'video.mp4';
    const burnUrl = new URL(API_BASE + '/burn/');
    burnUrl.searchParams.set('video_key',  videoKey);
    burnUrl.searchParams.set('filename',   fname);
    burnUrl.searchParams.set('font',       captionFont);
    burnUrl.searchParams.set('margin_v',   captionMarginPct.toFixed(4));
    burnUrl.searchParams.set('font_size',  Math.round(captionFontSize * 1.10));

    const burnErrorEl   = document.getElementById('burnError');
    const reprocessBtn  = document.getElementById('reprocessBtn');
    runBtn.disabled     = true;
    runBtn.textContent  = t('run.burning');
    burnErrorEl.style.display    = 'none';
    if (reprocessBtn) reprocessBtn.disabled = true;
    let burnBtnTimer = null;

    // Lock all editor cards and collapse them while burn is in progress
    const editorIds = ['captionEditorCard', 'hookCard', 'brollCard', 'stockBrollCard'];
    editorIds.forEach(id => document.getElementById(id)?.classList.add('burning'));
    [
      { h: document.querySelector('#captionEditorCard .card-header'), b: document.getElementById('captionBody') },
      { h: document.querySelector('#hookCard .card-header'),          b: document.getElementById('hookBody') },
      { h: document.querySelector('#brollCard .card-header'),         b: document.getElementById('brollBody') },
      { h: document.querySelector('#stockBrollCard .card-header'),    b: document.getElementById('stockBrollBody') },
    ].forEach(({ h, b }) => {
      if (h && !h.classList.contains('collapsed')) { h.classList.add('collapsed'); if (b) b.style.display = 'none'; }
    });
    try {
      // POST captions + selected B-rolls (Veo + stock) → get call_id immediately
      const allBroll = [...selectedBrolls, ...Object.values(stockBrollSelections)];

      // Collect hook settings if an option was selected
      let hookPayload = null;
      if (selectedHookIdx >= 0) {
        const hookTextEl = document.getElementById(`hookText${selectedHookIdx}`);
        const hookText   = hookTextEl ? hookTextEl.value.trim() : '';
        if (hookText) {
          hookPayload = {
            text:              hookText,
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
      const spawnResp = await apiFetch(burnUrl.toString(), {
        method: 'POST',
        body: JSON.stringify({ captions: edited, broll: allBroll, ...(hookPayload ? { hook: hookPayload } : {}) }),
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

      // Download to the device (optional - does NOT gate scheduling)
      runBtn.textContent = t('run.downloading');
      try {
        const dlAbort  = new AbortController();
        const dlKillId = setTimeout(() => dlAbort.abort(), 10 * 60 * 1000);
        try {
          const dlResp = await apiFetch(
            `${API_BASE}/download/${burnResult.output_key}/?filename=${encodeURIComponent(outFilename)}`,
            { signal: dlAbort.signal }
          );
          if (!dlResp.ok) throw new Error(`Download failed (${dlResp.status})`);
          resultBlob = new Blob([await dlResp.arrayBuffer()], { type: 'video/mp4' });
          resultName = outFilename;
          triggerDownload();
        } finally {
          clearTimeout(dlKillId);
        }
      } catch (dlErr) {
        if (dlErr.name !== 'AbortError')
          console.warn('Device download failed (video is still scheduled-ready):', dlErr.message);
      }
      // Burn + initial download finished - now the success banner (with
      // Download again) is truthful and usable.
      hasBurnedOnce = true;   // subsequent burns are re-burns
      document.getElementById('burnSuccessBanner').style.display = 'flex';
      // Re-enable editors for another round of changes on the same video
      editorIds.forEach(id => document.getElementById(id).classList.remove('burning'));
      [
        { h: document.querySelector('#captionEditorCard .card-header'), b: document.getElementById('captionBody') },
        { h: document.querySelector('#hookCard .card-header'),          b: document.getElementById('hookBody') },
        { h: document.querySelector('#brollCard .card-header'),         b: document.getElementById('brollBody') },
        { h: document.querySelector('#stockBrollCard .card-header'),    b: document.getElementById('stockBrollBody') },
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
          { h: document.querySelector('#hookCard .card-header'),          b: document.getElementById('hookBody') },
          { h: document.querySelector('#brollCard .card-header'),         b: document.getElementById('brollBody') },
          { h: document.querySelector('#stockBrollCard .card-header'),    b: document.getElementById('stockBrollBody') },
        ].forEach(({ h, b }) => {
          if (h) { h.classList.remove('collapsed'); if (b) b.style.display = 'block'; }
        });
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
    const views = { pipeline: 'pipelineView', history: 'historyView', guide: 'guideView', admin: 'adminView' };
    const tabs  = { pipeline: 'tabPipeline',  history: 'tabHistory',  guide: 'tabGuide',  admin: 'tabAdmin' };
    for (const k of Object.keys(views)) {
      document.getElementById(views[k]).style.display = (k === which) ? '' : 'none';
      document.getElementById(tabs[k]).classList.toggle('active', k === which);
      document.getElementById(tabs[k]).setAttribute('aria-selected', String(k === which));
    }
    if (which === 'history') loadHistory();
    if (which === 'admin') loadAdmin();
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
    if (saved === 'admin') {
      if (quotaInfo && quotaInfo.role === 'admin') switchTab('admin');
      else _pendingTabRestore = 'admin';
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
  async function loadAdmin() {
    const list = document.getElementById('adminList');
    const loading = document.getElementById('adminLoading');
    const errBox = document.getElementById('adminError');
    loading.style.display = '';
    errBox.style.display = 'none';
    list.innerHTML = '';
    try {
      const resp = await apiFetch(`${API_BASE}/admin/users`);
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const { users } = await resp.json();
      loading.style.display = 'none';
      users.forEach(u => list.appendChild(_adminRow(u)));
    } catch (e) {
      loading.style.display = 'none';
      errBox.textContent = t('admin.loadFailed');
      errBox.style.display = 'block';
    }
  }

  function _adminRow(u) {
    const row = document.createElement('div');
    row.className = 'admin-row';
    const name = document.createElement('div');
    name.className = 'admin-name';
    name.textContent = u.username + (u.role === 'admin' ? ' ★' : '');
    const used = document.createElement('div');
    used.className = 'admin-used';
    used.textContent = u.role === 'admin' ? t('admin.unlimited') : t('admin.used', {used: u.videos_used});
    row.append(name, used);
    if (u.role !== 'admin') {
      const inp = document.createElement('input');
      inp.type = 'number'; inp.min = -1; inp.max = 100000;
      inp.value = u.video_limit;
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
          btn.textContent = '✓';
        } catch {
          btn.textContent = t('admin.saveFailed');
        }
        setTimeout(() => { btn.textContent = t('admin.save'); btn.disabled = false; }, 1500);
      };
      row.append(inp, btn);
    }
    // Reset-password control (available for every account, admins included).
    const pwBtn = document.createElement('button');
    pwBtn.className = 'admin-reset-btn';
    pwBtn.textContent = t('admin.resetPw');
    pwBtn.onclick = () => _startPwReset(row, u, pwBtn);
    row.append(pwBtn);
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
        pwBtn.textContent = '✓';
        setTimeout(() => { pwBtn.textContent = t('admin.resetPw'); }, 1500);
      } catch {
        ok.disabled = false;
        ok.textContent = t('admin.saveFailed');
        setTimeout(() => { ok.textContent = t('admin.setPw'); }, 1500);
      }
    };
    row.append(inp, ok, cancel);
    inp.focus();
  }

  // ── History tab ──
  async function loadHistory() {
    const list    = document.getElementById('historyList');
    const empty   = document.getElementById('historyEmpty');
    const loading = document.getElementById('historyLoading');
    loading.style.display = '';
    empty.style.display   = 'none';
    list.innerHTML = '';
    try {
      const resp = await apiFetch(`${API_BASE}/jobs/`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const { jobs } = await resp.json();
      loading.style.display = 'none';
      if (!jobs || !jobs.length) { empty.style.display = ''; return; }
      jobs.forEach(job => list.appendChild(_historyCard(job)));
    } catch (e) {
      loading.style.display = 'none';
      empty.textContent = t('hist.loadError');
      empty.style.display = '';
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

    const thumb = document.createElement('img');
    thumb.className = 'history-thumb';
    thumb.loading = 'lazy';
    thumb.alt = '';
    thumb.src = _withToken(`${API_BASE}/thumbnail/${job.key}/`);
    thumb.onerror = () => { thumb.style.visibility = 'hidden'; };

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
      const fname = (job.name || 'video').replace(/\.mp4$/i, '') + '_edited.mp4';
      window.location.href = _withToken(`${API_BASE}/download/${job.key}/?filename=${encodeURIComponent(fname)}`);
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
      try {
        await apiFetch(`${API_BASE}/jobs/${job.key}/`, { method: 'DELETE' });
      } catch (_) {}
      loadHistory();
    };
    actions.append(sch, dl, del);

    card.append(thumb, info, actions);
    return card;
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
      sb.disabled = !has;
      sb.textContent = t('sched.suggest');
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
    const btn = document.getElementById('suggestCaptionBtn');
    const ta = document.getElementById('schedCaption');
    const errEl = document.getElementById('schedError');
    const orig = btn.textContent;
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
      btn.disabled = false; btn.textContent = orig;
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
    // A password-reset link takes priority over any existing session.
    const resetTok = new URLSearchParams(location.search).get('reset');
    if (resetTok) { showResetView(resetTok); return; }
    if (!authToken) { showAuthView(); return; }
    try {
      const r = await fetch(`${API_BASE}/auth/me`, { headers: { 'Authorization': 'Bearer ' + authToken } });
      if (!r.ok) throw new Error('unauthorized');
      quotaInfo = await r.json();   // reuse for the quota pill - no second /auth/me
      showApp();
    } catch {
      _sessionExpired();
    }
  })();

  // ── Language switch: refresh state-driven labels that data-i18n can't cover ──
  document.addEventListener('langchange', () => {
    const a = AGGR_MAP[aggrSlider.value - 1];
    aggrDesc.textContent = t(a.label);
    document.getElementById('enhanceVideoDesc').innerHTML = t(_EV_DESCS[_enhanceVideoMode()]);
    updateTimeEstimate();
    if (burnMode && !runBtn.disabled) updateBurnBtn();
    if (authMode === 'register') {
      document.getElementById('authSubmitBtn').textContent = t('auth.register');
      document.getElementById('authModeBtn').textContent = t('auth.toSignin');
    }
    const ap = document.getElementById('schedAutoPublish');
    if (ap && ap.checked) document.getElementById('autoPublishDesc').textContent = t('sched.apOn');
    const sb = document.getElementById('suggestCaptionBtn');
    if (sb) sb.textContent = t('sched.suggest');
    const upLbl = document.querySelector('#checkUpscale .check-label');
    if (upLbl) upLbl.textContent = _enhanceVideoMode() === 'esrgan' ? t('prog.upscale') : t('prog.enhanceVideo');
    updateQuotaUI();
    // Dynamic lists are built with t() at render time - re-render the open one
    // so its rows (used/unlimited/save, etc.) switch language too.
    if (authToken) {
      const av = document.getElementById('adminView');
      const hv = document.getElementById('historyView');
      if (av && av.style.display !== 'none') loadAdmin();
      if (hv && hv.style.display !== 'none') loadHistory();
    }
  });
