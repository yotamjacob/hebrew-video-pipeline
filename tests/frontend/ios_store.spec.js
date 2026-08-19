// App Store compliance surfaces in the iOS shell.
//
// Everything here exists because of a specific App Store guideline, and a
// regression in any of them is a rejection, not a cosmetic bug:
//   3.1.1  - no purchase route outside Apple IAP, and no other store named
//   4.8    - no third-party sign-in without an equivalent private option
//   5.1.1(v) - account deletion has to start inside the app
const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp } = require('./helpers');

// The iOS shell: Capacitor reports platform 'ios' and the StoreKit bridge is
// registered under the same plugin name the Android build uses.
async function installIOSShim(page, { withBilling = true } = {}) {
  await page.addInitScript(({ withBilling }) => {
    window.__billingCalls = [];
    window.__finished = [];
    const NativeBilling = {
      getProducts: async () => ({
        products: [
          { productId: 'pipeline_credits_10', formattedPrice: '₪59.00' },
          { productId: 'pipeline_credits_30', formattedPrice: '₪149.00' },
          { productId: 'pipeline_credits_100', formattedPrice: '₪399.00' },
        ],
      }),
      purchase: async options => {
        window.__billingCalls.push(options);
        return {
          state: 'purchased',
          transactionId: '2000000012345678',
          products: [options.productId],
        };
      },
      restorePurchases: async () => ({ purchases: [] }),
      finishTransaction: async options => { window.__finished.push(options); },
      addListener: async (event, callback) => {
        if (event === 'purchaseUpdated') window.__billingListener = callback;
        return { remove: async () => {} };
      },
    };
    window.Capacitor = {
      isNativePlatform: () => true,
      getPlatform: () => 'ios',
      Plugins: withBilling ? { NativeBilling } : {},
    };
  }, { withBilling });
}

// An Android shell with no billing bridge - the control case that proves the
// Play surfaces are hidden BY PLATFORM and not simply broken everywhere.
async function installOldAndroidShim(page) {
  await page.addInitScript(() => {
    window.Capacitor = {
      isNativePlatform: () => true,
      getPlatform: () => 'android',
      Plugins: {},
    };
  });
}

const QUOTA_ME = {
  username: 'tester',
  role: 'user',
  videos_used: 5,
  video_limit: 5,
  billing_account_id: 'a'.repeat(64),
  apple_account_token: '0d6f47a1-1111-2222-3333-444455556666',
};

test('iOS purchase verifies against Apple and only then finishes the transaction', async ({ page }) => {
  await installIOSShim(page);
  const requests = [];
  await page.route(/\/billing\/.*\/verify/, async (route, request) => {
    requests.push({ url: request.url(), body: request.postDataJSON() });
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ok: true, credits_granted: 30, purchased_credits: 30,
                             videos_used: 5, video_limit: 35 }),
    });
  });
  await bootApp(page, { me: QUOTA_ME });

  await page.evaluate(() => openBillingModal());
  await expect(page.locator('.billing-product')).toHaveCount(3);
  await page.locator('.billing-product').nth(1).click();
  await expect(page.locator('#billingStatus')).toContainText('30');

  expect(requests).toHaveLength(1);
  expect(requests[0].url).toContain('/billing/apple/verify');
  // The client may only name the transaction - never the product or its value.
  expect(requests[0].body).toEqual({ transaction_id: '2000000012345678' });

  // StoreKit binding is the UUID, not the 64-char Play hash.
  const calls = await page.evaluate(() => window.__billingCalls);
  expect(calls[0].accountId).toBe(QUOTA_ME.apple_account_token);

  // Finishing must happen AFTER the grant: before it, the unfinished
  // transaction is the user's only proof of purchase.
  const finished = await page.evaluate(() => window.__finished);
  expect(finished).toEqual([{ transactionId: '2000000012345678' }]);
});

test('iOS never shows a Google Play route, even with no billing bridge', async ({ page }) => {
  await installIOSShim(page, { withBilling: false });
  await bootApp(page, { me: QUOTA_ME });

  await page.evaluate(() => showQuotaExhausted());
  await expect(page.locator('#noticePlayCta')).toBeHidden();
  await expect(page.locator('#noticeBuyCta')).toBeHidden();
  // The copy must not name another store either.
  const body = await page.locator('#noticeBlockBody').textContent();
  expect(body).not.toMatch(/Google Play|Android/i);

  await page.evaluate(() => showError('limit_reached'));
  await expect(page.locator('#errorPlayCta')).toBeHidden();
  await expect(page.locator('#errorBuyCta')).toBeHidden();

  // And the pill must not navigate to the Play listing.
  const before = page.url();
  await page.evaluate(() => openQuotaPurchase());
  expect(page.url()).toBe(before);
});

test('an Android shell without billing still routes to the Play listing', async ({ page }) => {
  await installOldAndroidShim(page);
  await bootApp(page, { me: QUOTA_ME });

  await page.evaluate(() => showQuotaExhausted());
  await expect(page.locator('#noticePlayCta')).toBeVisible();
  await expect(page.locator('#noticePlayCta')).toHaveAttribute(
    'href', /play\.google\.com/);
});

test('iOS billing copy names the App Store, never Google Play', async ({ page }) => {
  await installIOSShim(page);
  await bootApp(page, { me: QUOTA_ME });

  await page.evaluate(() => openBillingModal());
  const footnote = page.locator('#billingOverlay [data-i18n="billing.secure"]');
  await expect(footnote).toContainText('App Store');
  await expect(footnote).not.toContainText('Google Play');

  // The switch has to survive a language change (applyI18n writes the base
  // Play string back in). Called directly because the open modal covers the
  // language toggle - the clobber is what is under test, not the button.
  await page.evaluate(() => setLang('en'));
  await expect(footnote).toContainText('App Store');
  await expect(footnote).not.toContainText('Google Play');
});

test('iOS offers only the email lane, no third-party sign-in (4.8)', async ({ page }) => {
  await installIOSShim(page);
  await page.addInitScript(() => localStorage.removeItem('hebpipe_token'));
  await page.route(/fonts\.googleapis\.com/, r =>
    r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.route(/fonts\.gstatic\.com/, r => r.abort());
  await page.route(/accounts\.google\.com\/gsi/, r =>
    r.fulfill({ status: 200, contentType: 'text/javascript', body: '' }));
  await page.route(/\/auth\/me/, r => r.fulfill({ status: 401, body: '{}' }));
  await page.route(/\/error-report/, r =>
    r.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' }));
  await page.goto('/');

  // Assert the sign-in step is actually up first - otherwise "Google button is
  // hidden" would pass simply because the whole view is still hidden.
  await expect(page.locator('#authView')).toBeVisible();
  await page.click('#authChoiceExisting');
  await expect(page.locator('#authStepEmail')).toBeVisible();

  await expect(page.locator('#googleSignInBtn')).toBeHidden();
  await expect(page.locator('#gsiButton')).toBeHidden();
  await expect(page.locator('.auth-or').first()).toBeHidden();
  // The email lane itself must still be there.
  await expect(page.locator('#authEmail')).toBeVisible();
});

test('account deletion runs in-app and signs the user out (5.1.1(v))', async ({ page }) => {
  await installIOSShim(page);
  let deleteBody = null;
  await page.route(/\/account\/delete/, async (route, request) => {
    deleteBody = request.postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json',
                          body: '{"ok":true,"files_removed":3}' });
  });
  await bootApp(page, { me: QUOTA_ME });
  // The record is gone server-side, so the reload that follows deletion must
  // land on the sign-in view. Registered last, so it wins over bootApp's stub.
  await page.route(/\/auth\/me/, r => deleteBody
    ? r.fulfill({ status: 401, body: '{}' })
    : r.fulfill({ status: 200, contentType: 'application/json',
                  body: JSON.stringify(QUOTA_ME) }));

  await page.evaluate(() => { deleteAccountFlow(); });
  // Two confirmations - the action is immediate and irreversible.
  await expect(page.locator('#confirmOverlay')).toBeVisible();
  await page.locator('#confirmOk').click();
  await expect(page.locator('#confirmBody')).toContainText(/סופית|permanent/i);
  await page.locator('#confirmOk').click();

  await expect.poll(() => deleteBody).toEqual({ confirm: true });
  // The session token is a stateless HMAC that outlives the record, so the app
  // must drop it and come back on the sign-in view rather than keep presenting
  // a dead account. (The suite's init script re-seeds a token on every
  // navigation, so the landing view - not localStorage - is the observable.)
  await expect(page.locator('#authView')).toBeVisible();
  await expect(page.locator('#pipelineView')).toBeHidden();
});

test('cancelling either confirmation deletes nothing', async ({ page }) => {
  await installIOSShim(page);
  let called = false;
  await page.route(/\/account\/delete/, async route => {
    called = true;
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{"ok":true}' });
  });
  await bootApp(page, { me: QUOTA_ME });

  await page.evaluate(() => { deleteAccountFlow(); });
  await page.locator('#confirmCancel').click();
  await expect(page.locator('#confirmOverlay')).toBeHidden();

  await page.evaluate(() => { deleteAccountFlow(); });
  await page.locator('#confirmOk').click();       // past the first gate
  await page.locator('#confirmCancel').click();   // stop at the second
  await expect(page.locator('#confirmOverlay')).toBeHidden();

  expect(called).toBe(false);
  expect(await page.evaluate(() => localStorage.getItem('hebpipe_token'))).toBe('test-token');
});
