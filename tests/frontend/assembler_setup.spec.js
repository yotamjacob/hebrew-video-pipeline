const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Setup card (2026-09-24, user): choose the layout (smart vertical by
// default, previewed) and the options BEFORE processing; nothing is analyzed
// until "עיבוד"; processing renders every suggested clip; afterwards the same
// card switches layouts / options and "רענון הקליפים" recreates the clips.
// Plus: long branding file names wrap, and the viral score explains itself.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';
const CANDS = {
  mode: 'clips', duration: 3600, summary: 's',
  candidates: [
    { clip: 0, start: 10, end: 40, duration: 30, title: 'קליפ א', hook: 'הוק', quote: 'q', score: 72, thumb: THUMB,
      virality: { hook: 4, retention: 8, emotion: 7, clarity: 9, shareability: 5, reasoning: 'r', tip: 'לקצר את הפתיחה בשתי שניות.' } },
    { clip: 0, start: 100, end: 190, duration: 90, title: 'קליפ ב', hook: '', quote: 'q', score: 41, thumb: THUMB,
      virality: { hook: 6, retention: 3, emotion: 6, clarity: 7, shareability: 6, reasoning: 'r', tip: '' } },
  ],
  issues: [], clips: [{ name: 'podcast.mp4', duration: 3600 }], sources: ['u1__s_src.mp4'],
};

async function boot(page, { posts = { analyze: [], render: [] }, holdUpload = null } = {}) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/\/profiles\/?$/, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{"profiles":[],"default":null}' }));
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 't'));
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), async (r) => {
    if (holdUpload) await holdUpload.promise;
    await r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.route(/\/upload_r2\/init\/$/, (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{}' }));
  await page.route(/\/assembler\/analyze\/$/, async (route, req) => {
    posts.analyze.push(req.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-an"}' });
  });
  await page.route(/\/assembler\/analyze-poll\//, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANDS) }));
  let n = 0;
  await page.route(/\/assembler\/render\/$/, async (route, req) => {
    posts.render.push(req.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ call_id: 'fc-r' + (n++) }) });
  });
  await page.route(/\/assembler\/render-poll\/(fc-r\d+)/, (route, req) => {
    const id = req.url().match(/fc-r(\d+)/)[1];
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ video_key: `u1__k_c${id}_out.mp4`, duration: 30, style_warnings: [] }) });
  });
  await page.route(/\/auth\/media-token/, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m"}' }));
  await page.route(/\/media\//, (r) => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();
  await page.goto('/assembler.html');
  return posts;
}
const pick = (page) => page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });

test('layout + options come BEFORE processing; nothing is analyzed until "עיבוד"', async ({ page }) => {
  const posts = await boot(page);
  await page.locator('#modeClips').click();
  await expect(page.locator('#setupCard')).toBeHidden();
  await pick(page);
  const setup = page.locator('#setupCard');
  await expect(setup).toBeVisible();
  // Three layouts, smart vertical selected by default, each previewed.
  await expect(page.locator('#frameRow .layout-opt')).toHaveCount(3);
  await expect(page.locator('#frameVert')).toHaveClass(/on/);
  await expect(page.locator('#frameVert')).toHaveAttribute('aria-checked', 'true');
  const painted = await page.evaluate(() => ['pvVert', 'pvFit', 'pvOrig'].map((id) => {
    const c = document.getElementById(id);
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let lit = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 200) lit++;
    return lit;
  }));
  expect(painted.every((n) => n > 50)).toBe(true);           // a real preview, not an empty box
  // The clip options are there before processing too.
  for (const id of ['#clipCapToggle', '#clipTightenToggle', '#clipHookToggle', '#clipCapStyle']) await expect(page.locator(id)).toBeVisible();
  await expect(page.locator('#capToggle')).toBeHidden();      // story-mode option
  // The upload runs, but the analysis waits for the click.
  await expect(page.locator('#stage')).toContainText('ההעלאה הסתיימה');
  await page.clock.fastForward(10_000);
  expect(posts.analyze).toHaveLength(0);
  await expect(page.locator('#processBtn')).toBeEnabled();
  await page.locator('#frameFit').click();
  await page.locator('#clipTightenToggle').uncheck();
  await page.locator('#processBtn').click();
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  // Processing = analysis + every suggested clip, with the chosen options.
  await expect.poll(() => posts.render.length).toBe(2);
  expect(posts.render.every((p) => p.reframe === 'fit' && p.tighten === false)).toBe(true);
  await expect(page.locator('#processBtn')).toBeHidden();
  await expect(page.locator('#renderClipsBtn')).toBeVisible();
});

test('"עיבוד" stays disabled until the video finished uploading, showing the progress', async ({ page }) => {
  let release;
  const holdUpload = { promise: new Promise((r) => { release = r; }) };
  const posts = await boot(page, { holdUpload });
  await page.locator('#modeClips').click();
  await pick(page);
  // The layout and options can be chosen while the upload runs...
  await page.locator('#frameFit').click();
  await page.locator('#clipHookToggle').uncheck();
  // ...but processing can not start yet.
  const btn = page.locator('#processBtn');
  await expect(btn).toBeDisabled();
  await expect(btn).toContainText('מעלים את הסרטון');
  await btn.click({ force: true });                       // a click on the disabled button does nothing
  expect(posts.analyze).toHaveLength(0);
  release();
  await expect(btn).toBeEnabled();
  await expect(btn).toHaveText('עיבוד');
  expect(posts.analyze).toHaveLength(0);                  // still waits for the click
  await btn.click();
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  await expect.poll(() => posts.render.length).toBe(2);
  expect(posts.render.every((p) => p.reframe === 'fit' && p.hook_text === '')).toBe(true);
});

test('after processing: switch the layout, see the note, refresh recreates every picked clip', async ({ page }) => {
  const posts = await boot(page);
  await page.locator('#modeClips').click();
  await pick(page);
  await page.locator('#processBtn').click();
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  await expect.poll(() => posts.render.length).toBe(2);
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand .c-out .o-actions')).toHaveCount(2);
  await expect(page.locator('#renderClipsBtn')).toHaveText('רענון הקליפים (2)');
  expect(posts.render.slice(0, 2).every((p) => p.reframe === '9:16')).toBe(true);   // the default
  await expect(page.locator('#settingsNote')).toBeHidden();
  await page.locator('#frameOrig').click();
  await expect(page.locator('#settingsNote')).toBeVisible();
  await page.locator('#clipHookToggle').uncheck();
  await page.locator('#renderClipsBtn').click();
  await expect(page.locator('#settingsNote')).toBeHidden();
  await expect.poll(() => posts.render.length).toBe(4);
  const refreshed = posts.render.slice(2);
  expect(refreshed.map((p) => p.filename).sort()).toEqual(['קליפ א.mp4', 'קליפ ב.mp4']);
  expect(refreshed.every((p) => p.reframe === undefined && p.hook_text === '')).toBe(true);
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand .edit-link').first()).toHaveAttribute('href', /u1__k_c[23]_out\.mp4/);
});

test('the options and the layout are remembered for the next session', async ({ page }) => {
  await boot(page);
  await page.locator('#modeClips').click();
  await pick(page);
  await page.locator('#frameOrig').click();
  await page.locator('#clipCapToggle').uncheck();
  await page.locator('#clipTightenToggle').uncheck();
  await page.reload();
  await page.locator('#modeClips').click();
  await pick(page);
  await expect(page.locator('#frameOrig')).toHaveClass(/on/);
  await expect(page.locator('#clipCapToggle')).not.toBeChecked();
  await expect(page.locator('#clipTightenToggle')).not.toBeChecked();
  await expect(page.locator('#clipHookToggle')).toBeChecked();
});

test('a refresh between the upload and "עיבוד" comes back to the choice, ready to process', async ({ page }) => {
  const posts = await boot(page);
  await page.locator('#modeClips').click();
  await pick(page);
  await expect(page.locator('#stage')).toContainText('ההעלאה הסתיימה');
  await page.reload();
  await expect(page.locator('#setupCard')).toBeVisible();
  await expect(page.locator('#processBtn')).toBeEnabled();
  await page.locator('#processBtn').click();
  await expect.poll(() => posts.analyze.length).toBe(1);
  expect(posts.analyze[0].upload_keys).toHaveLength(1);
  expect(posts.analyze[0].mode).toBe('clips');
});

test('a long intro / outro file name wraps inside its slot', async ({ page }) => {
  const posts = await boot(page);
  await page.locator('#modeClips').click();
  await pick(page);
  await page.locator('#processBtn').click();
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  await expect(page.locator('#brandCard')).toBeVisible();
  const long = 'Screen_Recording_20260916_184616_Egg Smash Adventures_extra_long_name_for_the_outro.mp4';
  await page.setInputFiles('#outroFile', { name: long, mimeType: 'video/mp4', buffer: Buffer.alloc(64 * 1024) });
  const name = page.locator('#outroName');
  await expect(name).toHaveText(long);
  const box = await name.boundingBox(), slot = await page.locator('#outroSlot').boundingBox(),
        card = await page.locator('#brandCard').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(slot.x - 1);
  expect(box.x + box.width).toBeLessThanOrEqual(slot.x + slot.width + 1);
  expect(slot.x + slot.width).toBeLessThanOrEqual(card.x + card.width + 1);
  expect(await name.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
  expect(box.height).toBeGreaterThan(20);                   // it wrapped onto more than one line
});

test('the viral score opens a dismissible card on how to improve it', async ({ page }) => {
  const posts = await boot(page);
  await page.locator('#modeClips').click();
  await pick(page);
  await page.locator('#processBtn').click();
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  const rows = page.locator('.cand');
  const score = rows.nth(0).locator('.score');
  await score.click();
  const card = rows.nth(0).locator('.viral-card');
  await expect(card).toBeVisible();
  await expect(score).toHaveAttribute('aria-expanded', 'true');
  await expect(card.locator('h4')).toContainText('72');
  // The two weakest dimensions of THIS clip first (hook 4, shareability 5), then its own tip.
  await expect(card.locator('li').nth(0)).toContainText('הוק (4/10)');
  await expect(card.locator('li').nth(1)).toContainText('שיתוף (5/10)');
  await expect(card).toContainText('לקצר את הפתיחה בשתי שניות.');
  await expect(card.locator('.v-how')).toContainText('הערכה עריכתית, לא נבואה');
  // Close button.
  await card.locator('.v-close').click();
  await expect(card).toHaveCount(0);
  await expect(score).toHaveAttribute('aria-expanded', 'false');
  // The long clip gets the length advice; Escape and an outside click dismiss.
  await rows.nth(1).locator('.score').click();
  await expect(rows.nth(1).locator('.viral-card')).toContainText('מעל 60 שניות');
  await page.keyboard.press('Escape');
  await expect(page.locator('.viral-card')).toHaveCount(0);
  await rows.nth(1).locator('.score').click();
  await page.locator('#clipsSummary').click();
  await expect(page.locator('.viral-card')).toHaveCount(0);
  // Only one open at a time.
  await rows.nth(0).locator('.score').click();
  await rows.nth(1).locator('.score').click();
  await expect(page.locator('.viral-card')).toHaveCount(1);
});

test('the mode choice and the upload zone are locked while the video is processing', async ({ page }) => {
  let release;
  const holdUpload = { promise: new Promise((r) => { release = r; }) };
  const posts = await boot(page, { holdUpload });
  await page.locator('#modeClips').click();
  const locked = async (yes) => {
    for (const id of ['#modeStory', '#modeClips']) {
      if (yes) await expect(page.locator(id)).toBeDisabled(); else await expect(page.locator(id)).toBeEnabled();
    }
    if (yes) await expect(page.locator('#drop')).toHaveClass(/locked/); else await expect(page.locator('#drop')).not.toHaveClass(/locked/);
    expect(await page.locator('#file').isDisabled()).toBe(yes);
    expect(await page.locator('#guidance').isDisabled()).toBe(yes);
  };
  await locked(false);
  await pick(page);
  await locked(true);                                          // uploading
  await expect(page.locator('#dropText')).toContainText('מעבדים');
  // A mode click or a dropped file does nothing mid-job (and never leaves the page).
  await page.locator('#modeStory').click({ force: true });
  await expect(page.locator('#modeClips')).toHaveClass(/on/);
  const dropped = await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.items.add(new File(['x'], 'second.mp4', { type: 'video/mp4' }));
    const e = new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true });
    document.getElementById('drop').dispatchEvent(e);
    return e.defaultPrevented;
  });
  expect(dropped).toBe(true);
  release();
  await expect(page.locator('#processBtn')).toBeEnabled();
  await locked(false);                                         // uploaded, waiting for "עיבוד"
  await page.locator('#processBtn').click();
  await expect.poll(() => posts.analyze.length).toBe(1);
  await locked(true);                                          // analyzing
  await page.clock.fastForward(3100);
  await expect.poll(() => posts.render.length).toBe(2);
  await locked(true);                                          // rendering the clips
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand .c-out .o-actions')).toHaveCount(2);
  await locked(false);                                         // all done - a new video can start
  await expect(page.locator('#dropText')).toContainText('הקלטה ארוכה אחת');
  expect(posts.analyze).toHaveLength(1);                       // the dropped second file never started anything
});
