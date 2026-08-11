import Foundation
import Capacitor
import StoreKit

/// StoreKit 2 credit packs - the iOS counterpart of NativeBillingPlugin.java.
///
/// The JS bridge surface is deliberately IDENTICAL to the Android plugin
/// (`getProducts` / `purchase` / `restorePurchases` + a `purchaseUpdated`
/// listener) so `site/app.js` runs one billing flow on both platforms and only
/// branches on which verify endpoint to call.
///
/// The one extra method is `finishTransaction`. Play consumables are consumed
/// SERVER-side after the grant; StoreKit consumables must be finished on the
/// DEVICE, and finishing before our server has granted would destroy the only
/// remaining record of a purchase the user paid for. So the order is always:
///   purchase() -> POST /billing/apple/verify -> finishTransaction()
/// An interrupted run simply leaves the transaction unfinished, and StoreKit
/// re-offers it through `restorePurchases()`/`Transaction.updates` on the next
/// launch, where the idempotent verify route grants it exactly once.
@objc(NativeBillingPlugin)
public class NativeBillingPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "NativeBillingPlugin"
    public let jsName = "NativeBilling"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "getProducts", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "purchase", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "restorePurchases", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "finishTransaction", returnType: CAPPluginReturnPromise)
    ]

    /// Must match APPLE_CREDIT_PRODUCTS in pipeline_core.py. The server is the
    /// authority on what a product is worth; this list only decides what the
    /// store sheet offers and which transactions the plugin will report.
    private static let productIds: [String] = [
        "pipeline_credits_10",
        "pipeline_credits_30",
        "pipeline_credits_100"
    ]

    private var updatesTask: Task<Void, Never>?

    override public func load() {
        // Purchases that complete outside an active purchase() call: an
        // Ask-to-Buy approval, a payment that finished while the app was
        // closed, or a purchase made on another device.
        updatesTask = Task { [weak self] in
            for await update in Transaction.updates {
                guard let self = self, case .verified(let transaction) = update else { continue }
                let payload = Self.json(transaction)
                DispatchQueue.main.async {
                    self.notifyListeners("purchaseUpdated", data: payload)
                }
            }
        }
    }

    deinit {
        updatesTask?.cancel()
    }

    @objc func getProducts(_ call: CAPPluginCall) {
        Task {
            do {
                let products = try await Product.products(for: Self.productIds)
                let items: [[String: Any]] = products
                    .sorted { $0.price < $1.price }
                    .map { product in
                        [
                            "productId": product.id,
                            "title": product.displayName,
                            "description": product.description,
                            // Always Apple's localized, currency-correct string
                            // - never a price we format ourselves.
                            "formattedPrice": product.displayPrice,
                            "priceAmountMicros": NSDecimalNumber(
                                decimal: product.price * 1_000_000).int64Value,
                            "priceCurrencyCode": product.priceFormatStyle.currencyCode
                        ]
                    }
                call.resolve(["products": items])
            } catch {
                call.reject("Could not load App Store products", "products_unavailable")
            }
        }
    }

    @objc func purchase(_ call: CAPPluginCall) {
        let productId = call.getString("productId") ?? ""
        let accountId = call.getString("accountId") ?? ""
        // appAccountToken must be a UUID (unlike Play's free-form obfuscated
        // account id). The server derives the same UUID from the uid and
        // refuses any transaction that does not carry it.
        guard Self.productIds.contains(productId),
              let accountToken = UUID(uuidString: accountId) else {
            call.reject("Invalid purchase request", "invalid_purchase")
            return
        }
        Task {
            do {
                guard let product = try await Product.products(for: [productId]).first else {
                    call.reject("Product is unavailable", "product_unavailable")
                    return
                }
                let result = try await product.purchase(options: [.appAccountToken(accountToken)])
                switch result {
                case .success(let verification):
                    switch verification {
                    case .verified(let transaction):
                        // NOT finished here - see the class comment.
                        call.resolve(Self.json(transaction))
                    case .unverified:
                        call.reject("Purchase could not be verified", "purchase_failed")
                    }
                case .pending:
                    // Ask-to-Buy / SCA. `purchaseUpdated` fires when it clears.
                    call.resolve(["state": "pending"])
                case .userCancelled:
                    call.reject("Purchase cancelled", "purchase_cancelled")
                @unknown default:
                    call.reject("App Store purchase failed", "purchase_failed")
                }
            } catch {
                call.reject("App Store purchase failed", "purchase_failed")
            }
        }
    }

    /// Every credit purchase this device has paid for but that we have not
    /// finished yet, i.e. everything the server may still owe credits for.
    @objc func restorePurchases(_ call: CAPPluginCall) {
        Task {
            var purchases: [[String: Any]] = []
            for await result in Transaction.unfinished {
                guard case .verified(let transaction) = result,
                      transaction.revocationDate == nil,
                      Self.productIds.contains(transaction.productID) else { continue }
                purchases.append(Self.json(transaction))
            }
            call.resolve(["purchases": purchases])
        }
    }

    /// Called by JS only after /billing/apple/verify granted the credits.
    @objc func finishTransaction(_ call: CAPPluginCall) {
        guard let raw = call.getString("transactionId"), let wanted = UInt64(raw) else {
            call.reject("Invalid transaction", "invalid_purchase")
            return
        }
        Task {
            for await result in Transaction.unfinished {
                if case .verified(let transaction) = result, transaction.id == wanted {
                    await transaction.finish()
                    break
                }
            }
            // Resolving even when nothing matched is deliberate: an already
            // finished transaction is the desired end state, not an error.
            call.resolve(["ok": true])
        }
    }

    private static func json(_ transaction: Transaction) -> [String: Any] {
        return [
            "transactionId": String(transaction.id),
            "originalTransactionId": String(transaction.originalID),
            // Named `products` (plural) to match the Play payload shape so the
            // shared JS can find the product id the same way on both platforms.
            "products": [transaction.productID],
            "purchaseTime": Int(transaction.purchaseDate.timeIntervalSince1970 * 1000),
            "state": transaction.revocationDate == nil ? "purchased" : "revoked"
        ]
    }
}
