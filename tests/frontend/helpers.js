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
                body: JSON.stringify({ captions, video_key: 'mock-video-key_cut.mp4' }) }));

  // Burn spawn
  await page.route(`${API_BASE}/burn/`, r =>
    burnStatus === 200
      ? r.fulfill({ status: 202, contentType: 'application/json',
                    body: JSON.stringify({ call_id: 'mock-burn-call-id' }) })
      : r.fulfill({ status: burnStatus, contentType: 'application/json',
                    body: JSON.stringify({ error: `Burn error ${burnStatus}` }) }));
  await page.route(`${API_BASE}/burn`, r =>
    r.fulfill({ status: 202, contentType: 'application/json',
                body: JSON.stringify({ call_id: 'mock-burn-call-id' }) }));

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

module.exports = { API_BASE, DEFAULT_CAPTIONS, DEFAULT_HOOKS, mockAllApis, selectFile, runFullUpload };
