# Backlog

## Publish on iOS (not started, parked 2026-07-27)

Capacitor already covers the platform: all four native plugins in use ship iOS
support (`@capgo/capacitor-uploader`, `@capgo/capacitor-social-login`,
`@capawesome/capacitor-file-picker`, `@capacitor/push-notifications`). The build
is the easy part; App Review is the work. Budget about a week.

**Prerequisites**
- Apple Developer Program, $99/year (individual = same day; company needs D-U-N-S).
- Full Xcode (this Mac has Command Line Tools only) + CocoaPods.
- `npm i @capacitor/ios && npx cap add ios`, bundle id `com.heb.pipeline` (match Android).

**Blocker 1: the remote web view.** `capacitor.config.json` sets
`server.url = https://hebrew-pipeline.app`, so the app is a window onto the live
site. That is the classic trigger for guideline 4.2 (minimum functionality,
"repackaged website"). Android tolerates it, Apple often does not. Fix = drop
`server.url` on iOS and ship `webDir: "site"` as bundled assets, leaning on the
native push / file picker / uploader / share sheet as real platform integration.
Cost: iOS loses the instant-update channel, so every frontend change becomes a
build + review, and the two platforms diverge (today's whole deploy story assumes
the remote URL). The `APP_VERSION` footer still identifies builds.

**Blocker 2: in-app purchase.** The quota-exhausted WhatsApp CTA sells more
videos outside the app, which guideline 3.1.1 requires to go through IAP;
steering users out is what Apple polices, and the rules keep shifting with the
court rulings. Decide BEFORE submitting: either implement StoreKit, or ship iOS
with no purchase path and hide the quota CTA on that platform.

**Probably fine, watch for it:** guideline 4.8 wants a privacy-preserving login
alongside Google. The passwordless email-code flow likely qualifies. If review
pushes back, `@capgo/capacitor-social-login` already ships `AppleProvider.swift`,
so Sign in with Apple is backend-only work (an Apple identity-token verifier
next to `_verify_google_id_token`).

**Easy to forget**
- `NSPhotoLibraryUsageDescription` in Info.plist, or the video picker fails silently.
- APNs auth key (.p8) uploaded to Firebase + Push Notifications capability,
  or the two-message push story is dead on iOS.
- TestFlight is the equivalent of the Android closed-testing track.

**Open question:** are there iOS users actually waiting? The real cost is the
permanently slower iteration loop, not the setup.
