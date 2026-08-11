# App Store release guide — פייפליין (`com.heb.pipeline`)

Everything Claude could do in code is **done** (see "What is already built"
below). This file is the list of things only you can do: buying accounts,
installing Xcode, creating records in Apple's consoles, and pressing Submit.

Work top to bottom. Steps 1-3 are blocking - nothing else can happen until
Xcode exists and the bundle id is registered.

---

## What is already built (no action needed)

| Area | Status |
|------|--------|
| `ios/` Capacitor project | Created, SPM-based (no CocoaPods needed to build) |
| Web assets | **Bundled** into the app, not loaded from hebrew-pipeline.app (Guideline 4.2) |
| Device family | iPhone only (`TARGETED_DEVICE_FAMILY = 1`) |
| App icon | 1024x1024, opaque, brand terracotta + pipe mark |
| Launch screen | Flat sand `#E8DFD3` (same decision as Android - scaled splash art goes blurry) |
| Info.plist | Photo-library purpose strings, `ITSAppUsesNonExemptEncryption=false`, forced Light appearance, he+en localizations, Hebrew launcher name |
| Entitlements | `App.entitlements` with `aps-environment` (push) |
| In-app purchase | StoreKit 2 plugin + `/billing/apple/verify` + refund webhook, fully server-verified |
| Google sign-in | Hidden in the iOS build (Guideline 4.8 avoided) |
| Google Play links | Impossible to reach in the iOS build (Guideline 3.1.1) |
| Account deletion | In-app, two-step confirm, real server-side purge (Guideline 5.1.1(v)) |
| Tests | 479 pytest + 671 Playwright green, incl. `tests/frontend/ios_store.spec.js` |

---

## 1. Apple Developer Program - $99/year

1. https://developer.apple.com/programs/enroll/ - sign in with your Apple ID.
2. Enroll as an **individual** unless you have a registered company with a
   D-U-N-S number. Individual enrolment is instant to a few days; an
   organization needs the D-U-N-S and takes 1-2 weeks.
3. Note what your **seller name** will be - for individual accounts Apple
   publishes your **legal name** on the listing. If you want a company name
   shown instead, that decision has to be made here, not later.
4. Enable two-factor auth on the Apple ID (required).

**Blocking:** nothing below works without this.

---

## 2. Install Xcode

Xcode is **not** on this Mac (only Command Line Tools). CocoaPods is already
installed via Homebrew, but this project uses Swift Package Manager, so Xcode
is the only missing piece.

1. Mac App Store → Xcode → Install (~10-15 GB, 30-60 min).
2. Open it once and accept the licence, let it install extra components.
3. Point the toolchain at it:
   ```bash
   sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
   xcodebuild -version          # should print Xcode 16.x
   ```
4. Xcode → Settings → Accounts → add your Apple ID (the developer account).

---

## 3. Register the App ID and capabilities

https://developer.apple.com/account → Certificates, Identifiers & Profiles.

1. **Identifiers → +** → App IDs → App → Bundle ID **`com.heb.pipeline`**
   (explicit, not wildcard). This is the same id as the Android package on
   purpose; it is permanent once used.
2. Tick capabilities: **Push Notifications**, **In-App Purchase**.
3. **Keys → +** → name "Pipeline APNs" → tick **Apple Push Notifications
   service (APNs)** → Continue → Register → **Download the .p8 once**
   (Apple never shows it again). Note the **Key ID**.

---

## 4. Create the App Store Connect record

https://appstoreconnect.apple.com → My Apps → **+** → New App.

- Platform: **iOS**
- Name: `Pipeline - Hebrew video editor` (30-char limit, same as Play)
- Primary language: **Hebrew**
- Bundle ID: `com.heb.pipeline`
- SKU: `pipeline-ios-1`
- User access: Full

Then under **App Information**:
- Category: Photo & Video (secondary: Productivity)
- Privacy Policy URL: `https://hebrew-pipeline.app/legal.html`
- Content rights: you own or have licensed the content

---

## 5. Create the three in-app purchases

App Store Connect → your app → **Monetization → In-App Purchases → +**.

Type **Consumable** for all three. The product IDs must match
`APPLE_CREDIT_PRODUCTS` in `pipeline_core.py` **exactly**:

| Product ID | Reference name | Display name (HE) | Target price |
|---|---|---|---|
| `pipeline_credits_10` | 10 video credits | 10 קרדיטים לסרטונים | ₪59 |
| `pipeline_credits_30` | 30 video credits | 30 קרדיטים לסרטונים | ₪149 |
| `pipeline_credits_100` | 100 video credits | 100 קרדיטים לסרטונים | ₪399 |

For each one:
- Pick the Israel price point nearest the target (Apple sets every other
  territory from it automatically).
- **Localizations**: Hebrew + English display name and description.
- **Review screenshot**: required, 640x920 or larger. Take it from the running
  app with the credit-pack sheet open (see step 9's simulator instructions).
- Review notes: "Consumable credits used to process videos. Verified
  server-side against the App Store Server API."

Each IAP has its own review state. They are reviewed **with** your first
binary, so submit them together.

**Also required:** Business → **Paid Apps agreement** must be Active, with tax
and banking details filled in. Without it, products stay "Missing Metadata"
and StoreKit returns an empty product list in the app.

---

## 6. App Store Server API key (this is what lets the server grant credits)

App Store Connect → **Users and Access → Integrations → In-App Purchase → +**

1. Name it "Pipeline server", generate, **download the .p8 once**.
2. Note the **Key ID** and, at the top of that page, the **Issuer ID**.
3. Create the Modal secret:
   ```bash
   modal secret create hebpipe-apple \
     APPLE_KEY_ID=XXXXXXXXXX \
     APPLE_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
     APPLE_PRIVATE_KEY="$(cat ~/Downloads/SubscriptionKey_XXXXXXXXXX.p8)"
   ```
   Use the **In-App Purchase** key type, not a generic App Store Connect API
   key - only the IAP key can read `/inApps/v1/transactions`.
4. Redeploy: `modal deploy app_modal.py`.

Until this secret exists, `/billing/apple/verify` answers **503** and no
purchase can grant. Everything else in the app keeps working.

### Refund webhook (do this too - it is how revoked purchases lose credits)

App Store Connect → your app → **App Information → App Store Server
Notifications**:
- Production URL and Sandbox URL both:
  `https://yotamjacob--hebrew-video-pipeline-api.modal.run/billing/apple/notify`
- Version: **V2**

---

## 7. Push notifications (optional for v1, but you already have the plumbing)

The backend sends "your video is ready" through FCM, which relays to APNs.

1. Firebase console → the existing `pipeline---hebrew-video-editor` project →
   Add app → **iOS** → bundle id `com.heb.pipeline`.
2. Download `GoogleService-Info.plist` and drag it into `ios/App/App/` in Xcode
   (tick "Copy items if needed", target App).
   **Never commit it** - the repo is public. Add it to `.gitignore` first:
   `echo "GoogleService-Info.plist" >> .gitignore`
3. Firebase → Project settings → Cloud Messaging → **APNs Authentication Key**
   → upload the .p8 from step 3, with its Key ID and your Team ID.

If you skip this, the app still works; it just will not push on iOS.

---

## 8. Build settings you set once in Xcode

```bash
npm run sync:ios     # copies site/ into the app, asserts no server.url
npx cap open ios
```

In Xcode, select the **App** target:

- **Signing & Capabilities**: tick "Automatically manage signing", pick your
  Team. Xcode will create the provisioning profile.
- Confirm the capability list shows **Push Notifications** and
  **In-App Purchase**. If Push is missing, click "+ Capability" and add it -
  that regenerates the entitlements entry.
- **General → Identity**: Version `1.0`, Build `1`.
  (Bump **Build** for every upload; bump **Version** for every public release.)
- Optional polish: add `he.lproj/InfoPlist.strings` to localize the two
  photo-library permission prompts into Hebrew. English is a perfectly
  acceptable fallback, so this is not a blocker.

---

## 9. Test the purchase flow BEFORE submitting

StoreKit sandbox is the only way to know the whole chain works.

1. App Store Connect → Users and Access → **Sandbox → Test Accounts → +**.
   Use an email address you control that is **not** an existing Apple ID.
2. On a real iPhone: Settings → App Store → Sandbox Account → sign in with it.
3. Xcode → run the app on that device.
4. Sign in to Pipeline, exhaust the free credits (or use an admin account),
   open the credit-pack sheet, buy the 10-pack.

What must happen, in order:
- Apple's payment sheet shows **[Environment: Sandbox]** and the localized price.
- The status line goes to "Verifying your purchase…".
- Credits appear in the quota pill.
- Modal logs show the grant. Check with `modal app logs hebrew-video-pipeline`.

If credits do not appear, the usual causes are: the `hebpipe-apple` secret is
missing or uses the wrong key type (503 in the logs), or the Paid Apps
agreement is not Active (the product sheet comes up empty).

**Also test:** kill the app mid-purchase, reopen it. The credits must appear
without a second charge - that is `Transaction.unfinished` replaying through
the idempotent verify route.

---

## 10. Screenshots + listing

**Screenshots (required):** 6.9" display, 1320 x 2868 px, 3 to 10 of them.
One size is enough now - Apple scales it down for smaller devices.

Easiest source: iPhone 16 Pro Max simulator (Xcode → Window → Devices and
Simulators), `Cmd+S` saves a correctly-sized shot to the Desktop.

Suggested set, mirroring the Play listing:
1. Upload screen with a video selected
2. The editor with captions burned into the preview
3. Hook / B-roll tab
4. History with finished videos
5. The finished-video screen

**Listing copy:** reuse the Play texts in `PLAY_RELEASE.md` section 5, with one
edit - remove every mention of Android or Google Play.

- Promotional text (170 chars, changeable without review)
- Description (4000 chars)
- Keywords (100 chars, comma-separated, no spaces):
  `וידאו,עריכה,כתוביות,עברית,רילס,שורטס,טיקטוק,סרטון,קליפ`
- Support URL: `https://hebrew-pipeline.app`
- Marketing URL: `https://hebrew-pipeline.app`

---

## 11. App Privacy (the nutrition label)

App Store Connect → your app → **App Privacy**. Answer for what the backend
actually stores:

| Data type | Collected | Linked to user | Used for tracking | Purpose |
|---|---|---|---|---|
| Email address | Yes | Yes | No | App Functionality (account) |
| Photos or Videos | Yes | Yes | No | App Functionality (the video you edit) |
| Audio Data | Yes | Yes | No | App Functionality (transcription) |
| Purchase History | Yes | Yes | No | App Functionality (credits) |
| Crash / Performance Data | Yes | No | No | App Functionality (error reports) |
| Identifiers | No | - | - | The app carries no ad id |

Answer **No** to "Do you or your third-party partners use data for tracking?"

---

## 12. Age rating and the review notes

**Age rating questionnaire:** all No. The app has no user-generated content
feed, no ads, no gambling, no unrestricted web access. Expect 4+.

**App Review Information** - this is the part reviewers actually read, and the
part most likely to cost you a rejection cycle if it is thin:

- Sign-in required: **Yes**
- Demo account: the review login already wired for Google Play works here too.
  Use `REVIEW_EMAIL` from the `hebpipe-review` Modal secret, and put
  `REVIEW_CODE` in the password field. **Before submitting, verify it still
  works** and that the account has credits (it is topped up to 25 on every
  login).
- Notes - paste something like:

  > Pipeline is a Hebrew video editor. Sign in with the demo email above; the
  > 6-digit code is fixed and shown in the password field (no real email is
  > sent for this address).
  >
  > To try the app: pick any short video with Hebrew speech, tap Process, and
  > the app transcribes it, cuts silences, and burns Hebrew captions. The demo
  > account has 25 credits.
  >
  > In-app purchases are consumable credit packs. Every purchase is verified
  > server-side against the App Store Server API before credits are granted.
  >
  > Account deletion is in the app: the "Delete my account" link at the bottom
  > of any screen deletes the account and all its data immediately.

---

## 13. Archive and upload

```bash
npm run sync:ios
npx cap open ios
```

In Xcode:
1. Device selector → **Any iOS Device (arm64)**. (Archive is greyed out while a
   simulator is selected.)
2. **Product → Archive** (5-10 min the first time).
3. In the Organizer window: **Distribute App → App Store Connect → Upload**.
4. Wait for the "processing complete" email (10-60 min).

Then in App Store Connect → your app → the version → pick the build, attach
the three IAPs to the submission, and **Submit for Review**.

First reviews typically take 24-48 hours.

---

## 14. If it comes back rejected

The three most likely reasons for this app, in order:

**4.2 Minimum Functionality** ("this is a website in a wrapper"). Reply in
Resolution Center pointing at the native behavior: bundled assets (no remote
URL), StoreKit purchases, push notifications, native share sheet, native file
picker, background upload, on-device video handling. Attach a screen recording
of a full edit. This is the one the bundled-assets decision was made to avoid.

**3.1.1 external purchase.** Should be impossible - the iOS build cannot render
a Play link and `tests/frontend/ios_store.spec.js` pins that. If it happens,
ask them for the screenshot; something regressed.

**2.1 needs more info / demo account did not work.** Almost always the review
login. Re-test `REVIEW_EMAIL` + `REVIEW_CODE` and reply with fresh credentials.

Answer in Resolution Center rather than resubmitting blind - a reply keeps your
place in the queue, a new submission goes to the back.

---

## Shipping updates after launch

This is where iOS differs from Android and it is easy to trip over:

- **Android** loads the live site. `vercel --prod` updates every installed app
  instantly.
- **iOS bundles its copy.** A UI change reaches iOS only through
  `npm run sync:ios` → bump Build → Archive → Upload → review.

So a frontend fix now needs: deploy Vercel (web + Android), *and* ship an iOS
build if it matters on iOS. Backend changes reach both immediately.

---

## Notes

- **Never commit** `GoogleService-Info.plist`, the `.p8` keys, or anything from
  `~/Downloads` with "AuthKey" or "SubscriptionKey" in the name. The repo is
  public.
- `ios/App/App/public/` and the copied `capacitor.config.json` are build
  artifacts and already gitignored - the committed source is `site/` plus
  `capacitor.config.js`.
- The iOS bundle id matches Android on purpose. The two stores have separate
  namespaces, so this costs nothing and keeps push/analytics consistent.
- Credits are shared across platforms: the purchase ledger sums `play:` and
  `apple:` grants for the same account, so a user who bought on Android sees
  those credits in the iOS app.
