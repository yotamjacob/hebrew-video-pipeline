const { test, expect } = require('@playwright/test');
const { API_BASE, bootApp } = require('./helpers');


async function installBillingShim(page) {
  await page.addInitScript(() => {
    window.__billingCalls = [];
    window.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {
        NativeBilling: {
          getProducts: async () => ({
            products: [
              { productId: 'pipeline_credits_5', formattedPrice: '₪49.00' },
              { productId: 'pipeline_credits_20', formattedPrice: '₪149.00' },
              { productId: 'pipeline_credits_50', formattedPrice: '₪299.00' },
            ],
          }),
          purchase: async options => {
            window.__billingCalls.push(options);
            return {
              state: 'purchased',
              purchaseToken: 'play-token-1234567890',
              products: [options.productId],
            };
          },
          restorePurchases: async () => ({ purchases: [] }),
          addListener: async (event, callback) => {
            if (event === 'purchaseUpdated') window.__billingListener = callback;
            return { remove: async () => {} };
          },
        },
      },
    };
  });
}


test('native exhausted state opens localized Google Play credit packs', async ({ page }) => {
  await installBillingShim(page);
  await bootApp(page, {
    me: {
      username: 'tester',
      role: 'user',
      videos_used: 5,
      video_limit: 5,
      billing_account_id: 'a'.repeat(64),
    },
  });

  await page.evaluate(() => showQuotaExhausted());
  await expect(page.locator('#noticeBuyCta')).toBeVisible();
  await expect(page.locator('#noticePlayCta')).toBeHidden();
  await page.locator('#noticeBuyCta').click();

  const packs = page.locator('.billing-product');
  await expect(packs).toHaveCount(3);
  await expect(packs.nth(0)).toContainText('5 קרדיטים לסרטונים');
  await expect(packs.nth(0)).toContainText('₪49.00');
  await expect(packs.nth(2)).toContainText('₪299.00');
});

test('native credit pill opens Play Billing before credits run out', async ({ page }) => {
  await installBillingShim(page);
  await bootApp(page, {
    me: {
      username: 'tester',
      role: 'user',
      videos_used: 2,
      video_limit: 5,
      billing_account_id: 'b'.repeat(64),
    },
  });

  const pill = page.locator('#quotaPill');
  await expect(pill).toContainText('נותרו 3 קרדיטים');
  await expect(pill).toHaveClass(/billing-enabled/);
  await expect(pill).toHaveAttribute('title', 'רכישת קרדיטים נוספים לסרטונים');
  await pill.click();
  await expect(page.locator('#billingOverlay')).toBeVisible();
  await expect(page.locator('.billing-product')).toHaveCount(3);
});

test('web exhausted-credit actions open the Pipeline Play Store listing', async ({ page }) => {
  await page.addInitScript(() => {
    window.__openedPlayUrl = null;
    window.open = url => {
      window.__openedPlayUrl = url;
      return {};
    };
  });
  await bootApp(page, {
    me: {
      username: 'tester',
      role: 'user',
      videos_used: 5,
      video_limit: 5,
    },
  });

  await page.locator('#quotaPill').click();
  await expect.poll(() => page.evaluate(() => window.__openedPlayUrl)).toBe(
    'https://play.google.com/store/apps/details?id=com.heb.pipeline',
  );
});

test('an older native shell routes to a Play update, never WhatsApp', async ({ page }) => {
  await page.addInitScript(() => {
    window.Capacitor = {
      isNativePlatform: () => true,
      Plugins: {},
    };
  });
  await bootApp(page, {
    me: {
      username: 'tester',
      role: 'user',
      videos_used: 5,
      video_limit: 5,
    },
  });

  await page.evaluate(() => showQuotaExhausted());
  await expect(page.locator('#noticeBlockBody')).toContainText('עדכנו את פייפליין');
  await expect(page.locator('#noticePlayCta')).toContainText('עדכון אפליקציית Android');
  await expect(page.locator('#noticePlayCta')).toHaveAttribute(
    'href',
    'https://play.google.com/store/apps/details?id=com.heb.pipeline',
  );
  await expect(page.locator('#noticeBuyCta')).toBeHidden();
  await expect(page.locator('#noticeBlock a[href*="wa.me"]')).toHaveCount(0);
});


test('a pending Play payment completing later is granted while the app is open', async ({ page }) => {
  await installBillingShim(page);
  await bootApp(page, {
    me: {
      username: 'tester',
      role: 'user',
      videos_used: 5,
      video_limit: 5,
      billing_account_id: 'c'.repeat(64),
    },
  });

  let verified = false;
  await page.route(`${API_BASE}/billing/play/verify`, route => {
    verified = true;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{"ok":true,"credits_granted":5,"video_limit":10,"videos_used":5}',
    });
  });
  await page.unroute(/\/auth\/me/);
  await page.route(/\/auth\/me/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      username: 'tester',
      role: 'user',
      videos_used: 5,
      video_limit: 10,
      billing_account_id: 'c'.repeat(64),
    }),
  }));

  await page.waitForFunction(() => typeof window.__billingListener === 'function');
  await page.evaluate(() => window.__billingListener({
    state: 'purchased',
    purchaseToken: 'delayed-play-token-123456',
    products: ['pipeline_credits_5'],
  }));
  await expect(page.locator('#quotaPill')).toHaveText('נותרו 5 קרדיטים לסרטונים');
  expect(verified).toBe(true);
});


test('native purchase is server-verified and refreshes the credit pill', async ({ page }) => {
  await installBillingShim(page);
  await bootApp(page, {
    me: {
      username: 'tester',
      role: 'user',
      videos_used: 5,
      video_limit: 5,
      billing_account_id: 'b'.repeat(64),
    },
  });

  let verifyBody = null;
  await page.route(`${API_BASE}/billing/play/verify`, async (route, request) => {
    verifyBody = request.postDataJSON();
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        credits_granted: 20,
        purchased_credits: 20,
        videos_used: 5,
        video_limit: 25,
      }),
    });
  });
  await page.unroute(/\/auth\/me/);
  await page.route(/\/auth\/me/, route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      username: 'tester',
      role: 'user',
      videos_used: 5,
      video_limit: 25,
      purchased_credits: 20,
      billing_account_id: 'b'.repeat(64),
    }),
  }));

  await page.locator('#quotaPill').click();
  await page.locator('[data-product-id="pipeline_credits_20"]').click();
  await expect(page.locator('#billingStatus')).toContainText('20 קרדיטים נוספו בהצלחה');
  await expect(page.locator('#quotaPill')).toHaveText('נותרו 20 קרדיטים לסרטונים');

  expect(verifyBody).toEqual({
    purchase_token: 'play-token-1234567890',
    product_id: 'pipeline_credits_20',
  });
  const calls = await page.evaluate(() => window.__billingCalls);
  expect(calls).toEqual([{
    productId: 'pipeline_credits_20',
    accountId: 'b'.repeat(64),
  }]);
});
