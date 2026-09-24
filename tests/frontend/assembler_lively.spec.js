const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Lively-conversation badge (2026-09-24): a candidate the analysis marked
// as a real back-and-forth (measured speaker turns AND the model agree)
// shows "שיחה ערה" + "start - end · who talks about what"; others show none.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';
const V = { hook: 8, retention: 7, emotion: 6, clarity: 8, shareability: 7, reasoning: 'x', tip: '' };
const CANDS = {
  mode: 'clips', duration: 3600.0, summary: 's',
  candidates: [
    { clip: 0, start: 90, end: 150, duration: 60, title: 'מקורות היוגה', hook: 'הוק', quote: 'q', score: 72, thumb: THUMB, virality: V,
      lively: true, lively_line: 'אלינה ודריה בדיון ער הלוך ושוב על מקורות היוגה', turns: { count: 4, per_min: 4, balance: 0.4, measured: true } },
    { clip: 0, start: 600, end: 640, duration: 40, title: 'מונולוג', hook: 'הוק', quote: 'q', score: 65, thumb: THUMB, virality: V,
      lively: false, lively_line: '', turns: { count: 0, per_min: 0, balance: 0, measured: true } },
  ],
  issues: [], clips: [{ name: 'podcast.mp4', duration: 3600 }], sources: ['u1234__srckey_src.mp4'],
};

async function boot(page) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/\/profiles\/?$/, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"profiles":[],"default":null}' }));
  await page.addInitScript(() => { localStorage.setItem('hebpipe_token', 'test-token'); });
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  await page.route(/\/upload_r2\/init\/$/, r => r.fulfill({ status: 503, contentType: 'application/json', body: '{"code":"r2_unavailable"}' }));
  await page.route(/\/assembler\/analyze\/$/, r => r.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-a"}' }));
  await page.route(/\/assembler\/analyze-poll\//, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANDS) }));
  await page.route(/\/assembler\/render\/$/, r => r.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-r"}' }));
  await page.route(/\/assembler\/render-poll\//, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"status":"pending"}' }));
  await page.route(/\/auth\/media-token/, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m"}' }));
  await page.route(/\/media\//, r => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();
  await page.goto('/assembler.html');
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await page.locator('#processBtn').click();
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand')).toHaveCount(2);
}

test('a lively candidate carries the badge with its range and topic; a monologue does not', async ({ page }) => {
  await boot(page);
  const lively = page.locator('.cand').nth(0).locator('.c-lively');
  await expect(lively).toBeVisible();
  await expect(lively.locator('.chip')).toHaveText('שיחה ערה');
  await expect(lively).toContainText('1:30 - 2:30 · אלינה ודריה בדיון ער הלוך ושוב על מקורות היוגה');
  await expect(page.locator('.cand').nth(1).locator('.c-lively')).toHaveCount(0);
});

test('the badge keeps the analysed range when the clip is trimmed', async ({ page }) => {
  await boot(page);
  const card = page.locator('.cand').nth(0);
  await card.locator('.c-trim .step').first().locator('button').last().click();   // +1 s before
  await expect(card.locator('.c-time')).toContainText('1:29 - 2:30');
  await expect(card.locator('.c-lively .when')).toHaveText('1:30 - 2:30');
});

test('the badge range reads in the same visual order as the time line (RTL)', async ({ page }) => {
  await boot(page);
  const pos = await page.evaluate(() => {
    const at = (el, needle) => {
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let n;
      while ((n = w.nextNode())) {
        const i = n.data.indexOf(needle);
        if (i >= 0) { const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + needle.length); return r.getBoundingClientRect().left; }
      }
      return null;
    };
    const t = document.querySelector('.cand .c-time'), b = document.querySelector('.cand .c-lively .when');
    return { time: at(t, '1:30') > at(t, '2:30'), badge: at(b, '1:30') > at(b, '2:30') };
  });
  expect(pos.badge).toBe(pos.time);
});
