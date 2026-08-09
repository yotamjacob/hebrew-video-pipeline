// Admin "Marketing - Launch Month" checklist: fixed task list, one shared
// blob, stamp-based resets (daily = today's date, weekly = this week's
// Monday, one-time = "1"). A stale stamp simply renders unchecked.
const { test, expect } = require('@playwright/test');
const { API_BASE, mockAllApis, bootApp } = require('./helpers');

const ADMIN_ME = { username: 'boss', role: 'admin', videos_used: 0, video_limit: null };

function fmt(d) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
    + '-' + String(d.getDate()).padStart(2, '0');
}
const today = () => fmt(new Date());
const yesterday = () => fmt(new Date(Date.now() - 86400000));
const monday = () => { const d = new Date(); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return fmt(d); };

async function bootAdmin(page, state) {
  await bootApp(page, { me: ADMIN_ME });
  await mockAllApis(page);
  await page.route(/\/admin\/users/, r =>
    r.fulfill({ status: 200, contentType: 'application/json',
                body: JSON.stringify({ users: [ADMIN_ME] }) }));
  const posted = [];
  await page.route(/\/admin\/marketing\/?(\?.*)?$/, (route, request) => {
    if (request.method() === 'POST') {
      posted.push(JSON.parse(request.postData()));
      return route.fulfill({ status: 200, contentType: 'application/json',
                             body: request.postData() });
    }
    return route.fulfill({ status: 200, contentType: 'application/json',
                           body: JSON.stringify(state) });
  });
  await page.click('#tabAdmin');
  await expect(page.locator('#mktBody')).toBeVisible();
  return posted;
}

test('renders counters + three sections; stale daily/weekly stamps read as unchecked', async ({ page }) => {
  await bootAdmin(page, {
    counters: { installs: 12, activated: 5, paid: 1 },
    tasks: {
      ot1: '1',            // one-time: stays checked forever
      d1: yesterday(),     // daily, checked yesterday → auto-reset (unchecked)
      d2: today(),         // daily, checked today → still checked
      w1: monday(),        // weekly, this week → still checked
      w3: '2020-01-06',    // weekly, long ago → unchecked
    },
  });
  await expect(page.locator('#mktC_installs')).toHaveValue('12');
  await expect(page.locator('#mktC_activated')).toHaveValue('5');
  await expect(page.locator('#mktC_paid')).toHaveValue('1');
  await expect(page.locator('.mkt-sec-title')).toHaveCount(3);
  await expect(page.locator('.mkt-task')).toHaveCount(15);
  const checked = ids => page.evaluate(sel =>
    [...document.querySelectorAll('.mkt-task input')].filter(c => c.checked).length, ids);
  expect(await checked()).toBe(3);   // ot1 + d2 + w1
  // Owner tags render
  await expect(page.locator('.mkt-owner').first()).toHaveText('A');
  expect(await page.locator('.mkt-owner', { hasText: 'Both' }).count()).toBeGreaterThan(0);
});

test('checking a daily task saves a today-stamp; unchecking removes it', async ({ page }) => {
  const posted = await bootAdmin(page, { counters: {}, tasks: {} });
  // d1 is the 7th task (6 one-time before it)
  const d1 = page.locator('.mkt-task input').nth(6);
  await d1.check();
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(1);
  expect(posted[0].tasks.d1).toBe(today());
  await d1.uncheck();
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(2);
  expect(posted[1].tasks.d1).toBeUndefined();
});

test('one-time tasks store "1"; weekly tasks store this Monday', async ({ page }) => {
  const posted = await bootAdmin(page, { counters: {}, tasks: {} });
  await page.locator('.mkt-task input').first().check();       // ot1
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(1);
  expect(posted[0].tasks.ot1).toBe('1');
  await page.locator('.mkt-task input').nth(11).check();       // w1 (6 once + 5 daily)
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(2);
  expect(posted[1].tasks.w1).toBe(monday());
});

test('editing a counter persists the new number', async ({ page }) => {
  const posted = await bootAdmin(page, { counters: { installs: 3 }, tasks: {} });
  await page.fill('#mktC_installs', '17');
  await page.locator('#mktC_installs').blur();
  await expect.poll(() => posted.length, { timeout: 5000 }).toBe(1);
  expect(posted[0].counters.installs).toBe(17);
});
