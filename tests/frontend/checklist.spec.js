const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, selectFile, runFullUpload, DEFAULT_CAPTIONS, bootApp } = require('./helpers');

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('checklist shows the real backend step times on completion', async ({ page }) => {
  await runFullUpload(page);   // helpers mock reports step_times {enhance: 12.3, cut: 20.1}
  await expect(page.locator('#checkEnhanceTime')).toHaveText('0:12');
  await expect(page.locator('#checkCutTime')).toHaveText('0:20');
});

test("cut step is labeled 'transcribe' when Cut silences is toggled off", async ({ page }) => {
  // The worker's 'cut' stage also covers transcription (captions need it), so
  // it runs even with the toggle off - labeled "Cut silences" it read as the
  // toggle being ignored. The label must follow what the run actually does.
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  const label = page.locator('#checkCut .check-label');
  // Toggle off → transcription label.
  await page.evaluate(() => {
    const el = document.getElementById('cutSilences');
    el.checked = false; el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#runBtn');
  await expect(label).toHaveText('תמלול הדיבור');
});

test('rows for disabled tools are hidden from the checklist', async ({ page }) => {
  // Cut + captions off (audio enhance still on): no transcription runs, so
  // neither a cut nor a transcribe row may appear; enhance stays listed.
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.evaluate(() => {
    for (const id of ['cutSilences', 'burnCaptions']) {
      const el = document.getElementById(id);
      el.checked = false; el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await page.click('#runBtn');
  await expect(page.locator('#statusChecklist')).toBeVisible();
  await expect(page.locator('#checkCut')).toBeHidden();
  await expect(page.locator('#checkEnhance')).toBeVisible();
});

test('live progress from process_poll drives step transitions', async ({ page }) => {
  await mockAllApis(page);
  // First two polls: still running, enhance finished for real in 8s, cut active.
  // Third poll: done.
  let polls = 0;
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r => {
    polls += 1;
    if (polls < 3) {
      return r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'running',
                               progress: { stage: 'cut', done: { enhance: 8.2 } } }) });
    }
    return r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { enhance: 8.2, cut: 14.9 } }) });
  });

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // While running: enhance closed with its real time, cut spinning
  await expect(page.locator('#checkEnhance')).toHaveClass(/done/, { timeout: 10_000 });
  await expect(page.locator('#checkEnhanceTime')).toHaveText('0:08');
  await expect(page.locator('#checkCut')).toHaveClass(/active/);

  // Done: cut closed with its real time
  await expect(page.locator('#checkCut')).toHaveClass(/done/, { timeout: 15_000 });
  await expect(page.locator('#checkCutTime')).toHaveText('0:15');
});

test('steps that never ran are hidden, not estimated', async ({ page }) => {
  await mockAllApis(page);
  // Backend reports only cut (enhance toggle off → never ran)
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { cut: 9.4 } }) }));

  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  // The input is visually hidden behind a styled toggle - flip it directly
  await page.evaluate(() => {
    const el = document.getElementById('enhanceAudio');
    el.checked = false;
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('#runBtn');

  await expect(page.locator('#checkCut')).toHaveClass(/done/, { timeout: 10_000 });
  await expect(page.locator('#checkCutTime')).toHaveText('0:09');
  await expect(page.locator('#checkEnhance')).toBeHidden();
});

test('AI upscale gets its own live progress row with real times', async ({ page }) => {
  await mockAllApis(page);
  let polls = 0;
  await page.unroute(`${API_BASE}/process_poll/**`);
  await page.route(`${API_BASE}/process_poll/**`, r => {
    polls += 1;
    if (polls < 3) {
      return r.fulfill({ status: 202, contentType: 'application/json',
        body: JSON.stringify({ status: 'running',
                               progress: { stage: 'upscale', done: { enhance: 5.0, cut: 9.2 } } }) });
    }
    return r.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ captions: DEFAULT_CAPTIONS, video_key: 'mock-video-key_cut.mp4',
                             step_times: { enhance: 5.0, cut: 9.2, upscale: 33.4 } }) });
  });

  await page.click('label[for="ev_esrgan"]');
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');

  // While running: upscale row visible, labelled, spinning; earlier steps closed real
  await expect(page.locator('#checkUpscale')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#checkUpscale')).toHaveClass(/active/);
  await expect(page.locator('#checkUpscale .check-label')).toHaveText('שדרוג AI');
  await expect(page.locator('#checkCutTime')).toHaveText('0:09');

  // Done: closed with the backend's real duration
  await expect(page.locator('#checkUpscale')).toHaveClass(/done/, { timeout: 15_000 });
  await expect(page.locator('#checkUpscaleTime')).toHaveText('0:33');
});

test('every enabled tool is listed as pending from the moment the run starts, in order', async ({ page }) => {
  // Rows used to pop into existence only when their step STARTED (auto B-roll
  // and hook appear minutes in, after the editor opens) - the card must show
  // the full plan up front.
  await mockAllApis(page);
  // Hold the upload phase open: with every API mocked to answer instantly the
  // whole run (upload -> process -> editor -> auto tools) finishes before the
  // assertions below - rows are already active/done. Real chunk POSTs get a
  // delay (the index-9999 probe stays instant); last-registered route wins.
  await page.route(/\/upload_chunk\//, async (route, request) => {
    if (request.headers()['x-upload-index'] !== '9999')
      await new Promise(r => setTimeout(r, 3000));
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.evaluate(() => {
    for (const id of ['autoBroll', 'autoHook']) {
      const el = document.getElementById(id);
      if (el && !el.checked) { el.checked = true; el.dispatchEvent(new Event('change', { bubbles: true })); }
    }
  });
  await page.click('#runBtn');
  // Assert during the UPLOAD phase - before any backend stage has begun.
  await expect(page.locator('#checkUpload')).toHaveClass(/active/);
  for (const id of ['checkFinalize', 'checkBroll', 'checkHook']) {
    await expect(page.locator('#' + id)).toBeVisible();
    await expect(page.locator('#' + id)).toHaveClass(/pending/);
  }
  // "Loading preview" (finalize) precedes the background B-roll/hook rows -
  // that is the order the steps actually complete in.
  const order = await page.evaluate(() =>
    [...document.querySelectorAll('.check-item')].map(el => el.id));
  expect(order.indexOf('checkFinalize')).toBeLessThan(order.indexOf('checkBroll'));
  expect(order.indexOf('checkBroll')).toBeLessThan(order.indexOf('checkHook'));
  // Burn belongs to the export step - never pre-listed during processing.
  await expect(page.locator('#checkBurn')).toBeHidden();
});

test('attaching a file reveals a loud card and scrolls it into view', async ({ page }) => {
  await bootApp(page);
  await selectFile(page);
  const card = page.locator('#fileInfo');
  await expect(card).toBeVisible();
  await expect(card).toContainText('test.mp4');
  // The attach must be self-evident: the card scrolls into the viewport
  // (born-below-the-fold attaches used to read as a no-op on phones).
  await expect(card).toBeInViewport();
  // Bold treatment: the strongest border weight on the page (2px olive).
  expect(await card.evaluate((el) => getComputedStyle(el).borderTopWidth)).toBe('2px');
});
