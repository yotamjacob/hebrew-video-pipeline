// Re-editing a previous export: History → edit reopens the editor on the stored
// cut source with the captions/hook/B-roll/styling that produced it, and a
// re-burn targets that source (no re-upload, no credit).
const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, bootApp } = require('./helpers');

const SRC_KEY = 'u0__src1_cut.mp4';
const OUT_KEY = 'u0__out1_out.mp4';

const EDITABLE_JOB = {
  key: OUT_KEY, name: 'yoga_morning.mp4', ts: 1780000000,
  size: 52428800, duration: 95, editable: true,
};
const LEGACY_JOB = {
  key: 'u0__old_out.mp4', name: 'older_clip.mp4', ts: 1779000000,
  size: 31457280, duration: 62, editable: false,
};

const EDIT_STATE = {
  key: OUT_KEY,
  name: 'yoga_morning.mp4',
  src_key: SRC_KEY,
  captions: [
    { start: 0.5, end: 2.0, text: 'שלום לכולם' },
    { start: 2.2, end: 4.0, text: 'ברוכים הבאים' },
  ],
  hook: {
    text: 'הטעות שכולם עושים', lines: ['הטעות שכולם עושים'],
    font: 'Assistant', font_color: '#FFFF00', bg_color: '#112233',
    bg_opacity: 0.8, font_size_pct: 130, border_color: '#000000',
    border_size: 0, start_seconds: 0, duration_seconds: 3,
    vertical_position: 20,
  },
  broll: [
    { start: 3, end: 5, download_url: 'https://player.vimeo.com/x.mp4', source: 'pexels' },
  ],
  font: 'Rubik',
  font_size: 56,
  margin_v_pct: 0.15,
  caption_style: { font_color: '#FF0000', border_color: '#0000FF',
                   border_size: 4, bg_color: '#010203', bg_opacity: 0.5 },
};

function mockJobs(page, jobs) {
  return page.route(/\/jobs\/?(\?.*)?$/, (route, request) => {
    if (request.method() === 'GET') {
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: JSON.stringify({ jobs }) });
    }
    return route.fallback();
  });
}

// The edit endpoint. `state` may be a number to reply with that status instead.
function mockEditState(page, state = EDIT_STATE) {
  return page.route(/\/jobs\/.*\/edit/, r =>
    typeof state === 'number'
      ? r.fulfill({ status: state, contentType: 'application/json',
                    body: JSON.stringify({ error: 'source_gone' }) })
      : r.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify(state) }));
}

async function openEditor(page, { state = EDIT_STATE, jobs = [EDITABLE_JOB] } = {}) {
  await mockAllApis(page);
  await mockJobs(page, jobs);
  await mockEditState(page, state);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await page.click('#tabHistory');
  await page.locator('.history-card').first().waitFor();
  await page.locator('.history-card').first().locator('button[title]').first().click();
}

test.beforeEach(async ({ page }) => {
  await bootApp(page);
});

test('only jobs with saved editor state offer an edit button', async ({ page }) => {
  await mockAllApis(page);
  await mockJobs(page, [EDITABLE_JOB, LEGACY_JOB]);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  await page.click('#tabHistory');
  await expect(page.locator('.history-card')).toHaveCount(2);
  // The editable job gains one extra action button over the legacy one.
  const first  = page.locator('.history-card').nth(0).locator('.history-actions button');
  const second = page.locator('.history-card').nth(1).locator('.history-actions button');
  await expect(first).toHaveCount(4);    // edit + schedule + download + delete
  await expect(second).toHaveCount(3);   // schedule + download + delete
});

test('editing a past export restores captions, font, size and position', async ({ page }) => {
  await openEditor(page);

  await expect(page.locator('#pipelineView')).toBeVisible();
  await expect(page.locator('#captionEditorCard')).toBeVisible();
  await expect(page.locator('#historyEditHint')).toBeVisible();

  const rows = page.locator('#captionsList .caption-row');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).locator('input[type=text], textarea').first()).toHaveValue('שלום לכולם');
  await expect(rows.nth(1).locator('input[type=text], textarea').first()).toHaveValue('ברוכים הבאים');

  await expect(page.locator('#fontSelect')).toHaveValue('Rubik');
  await expect(page.locator('#captionFontSizeSlider')).toHaveValue('56');
  // Vertical position has no input - it drives the overlay's bottom offset.
  await expect.poll(() => page.evaluate(() =>
    document.getElementById('playerCap')?.style.bottom)).toBe('15%');
});

test('a stored size with no matching option snaps instead of blanking', async ({ page }) => {
  // The size + hook-scale controls are <select>s of discrete steps. An exact
  // assignment that matches no <option> leaves the control EMPTY and silently
  // out of sync with what burns, so both must snap to the nearest step.
  await openEditor(page, { state: { ...EDIT_STATE, font_size: 63,
                                    hook: { ...EDIT_STATE.hook, font_size_pct: 121 } } });
  await expect(page.locator('#captionFontSizeSlider')).toHaveValue('64');
  await expect(page.locator('#hookFontSize')).toHaveValue('115');

  let burnUrl = null;
  await page.route(/\/burn\/?(\?|$)/, (route, request) => {
    burnUrl = request.url();
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnUrl, { timeout: 10_000 }).not.toBeNull();
  // The burn uses the SNAPPED size, matching the control the user sees.
  expect(new URL(burnUrl).searchParams.get('font_size')).toBe('64');
});

test('the stored caption style comes back', async ({ page }) => {
  await openEditor(page);
  await expect(page.locator('#capFontColor')).toHaveValue('#ff0000');
  await expect(page.locator('#capBorderColor')).toHaveValue('#0000ff');
  await expect(page.locator('#capBorderSize')).toHaveValue('4');
  await expect(page.locator('#capBgColor')).toHaveValue('#010203');
  await expect(page.locator('#capBgOpacity')).toHaveValue('50');
});

test('the chosen hook is restored and pre-selected', async ({ page }) => {
  await openEditor(page);
  await expect(page.locator('#hookOption0')).toHaveClass(/selected/);
  await expect(page.locator('#hookText0')).toHaveValue('הטעות שכולם עושים');
  await expect(page.locator('#hookFont')).toHaveValue('Assistant');
  await expect(page.locator('#hookFontColor')).toHaveValue('#ffff00');
  await expect(page.locator('#hookBgOpacity')).toHaveValue('80');
  await expect(page.locator('#hookFontSize')).toHaveValue('130');
  await expect(page.locator('#hookPosition')).toHaveValue('20');
});

test('kept B-roll picks are shown as a count, not an empty tab', async ({ page }) => {
  await openEditor(page);
  await expect(page.locator('.broll-kept-note')).toContainText('1');
});

test('re-burn targets the stored cut source, not the burned output', async ({ page }) => {
  await openEditor(page);

  let burnUrl = null, burnBody = null;
  await page.route(/\/burn\/?(\?|$)/, (route, request) => {
    burnUrl  = request.url();
    burnBody = request.postDataJSON();
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });

  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnUrl, { timeout: 10_000 }).not.toBeNull();

  const qs = new URL(burnUrl).searchParams;
  expect(qs.get('video_key')).toBe(SRC_KEY);      // the CUT, never the _out.mp4
  expect(qs.get('font')).toBe('Rubik');
  expect(qs.get('font_size')).toBe('56');
  expect(parseFloat(qs.get('margin_v'))).toBeCloseTo(0.15, 3);
  expect(qs.get('filename')).toBe('yoga_morning.mp4');   // keeps its name
  expect(burnBody.captions.map(c => c.text)).toEqual(['שלום לכולם', 'ברוכים הבאים']);
  expect(burnBody.hook.text).toBe('הטעות שכולם עושים');
  expect(burnBody.broll).toHaveLength(1);
});

test('an edited caption reaches the re-burn', async ({ page }) => {
  await openEditor(page);
  const firstInput = page.locator('#captionsList .caption-row').first()
    .locator('input[type=text], textarea').first();
  await firstInput.fill('טקסט מתוקן');

  let burnBody = null;
  await page.route(/\/burn\/?(\?|$)/, (route, request) => {
    burnBody = request.postDataJSON();
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify({ call_id: 'mock-burn-call-id' }) });
  });
  await page.click('#runBtn');
  await page.click('#confirmOk');
  await expect.poll(() => burnBody, { timeout: 10_000 }).not.toBeNull();
  expect(burnBody.captions[0].text).toBe('טקסט מתוקן');
});

test('a pruned source explains itself instead of opening a broken editor', async ({ page }) => {
  await openEditor(page, { state: 410 });
  await expect(page.locator('#captionEditorCard')).toBeHidden();
  await expect(page.locator('.celebrate-toast')).toBeVisible();
  // The dead affordance is removed rather than left to fail again.
  await expect(page.locator('.history-card').first()
    .locator('.history-actions button')).toHaveCount(3);
});

test('/?edit=<key> deep link opens that job in the editor and strips the param', async ({ page }) => {
  // The assembler page links here ("edit in the pipeline"): the editor
  // opens on the History job without visiting the History tab, and the
  // query is consumed so a reload does not re-trigger it.
  await mockAllApis(page);
  await mockJobs(page, [EDITABLE_JOB]);
  await mockEditState(page);
  await page.route(/\/thumbnail\//, r => r.fulfill({ status: 404, body: '' }));
  const editCalls = [];
  await page.route(/\/jobs\/.*\/edit/, (route, request) => {
    editCalls.push(request.url());
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(EDIT_STATE) });
  });
  await page.goto('/?edit=' + encodeURIComponent(EDITABLE_JOB.key));
  await expect(page.locator('#captionEditorCard')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('#historyEditHint')).toBeVisible();
  expect(editCalls.some(u => u.includes(EDITABLE_JOB.key))).toBe(true);
  await expect.poll(() => page.evaluate(() => location.search)).toBe('');
});
