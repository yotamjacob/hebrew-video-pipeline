const { test, expect } = require('@playwright/test');
const { API_BASE } = require('./helpers');

// Caption style picker (2026-09-24): no styling UI on the assembler page -
// one "סגנון כתוביות" select per mode lists the user's saved profiles
// (/profiles), the built-in presets and "ברירת מחדל"; the pick rides the
// render payload (profile -> font / size / margin / caption_style minus the
// editor-only effects + hook_style), persists with the brand settings, and
// a server style_warnings list shows as a soft note.
const THUMB = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APvSiigD/9k=';
const V = { hook: 8, retention: 7, emotion: 6, clarity: 8, shareability: 7, reasoning: 'x', tip: '' };
const CANDS = {
  mode: 'clips', duration: 3600.0, summary: 's',
  candidates: [{ clip: 0, start: 10, end: 40, duration: 30, title: 'קליפ', hook: 'הוק', quote: 'q', score: 70, thumb: THUMB, virality: V }],
  issues: [], clips: [{ name: 'podcast.mp4', duration: 3600 }], sources: ['u1234__srckey_src.mp4'],
};
const MOMENTS = {
  duration: 120, title: 't', story: 's',
  moments: [0, 1, 2].map((i) => ({ clip: 0, start: i * 20, end: i * 20 + 10, role: 'story', quote: 'q', reason: 'r', thumb: THUMB })),
  clips: [{ name: 'a.mp4', duration: 120 }],
};
const PROFILES = [
  { name: 'יוגאלינה', data: {
    font: 'Rubik', font_size: 56, margin_v: 0.12,
    caption_style: { font_color: '#FFE45C', border_color: '#000000', border_size: 3, bg_color: '#000000', bg_opacity: 0,
                     mode: 'karaoke', highlight_color: '#C26D4B', progress_bar: true, progress_color: '#C26D4B',
                     auto_zoom: true, zoom_strength: 'medium' },
    hook: { font: 'SecularOne', font_color: '#FFFFFF', bg_color: '#C26D4B', bg_opacity: '70', border_color: '#000000', border_size: '2', size: '120' },
  } },
  { name: 'נקי ופשוט', data: { font: 'Heebo', font_size: 48, margin_v: 0.08, caption_style: { mode: 'classic' } } },
];

async function boot(page, { renderPosts = [], profiles = PROFILES, saved = null, warnings = [], mode = 'clips' } = {}) {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/\/profiles\/?$/, r => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ profiles, default: null }) }));
  await page.addInitScript((saved) => {
    localStorage.setItem('hebpipe_token', 'test-token');
    if (saved) localStorage.setItem('hebpipe_asm_brand', JSON.stringify(saved));
  }, saved);
  await page.route(new RegExp(`${API_BASE}/upload_chunk`.replace(/[/.]/g, '\\$&')), r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  await page.route(/\/upload_r2\/init\/$/, r =>
    r.fulfill({ status: 503, contentType: 'application/json', body: '{"code":"r2_unavailable"}' }));
  await page.route(/\/assembler\/analyze\/$/, r => r.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-a"}' }));
  await page.route(/\/assembler\/analyze-poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(mode === 'clips' ? CANDS : MOMENTS) }));
  await page.route(/\/assembler\/render\/$/, async (route, request) => {
    renderPosts.push(request.postDataJSON());
    await route.fulfill({ status: 202, contentType: 'application/json', body: '{"call_id":"fc-r"}' });
  });
  await page.route(/\/assembler\/render-poll\//, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ video_key: 'u1__k_c1_out.mp4', duration: 30, style_warnings: warnings }) }));
  await page.route(/\/auth\/media-token/, r => r.fulfill({ status: 200, contentType: 'application/json', body: '{"token":"m"}' }));
  await page.route(/\/media\//, r => r.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(64) }));
  await page.clock.install();
  await page.goto('/assembler.html');
}

// The style is chosen BEFORE "עיבוד" (setup card, 2026-09-24): `before`
// runs once the file is picked; processing then renders every suggested
// clip with it automatically.
async function toClips(page, before = null) {
  await page.locator('#modeClips').click();
  await page.setInputFiles('#file', { name: 'podcast.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await expect(page.locator('#setupCard')).toBeVisible();
  if (before) await before();
  await page.locator('#processBtn').click();
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand')).toHaveCount(1);
}

const brandSaved = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('hebpipe_asm_brand') || '{}'));

test('options: saved profiles, then presets, then default (default selected)', async ({ page }) => {
  await boot(page);
  const sel = page.locator('#clipCapStyle');
  await expect(sel.locator('optgroup[label="הסגנונות שלי"] option')).toHaveText(['יוגאלינה', 'נקי ופשוט']);
  await expect(sel.locator('optgroup[label="סגנונות מוכנים"] option')).toHaveText(['נקי', 'קריוקי', 'מילה-מילה', 'כתבה', 'צהוב ויראלי', 'בהיר']);
  const all = await sel.locator('option').evaluateAll((os) => os.map((o) => o.value));
  expect(all[all.length - 1]).toBe('default');
  await expect(sel).toHaveValue('default');
  await expect(page.locator('#capStyle')).toHaveValue('default');     // story mode's select too
});

test('a saved profile rides the render: style minus editor effects + hook design', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts });
  await toClips(page, async () => {
    await page.locator('#clipCapStyle').selectOption('profile:יוגאלינה');
    // Both modes' selects stay in step, and the pick is persisted with the brand settings.
    await expect(page.locator('#capStyle')).toHaveValue('profile:יוגאלינה');
    expect((await brandSaved(page)).capStyle).toBe('profile:יוגאלינה');
  });
  await expect.poll(() => renderPosts.length).toBe(1);   // the automatic render carries it
  const p = renderPosts[0];
  expect(p.font).toBe('Rubik');
  expect(p.font_size).toBe(56);
  expect(p.margin_v).toBe(0.12);
  expect(p.caption_style).toEqual({ font_color: '#FFE45C', border_color: '#000000', border_size: 3, bg_color: '#000000',
                                    bg_opacity: 0, mode: 'karaoke', highlight_color: '#C26D4B' });
  expect(p.hook_style).toEqual({ font: 'SecularOne', font_color: '#FFFFFF', bg_color: '#C26D4B', border_color: '#000000',
                                 bg_opacity: 0.7, border_size: 2, font_size_pct: 120 });
});

test('a preset sends only its font + style; default sends nothing', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts });
  await toClips(page, () => page.locator('#clipCapStyle').selectOption('preset:karaoke'));
  await expect.poll(() => renderPosts.length).toBe(1);
  expect(renderPosts[0].font).toBe('Heebo');
  expect(renderPosts[0].caption_style.mode).toBe('karaoke');
  expect(renderPosts[0].font_size).toBeUndefined();
  expect(renderPosts[0].hook_style).toBeUndefined();
  await page.clock.fastForward(3100);
  await page.locator('#clipCapStyle').selectOption('default');
  await expect(page.locator('#settingsNote')).toBeVisible();
  await page.locator('#renderClipsBtn').click();   // "רענון הקליפים" applies the change
  await expect.poll(() => renderPosts.length).toBe(2);
  for (const k of ['font', 'font_size', 'margin_v', 'caption_style', 'hook_style']) expect(renderPosts[1][k]).toBeUndefined();
});

test('captions off disables the picker and sends no style', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts, saved: { capStyle: 'preset:viral' } });
  await toClips(page, async () => {
    await expect(page.locator('#clipCapStyle')).toHaveValue('preset:viral');   // restored from the brand settings
    await page.locator('#clipCapToggle').uncheck();
    await expect(page.locator('#clipCapStyle')).toBeDisabled();
  });
  await expect.poll(() => renderPosts.length).toBe(1);
  expect(renderPosts[0].captions).toBe(false);
  expect(renderPosts[0].caption_style).toBeUndefined();
});

test('a saved profile that was deleted falls back to the default quietly', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts, saved: { capStyle: 'profile:נמחק' } });
  await expect(page.locator('#clipCapStyle')).toHaveValue('default');
  await expect.poll(async () => (await brandSaved(page)).capStyle).toBe('default');
  await toClips(page);
  await expect.poll(() => renderPosts.length).toBe(1);
  expect(renderPosts[0].caption_style).toBeUndefined();
});

test('server style warnings show as a soft note on the tile', async ({ page }) => {
  await boot(page, { warnings: [{ field: 'caption_style.font_color', value: 'red', fallback: '#FFFFFF' }] });
  await toClips(page, () => page.locator('#clipCapStyle').selectOption('profile:נקי ופשוט'));
  await page.clock.fastForward(3100);
  await expect(page.locator('.cand .c-out .style-note')).toContainText('caption_style.font_color');
});

test('story mode renders with the same pick', async ({ page }) => {
  const renderPosts = [];
  await boot(page, { renderPosts, mode: 'story', saved: { capStyle: 'preset:news' } });
  await page.setInputFiles('#file', { name: 'a.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(1024 * 1024) });
  await page.locator('#processBtn').click();   // analysis waits for the explicit click (2026-09-24)
  await page.clock.fastForward(3100);
  await expect(page.locator('.moment')).toHaveCount(3);
  await expect(page.locator('#capStyle')).toHaveValue('preset:news');
  await page.locator('#renderBtn').click();
  await expect.poll(() => renderPosts.length).toBe(1);
  expect(renderPosts[0].font).toBe('Frank Ruhl Libre');
  expect(renderPosts[0].caption_style.bg_opacity).toBe(0.6);
});
