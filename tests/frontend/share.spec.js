/**
 * Share button visibility + behavior.
 *
 * Native (Capacitor): the button always shows once a result exists.
 * Web: shows only where the Web Share API can share FILES (navigator.canShare)
 *      and hands navigator.share a File built from the finished video.
 * Web without the capability: stays hidden.
 */
const { test, expect } = require('@playwright/test');
const { mockAllApis, selectFile, bootApp } = require('./helpers');

async function burnToBanner(page) {
  await mockAllApis(page);
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  await page.click('#runBtn');   // burn
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await page.waitForSelector('#burnSuccessBanner', { state: 'visible', timeout: 15_000 });
}

test('native: share button appears after burn', async ({ page }) => {
  await page.addInitScript(() => {
    window.__nativeDownloads = [];
    window.__openDownloads = 0;
    window.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        NativeDownloader: {
          download: async opts => {
            window.__nativeDownloads.push(opts);
            return { id: 42, filename: opts.filename, location: `Downloads/${opts.filename}` };
          },
          openDownloads: async () => { window.__openDownloads += 1; },
        },
        Filesystem: {
          downloadFile: async opts => ({ path: `/cache/${opts.path}` }),
          writeFile: async () => {},
          getUri: async ({ path }) => ({ uri: `content://cache/${path}` }),
        },
      },
    };
  });
  await bootApp(page);
  await burnToBanner(page);
  await expect(page.locator('#burnShareBtn')).toBeVisible({ timeout: 5_000 });
  await expect.poll(() => page.evaluate(() => window.__nativeDownloads.length)).toBe(1);
  const download = await page.evaluate(() => window.__nativeDownloads[0]);
  expect(download.url).toContain('/download/mock-output-key.mp4');
  expect(download.url).toContain('token=');
  expect(download.filename).toBe('test_edited.mp4');
  expect(download.mimeType).toBe('video/mp4');
  expect(download.description).toBeTruthy();
  await expect(page.locator('#_dlFrame')).toHaveCount(0);

  const openDownloads = page.locator('#burnOpenDownloadsBtn');
  await expect(openDownloads).toBeVisible();
  const followsDownloadButton = await page.evaluate(() => {
    const downloadButton = document.getElementById('burnDownloadBtn');
    const openButton = document.getElementById('burnOpenDownloadsBtn');
    return Boolean(downloadButton.compareDocumentPosition(openButton)
      & Node.DOCUMENT_POSITION_FOLLOWING);
  });
  expect(followsDownloadButton).toBe(true);
  await openDownloads.click();
  await expect.poll(() => page.evaluate(() => window.__openDownloads)).toBe(1);
});

test('native: failed download shows a persistent red error toast', async ({ page }) => {
  await page.addInitScript(() => {
    window.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        NativeDownloader: {
          download: async () => {
            throw new Error('Parent directory does not exist');
          },
          openDownloads: async () => {},
        },
      },
    };
  });
  await bootApp(page);
  await burnToBanner(page);

  const toast = page.locator('#celebrateToast');
  await expect(toast).toHaveClass(/error/);
  await expect(toast).toHaveClass(/show/);
  await expect(toast).toHaveAttribute('role', 'alert');
  await expect(toast.locator('.celebrate-toast-error-icon')).toBeVisible();
  await expect(toast).toHaveCSS('color', 'rgb(153, 27, 27)');

  await page.waitForTimeout(3000);
  await expect(toast).toHaveClass(/show/);
});

test('web with Web Share files support: button appears and shares a File', async ({ page }) => {
  await page.addInitScript(() => {
    navigator.canShare = d => !!(d && d.files && d.files.length);
    navigator.share = async d => {
      window.__shared = {
        files: (d.files || []).length,
        name: d.files && d.files[0] && d.files[0].name,
        type: d.files && d.files[0] && d.files[0].type,
      };
    };
  });
  await bootApp(page);
  await burnToBanner(page);
  const btn = page.locator('#burnShareBtn');
  await expect(btn).toBeVisible({ timeout: 5_000 });
  // The button must be READY immediately - plain label, enabled, no spinner
  // phase (it used to sit in a loading state for the whole warm-up download).
  await expect(btn).toBeEnabled();
  await expect(btn).not.toContainText('...');
  await btn.click();
  const shared = await page.evaluate(() => window.__shared);
  expect(shared.files).toBe(1);
  expect(shared.type).toBe('video/mp4');
});

test('share warm-up fetches the video in parallel byte ranges', async ({ page }) => {
  const { API_BASE } = require('./helpers');
  const TOTAL = 1024 * 1024;                 // 1 MB mock file
  const buf = Buffer.alloc(TOTAL, 7);
  const rangeReqs = [];
  await page.addInitScript(() => {
    window.__RANGE_CHUNK = 256 * 1024;       // → 4 ranges over the 1 MB file
    navigator.canShare = d => !!(d && d.files && d.files.length);
    navigator.share = async d => { window.__shared = { size: d.files[0].size }; };
  });
  await bootApp(page);
  await mockAllApis(page);
  // Range-aware download mock (registered after mockAllApis → takes priority).
  await page.route(`${API_BASE}/download/**`, (route, request) => {
    const hdr = request.headers()['range'] || '';
    const m = /bytes=(\d+)-(\d+)/.exec(hdr);
    if (!m) {
      return route.fulfill({ status: 200, headers: { 'Content-Type': 'video/mp4' }, body: buf });
    }
    rangeReqs.push(hdr);
    const start = +m[1], end = Math.min(+m[2], TOTAL - 1);
    return route.fulfill({
      status: 206,
      headers: {
        'Content-Type': 'video/mp4',
        'Accept-Ranges': 'bytes',
        'Content-Range': `bytes ${start}-${end}/${TOTAL}`,
        // The real backend exposes these via CORS (app_modal CORS list); the
        // frontend can't read Content-Range without it.
        'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
      },
      body: buf.slice(start, end + 1),
    });
  });
  await selectFile(page);
  await page.waitForSelector('#runBtn:not([disabled])', { timeout: 10_000 });
  await page.click('#runBtn');
  await page.waitForSelector('#captionEditorCard', { state: 'visible', timeout: 10_000 });
  await page.click('#runBtn');   // burn
  const ok = page.locator('#confirmOk');
  if (await ok.isVisible().catch(() => false)) await ok.click();
  await page.waitForSelector('#burnSuccessBanner', { state: 'visible', timeout: 15_000 });

  // The warm-up must split the file into multiple concurrent range requests.
  await expect.poll(() => rangeReqs.length, { timeout: 8_000 }).toBeGreaterThanOrEqual(4);

  // And the shared File must reassemble to the exact total size.
  await page.click('#burnShareBtn');
  await expect.poll(() => page.evaluate(() => window.__shared && window.__shared.size),
                    { timeout: 8_000 }).toBe(TOTAL);
});

test('web without Web Share files support: button stays hidden', async ({ page }) => {
  await bootApp(page);   // headless Chromium has no navigator.canShare
  await burnToBanner(page);
  await expect(page.locator('#burnShareBtn')).toBeHidden();
});

// Field report 2026-08-11: "downloads from History only open in the share
// window, but are not saved to device".
//
// Cause: the share warm-up caches the finished file, and the URL a History row
// builds is byte-identical to the fresh burn's (same output key, same
// `<stem>_edited.mp4`). The newest row therefore hit a local-first shortcut
// that saved with Filesystem.copy - the ONE Filesystem write that never runs a
// MediaScan - so the file was never indexed and no file manager could see it,
// and its toast action was a Share sheet.
test('native shell without saveToDownloads: a warm-cached file still goes through DownloadManager, never an unindexed copy', async ({ page }) => {
  await page.addInitScript(() => {
    window.__nativeDownloads = [];
    window.__copies = [];
    window.Capacitor = {
      isNativePlatform: () => true,
      getPlatform: () => 'android',
      Plugins: {
        NativeDownloader: {
          download: async opts => {
            window.__nativeDownloads.push(opts);
            return { id: 7, filename: opts.filename };
          },
          openDownloads: async () => {},
        },
        Filesystem: {
          // Present and willing - the old code preferred it once the warm-up
          // had cached the file.
          copy: async opts => { window.__copies.push(opts); },
          downloadFile: async opts => ({ path: `/docs/${opts.path}` }),
          writeFile: async () => {},
          getUri: async ({ path }) => ({ uri: `file:///docs/${path}` }),
          addListener: async () => ({ remove: async () => {} }),
        },
        Share: { share: async () => {} },
      },
    };
  });
  await bootApp(page);
  await burnToBanner(page);

  // Let the share warm-up finish, so the cache genuinely covers this URL -
  // that is the state that used to divert the save.
  await expect(page.locator('#burnShareBtn')).toBeEnabled({ timeout: 10_000 });

  await page.evaluate(() => { window.__nativeDownloads.length = 0; });
  await page.click('#burnDownloadBtn');

  await expect.poll(() => page.evaluate(() => window.__nativeDownloads.length)).toBe(1);
  // The save must land in public Downloads via DownloadManager...
  const download = await page.evaluate(() => window.__nativeDownloads[0]);
  expect(download.filename).toBe('test_edited.mp4');
  expect(download.mimeType).toBe('video/mp4');
  // ...and must NOT take the unindexed copy shortcut.
  expect(await page.evaluate(() => window.__copies.length)).toBe(0);
});

test('native legacy shell: the save toast offers Share, and does not promise to open', async ({ page }) => {
  await page.addInitScript(() => {
    window.__copies = [];
    window.__shared = 0;
    window.Capacitor = {
      isNativePlatform: () => true,
      getPlatform: () => 'android',
      // No NativeDownloader at all - a shell predating the bridge.
      Plugins: {
        Filesystem: {
          copy: async opts => { window.__copies.push(opts); },
          downloadFile: async opts => ({ path: `/docs/${opts.path}` }),
          writeFile: async () => {},
          getUri: async ({ path }) => ({ uri: `file:///docs/${path}` }),
          addListener: async () => ({ remove: async () => {} }),
        },
        Share: { share: async () => { window.__shared += 1; } },
      },
    };
  });
  await bootApp(page);
  await burnToBanner(page);
  await page.click('#burnDownloadBtn');

  const toast = page.locator('#celebrateToast');
  await expect(toast).toHaveClass(/show/, { timeout: 10_000 });
  // downloadFile indexes the result; copy does not, so copy must not be used.
  expect(await page.evaluate(() => window.__copies.length)).toBe(0);
  // The action can only share here, so it must say share - labelling it
  // "Open video" is what made the sharing modal feel like a bug.
  const action = toast.locator('.celebrate-toast-action');
  await expect(action).toContainText(/שיתוף|Share/);
  await action.click();
  await expect.poll(() => page.evaluate(() => window.__shared)).toBe(1);
});

// The fast path, restored natively in Android 1.4.0: saveToDownloads copies the
// warm-cached bytes straight into the MediaStore Downloads collection, so the
// saved file is indexed by construction - the property Filesystem.copy lacked.
test('native: a warm-cached file is saved from cache, without re-downloading', async ({ page }) => {
  await page.addInitScript(() => {
    window.__saves = [];
    window.__nativeDownloads = [];
    window.Capacitor = {
      isNativePlatform: () => true,
      getPlatform: () => 'android',
      Plugins: {
        NativeDownloader: {
          download: async opts => { window.__nativeDownloads.push(opts); return { id: 3 }; },
          saveToDownloads: async opts => {
            window.__saves.push(opts);
            return { uri: 'content://media/external/downloads/12', filename: opts.filename };
          },
          openDownloads: async () => {},
        },
        Filesystem: {
          downloadFile: async opts => ({ path: `/docs/${opts.path}` }),
          writeFile: async () => {},
          getUri: async ({ path }) => ({ uri: `file:///cache/${path}` }),
          addListener: async () => ({ remove: async () => {} }),
        },
        Share: { share: async () => {} },
      },
    };
  });
  await bootApp(page);
  await burnToBanner(page);
  await expect(page.locator('#burnShareBtn')).toBeEnabled({ timeout: 10_000 });

  await page.evaluate(() => { window.__nativeDownloads.length = 0; });
  await page.click('#burnDownloadBtn');

  await expect.poll(() => page.evaluate(() => window.__saves.length)).toBe(1);
  const save = await page.evaluate(() => window.__saves[0]);
  expect(save.filename).toBe('test_edited.mp4');
  expect(save.mimeType).toBe('video/mp4');
  expect(save.path).toContain('/cache/');          // the warm copy, not the network
  // The whole point is NOT fetching the bytes again.
  expect(await page.evaluate(() => window.__nativeDownloads.length)).toBe(0);
});

test('native: a failing cached save falls back to DownloadManager', async ({ page }) => {
  await page.addInitScript(() => {
    window.__nativeDownloads = [];
    window.Capacitor = {
      isNativePlatform: () => true,
      getPlatform: () => 'android',
      Plugins: {
        NativeDownloader: {
          download: async opts => { window.__nativeDownloads.push(opts); return { id: 4 }; },
          saveToDownloads: async () => { throw new Error('Could not save into Downloads'); },
          openDownloads: async () => {},
        },
        Filesystem: {
          downloadFile: async opts => ({ path: `/docs/${opts.path}` }),
          writeFile: async () => {},
          getUri: async ({ path }) => ({ uri: `file:///cache/${path}` }),
          addListener: async () => ({ remove: async () => {} }),
        },
        Share: { share: async () => {} },
      },
    };
  });
  await bootApp(page);
  await burnToBanner(page);
  await expect(page.locator('#burnShareBtn')).toBeEnabled({ timeout: 10_000 });

  await page.evaluate(() => { window.__nativeDownloads.length = 0; });
  await page.click('#burnDownloadBtn');

  // A slow success beats a silent loss.
  await expect.poll(() => page.evaluate(() => window.__nativeDownloads.length)).toBe(1);
  const download = await page.evaluate(() => window.__nativeDownloads[0]);
  expect(download.filename).toBe('test_edited.mp4');
});
