const { test, expect } = require('@playwright/test');
const { runFullUpload, bootApp } = require('./helpers');

// Record every programmatic anchor click so the browser download path can be
// inspected (a detached anchor, or an object URL revoked in the same tick, is
// exactly what silently failed on mobile browsers).
async function trackAnchorClicks(page) {
  await page.addInitScript(() => {
    window.__anchorClicks = [];
    const realClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      window.__anchorClicks.push({
        download: this.download,
        href: this.href,
        connected: this.isConnected,
      });
      return realClick.call(this);
    };
  });
}

// Minimal native shell: Filesystem + Share, injected AFTER the (web) upload so
// the rest of the flow is untouched. _isNative()/_capPlugin read window.Capacitor
// at call time.
async function goNative(page) {
  await page.evaluate(() => {
    window.__fsWrites = [];
    window.__shares = [];
    window.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        Filesystem: {
          writeFile: async opts => { window.__fsWrites.push(opts); return { uri: 'file:///cache/' + opts.path }; },
          getUri: async opts => ({ uri: 'file:///cache/' + opts.path }),
        },
        Share: {
          share: async opts => { window.__shares.push(opts); return {}; },
        },
      },
    };
  });
}

test.beforeEach(async ({ page }) => {
  await trackAnchorClicks(page);
  await bootApp(page);
});

test('browser SRT download clicks an attached anchor and keeps the object URL alive', async ({ page }) => {
  await runFullUpload(page);
  await page.click('#downloadSrtBtn');

  const clicks = await page.evaluate(() => window.__anchorClicks);
  expect(clicks).toHaveLength(1);
  expect(clicks[0].download).toMatch(/\.srt$/);
  // A detached anchor is ignored by mobile browsers.
  expect(clicks[0].connected).toBe(true);

  // The object URL must still resolve after the click returns - revoking it
  // synchronously cancelled the download before it started.
  const srt = await page.evaluate(href => fetch(href).then(r => r.text()), clicks[0].href);
  expect(srt).toContain('00:00:00,500 --> 00:00:02,300');
  expect(srt).toContain('שלום עולם');
});

test('native SRT download writes the file and opens the share sheet', async ({ page }) => {
  await runFullUpload(page);
  await goNative(page);
  await page.click('#downloadSrtBtn');

  await expect.poll(() => page.evaluate(() => window.__shares.length)).toBe(1);
  const writes = await page.evaluate(() => window.__fsWrites);
  expect(writes).toHaveLength(1);
  expect(writes[0].directory).toBe('CACHE');
  expect(writes[0].encoding).toBe('utf8');
  expect(writes[0].path).toMatch(/\.srt$/);
  expect(writes[0].data).toContain('שלום עולם');

  const shares = await page.evaluate(() => window.__shares);
  expect(shares[0].files).toEqual(['file:///cache/' + writes[0].path]);

  // The browser blob path must NOT also run on native.
  expect(await page.evaluate(() => window.__anchorClicks.length)).toBe(0);
  await expect(page.locator('#downloadSrtBtn')).toBeEnabled();
});
