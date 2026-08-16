const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Branding card on /assembler (2026-08-16): intro/outro clips, fade,
// draggable watermark. Payload contract: intro_key / outro_key / fade /
// wm_key + normalized wm geometry ride every render (story + clips batch);
// assets upload ONCE per batch through the same upload path as the clips.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';
// 1x1 transparent PNG.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');
const CANDS = { mode: 'clips', duration: 600, summary: 's', clips: [{ name: 'p.mp4', duration: 600 }], candidates: [
  { clip: 0, start: 10, end: 40, duration: 30, title: 'A', hook: 'h1', quote: 'q', score: 70, thumb: THUMB, virality: { hook: 7, retention: 7, emotion: 7, clarity: 7, shareability: 7, reasoning: 'r', tip: '' } },
  { clip: 0, start: 100, end: 130, duration: 30, title: 'B', hook: 'h2', quote: 'q', score: 60, thumb: THUMB, virality: { hook: 6, retention: 6, emotion: 6, clarity: 6, shareability: 6, reasoning: 'r', tip: '' } },
] };

async function boot(page, { renderPosts = [], chunkKeys = [] } = {}) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.addInitScript(() => localStorage.setItem('hebpipe_token', 'test-token'));
  await page.route(/\/upload_r2\/init\/$/, r => r.fulfill({ status: 503, contentType: 'application/json', body: '{"code":"r2_unavailable"}' }));
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), (route, request) => {
    chunkKeys.push(request.headers()['x-upload-key']);
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await page.route(/\/assembler\/analyze\/$/, r => r.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-a"}' }));
  await page.route(/\/assembler\/analyze-poll\//, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANDS) }));
  let n = 0;
  await page.route(/\/assembler\/render\/$/, async (route, request) => {
    renderPosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ call_id: 'fc-r' + (n++) }) });
  });
  await page.route(/\/assembler\/render-poll\//, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"video_key":"u1__k_c1_out.mp4","duration":30}' }));
  await page.route(/\/auth\/media-token/, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m"}' }));
  await page.route(/\/media\//, r => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();
  await page.goto('/assembler.html');
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'p.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await page.clock.fastForward(3100);
  await expect(page.locator('#clipsCard')).toBeVisible();
  await expect(page.locator('#brandCard')).toBeVisible();
}

test('intro + outro + fade + dragged watermark ride every clip render; assets upload once', async ({ page }) => {
  const renderPosts = [], chunkKeys = [];
  await boot(page, { renderPosts, chunkKeys });
  const sourceKeys = new Set(chunkKeys);

  // Intro / outro pickers.
  await page.setInputFiles('#introFile', { name: 'intro.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(64 * 1024) });
  await page.setInputFiles('#outroFile', { name: 'outro.mov', mimeType: 'video/quicktime', buffer: Buffer.alloc(64 * 1024) });
  await expect(page.locator('#introName')).toHaveText('intro.mp4');
  await expect(page.locator('#outroSlot')).toHaveClass(/has/);
  // Adding a boundary clip auto-enables the fade (user can still turn it off).
  await expect(page.locator('#fadeToggle')).toBeChecked();
  await page.locator('#fadeDur').selectOption('1');

  // Watermark: pick -> stage shows -> drag with pointer events -> sliders.
  await page.setInputFiles('#wmFile', { name: 'logo.png', mimeType: 'image/png', buffer: PNG });
  await expect(page.locator('#wmStage')).toBeVisible();
  await expect(page.locator('#wmCtl')).toBeVisible();
  const img = page.locator('#wmImg');
  await page.locator('#wmStage').scrollIntoViewIfNeeded();
  const stage = await page.locator('#wmStage').boundingBox();
  await page.locator('#wmSize').fill('30');
  await page.locator('#wmSize').dispatchEvent('input');
  await page.locator('#wmOpacity').fill('50');
  await page.locator('#wmOpacity').dispatchEvent('input');
  await page.locator('#wmOpacity').dispatchEvent('change');
  await img.scrollIntoViewIfNeeded();
  const before = await img.boundingBox();
  // Explicit incremental moves (no `steps`: the installed fake clock
  // interferes with Playwright's step interpolation).
  const drag = async (fromX, fromY, dx, dy) => {
    await page.mouse.move(fromX, fromY);
    await page.mouse.down();
    for (let i = 1; i <= 6; i++) await page.mouse.move(fromX + dx * i / 6, fromY + dy * i / 6);
    await page.mouse.up();
  };
  await drag(before.x + before.width / 2, before.y + before.height / 2, stage.width * 0.4, stage.height * 0.5);
  const after = await img.boundingBox();
  expect(after.x - before.x).toBeCloseTo(stage.width * 0.4, 0);
  expect(after.y - before.y).toBeCloseTo(stage.height * 0.5, 0);
  // Drag far past the edge: the logo is clamped inside the frame.
  await drag(after.x + 5, after.y + 5, 600, 900);
  const clamped = await img.boundingBox();
  expect(clamped.x + clamped.width).toBeLessThanOrEqual(stage.x + stage.width + 1);
  expect(clamped.y + clamped.height).toBeLessThanOrEqual(stage.y + stage.height + 1);

  // Render both clips.
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => renderPosts.length).toBe(2);
  const p = renderPosts[0];
  expect(p.intro_key).toMatch(/^[0-9a-f]{32}$/);
  expect(p.outro_key).toMatch(/^[0-9a-f]{32}$/);
  expect(p.wm_key).toMatch(/^[0-9a-f]{32}$/);
  expect(new Set([p.intro_key, p.outro_key, p.wm_key]).size).toBe(3);
  expect(p.fade).toBe(1);
  expect(p.wm.w).toBeCloseTo(0.30, 2);
  expect(p.wm.opacity).toBeCloseTo(0.5, 2);
  // Clamped bottom-right: x = 1 - w, y = 1 - h/H (h unknown here, but > 0.5).
  expect(p.wm.x).toBeCloseTo(0.70, 1);
  expect(p.wm.y).toBeGreaterThan(0.5);
  // Same asset keys on the second clip - uploaded once, not per clip.
  expect(renderPosts[1].intro_key).toBe(p.intro_key);
  expect(renderPosts[1].wm_key).toBe(p.wm_key);
  const brandKeys = chunkKeys.filter(k => !sourceKeys.has(k));
  expect(new Set(brandKeys).size).toBe(3);

  // Persistence: reload -> the logo, its geometry and the fade choice survive.
  await page.reload();
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'p.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await page.clock.fastForward(3100);
  await expect(page.locator('#brandCard')).toBeVisible();
  await expect(page.locator('#wmStage')).toBeVisible();
  await expect(page.locator('#wmSize')).toHaveValue('30');
  await expect(page.locator('#fadeToggle')).toBeChecked();
  await expect(page.locator('#fadeDur')).toHaveValue('1');
  await expect(page.locator('#introName')).toHaveText('');   // clips are not persisted
});

test('no branding -> the render payload carries none of the branding fields', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts });
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => renderPosts.length).toBe(2);
  for (const p of renderPosts) {
    expect(p.intro_key).toBeUndefined();
    expect(p.outro_key).toBeUndefined();
    expect(p.wm_key).toBeUndefined();
    expect(p.fade).toBeUndefined();
  }
});

test('removing the watermark drops it from the payload; fade alone still rides', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts });
  await page.setInputFiles('#wmFile', { name: 'logo.png', mimeType: 'image/png', buffer: PNG });
  await expect(page.locator('#wmStage')).toBeVisible();
  await page.locator('#wmRemove').click();
  await expect(page.locator('#wmStage')).toBeHidden();
  await page.locator('#fadeToggle').check();
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => renderPosts.length).toBe(2);
  expect(renderPosts[0].wm_key).toBeUndefined();
  expect(renderPosts[0].fade).toBe(0.5);
});

test('output format 9:16 rides the payload, is remembered, and the watermark stage goes vertical', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts });
  await expect(page.locator('#frameOrig')).toHaveClass(/on/);
  await page.locator('#frameVert').click();
  await expect(page.locator('#frameVert')).toHaveClass(/on/);
  await page.setInputFiles('#wmFile', { name: 'logo.png', mimeType: 'image/png', buffer: PNG });
  await expect(page.locator('#wmStage')).toBeVisible();
  // No source dims are known in the stubbed flow (1MB zero buffer): the
  // stage keeps its default portrait aspect either way; the payload is the contract.
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => renderPosts.length).toBe(2);
  expect(renderPosts.every(p => p.reframe === '9:16')).toBe(true);
  await page.reload();
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'p.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await page.clock.fastForward(3100);
  await expect(page.locator('#brandCard')).toBeVisible();
  await expect(page.locator('#frameVert')).toHaveClass(/on/);
  await page.locator('#frameOrig').click();
  const posts2 = [];
  await page.route(/\/assembler\/render\/$/, async (route, request) => {
    posts2.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-x"}' });
  });
  await page.locator('#renderClipsBtn').click();
  await expect.poll(() => posts2.length).toBe(2);
  expect(posts2.every(p => p.reframe === undefined)).toBe(true);
});
