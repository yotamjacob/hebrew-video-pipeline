const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Standalone /assembler page (hidden beta). It loads NO app.js - its only
// network calls are the assembler routes + upload_chunk + media-token, all
// stubbed here. A 1x1 jpeg keeps the thumbnail data-URI path honest.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';

const MOMENTS = {
  duration: 240.0,
  title: 'ביקור ביקב משפחתי',
  moments: [
    { start: 5.0, end: 15.0, role: 'hook', quote: 'זה היקב שסבא שלי חלם עליו', reason: 'פתיח רגשי שתופס', thumb: THUMB },
    { start: 60.0, end: 75.0, role: 'story', quote: 'התחלנו משלוש חביות במרתף', reason: 'הסיפור המרכזי', thumb: THUMB },
    { start: 180.0, end: 192.0, role: 'gold', quote: 'היין הזה זכה במדליה', reason: 'רגע השיא', thumb: THUMB },
  ],
  segments: [],
};

async function bootAssembler(page) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 'test-token'));
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  await page.route(/\/assembler\/analyze\/$/, r =>
    r.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-analyze"}' }));
  await page.route(/\/assembler\/analyze-poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(MOMENTS) }));
  await page.route(/\/assembler\/render\/$/, r =>
    r.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-render"}' }));
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

test('full flow: upload -> storyboard -> curate -> render -> result', async ({ page }) => {
  await bootAssembler(page);
  await expect(page.locator('#uploadCard')).toBeVisible();
  await expect(page.locator('#authGate')).toBeHidden();

  await page.setInputFiles('#file', {
    name: 'winery.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(3 * 1024 * 1024),
  });
  await page.clock.fastForward(3100);   // one poll tick
  await expect(page.locator('#board')).toBeVisible();
  await expect(page.locator('#boardTitle')).toHaveText('ביקור ביקב משפחתי');
  const rows = page.locator('.moment');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0).locator('.m-role')).toHaveText('פתיח');
  await expect(rows.nth(0).locator('img')).toHaveAttribute('src', /^data:image\/jpeg;base64,/);
  // 10 + 15 + 12 kept seconds
  await expect(page.locator('#total')).toContainText('0:37');

  // Drop the middle moment - total updates, row dims.
  await rows.nth(1).locator('.drop-btn').click();
  await expect(page.locator('.moment').nth(1)).toHaveClass(/dropped/);
  await expect(page.locator('#total')).toContainText('0:22');
  // Bring it back and move it to the top.
  await page.locator('.moment').nth(1).locator('.drop-btn').click();
  await page.locator('.moment').nth(1).locator('button[title="הזזה למעלה"]').click();
  await expect(page.locator('.moment').nth(0).locator('.m-quote')).toContainText('שלוש חביות');

  // Render posts the kept segments in STORYBOARD order.
  const posts = [];
  await page.route(/\/assembler\/render\/$/, async (route, request) => {
    posts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-render"}' });
  });
  await page.locator('#renderBtn').click();
  await page.clock.fastForward(3100);
  await expect(page.locator('#result')).toBeVisible();
  await expect(page.locator('#outVideo')).toHaveAttribute('src', /\/media\/u1234__abc_out\.mp4\?token=m\.test/);
  expect(posts[0].segments).toEqual([[60, 75], [5, 15], [180, 192]]);
});
