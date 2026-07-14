/**
 * Font-editor edge cases - captions AND hooks, on BOTH orientations.
 *
 * Every test in this file runs twice: once with the portrait fixture
 * (1080x1920) and once with the landscape fixture (1920x1080), because the
 * editor math (caption wrap width, hook box geometry, size formulas) depends
 * on the video's real dimensions and historically only portrait was exercised.
 *
 * Covered edges:
 *   - caption size extremes (smallest / largest dropdown option)
 *   - very long caption text (must wrap, must not overflow the player)
 *   - a single unbreakable word (cannot wrap - must not crash or loop)
 *   - empty caption text
 *   - mixed Hebrew/English/punctuation text (word preservation)
 *   - hook size extremes (50% vs 200% - linear scaling of the overlay)
 *   - hook vertical position extremes (0 / 100 - box clamped inside player)
 *   - long hook text (multi-line wrap + WYSIWYG lines in the burn payload)
 *   - empty hook text (overlay hidden)
 *   - hook size labels relabeled to real burn px for the loaded video
 *
 * app.js is a classic top-level script, so its function declarations
 * (updatePreviewCaption, updatePlayerHook, drawHookPreview, _hookPreviewPayload)
 * and top-level let bindings (_hookLines) are reachable from page.evaluate.
 */
const { test, expect } = require('@playwright/test');
const fs   = require('fs');
const path = require('path');
const { API_BASE, mockAllApis, selectFile, bootApp, DEFAULT_CAPTIONS } = require('./helpers');

const FIXTURES = {
  portrait:  { mp4: path.join(__dirname, 'fixtures/portrait_1080x1920.mp4'),
               jpg: path.join(__dirname, 'fixtures/thumb_portrait.jpg') },
  landscape: { mp4: path.join(__dirname, 'fixtures/landscape_1920x1080.mp4'),
               jpg: path.join(__dirname, 'fixtures/thumb_landscape.jpg') },
};

const LONG_CAPTION = 'זהו משפט ארוך מאוד שחייב להישבר לכמה שורות כי הוא פשוט לא נגמר ' +
                     'וממשיך עוד ועוד עם המון מילים קטנות וגדולות שנמשכות לאורך כל הרוחב של הסרטון';
const LONG_HOOK    = 'הסוד הכי גדול שאף אחד לא סיפר לכם על עריכת וידאו מהירה בעברית בלחיצת כפתור אחת';
const MIXED_TEXT   = 'זה CSS ממש מגניב, נכון? 100% עובד!';
const GIANT_WORD   = 'א'.repeat(80);

/** Serve a real MP4 for /download and a real JPEG for /thumbnail. */
async function mockMediaFiles(page, { mp4, jpg }) {
  const vbuf = fs.readFileSync(mp4);
  await page.route(`${API_BASE}/download/**`, route =>
    route.fulfill({ status: 200,
      headers: { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
                 'Content-Length': String(vbuf.length) },
      body: vbuf }));
  const ibuf = fs.readFileSync(jpg);
  await page.route(`${API_BASE}/thumbnail/**`, route =>
    route.fulfill({ status: 200,
      headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'max-age=300' },
      body: ibuf }));
}

/** Full flow into the editor with a REAL fixture video + custom captions. */
async function runEditor(page, orientation, { captions = DEFAULT_CAPTIONS } = {}) {
  await mockAllApis(page, { captions });
  await mockMediaFiles(page, FIXTURES[orientation]);   // registered after → wins (LIFO)
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])');
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  // Real metadata must land: the wrap gets its true aspect + videoWidth is real.
  await page.waitForFunction(() => {
    const vid = document.getElementById('cutVideo');
    const wrap = document.getElementById('playerWrap');
    return vid && vid.videoWidth > 0 && wrap && wrap.offsetWidth > 0;
  }, { timeout: 10_000 });
}

/** Select the caption size and repaint the preview. */
async function setCaptionSize(page, px) {
  await page.locator('#captionFontSizeSlider').evaluate((el, v) => {
    el.value = String(v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, px);
  await page.evaluate(() => updatePreviewCaption());
}

/** Generate hooks (mocked), pick option 0, and land in the hook tab. */
async function pickHook(page) {
  await page.click('#tabBtnHook');
  await page.click('#generateHookBtn');
  await page.waitForSelector('#hookOptions', { state: 'visible', timeout: 8_000 });
  await page.click('#hookOption0');
  await page.waitForSelector('#hookControls', { state: 'visible' });
}

/** Set a hook control value and refresh the on-player overlay deterministically. */
async function setHookControl(page, id, value) {
  await page.evaluate(({ id, value }) => {
    const el = document.getElementById(id);
    el.value = String(value);
    el.dispatchEvent(new Event('input',  { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    updatePlayerHook();
  }, { id, value });
}

for (const orientation of ['portrait', 'landscape']) {
  test.describe(`${orientation} video`, () => {
    test.beforeEach(async ({ page }) => { await bootApp(page); });

    // ── Captions ─────────────────────────────────────────────────────────────

    test('caption size extremes follow the preview formula (24px and 150px)', async ({ page }) => {
      await runEditor(page, orientation);
      for (const px of [24, 150]) {
        await setCaptionSize(page, px);
        const { cssPx, clientW, displayW } = await page.evaluate(() => {
          const vid = document.getElementById('cutVideo');
          const cap = document.getElementById('playerCap');
          return {
            cssPx:    parseFloat(getComputedStyle(cap).fontSize),
            clientW:  vid.clientWidth,
            displayW: vid.videoWidth,
          };
        });
        const expected = Math.max(7, px * clientW / displayW);
        expect(Math.abs(cssPx - expected)).toBeLessThanOrEqual(0.5);
      }
    });

    test('a very long caption wraps to multiple lines and never overflows the player', async ({ page }) => {
      await runEditor(page, orientation, {
        captions: [{ start: 0, end: 8, text: LONG_CAPTION }],
      });
      await setCaptionSize(page, 64);
      const { text, overflow } = await page.evaluate(() => {
        const cap  = document.getElementById('playerCap');
        const wrap = document.getElementById('playerWrap');
        return { text: cap.textContent, overflow: cap.scrollWidth - wrap.clientWidth };
      });
      expect(text.split('\n').length).toBeGreaterThan(1);   // it wrapped
      expect(overflow).toBeLessThanOrEqual(2);              // and fits (±2px subpixel)
    });

    test('a single unbreakable 80-char word does not crash the wrap', async ({ page }) => {
      const errors = [];
      page.on('pageerror', e => errors.push(String(e)));
      await runEditor(page, orientation, {
        captions: [{ start: 0, end: 8, text: GIANT_WORD }],
      });
      await setCaptionSize(page, 48);
      const text = await page.locator('#playerCap').textContent();
      expect(text).toBe(GIANT_WORD);          // one un-splittable line, verbatim
      expect(errors).toEqual([]);
    });

    test('an empty caption text keeps the editor alive', async ({ page }) => {
      const errors = [];
      page.on('pageerror', e => errors.push(String(e)));
      await runEditor(page, orientation, {
        captions: [{ start: 0, end: 8, text: '' }],
      });
      await setCaptionSize(page, 100);        // interacting must not throw
      expect(errors).toEqual([]);
    });

    test('mixed Hebrew/English/punctuation caption keeps every word', async ({ page }) => {
      await runEditor(page, orientation, {
        captions: [{ start: 0, end: 8, text: MIXED_TEXT }],
      });
      await setCaptionSize(page, 48);
      const text = await page.locator('#playerCap').textContent();
      const words = s => s.split(/\s+/).filter(Boolean).sort();
      expect(words(text)).toEqual(words(MIXED_TEXT));
    });

    // ── Hooks ────────────────────────────────────────────────────────────────

    test('hook overlay font follows the size formula at both extremes (50% and 200%)', async ({ page }) => {
      await runEditor(page, orientation);
      await pickHook(page);
      for (const pct of [50, 200]) {
        await setHookControl(page, 'hookFontSize', pct);
        const { cssFs, wrapW, wrapH } = await page.evaluate(() => {
          updatePlayerHook();   // recompute against the CURRENT wrap box (late
                                // metadata can resize the wrap under load)
          const wrap = document.getElementById('playerWrap');
          return {
            cssFs: parseFloat(getComputedStyle(document.getElementById('playerHook')).fontSize),
            wrapW: wrap.clientWidth, wrapH: wrap.clientHeight,
          };
        });
        // fs = max(8, min(W,H) * 0.075 * pct) - the 8px floor is real behavior
        // on small player boxes, so assert the exact formula, not pure linearity.
        const expected = Math.max(8, Math.min(wrapW, wrapH) * 0.075 * (pct / 100));
        expect(Math.abs(cssFs - expected)).toBeLessThanOrEqual(0.5);
      }
    });

    test('hook position extremes keep the box inside the player', async ({ page }) => {
      await runEditor(page, orientation);
      await pickHook(page);
      for (const pos of [0, 100]) {
        await setHookControl(page, 'hookPosition', pos);
        const { top, bottom, wrapH, visible } = await page.evaluate(() => {
          updatePlayerHook();   // recompute against the CURRENT wrap box (late
                                // metadata can resize the wrap under load)
          const ph   = document.getElementById('playerHook');
          const wrap = document.getElementById('playerWrap');
          return { top: ph.offsetTop, bottom: ph.offsetTop + ph.offsetHeight,
                   wrapH: wrap.clientHeight, visible: ph.style.display !== 'none' };
        });
        expect(visible).toBe(true);
        expect(top).toBeGreaterThanOrEqual(-1);
        expect(bottom).toBeLessThanOrEqual(wrapH + 1);
      }
    });

    test('a long hook at 200% wraps to multiple lines and the burn payload carries them', async ({ page }) => {
      await runEditor(page, orientation);
      await pickHook(page);
      await page.locator('#hookText0').evaluate((el, v) => {
        el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }, LONG_HOOK);
      await setHookControl(page, 'hookFontSize', 200);
      const payload = await page.evaluate(() => _hookPreviewPayload());
      expect(payload).not.toBeNull();
      expect(payload.lines.length).toBeGreaterThan(1);
      // The lines partition the text - same words, same order.
      const words = s => s.split(/\s+/).filter(Boolean).join(' ');
      expect(words(payload.lines.join(' '))).toBe(words(LONG_HOOK));
      // And the on-player overlay renders the same number of lines.
      const domLines = await page.locator('#playerHook .hook-line').count();
      expect(domLines).toBe(payload.lines.length);
    });

    test('clearing the hook text hides the overlay', async ({ page }) => {
      await runEditor(page, orientation);
      await pickHook(page);
      await page.locator('#hookText0').evaluate(el => {
        el.value = '';
        el.dispatchEvent(new Event('input', { bubbles: true }));
      });
      await page.evaluate(() => updatePlayerHook());
      await expect(page.locator('#playerHook')).toBeHidden();
    });

    test('hook size options are relabeled to the real burn px for this video', async ({ page }) => {
      await runEditor(page, orientation);
      await pickHook(page);
      const { labels, vw, vh } = await page.evaluate(() => {
        const vid = document.getElementById('cutVideo');
        const sel = document.getElementById('hookFontSize');
        const labels = {};
        Array.from(sel.options).forEach(o => { labels[o.value] = o.textContent; });
        return { labels, vw: vid.videoWidth, vh: vid.videoHeight };
      });
      const base = Math.min(vw, vh) * 0.075;
      for (const pct of [50, 100, 200]) {
        expect(labels[String(pct)]).toBe(Math.round(base * pct / 100) + ' px');
      }
    });
  });
}
