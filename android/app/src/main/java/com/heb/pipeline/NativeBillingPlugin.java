package com.heb.pipeline;

import com.android.billingclient.api.BillingClient;
import com.android.billingclient.api.BillingClientStateListener;
import com.android.billingclient.api.BillingFlowParams;
import com.android.billingclient.api.BillingResult;
import com.android.billingclient.api.PendingPurchasesParams;
import com.android.billingclient.api.ProductDetails;
import com.android.billingclient.api.Purchase;
import com.android.billingclient.api.PurchasesUpdatedListener;
import com.android.billingclient.api.QueryProductDetailsParams;
import com.android.billingclient.api.QueryPurchasesParams;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

@CapacitorPlugin(name = "NativeBilling")
public class NativeBillingPlugin extends Plugin implements PurchasesUpdatedListener {
    // Must match PLAY_CREDIT_PRODUCTS in pipeline_core.py. The 5/20/50 ladder
    // was retired on 2026-07-31 in favour of 10/30/100, but the old ids stay
    // listed for as long as those products are live in Play: a restore of one
    // still has to resolve, and dropping it would silently strip credits a user
    // already paid for. getProducts() only ever offers what Play returns.
    private static final List<String> PRODUCT_IDS = Arrays.asList(
        "pipeline_credits_10",
        "pipeline_credits_30",
        "pipeline_credits_100",
        "pipeline_credits_5",
        "pipeline_credits_20",
        "pipeline_credits_50"
    );
    private static final Set<String> PRODUCT_ID_SET = new HashSet<>(PRODUCT_IDS);

    private BillingClient billingClient;
    private boolean connecting = false;
    private final List<Runnable> connectionWaiters = new ArrayList<>();
    private final List<PluginCall> connectionCalls = new ArrayList<>();
    private PluginCall purchaseCall;

    @Override
    public void load() {
        billingClient = BillingClient.newBuilder(getContext())
            .setListener(this)
            .enablePendingPurchases(
                PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
            .enableAutoServiceReconnection()
            .build();
    }

    private void withBilling(PluginCall call, Runnable action) {
        if (billingClient != null && billingClient.isReady()) {
            action.run();
            return;
        }
        synchronized (connectionWaiters) {
            connectionWaiters.add(action);
            connectionCalls.add(call);
            if (connecting) return;
            connecting = true;
        }
        billingClient.startConnection(new BillingClientStateListener() {
            @Override
            public void onBillingSetupFinished(BillingResult result) {
                List<Runnable> waiters;
                List<PluginCall> calls;
                synchronized (connectionWaiters) {
                    connecting = false;
                    waiters = new ArrayList<>(connectionWaiters);
                    calls = new ArrayList<>(connectionCalls);
                    connectionWaiters.clear();
                    connectionCalls.clear();
                }
                if (result.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                    for (PluginCall queued : calls) {
                        if (queued == purchaseCall) {
                            finishPurchaseError("Google Play Billing is unavailable",
                                                "billing_unavailable");
                        } else {
                            queued.reject("Google Play Billing is unavailable",
                                          "billing_unavailable");
                        }
                    }
                    return;
                }
                for (Runnable waiter : waiters) waiter.run();
            }

            @Override
            public void onBillingServiceDisconnected() {
                synchronized (connectionWaiters) {
                    connecting = false;
                }
            }
        });
    }

    private QueryProductDetailsParams productQuery(List<String> ids) {
        List<QueryProductDetailsParams.Product> products = new ArrayList<>();
        for (String id : ids) {
            products.add(QueryProductDetailsParams.Product.newBuilder()
                .setProductId(id)
                .setProductType(BillingClient.ProductType.INAPP)
                .build());
        }
        return QueryProductDetailsParams.newBuilder().setProductList(products).build();
    }

    @PluginMethod
    public void getProducts(PluginCall call) {
        withBilling(call, () ->
            billingClient.queryProductDetailsAsync(productQuery(PRODUCT_IDS), (result, queryResult) -> {
                if (result.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                    call.reject("Could not load Google Play products", "products_unavailable");
                    return;
                }
                JSArray products = new JSArray();
                for (ProductDetails details : queryResult.getProductDetailsList()) {
                    List<ProductDetails.OneTimePurchaseOfferDetails> offers =
                        details.getOneTimePurchaseOfferDetailsList();
                    if (offers == null || offers.isEmpty()) continue;
                    ProductDetails.OneTimePurchaseOfferDetails offer = offers.get(0);
                    JSObject item = new JSObject();
                    item.put("productId", details.getProductId());
                    item.put("title", details.getTitle());
                    item.put("description", details.getDescription());
                    item.put("formattedPrice", offer.getFormattedPrice());
                    item.put("priceAmountMicros", offer.getPriceAmountMicros());
                    item.put("priceCurrencyCode", offer.getPriceCurrencyCode());
                    products.put(item);
                }
                JSObject response = new JSObject();
                response.put("products", products);
                call.resolve(response);
            })
        );
    }

    @PluginMethod
    public void purchase(PluginCall call) {
        String productId = call.getString("productId", "");
        String accountId = call.getString("accountId", "");
        if (!PRODUCT_ID_SET.contains(productId) || accountId.length() != 64) {
            call.reject("Invalid purchase request", "invalid_purchase");
            return;
        }
        synchronized (this) {
            if (purchaseCall != null) {
                call.reject("Another purchase is already in progress", "purchase_in_progress");
                return;
            }
            purchaseCall = call;
            call.setKeepAlive(true);
        }
        withBilling(call, () ->
            billingClient.queryProductDetailsAsync(
                productQuery(Arrays.asList(productId)), (result, queryResult) -> {
                    if (result.getResponseCode() != BillingClient.BillingResponseCode.OK
                        || queryResult.getProductDetailsList().isEmpty()) {
                        finishPurchaseError("Product is unavailable", "product_unavailable");
                        return;
                    }
                    ProductDetails details = queryResult.getProductDetailsList().get(0);
                    List<ProductDetails.OneTimePurchaseOfferDetails> offers =
                        details.getOneTimePurchaseOfferDetailsList();
                    if (offers == null || offers.isEmpty()) {
                        finishPurchaseError("No eligible Google Play offer", "product_unavailable");
                        return;
                    }
                    BillingFlowParams.ProductDetailsParams detailParams =
                        BillingFlowParams.ProductDetailsParams.newBuilder()
                            .setProductDetails(details)
                            .setOfferToken(offers.get(0).getOfferToken())
                            .build();
                    BillingFlowParams flowParams = BillingFlowParams.newBuilder()
                        .setProductDetailsParamsList(Arrays.asList(detailParams))
                        .setObfuscatedAccountId(accountId)
                        .build();
                    getActivity().runOnUiThread(() -> {
                        BillingResult launch = billingClient.launchBillingFlow(
                            getActivity(), flowParams);
                        if (launch.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                            finishPurchaseError(
                                "Could not open Google Play purchase",
                                "purchase_launch_failed");
                        }
                    });
                })
        );
    }

    @PluginMethod
    public void restorePurchases(PluginCall call) {
        withBilling(call, () ->
            billingClient.queryPurchasesAsync(
                QueryPurchasesParams.newBuilder()
                    .setProductType(BillingClient.ProductType.INAPP)
                    .build(),
                (result, purchases) -> {
                    if (result.getResponseCode() != BillingClient.BillingResponseCode.OK) {
                        call.reject("Could not restore Google Play purchases",
                                    "restore_failed");
                        return;
                    }
                    JSObject response = new JSObject();
                    response.put("purchases", purchasesJson(purchases));
                    call.resolve(response);
                })
        );
    }

    @Override
    public void onPurchasesUpdated(BillingResult result, List<Purchase> purchases) {
        if (result.getResponseCode() == BillingClient.BillingResponseCode.USER_CANCELED) {
            finishPurchaseError("Purchase cancelled", "purchase_cancelled");
            return;
        }
        if (result.getResponseCode() != BillingClient.BillingResponseCode.OK
            || purchases == null || purchases.isEmpty()) {
            finishPurchaseError("Google Play purchase failed", "purchase_failed");
            return;
        }
        Purchase purchase = purchases.get(0);
        JSObject response = purchaseJson(purchase);
        if (purchase.getPurchaseState() == Purchase.PurchaseState.PENDING) {
            response.put("state", "pending");
        } else if (purchase.getPurchaseState() == Purchase.PurchaseState.PURCHASED) {
            response.put("state", "purchased");
        } else {
            finishPurchaseError("Purchase was not completed", "purchase_failed");
            return;
        }
        // Also publish the update independently of the active purchase call.
        // A pending payment can complete hours later, after its original JS
        // promise resolved; the listener lets the open app verify it at once.
        notifyListeners("purchaseUpdated", response);
        finishPurchaseSuccess(response);
    }

    private JSArray purchasesJson(List<Purchase> purchases) {
        JSArray out = new JSArray();
        for (Purchase purchase : purchases) out.put(purchaseJson(purchase));
        return out;
    }

    private JSObject purchaseJson(Purchase purchase) {
        JSObject item = new JSObject();
        item.put("purchaseToken", purchase.getPurchaseToken());
        item.put("products", new JSArray(purchase.getProducts()));
        item.put("purchaseTime", purchase.getPurchaseTime());
        item.put("state", purchase.getPurchaseState() == Purchase.PurchaseState.PURCHASED
            ? "purchased"
            : purchase.getPurchaseState() == Purchase.PurchaseState.PENDING
                ? "pending" : "unspecified");
        return item;
    }

    private synchronized void finishPurchaseSuccess(JSObject result) {
        PluginCall call = purchaseCall;
        purchaseCall = null;
        if (call == null) return;
        call.setKeepAlive(false);
        call.resolve(result);
    }

    private synchronized void finishPurchaseError(String message, String code) {
        PluginCall call = purchaseCall;
        purchaseCall = null;
        if (call == null) return;
        call.setKeepAlive(false);
        call.reject(message, code);
    }

    @Override
    protected void handleOnDestroy() {
        if (billingClient != null) billingClient.endConnection();
    }
}
