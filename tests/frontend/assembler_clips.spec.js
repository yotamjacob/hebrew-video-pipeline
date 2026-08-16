const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Clips mode on the standalone /assembler page (long -> shorts, 2026-08-16):
// mode switch -> analyze posts mode:'clips' -> candidates render sorted with
// virality scores + rubric -> pick/unpick + edit hook -> batch render posts
// one render per picked clip (single window, tighten/captions/hook_text,
// distinct variant) -> result tiles.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';

const CANDS = {
  mode: 'clips',
  duration: 3600.0,
  summary: 'פודקאסט על יזמות - שיחה עם מייסדת סטארטאפ על הכישלון הראשון שלה.',
  candidates: [
    { clip: 0, start: 612.3, end: 654.9, duration: 42.6, title: 'הטעות שעלתה לי מיליון', hook: 'הטעות שעלתה לי מיליון שקל', quote: 'חתמתי בלי לקרוא את הסעיף הקטן', score: 78, thumb: THUMB,
      virality: { hook: 9, retention: 8, emotion: 7, clarity: 8, shareability: 8, reasoning: 'פתיח עם מספר קונקרטי, סיפור אישי עם תשלום ברור.', tip: 'לחתוך את שתי השניות של הצחוק בסוף.' } },
    { clip: 0, start: 1502.0, end: 1531.5, duration: 29.5, title: 'למה משקיעים אומרים לא', hook: 'שלוש סיבות שמשקיע אומר לא', quote: 'זה אף פעם לא בגלל הרעיון', score: 61, thumb: THUMB,
      virality: { hook: 7, retention: 6, emotion: 4, clarity: 8, shareability: 7, reasoning: 'טיפ ישים ומובנה, פחות רגש.', tip: '' } },
    { clip: 0, start: 2400.0, end: 2418.0, duration: 18.0, title: 'הרגע שרציתי לוותר', hook: '', quote: 'ישבתי במכונית ובכיתי', score: 44, thumb: THUMB,
      virality: { hook: 5, retention: 5, emotion: 8, clarity: 6, shareability: 4, reasoning: 'רגשי אבל תלוי הקשר.', tip: 'להוסיף כיתוב הקשר בפתיחה.' } },
    // A scoring batch failed server-side for this one: no rubric, score null.
    { clip: 0, start: 3000.0, end: 3030.0, duration: 30.0, title: 'קליפ בלי ציון', hook: '', quote: 'משהו', score: null, scored: false, thumb: THUMB,
      virality: { hook: null, retention: null, emotion: null, clarity: null, shareability: null, reasoning: '', tip: '' } },
  ],
  issues: ['batch 3: ValueError: unparseable answer'],
  clips: [{ name: 'podcast.mp4', duration: 3600 }],
};

async function boot(page, { analyzePosts = [], renderPosts = [] } = {}) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 'test-token'));
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  // R2 unconfigured -> the page falls back to the chunk path (the default
  // these specs exercise); the R2 happy path has its own test.
  await page.route(/\/upload_r2\/init\/$/, r =>
    r.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"R2 not configured","code":"r2_unavailable"}' }));
  await page.route(/\/assembler\/analyze\/$/, async (route, request) => {
    analyzePosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-analyze"}' });
  });
  await page.route(/\/assembler\/analyze-poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANDS) }));
  let n = 0;
  await page.route(/\/assembler\/render\/$/, async (route, request) => {
    renderPosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ call_id: 'fc-render-' + (n++) }) });
  });
  await page.route(/\/assembler\/render-poll\/(fc-render-\d+)/, (route, request) => {
    const id = request.url().match(/fc-render-(\d+)/)[1];
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ video_key: `u1234__abc_c${id}_out.mp4`, duration: 30.0 }) });
  });
  await page.route(/\/auth\/media-token/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m.test"}' }));
  await page.route(/\/media\//, r => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();
  await page.goto('/assembler.html');
}

test('mode switch changes the uploader copy and single-file mode', async ({ page }) => {
  await boot(page);
  await expect(page.locator('#modeStory')).toHaveClass(/on/);
  await expect(page.locator('#file')).toHaveAttribute('multiple', '');
  await page.locator('#modeClips').click();
  await expect(page.locator('#modeClips')).toHaveClass(/on/);
  await expect(page.locator('#modeStory')).not.toHaveClass(/on/);
  await expect(page.locator('#dropText')).toContainText('הקלטה ארוכה אחת');
  await expect(page.locator('#hintText')).toContainText('90 דקות');
  await expect(page.locator('#file')).not.toHaveAttribute('multiple', '');
});

test('clips flow: upload -> scored candidates -> curate -> batch render', async ({ page }) => {
  const analyzePosts = [], renderPosts = [];
  await boot(page, { analyzePosts, renderPosts });
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(2 * 1024 * 1024) });
  await expect.poll(() => analyzePosts.length).toBe(1);
  expect(analyzePosts[0].mode).toBe('clips');
  expect(analyzePosts[0].upload_keys).toHaveLength(1);
  await page.clock.fastForward(3100);

  await expect(page.locator('#clipsCard')).toBeVisible();
  await expect(page.locator('#clipsSummary')).toContainText('פודקאסט על יזמות');
  // The story-mode cards stay hidden in clips mode.
  await expect(page.locator('#board')).toBeHidden();
  await expect(page.locator('#storyCard')).toBeHidden();

  const rows = page.locator('.cand');
  await expect(rows).toHaveCount(4);
  // The unscored one: dash badge, no rubric bars / reasoning, still pickable; a soft note explains.
  await expect(rows.nth(3).locator('.score')).toHaveText(/^-/);
  await expect(rows.nth(3).locator('.score')).toHaveClass(/none/);
  await expect(rows.nth(3).locator('.bars')).toBeHidden();
  await expect(rows.nth(3).locator('.c-why')).toBeHidden();
  await expect(page.locator('#clipsIssues')).toContainText('1 קליפים לא קיבלו ציון');
  // Score badge, tiered by value; rubric bars; reasoning + tip.
  await expect(rows.nth(0).locator('.score')).toContainText('78');
  await expect(rows.nth(0).locator('.score')).toHaveClass(/t3/);
  await expect(rows.nth(1).locator('.score')).toHaveClass(/t2/);
  await expect(rows.nth(2).locator('.score')).toHaveClass(/t1/);
  await expect(rows.nth(0).locator('.c-title')).toHaveText('הטעות שעלתה לי מיליון');
  await expect(rows.nth(0).locator('.bar-item')).toHaveCount(5);
  await expect(rows.nth(0).locator('.bar-item').first()).toContainText('הוק 9');
  await rows.nth(0).locator('.c-why summary').click();
  await expect(rows.nth(0).locator('.c-why')).toContainText('מספר קונקרטי');
  await expect(rows.nth(0).locator('.c-why .tip')).toContainText('טיפ:');
  await expect(rows.nth(1).locator('.c-why .tip')).toHaveCount(0);   // empty tip -> no line
  await expect(rows.nth(0).locator('.c-time')).toContainText('10:12 - 10:55 (43 שנ\')');
  // Hook input prefilled from the model, editable.
  await expect(rows.nth(0).locator('.hook-input')).toHaveValue('הטעות שעלתה לי מיליון שקל');
  await rows.nth(0).locator('.hook-input').fill('המיליון שאיבדתי בגלל סעיף אחד');
  await expect(page.locator('#renderClipsBtn')).toHaveText('יצירת 4 קליפים');

  // Unpick the third and fourth clips.
  await rows.nth(2).locator('.pick-box').uncheck();
  await rows.nth(3).locator('.pick-box').uncheck();
  await expect(rows.nth(2)).toHaveClass(/off/);
  await expect(page.locator('#renderClipsBtn')).toHaveText('יצירת 2 קליפים');

  // Batch render: one POST per picked clip.
  await page.locator('#clipTightenToggle').uncheck();
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => renderPosts.length).toBe(2);
  const byTitle = Object.fromEntries(renderPosts.map(p => [p.filename, p]));
  const p1 = byTitle['הטעות שעלתה לי מיליון.mp4'], p2 = byTitle['למה משקיעים אומרים לא.mp4'];
  expect(p1).toBeTruthy(); expect(p2).toBeTruthy();
  expect(p1.segments).toEqual([[0, 612.3, 654.9]]);
  expect(p2.segments).toEqual([[0, 1502.0, 1531.5]]);
  expect(p1.hook_text).toBe('המיליון שאיבדתי בגלל סעיף אחד');   // the edited hook
  expect(p2.hook_text).toBe('שלוש סיבות שמשקיע אומר לא');
  expect(p1.captions).toBe(true);
  expect(p1.tighten).toBe(false);                                 // toggle honored
  expect(p1.variant).toMatch(/^c1_[0-9a-f]{6}$/);
  expect(p2.variant).toMatch(/^c2_[0-9a-f]{6}$/);
  expect(p1.variant.slice(3)).toBe(p2.variant.slice(3));          // same batch id
  expect(p1.upload_keys).toEqual(analyzePosts[0].upload_keys);

  // Both tiles resolve after a poll tick.
  await page.clock.fastForward(3100);
  await expect(page.locator('#clipsOut')).toBeVisible();
  await expect(page.locator('.out')).toHaveCount(2);
  await expect(page.locator('.out video')).toHaveCount(2);
  await expect(page.locator('.out a')).toHaveCount(2);
  await expect(page.locator('#clipsStage')).toContainText('כל 2 הקליפים מוכנים');
});

test('hook toggle off sends an empty hook_text', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts });
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await expect(page.locator('#clipsCard')).toBeVisible({ timeout: 10000 });
  await page.locator('#clipHookToggle').uncheck();
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => renderPosts.length).toBe(4);
  expect(renderPosts.every(p => p.hook_text === '')).toBe(true);
  expect(renderPosts.every(p => p.tighten === true)).toBe(true);
});

test('clips analysis keeps polling well past the old 10-minute cap and shows elapsed time', async ({ page }) => {
  const analyzePosts = [];
  await boot(page, { analyzePosts });
  // Never-finishing analysis: 202 forever.
  await page.route(/\/assembler\/analyze-poll\//, r =>
    r.fulfill({ status: 202, contentType: 'application/json', body: '{"status":"running"}' }));
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await expect.poll(() => analyzePosts.length).toBe(1);
  // Drive the clock until 250 ticks (12.5 min) have been counted - past the
  // old 200-tick cap. Each tick's poll fetch is async, so advance one tick
  // at a time and wait for the counter to catch up.
  const stage = page.locator('#stage');
  const elapsed = async () => {
    const m = /\((\d+):(\d\d)\)/.exec(await stage.textContent());
    return m ? Number(m[1]) * 60 + Number(m[2]) : 0;
  };
  // Each tick's poll fetch must land before the next timer is armed, so
  // give the event loop a beat between advances; 320 advances comfortably
  // clear 250 counted ticks.
  for (let i = 0; i < 320 && (await elapsed()) < 750; i++) {
    await page.clock.fastForward(3000);
    await page.waitForTimeout(5);
  }
  expect(await elapsed()).toBeGreaterThanOrEqual(750);
  await expect(page.locator('#err')).toBeHidden();
  await expect(page.locator('#clipsCard')).toBeHidden();
});

test('R2 direct upload: parts PUT to presigned URLs, complete, then analyze', async ({ page }) => {
  const analyzePosts = [], puts = [], completes = [];
  await boot(page, { analyzePosts });
  // Override the 503 stub with a real init answer: 2 parts of 1 MB.
  await page.route(/\/upload_r2\/init\/$/, async (route, request) => {
    const body = request.postDataJSON();
    expect(body.size).toBe(2 * 1024 * 1024);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      upload_id: 'mp-1', part_size: 1024 * 1024,
      urls: ['https://r2.test/part/1?sig=a', 'https://r2.test/part/2?sig=b'] }) });
  });
  await page.route(/https:\/\/r2\.test\/part\//, async (route, request) => {
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'PUT', 'Access-Control-Allow-Headers': '*', 'Access-Control-Expose-Headers': 'ETag' } });
      return;
    }
    puts.push({ url: request.url(), len: request.postDataBuffer().length });
    await route.fulfill({ status: 200, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Expose-Headers': 'ETag', 'ETag': '"etag-' + puts.length + '"' }, body: '' });
  });
  await page.route(/\/upload_r2\/complete\/$/, async (route, request) => {
    completes.push(request.postDataJSON());
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  let chunkPosts = 0;
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), r => { chunkPosts++; return r.fulfill({ status: 200, body: '{"ok":true}' }); });

  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(2 * 1024 * 1024) });
  await expect.poll(() => analyzePosts.length).toBe(1);
  expect(puts).toHaveLength(2);
  expect(puts.map(p => p.len).sort()).toEqual([1024 * 1024, 1024 * 1024]);
  expect(completes).toHaveLength(1);
  expect(completes[0].upload_id).toBe('mp-1');
  expect(completes[0].etags).toHaveLength(2);
  expect(completes[0].etags.every(e => /^"etag-\d"$/.test(e))).toBe(true);
  expect(completes[0].key).toBe(analyzePosts[0].upload_keys[0]);   // same key the analyze uses
  expect(chunkPosts).toBe(0);                                       // no fallback needed
  await expect(page.locator('#bar')).toHaveAttribute('style', /width: 100%/);
});

test('R2 part without a readable ETag falls back to the chunk path with the same key', async ({ page }) => {
  const analyzePosts = [];
  await boot(page, { analyzePosts });
  await page.route(/\/upload_r2\/init\/$/, r => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify({ upload_id: 'mp-2', part_size: 1024 * 1024, urls: ['https://r2.test/part/1'] }) }));
  // 200 but no ETag exposed = bucket CORS missing ExposeHeaders.
  await page.route(/https:\/\/r2\.test\/part\//, r => r.fulfill({ status: 200, headers: { 'Access-Control-Allow-Origin': '*' }, body: '' }));
  const chunkKeys = [];
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), (route, request) => {
    chunkKeys.push(request.headers()['x-upload-key']);
    return route.fulfill({ status: 200, body: '{"ok":true}' });
  });
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await expect.poll(() => analyzePosts.length).toBe(1);
  expect(chunkKeys.length).toBeGreaterThan(0);
  expect(new Set(chunkKeys)).toEqual(new Set([analyzePosts[0].upload_keys[0]]));
});
