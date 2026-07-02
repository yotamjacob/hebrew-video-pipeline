// Shared helpers for Playwright tests

const API_BASE = 'https://yotamjacob--hebrew-video-pipeline-api.modal.run';

const DEFAULT_CAPTIONS = [
  { start: 0.5,  end: 2.3, text: 'שלום עולם' },
  { start: 3.0,  end: 5.1, text: 'בדיקה שנייה' },
  { start: 5.8,  end: 8.4, text: 'מבחן שלישי' },
];

const DEFAULT_HOOKS = [
  { text: 'סוד שאף אחד לא יגלה לך', rationale: 'Creates mystery' },
  { text: 'זה שינה את חיי לגמרי',    rationale: 'Emotional hook' },
  { text: 'הטעות שכולם עושים',        rationale: 'Curiosity gap' },
];

// Seed a signed-in session and mock the session check, then load the app.
// Must run BEFORE page.goto so the boot-time /auth/me check is intercepted.
async function bootApp(page) {
  // External Google Fonts block the page 'load' event under bad network
  // conditions and flake the whole suite — serve an empty stylesheet instead.
  await page.route(/fonts\.googleapis\.com/, r =>
    r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/fonts\.gstatic\.com/, r => r.abort());
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 'test-token'));
  await page.route(/\/auth\/me/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"username":"tester"}' }));
  await page.goto('/');
}

// Intercept all Modal API routes with deterministic mock responses.
// Pass overrides to simulate error scenarios.
async function mockAllApis(page, {
  captions      = DEFAULT_CAPTIONS,
  processStatus = 200,   // 200 = done immediately, 500 = server error
  burnStatus    = 200,
  hookStatus    = 200,
  uploadStatus  = 200,
} = {}) {
  // Warmup — always succeeds
  await page.route(`${API_BASE}/warmup/`, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"ok"}' }));

  // Metricool status — checked when the schedule card reveals; unmocked it
  // would hit the real API with the fake test token, 401, and bounce the
  // whole app back to the login view mid-test
  await page.route(/\/oauth\/status/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"connected":false}' }));

  // Cancel — always succeeds
  await page.route(`${API_BASE}/cancel/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"cancelled"}' }));

  // Chunked upload
  await page.route(`${API_BASE}/upload_chunk/**`, r =>
    uploadStatus === 200
      ? r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' })
      : r.fulfill({ status: uploadStatus, contentType: 'application/json',
                    body: JSON.stringify({ error: `Upload failed (${uploadStatus})` }) }));

  // Process spawn — POST to /process/ (regex excludes /process_poll/)
  await page.route(/\/process\/[^_]/, (route, request) => {
    if (request.method() !== 'POST') return route.continue();
    return processStatus === 200 || processStatus === 202
      ? route.fulfill({ status: 202, contentType: 'application/json',
                        body: JSON.stringify({ call_id: 'mock-process-call-id' }) })
      : route.fulfill({ status: processStatus, contentType: 'application/json',
                        body: JSON.stringify({ error: `Server error ${processStatus}` }) });
  });

  // Process poll — resolves immediately with captions
  await page.route(`${API_BASE}/process_poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ captions, video_key: 'mock-video-key_cut.mp4',
                                       step_times: { enhance: 12.3, cut: 20.1 } }) }));

  // Burn spawn — regex because the app appends query params (glob patterns
  // without wildcards match the full URL and would fall through to the network)
  await page.route(/\/burn\/?(\?|$)/, r =>
    burnStatus === 200
      ? r.fulfill({ status: 202, contentType: 'application/json',
                    body: JSON.stringify({ call_id: 'mock-burn-call-id' }) })
      : r.fulfill({ status: burnStatus, contentType: 'application/json',
                    body: JSON.stringify({ error: `Burn error ${burnStatus}` }) }));

  // Burn poll
  await page.route(`${API_BASE}/burn_poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ output_key: 'mock-output-key.mp4' }) }));

  // Download — return a minimal valid MP4-ish binary
  await page.route(`${API_BASE}/download/**`, r =>
    r.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'video/mp4',
        'Content-Disposition': 'attachment; filename="output_edited.mp4"',
      },
      body: Buffer.alloc(512, 0),
    }));

  // Hook generate spawn
  await page.route(`${API_BASE}/generate-hook/`, r =>
    hookStatus === 200
      ? r.fulfill({ status: 202, contentType: 'application/json',
                    body: JSON.stringify({ call_id: 'mock-hook-call-id' }) })
      : r.fulfill({ status: hookStatus, contentType: 'application/json',
                    body: JSON.stringify({ error: `Hook server error ${hookStatus}` }) }));
  await page.route(`${API_BASE}/generate-hook`, r =>
    r.fulfill({ status: 202, contentType: 'application/json',
                body: JSON.stringify({ call_id: 'mock-hook-call-id' }) }));

  // Hook poll
  await page.route(`${API_BASE}/generate-hook-poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ hooks: DEFAULT_HOOKS }) }));

  // Stock B-roll (not tested in detail, just suppress)
  await page.route(`${API_BASE}/stock-broll/**`, r =>
    r.fulfill({ status: 202, contentType: 'application/json',
                body: JSON.stringify({ call_id: 'mock-stock-call-id' }) }));
  await page.route(`${API_BASE}/stock-broll-poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
  await page.route(`${API_BASE}/stock-broll-clips/**`, r =>
    r.fulfill({ status: 202, contentType: 'application/json',
                body: JSON.stringify({ call_id: 'mock-clips-call-id' }) }));
  await page.route(`${API_BASE}/stock-broll-clips-poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ clips: [] }) }));

  // Thumbnail — return a minimal valid JPEG so drawHookPreview doesn't hit the real API
  await page.route(`${API_BASE}/thumbnail/**`, r =>
    r.fulfill({
      status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=300' },
      body: Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=', 'base64'),
    }));
}

// Select a fake video file via the file input, triggering handleFile()
async function selectFile(page, { name = 'test.mp4', sizeMB = 1 } = {}) {
  await page.setInputFiles('#fileInput', {
    name,
    mimeType: 'video/mp4',
    buffer: Buffer.alloc(sizeMB * 1024 * 1024),
  });
}

// Full happy path: select file → process → wait for caption editor
async function runFullUpload(page, options = {}) {
  await mockAllApis(page, options);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
}

module.exports = { API_BASE, DEFAULT_CAPTIONS, DEFAULT_HOOKS, mockAllApis, selectFile, runFullUpload, bootApp };
