const { test, expect } = require('@playwright/test');
const { mockAllApis, bootApp, API_BASE, DEFAULT_CAPTIONS } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

// Pick an audio file via the input, triggering handleFile() in audio mode.
async function selectAudio(page, { name = 'recording.ogg', mimeType = 'audio/ogg' } = {}) {
  await page.setInputFiles('#fileInput', {
    name, mimeType, buffer: Buffer.alloc(1024 * 1024),
  });
}

// Audio process_poll returns an .m4a output key (as the real backend does).
async function mockAudioApis(page, captions = DEFAULT_CAPTIONS) {
  await mockAllApis(page, { captions });
  await page.route(`${API_BASE}/process_poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ captions, video_key: 'mock-key_cut.m4a',
                                       step_times: { enhance: 5, cut: 8 } }) }));
  await page.route(`${API_BASE}/download/**`, r =>
    r.fulfill({ status: 200,
                headers: { 'Content-Type': 'audio/mp4',
                           'Content-Disposition': 'attachment; filename="recording_clean.m4a"' },
                body: Buffer.alloc(256, 0) }));
}

test('file input accepts audio files', async ({ page }) => {
  await expect(page.locator('#fileInput')).toHaveAttribute('accept', /audio/);
});

test('selecting an audio file hides the video-only options', async ({ page }) => {
  await mockAudioApis(page);
  await selectAudio(page);
  await expect(page.locator('#burnCaptionsRow')).toBeHidden();
  await expect(page.locator('#enhanceVideoRow')).toBeHidden();
  await expect(page.locator('#enhanceVideoPanel')).toBeHidden();
  // audio-safe options remain interactive (the toggle <input> is CSS-hidden
  // behind a custom switch, so assert enabled rather than visible)
  await expect(page.locator('#cutSilences')).toBeEnabled();
  await expect(page.locator('#enhanceAudio')).toBeEnabled();
  // run is always available for audio (captions always produced)
  await expect(page.locator('#runBtn')).toBeEnabled();
});

test('audio file shows the limited-functionality info notice', async ({ page }) => {
  await mockAudioApis(page);
  await expect(page.locator('#noticeAudio')).toBeHidden();
  await selectAudio(page);
  await expect(page.locator('#noticeAudio')).toBeVisible();
});

test('audio processing lands on the reduced editor (audio player + downloads, no burn)', async ({ page }) => {
  await mockAudioApis(page);
  await selectAudio(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });

  // Audio player shown; video player hidden
  await expect(page.locator('#audioPlayer')).toBeVisible();
  await expect(page.locator('#captionPlayer')).toBeHidden();
  // Downloads present
  await expect(page.locator('#downloadAudioBtn')).toBeVisible();
  await expect(page.locator('#downloadSrtBtn')).toBeVisible();
  // Video-only sections hidden, no burn button
  await expect(page.locator('#hookCard')).toBeHidden();
  await expect(page.locator('#stockBrollCard')).toBeHidden();
  await expect(page.locator('#runBtn')).toBeHidden();

  // Captions still editable
  await expect(page.locator('.caption-input')).toHaveCount(DEFAULT_CAPTIONS.length);
});

test('switching from audio back to a video file restores video options', async ({ page }) => {
  await mockAudioApis(page);
  await selectAudio(page);
  await expect(page.locator('#enhanceVideoRow')).toBeHidden();
  // re-pick a video (no confirm dialog: fileInput change just re-runs handleFile)
  await page.setInputFiles('#fileInput', { name: 'clip.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await expect(page.locator('#enhanceVideoRow')).toBeVisible();
  await expect(page.locator('#burnCaptionsRow')).toBeVisible();
});
