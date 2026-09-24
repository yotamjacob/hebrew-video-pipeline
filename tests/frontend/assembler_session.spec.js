const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Refresh-proof assembler session (2026-09-24, user: "make everything
// persistent to refresh to not lose data"): the analysis, every per-clip edit,
// renders (done + still running) and the story's order survive a reload; a
// running analysis / render resumes polling; an interrupted upload says so; a
// leave warning guards in-flight work.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';
const V = { hook: 8, retention: 7, emotion: 6, clarity: 8, shareability: 7, reasoning: 'x', tip: '' };
const CANDS = {
  mode: 'clips', duration: 3600, summary: 'שיחה על נשימה.',
  candidates: [
    { clip: 0, start: 612.3, end: 654.9, duration: 42.6, title: 'נשימה אחת', hook: 'נשימה אחת שמשנה הכל', quote: 'q', score: 70, thumb: THUMB, virality: V },
    { clip: 0, start: 1502, end: 1531.5, duration: 29.5, title: 'למה ממהרים', hook: '', quote: 'q', score: 55, thumb: THUMB, virality: V },
  ],
  issues: [], clips: [{ name: 'podcast.mp4', duration: 3600 }], sources: ['u1__s_src.mp4'],
};
const MOMENTS = {
  duration: 120, title: 'כותרת', story: 'סיפור',
  moments: [0, 1, 2].map((i) => ({ clip: 0, start: i * 20, end: i * 20 + 10, role: 'story', quote: 'רגע ' + i, reason: 'r', thumb: THUMB })),
  clips: [{ name: 'a.mp4', duration: 120 }],
};

// `gates` hold a poll back (202) until the test opens it.
async function boot(page, { mode = 'clips', gates = {}, posts = { analyze: [], render: [] } } = {}) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/\/profiles\/?$/, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{"profiles":[],"default":null}' }));
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 't'));
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  await page.route(/\/upload_r2\/init\/$/, (r) => r.fulfill({ status: 503, contentType: 'application/json', body: '{}' }));
  await page.route(/\/assembler\/analyze\/$/, async (route, req) => {
    posts.analyze.push(req.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-an"}' });
  });
  await page.route(/\/assembler\/analyze-poll\//, (r) => (gates.analyze && !gates.analyze.open)
    ? r.fulfill({ status: 202, contentType: 'application/json', body: '{"status":"running"}' })
    : r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mode === 'clips' ? CANDS : MOMENTS) }));
  let n = 0;
  await page.route(/\/assembler\/render\/$/, async (route, req) => {
    posts.render.push(req.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ call_id: 'fc-r' + (n++) }) });
  });
  await page.route(/\/assembler\/render-poll\/(fc-r\d+)/, (route, req) => {
    if (gates.render && !gates.render.open) return route.fulfill({ status: 202, contentType: 'application/json', body: '{"status":"running"}' });
    const id = req.url().match(/fc-r(\d+)/)[1];
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ video_key: `u1__k_c${id}_out.mp4`, duration: 30, style_warnings: [] }) });
  });
  await page.route(/\/assembler\/social-caption\/$/, (r) => r.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-s"}' }));
  await page.route(/\/assembler\/social-caption-poll\//, (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ caption: 'כיתוב' }) }));
  await page.route(/\/auth\/media-token/, (r) => r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m"}' }));
  await page.route(/\/media\//, (r) => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();
  await page.goto('/assembler.html');
  return posts;
}

async function upload(page, { clipsMode = true } = {}) {
  if (clipsMode) await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await page.locator('#processBtn').click();   // analysis waits for the explicit click (2026-09-24)
}

const stored = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('hebpipe_asm_session') || 'null'));

test('clips: analysis, edits, a finished render and its social caption survive a refresh', async ({ page }) => {
  const posts = await boot(page);
  await upload(page);
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  const rows = page.locator('.cand');
  await expect(rows).toHaveCount(2);
  // Processing rendered both suggested clips automatically (2026-09-24).
  await expect.poll(() => posts.render.length).toBe(2);
  await page.clock.fastForward(3100);
  await expect(page.locator('#renderClipsBtn')).toBeEnabled();
  // Edits: trim clip 1 two seconds earlier, rewrite its hook, unpick clip 2.
  const steps = rows.nth(0).locator('.c-trim .step');
  await steps.nth(0).locator('button').nth(1).click();
  await steps.nth(0).locator('button').nth(1).click();
  await rows.nth(0).locator('.hook-input').fill('הוק חדש');
  await rows.nth(1).locator('.pick-box').uncheck();
  await page.locator('#renderClipsBtn').click();
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand').nth(0).locator('.c-out .o-actions')).toBeVisible();
  await page.locator('.cand').nth(0).locator('.o-soc-btn').click();
  await page.clock.fastForward(3100);
  await page.locator('.cand').nth(0).locator('textarea.o-cap').fill('כיתוב ערוך');
  await expect.poll(async () => (await stored(page))?.cands?.[0]?.social?.caption).toBe('כיתוב ערוך');

  await page.reload();
  await expect(page.locator('#clipsCard')).toBeVisible();
  await expect(page.locator('#modeClips')).toHaveClass(/on/);
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).locator('.hook-input')).toHaveValue('הוק חדש');
  await expect(rows.nth(0).locator('.c-trim .step').nth(0).locator('.val')).toHaveText('+2 שנ\'');
  await expect(rows.nth(1).locator('.pick-box')).not.toBeChecked();
  await expect(page.locator('.cand').nth(0).locator('.c-preview video')).toHaveAttribute('src', /u1__k_c2_out\.mp4/);   // its player came back
  await expect(page.locator('.cand').nth(0).locator('.edit-link')).toHaveAttribute('href', /u1__k_c2_out\.mp4/);   // the refresh's render
  await expect(page.locator('.cand').nth(0).locator('.edit-link')).toHaveAttribute('href', '/?edit=u1__k_c2_out.mp4');
  await expect(page.locator('.cand').nth(1).locator('.edit-link')).toHaveAttribute('href', '/?edit=u1__k_c1_out.mp4');   // unpicked: keeps its first render
  await expect(page.locator('.cand textarea.o-cap')).toHaveValue('כיתוב ערוך');
  await expect(page.locator('#setupCard')).toBeVisible();
  await expect(page.locator('#renderClipsBtn')).toHaveText('רענון הקליפים (1)');
  // A refresh after the restore still carries the restored edits.
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => posts.render.length).toBe(4);
  expect(posts.render[3].segments).toEqual([[0, 610.3, 654.9]]);
  expect(posts.render[3].hook_text).toBe('הוק חדש');
});

test('a running analysis resumes polling after a refresh', async ({ page }) => {
  const gates = { analyze: { open: false } };
  const posts = await boot(page, { gates });
  await upload(page);
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);                     // one "running" tick
  await expect(page.locator('#stage')).toContainText('מתמללים');
  // (the label shows before the analyze response lands - wait for the save)
  await expect.poll(async () => (await stored(page))?.phase).toBe('analyzing');
  // Leaving now warns (work in flight).
  expect(await page.evaluate(() => {
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  })).toBe(true);

  await page.reload();
  await expect(page.locator('#stage')).toContainText('מתמללים');
  gates.analyze.open = true;
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand')).toHaveCount(2);
  expect(posts.analyze.length).toBe(1);                   // resumed - never re-spawned
  expect((await stored(page)).phase).toBe('ready');
  // ...and processing went on to render the suggested clips.
  await expect.poll(() => posts.render.length).toBe(2);
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand .c-out .o-actions')).toHaveCount(2);
  // Nothing in flight any more: leaving does not warn.
  expect(await page.evaluate(() => {
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  })).toBe(false);
});

test('a render still running at refresh finishes into its tile', async ({ page }) => {
  const gates = { render: { open: false } };
  const posts = await boot(page, { gates });
  await upload(page);
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  // The automatic batch is running (render polls held at "running").
  await expect.poll(() => posts.render.length).toBe(2);
  await expect.poll(async () => (await stored(page))?.cands?.[1]?.render?.status).toBe('pending');

  await page.reload();
  await expect(page.locator('.cand .c-out .o-state .spinner')).toHaveCount(2);
  gates.render.open = true;
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand .c-out .o-actions')).toHaveCount(2);
  await expect(page.locator('.cand').nth(0).locator('.edit-link')).toHaveAttribute('href', /u1__k_c0_out\.mp4/);
  expect(posts.render.length).toBe(2);                    // polled, never re-rendered
  expect((await stored(page)).cands[0].render.status).toBe('done');
});

test('an upload cut by a refresh says so and starts clean', async ({ page }) => {
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('seeded')) {
      sessionStorage.setItem('seeded', '1');
      localStorage.setItem('hebpipe_asm_session', JSON.stringify({ v: 1, ts: Date.now(), mode: 'clips', clips: [{ key: 'k', name: 'p.mp4' }], phase: 'uploading' }));
    }
  });
  await boot(page);
  await expect(page.locator('#err')).toContainText('בחרו את הקובץ שוב');
  expect(await stored(page)).toBeNull();
});

test('story mode: order, dropped moments and the finished video survive a refresh', async ({ page }) => {
  const posts = await boot(page, { mode: 'story' });
  await upload(page, { clipsMode: false });
  await expect.poll(() => posts.analyze.length).toBe(1);
  await page.clock.fastForward(3100);
  const rows = page.locator('.moment');
  await expect(rows).toHaveCount(3);
  await rows.nth(1).locator('.drop-btn').click();                 // drop moment 1
  await rows.nth(2).locator('button').filter({ hasText: /./ }).first().click().catch(() => {});
  const orderBefore = await rows.evaluateAll((els) => els.map((e) => e.textContent));
  await page.locator('#renderBtn').click();
  await page.clock.fastForward(3100);
  await expect(page.locator('#result')).toBeVisible();

  await page.reload();
  await expect(rows).toHaveCount(3);
  expect(await rows.evaluateAll((els) => els.map((e) => e.textContent))).toEqual(orderBefore);
  await expect(page.locator('.moment.dropped')).toHaveCount(1);
  await expect(page.locator('#result')).toBeVisible();
  await expect(page.locator('#outVideo')).toHaveAttribute('src', /u1__k_c0_out\.mp4/);
});

test('an expired session (sources swept after 48 h) is ignored', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('hebpipe_asm_session', JSON.stringify({ v: 1, ts: Date.now() - 48 * 3600 * 1000, mode: 'clips',
      clips: [{ key: 'k', name: 'p.mp4' }], phase: 'ready', result: { mode: 'clips', candidates: [], clips: [] } }));
  });
  await boot(page);
  await expect(page.locator('#clipsCard')).toBeHidden();
  await expect(page.locator('#uploadCard')).toBeVisible();
});
