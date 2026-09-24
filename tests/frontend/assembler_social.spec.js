const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Per-clip social caption + hashtags on the clips-mode result tiles
// (2026-09-24): the button posts the clip's CURRENT range (after the trim
// steppers) + the rendered clip key; the result is an editable caption, an
// editable hashtag line and a copy button; the text lives on the candidate,
// so a re-render (which rebuilds the tiles) keeps it.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';
const V = { hook: 8, retention: 7, emotion: 6, clarity: 8, shareability: 7, reasoning: 'x', tip: '' };
const CANDS = {
  mode: 'clips', duration: 3600.0, summary: 'שיחה על נשימה ויוגה.',
  candidates: [
    { clip: 0, start: 612.3, end: 654.9, duration: 42.6, title: 'נשימה אחת שמשנה הכל', hook: 'נשימה אחת שמשנה הכל', quote: 'q', score: 70, thumb: THUMB, virality: V },
    { clip: 0, start: 1502.0, end: 1531.5, duration: 29.5, title: 'למה אנחנו ממהרים', hook: '', quote: 'q', score: 55, thumb: THUMB, virality: V },
  ],
  issues: [], clips: [{ name: 'podcast.mp4', duration: 3600 }], sources: ['u1234__srckey_src.mp4'],
};
const SOCIAL = { caption: 'נשימה אחת יכולה לשנות את היום\nשמרו לפעם הבאה שהלחץ עולה', hashtags: ['#נשימה', '#יוגה', '#רוגע', '#מיינדפולנס', '#breathwork'] };

async function boot(page, { socialPosts = [], socialResult = SOCIAL, analyzePosts = [] } = {}) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/\/profiles\/?$/, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"profiles":[],"default":null}' }));
  await page.addInitScript(() => {
    localStorage.setItem('hebpipe_token', 'test-token');
    // Capture clipboard writes (no permission prompts in CI).
    window.__copied = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (t) => { window.__copied.push(t); } } });
  });
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  await page.route(/\/upload_r2\/init\/$/, r =>
    r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"R2 not configured","code":"r2_unavailable"}' }));
  await page.route(/\/assembler\/analyze\/$/, async (route, request) => {
    analyzePosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-analyze"}' });
  });
  await page.route(/\/assembler\/analyze-poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANDS) }));
  let n = 0;
  await page.route(/\/assembler\/render\/$/, r =>
    r.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ call_id: 'fc-render-' + (n++) }) }));
  await page.route(/\/assembler\/render-poll\/(fc-render-\d+)/, (route, request) => {
    const id = request.url().match(/fc-render-(\d+)/)[1];
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ video_key: `u1234__abc_c${id}_out.mp4`, duration: 30.0, style_warnings: [] }) });
  });
  await page.route(/\/assembler\/social-caption\/$/, async (route, request) => {
    socialPosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-social"}' });
  });
  await page.route(/\/assembler\/social-caption-poll\/fc-social/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(socialResult) }));
  await page.route(/\/auth\/media-token/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m.test"}' }));
  await page.route(/\/media\//, r => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();
  await page.goto('/assembler.html');
}

// Processing = analysis + an automatic render of every suggested clip
// (2026-09-24). `settle` lets that first batch land (tiles with videos).
async function toTiles(page, analyzePosts, { settle = true } = {}) {
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await page.locator('#processBtn').click();
  await expect.poll(() => analyzePosts.length).toBe(1);
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand')).toHaveCount(2);
  await expect(page.locator('.out')).toHaveCount(2);
  if (settle) {
    await page.clock.fastForward(3100);
    await expect(page.locator('.out video')).toHaveCount(2);
    await expect(page.locator('#renderClipsBtn')).toBeEnabled();
  }
}

test('social caption: trimmed range + rendered key -> editable caption, hashtags, copy', async ({ page }) => {
  const socialPosts = [], analyzePosts = [];
  await boot(page, { socialPosts, analyzePosts });
  await toTiles(page, analyzePosts);
  // Trim the first clip: start 2 s earlier, end 1 s earlier.
  const steps = page.locator('.cand').nth(0).locator('.c-trim .step');
  await steps.nth(0).locator('button').nth(1).click();
  await steps.nth(0).locator('button').nth(1).click();
  await steps.nth(1).locator('button').nth(0).click();
  await page.locator('#renderClipsBtn').click();
  await page.clock.fastForward(3100);
  await expect(page.locator('.out video')).toHaveCount(2);

  const tile = page.locator('.out').nth(0);
  const btn = tile.locator('.o-soc-btn');
  await expect(btn).toHaveText('כיתוב לרשתות');
  await btn.click();
  await expect.poll(() => socialPosts.length).toBe(1);
  const p = socialPosts[0];
  expect(p.upload_key).toBe(analyzePosts[0].upload_keys[0]);
  expect(p.start).toBeCloseTo(610.3, 5);
  expect(p.end).toBeCloseTo(653.9, 5);
  expect(p.video_key).toBe('u1234__abc_c2_out.mp4');   // the refresh's render (the automatic batch took 0 and 1)
  expect(p.title).toBe('נשימה אחת שמשנה הכל');
  expect(p.hook).toBe('נשימה אחת שמשנה הכל');
  expect(Object.keys(p).sort()).toEqual(['end', 'hook', 'start', 'title', 'upload_key', 'video_key']);

  await page.clock.fastForward(3100);
  const cap = tile.locator('textarea.o-cap');
  await expect(cap).toHaveValue(SOCIAL.caption);
  await expect(tile.locator('input.o-tags')).toHaveValue(SOCIAL.hashtags.join(' '));
  await expect(btn).toHaveText('כיתוב חדש');

  // Edit both, copy -> caption + blank line + hashtags.
  await cap.fill('כיתוב ערוך');
  await tile.locator('input.o-tags').fill('#אחד #שניים');
  await tile.locator('.o-copy').click();
  await expect.poll(() => page.evaluate(() => window.__copied)).toEqual(['כיתוב ערוך\n\n#אחד #שניים']);
  await expect(tile.locator('.o-soc-msg')).toHaveText('הועתק.');

  // A re-render rebuilds the tiles - the edited text survives on the candidate.
  await page.locator('#renderClipsBtn').click();
  await expect(page.locator('.out').nth(0).locator('textarea.o-cap')).toHaveValue('כיתוב ערוך');
  await expect(page.locator('.out').nth(0).locator('input.o-tags')).toHaveValue('#אחד #שניים');
  await expect(page.locator('.out').nth(1).locator('textarea.o-cap')).toHaveCount(0);
});

test('social caption before the render finishes sends no video key', async ({ page }) => {
  const socialPosts = [], analyzePosts = [];
  await boot(page, { socialPosts, analyzePosts });
  await toTiles(page, analyzePosts, { settle: false });
  // The tiles exist (queued / cutting) before the render poll resolves.
  await page.locator('.out').nth(1).locator('.o-soc-btn').click();
  await expect.poll(() => socialPosts.length).toBe(1);
  expect(socialPosts[0].video_key).toBe('');
  expect(socialPosts[0].start).toBe(1502.0);
  expect(socialPosts[0].hook).toBe('');
});

test('an expired transcript shows a specific soft message', async ({ page }) => {
  const analyzePosts = [];
  await boot(page, { analyzePosts, socialResult: { error: 'no_transcript' } });
  await toTiles(page, analyzePosts);
  const tile = page.locator('.out').nth(0);
  await tile.locator('.o-soc-btn').click();
  await page.clock.fastForward(3100);
  await expect(tile.locator('.o-soc-msg')).toContainText('48 שעות');
  await expect(tile.locator('textarea.o-cap')).toHaveCount(0);
  await expect(tile.locator('.o-soc-btn')).toBeEnabled();
});

test('hook input: the keyboard auto-space after a geresh is removed (and reaches the render)', async ({ page }) => {
  const analyzePosts = [];
  await boot(page, { analyzePosts });
  await toTiles(page, analyzePosts);
  const hi = page.locator('.cand').nth(1).locator('.hook-input');
  // Replay a keyboard that prepends its auto-space to the next letter.
  await hi.evaluate((el) => {
    for (const [value, data] of [['ג', 'ג'], ['ג׳', '׳'], ['ג׳ ו', ' ו'], ['ג׳ונסון', 'נסון']]) {
      el.value = value;
      el.setSelectionRange(value.length, value.length);
      el.dispatchEvent(new InputEvent('input', { inputType: 'insertText', data, bubbles: true }));
    }
  });
  await expect(hi).toHaveValue('ג׳ונסון');
});
