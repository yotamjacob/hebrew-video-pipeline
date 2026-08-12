import Capacitor

/// Bridge subclass whose only job is registering the manually-written plugin.
///
/// NativeBillingPlugin lives in the app target, not in an SPM package, so
/// `cap sync` never lists it in capacitor.config.json's `packageClassList` -
/// the ONLY list the stock bridge auto-registers from (there is no runtime
/// class scan on iOS). Hand-editing packageClassList doesn't survive the next
/// sync, and `registerPluginType()` is a guarded no-op while
/// `autoRegisterPlugins` is true - `registerPluginInstance()` from
/// `capacitorDidLoad()` is the one durable hook. Main.storyboard points at
/// this class; scripts/check_ios_config.js asserts that stays true.
class AppViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(NativeBillingPlugin())
    }
}
