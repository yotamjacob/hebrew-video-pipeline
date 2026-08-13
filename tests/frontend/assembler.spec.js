const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Standalone /assembler page (hidden beta). It loads NO app.js - its only
// network calls are the assembler routes + upload_chunk + media-token, all
// stubbed here. A 1x1 jpeg keeps the thumbnail data-URI path honest.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';

// Cross-clip storyboard: moments come from TWO different clips.
const MOMENTS = {
  duration: 420.0,
  title: 'ביקור ביקב משפחתי',
  story: 'רחל, בעלת יקב משפחתי בגליל, פותחת את הדלת לסיפור של שלושה דורות: מהחביות הראשונות במרתף של סבה ועד מדליית זהב עולמית. הסרטון נע מהתשוקה האישית שלה אל הנוף והעבודה בכרם, ונסגר בהזמנה חמה לבוא לטעום.',
  moments: [
    { clip: 0, start: 5.0, end: 15.0, role: 'hook', quote: 'זה היקב שסבא שלי חלם עליו', reason: 'פתיח רגשי שתופס', thumb: THUMB },
    { clip: 1, start: 60.0, end: 75.0, role: 'story', quote: 'התחלנו משלוש חביות במרתף', reason: 'הסיפור המרכזי', thumb: THUMB },
    { clip: 0, start: 180.0, end: 192.0, role: 'gold', quote: 'היין הזה זכה במדליה', reason: 'רגע השיא', thumb: THUMB },
  ],
  clips: [{ name: 'interview.mp4', duration: 240 }, { name: 'tour.mp4', duration: 180 }],
};

async function bootAssembler(page, { analyzePosts = [], renderPosts = [] } = {}) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 'test-token'));
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  await page.route(/\/assembler\/analyze\/$/, async (route, request) => {
    analyzePosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-analyze"}' });
  });
  await page.route(/\/assembler\/analyze-poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOMENTS) }));
  await page.route(/\/assembler\/render\/$/, async (route, request) => {
    renderPosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-render"}' });
  });
  await page.route(/\/assembler\/render-poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"video_key":"u1234__abc_out.mp4","duration":37.0}' }));
  await page.route(/\/auth\/media-token/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m.test"}' }));
  await page.route(/\/media\//, r => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();   // the 3s poll waits become instant fast-forwards
  await page.goto('/assembler.html');
}

test('signed out: the auth gate shows instead of the uploader', async ({ page }) => {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.goto('/assembler.html');
  await expect(page.locator('#authGate')).toBeVisible();
  await expect(page.locator('#uploadCard')).toBeHidden();
});

test('multi-clip flow: two uploads -> cross-clip storyboard -> curate -> render', async ({ page }) => {
  const analyzePosts = [], renderPosts = [];
  await bootAssembler(page, { analyzePosts, renderPosts });
  await expect(page.locator('#uploadCard')).toBeVisible();

  await page.setInputFiles('#file', [
    { name: 'interview.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(3 * 1024 * 1024) },
    { name: 'tour.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(2 * 1024 * 1024) },
  ]);
  // The upload chain is real network (the clock only freezes timers) - wait
  // for the analyze POST to exist before advancing the poll timer.
  await expect.poll(() => analyzePosts.length).toBe(1);
  await page.clock.fastForward(3100);   // one poll tick

  // Both clips were uploaded under distinct keys, sent together to analyze.
  expect(analyzePosts[0].upload_keys).toHaveLength(2);
  expect(new Set(analyzePosts[0].upload_keys).size).toBe(2);
  expect(analyzePosts[0].filenames).toEqual(['interview.mp4', 'tour.mp4']);

  await expect(page.locator('#board')).toBeVisible();
  // The AI's narrative reading shows BEFORE the storyboard + is copyable
  // (the seed for the Phase-3 voice-over script).
  await expect(page.locator('#storyCard')).toBeVisible();
  await expect(page.locator('#storyText')).toContainText('שלושה דורות');
  await expect(page.locator('#copyStory')).toBeVisible();
  const rows = page.locator('.moment');
  await expect(rows).toHaveCount(3);
  // Clip badges show which clip each moment comes from (only with 2+ clips).
  await expect(rows.nth(0).locator('.m-clip')).toHaveText('interview.mp4');
  await expect(rows.nth(1).locator('.m-clip')).toHaveText('tour.mp4');
  await expect(page.locator('#total')).toContainText('0:37');

  // Drop + restore + reorder still work across clips.
  await rows.nth(1).locator('.drop-btn').click();
  await expect(page.locator('#total')).toContainText('0:22');
  await page.locator('.moment').nth(1).locator('.drop-btn').click();
  await page.locator('.moment').nth(1).locator('button[title="הזזה למעלה"]').click();
  await expect(page.locator('.moment').nth(0).locator('.m-quote')).toContainText('שלוש חביות');

  // Render posts [clip, start, end] triples in STORYBOARD order + all keys.
  await page.locator('#renderBtn').click();
  await expect.poll(() => renderPosts.length).toBe(1);
  await page.clock.fastForward(3100);
  await expect(page.locator('#result')).toBeVisible();
  await expect(page.locator('#outVideo')).toHaveAttribute('src', /\/media\/u1234__abc_out\.mp4\?token=m\.test/);
  expect(renderPosts[0].upload_keys).toEqual(analyzePosts[0].upload_keys);
  expect(renderPosts[0].segments).toEqual([[1, 60, 75], [0, 5, 15], [0, 180, 192]]);
  expect(renderPosts[0].captions).toBe(true);   // synced-captions toggle defaults ON
});

test('single clip: no clip badges, bare flow still works', async ({ page }) => {
  const analyzePosts = [];
  await bootAssembler(page, { analyzePosts });
  await page.setInputFiles('#file', {
    name: 'winery.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024),
  });
  await expect.poll(() => analyzePosts.length).toBe(1);
  await page.clock.fastForward(3100);
  expect(analyzePosts[0].upload_keys).toHaveLength(1);
  await expect(page.locator('#board')).toBeVisible();
  await expect(page.locator('.m-clip')).toHaveCount(0);   // badge is multi-clip only

  // Captions off is honored in the render payload.
  const renderPosts = [];
  await page.route(/\/assembler\/render\/$/, async (route, request) => {
    renderPosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-render"}' });
  });
  await page.locator('#capToggle').uncheck();
  await page.locator('#renderBtn').click();
  await expect.poll(() => renderPosts.length).toBe(1);
  expect(renderPosts[0].captions).toBe(false);
});
