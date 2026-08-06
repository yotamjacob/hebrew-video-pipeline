# Google Play release guide — פייפליין (`com.heb.pipeline`)

Everything you need to get the app into **internal testing** on Google Play,
then to production. Work top to bottom.

---

## 0. CRITICAL — back up your signing key first

The app is signed with an **upload keystore** that lives only on this Mac:

| Item | Value |
|------|-------|
| Keystore file | `android/pipeline-upload.jks` |
| Key alias | `upload` |
| Store/key password | see `android/keystore.properties` (local, gitignored) — save it in your password manager |
| Cert SHA-256 | `3F:55:C6:20:5D:34:67:9D:00:FD:F2:E9:A4:E4:5B:CE:CE:F3:B8:99:07:5E:6B:F4:1D:C5:2A:9C:E8:79:53:F2` |

> This guide is committed to a **public** repo, so the actual password is **not**
> written here. It lives in `android/keystore.properties` (gitignored) and in the
> chat where the key was generated. Copy it into your password manager now.

**Do this now:**
1. Copy `android/pipeline-upload.jks` somewhere safe and off this machine
   (password manager attachment, private cloud drive, encrypted USB).
2. Save the two passwords in your password manager.

The keystore + `android/keystore.properties` are **gitignored** — they are NOT in
the repo and never will be. If this Mac dies and you have no backup, and you did
**not** enroll in Play App Signing (step 2), you can never update the app again.
With Play App Signing enrolled (recommended, default), a lost *upload* key is
recoverable via Google support — but back it up anyway.

---

## 1. Files to upload (already on this Mac)

| Purpose | Path |
|---------|------|
| **App bundle (AAB)** — this is what you upload to Play | `android/app/build/outputs/bundle/release/app-release.aab` |
| **App icon** 512×512 | `assets/play-icon-512.png` |
| **Feature graphic** 1024×500 | `assets/play-feature-1024x500.png` |
| Phone screenshots | capture from the app (see step 5) |

Rebuild the AAB anytime after code changes:
```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="$HOME/Library/Android/sdk"
npx cap sync android && (cd android && ./gradlew bundleRelease)
```
Bump `versionCode` (and usually `versionName`) in `android/app/build.gradle`
before every new upload — Play rejects a duplicate `versionCode`.

---

## 2. Create the app in Play Console

1. Go to https://play.google.com/console → **Create app**.
2. App name: **פייפליין**. Default language: **Hebrew (עברית)**. Type: **App**.
   Free. Accept the declarations.
3. When you upload your first bundle, Play will offer **Play App Signing** —
   **accept it** (Google holds the real app-signing key; your `.jks` is just the
   upload key). This is the default and strongly recommended.

## 3. Upload the bundle to Internal testing

1. Left nav → **Testing → Internal testing** → **Create new release**.
2. Upload `app-release.aab`.
3. Release name: `1.3.0 (7)`. Add brief release notes (e.g. "Google Play credit packs").
4. **Save** → **Review release** → **Start rollout to Internal testing**.
5. Under **Testers**, create an email list (add your own Gmail + testers), save.
6. Copy the **"Copy link"** join URL — open it on each tester's phone, accept,
   then install via the Play Store. No more sideloading.

## 4. Configure Google Play credit packs

The Android app sells **consumable, non-expiring video credits**. Product IDs
are immutable and must match the app exactly:

| Product ID | Credits | Price | Per video |
|------------|---------|-------|-----------|
| `pipeline_credits_10` | 10 | ₪59 | ₪5.90 |
| `pipeline_credits_30` | 30 | ₪149 | ₪4.97 |
| `pipeline_credits_100` | 100 | ₪399 | ₪3.99 |

The `_5` / `_20` / `_50` IDs were retired on 2026-07-31 but remain in
`PLAY_CREDIT_PRODUCTS` and `BILLING_CREDITS`, so a purchase of one still grants
while it is live. Deactivate them in Play once the new ladder is published;
past purchases are unaffected either way, since each grant stores its credit
count at purchase time.

**Credits are not a flat per-video price.** One credit covers 10 minutes of
source; a longer video costs 2, and the 4K upscale adds 1 (and is refused above
3 minutes of source, where it would outrun the job timeout). See `_credit_cost`
in `pipeline_core.py`.

1. Play Console → **Monetize with Play → Products → One-time products**.
2. Create each ID above as a consumable one-time product, add its Hebrew and
   English name/description, configure the purchase option and regions/prices,
   then activate it. The app reads Play's localized price at runtime; prices
   are not hardcoded in the frontend.
3. Play Console → **Users and permissions**: add the service-account email used
   by the backend and grant purchase/order access, including **View financial
   data, orders, and cancellation survey responses**, for this app.
4. The Modal `hebpipe-fcm` secret already mounts `FCM_SERVICE_ACCOUNT`. Either
   grant that service account the Play permission above, or add a dedicated
   least-privileged JSON key to the same secret as
   `PLAY_SERVICE_ACCOUNT_JSON`. Do not remove the existing FCM value.
5. Add tester Gmail accounts under **Settings → License testing**. Billing only
   works reliably in a build installed from a Play test/production track, not
   a locally sideloaded APK.

The server verifies every token with Android Publisher API, binds it to the
signed-in account, writes one idempotent grant to `hebpipe-purchases`, and only
then consumes the Play purchase. Pending purchases receive no credits until
Play reports `PURCHASED`. Reopening the app restores completed purchases if the
original callback was interrupted.

Before production, test all three packs with a Play license tester, including
cancel, pending payment, app-close-after-payment, and a second purchase of the
same consumable.

## 5. Store listing (Main store listing)

- **App name:** פייפליין
- **Short description (≤80 chars):**
  `עריכת וידאו בלחיצת כפתור: כתוביות בעברית, חיתוך שתיקות וקליפים`
- **Full description (draft — edit freely):**
  ```
  פייפליין הופך עריכת וידאו לפעולה של לחיצת כפתור.
  מעלים סרטון, ומקבלים בחזרה גרסה ערוכה: כתוביות בעברית מדויקות,
  חיתוך אוטומטי של שתיקות והיסוסים, הוק פותח, וקליפי בי-רול.
  מתאים ליוצרי תוכן, מרצים ובעלי עסקים שרוצים תוצאה מהירה ומקצועית.
  ```
- **App icon:** `assets/play-icon-512.png`
- **Feature graphic:** `assets/play-feature-1024x500.png`
- **Phone screenshots:** at least 2 (see step 6).

## 6. Screenshots (2–8 phone shots)

On your phone, in the app, capture these and transfer to your computer:
1. Upload screen, 2. Caption editor, 3. Options/toggles, 4. Result/download,
5. History. (Power + Volume-Down takes an Android screenshot.)
Play wants PNG/JPEG, 16:9 or 9:16, each side 320–3840 px — phone screenshots
qualify as-is.

## 7. Policy sections (App content — all required)

- **Privacy policy URL:** `https://hebrew-pipeline.app/legal.html`
- **Data safety:** declare what the app collects. For this app:
  - **Personal info → Email address** — collected, for account
    management/app functionality; encrypted in transit; not shared; not for ads.
  - **App activity / Files → User videos & audio** — uploaded for processing
    (app functionality); not shared with third parties (except Metricool only
    when a user explicitly connects it to schedule a post).
  - **Passwords** — collected, stored hashed. Encrypted in transit.
  - No location, no advertising ID, no third-party analytics.
  - Users can request account/data deletion. **Account deletion URL** (put this
    in Data safety → "Data deletion" → *provide a URL*):
    `https://hebrew-pipeline.app/delete-account.html`
- **Content rating:** fill the questionnaire → this app rates **Everyone**.
- **Target audience:** 18+ (or 13+); it is not directed at children.
- **Ads:** No.
- **Government app:** No. **Financial features:** No.

## 8. Roll out

Internal testing goes live within minutes (no full review). When you're ready
for the public, promote the same release: **Testing → Closed/Open testing** or
**Production → Create release** (production gets a full Google review, often a
few days for a first submission).

---

## 9. Production access (the 12-tester / 14-day requirement)

This account must earn production access before it can publish publicly.
**Internal testing does not count.** Only a **Closed testing** track does, and
the clock only runs while at least **12 testers are opted in continuously for
14 days**. One uninstall breaks "continuous" and restarts it — recruit 18-20.

Production access was **rejected once (2026-08-01)** on two grounds: testers
were not active enough, and "the app was not ready". What each means here:

**Testers not active enough.** Google reads real engagement, not the opt-in
count. So:
- Testers must install from the **closed-track opt-in link**, via the Play
  Store. A sideloaded APK produces no Play telemetry, so a tester who used the
  app daily still looks inactive — and Play Billing won't work either.
- Give each tester a concrete weekly task (upload a real video, edit the
  captions, export it), not "try it out". Sessions are what's measured.
- Ship 2-3 closed-track releases during the 14 days. A build that never changes
  reads as abandoned; Google wants to see feedback turn into changes.
- Collect the feedback in writing. The `.footer-whatsapp` link in every footer
  is the channel — keep a log of what was said and what shipped in response.

**App not ready.** The usual cause is a reviewer who cannot get in or cannot
finish a run:
- **Reviewer login (fixed 2026-08-01).** Sign-in is passwordless — Google, or a
  6-digit code we email — and a reviewer can use neither, which is very likely
  what "not ready" meant. So ONE address signs in with a **fixed code**, held in
  the `hebpipe-review` Modal secret as `REVIEW_EMAIL` + `REVIEW_CODE`. This repo
  is public, so the values are NOT written here — read them from the password
  manager, or rotate with:
  ```bash
  modal secret create --force hebpipe-review REVIEW_EMAIL=… REVIEW_CODE=…
  ```
  Put that email and code in **App content → App access** as the credentials,
  with instructions: *"Choose 'I have an account', enter the email, then enter
  this code."* Either lane works and no mail is involved.
  The account is a normal user (never an admin — the Admin tab lists every
  user's email) and gets `REVIEW_VIDEO_LIMIT` = 25 credits **topped up on every
  login**, so it cannot strand the reviewer at zero. Blank `REVIEW_CODE` to turn
  the login off once production access is granted; a code under 6 characters is
  refused, so blanking is a real off switch. Tests: `tests/backend/test_review_login.py`.
- Signup is **open** (the invite code was removed 2026-08-01 for the same
  reason), so nothing blocks account creation either.
- Walk the whole flow on a clean install first: sign in, upload, edit captions,
  export, download. Any crash or dead end reads as "not ready".

**Writing the application.** The form is scored on substance, and the app's own
admin data is the best source: `/admin/users` has `videos_used` per account,
`/admin/costs` has per-video compute, `/admin/errors` has field failures. Answer
with counts and named fixes ("14 testers, 11 processed at least one video, 63
videos total, 3 bugs reported and fixed in 1.3.2 and 1.3.3"), not prose.

---

## Notes

- The app is a Capacitor shell that loads the deployed web frontend at
  `https://hebrew-pipeline.app` and calls the Modal API remotely. Web-only UI
  changes deploy immediately; native Java/plugin changes require a rebuilt AAB
  and a new `versionCode`. See `capacitor.config.json` and `CLAUDE.md`.
- Native Billing requires Android binary **1.3.0 (versionCode 7)** or newer.
  Older installed shells load the live website but do not expose the purchase
  bridge, so they keep the web contact flow until upgraded from Play.
- **Next release ships `ParallelUploader`** (`ParallelUploaderPlugin.java` +
  `ParallelUploadService.java`, registered in `MainActivity`, service +
  WAKE_LOCK in the manifest): parallel multipart upload to presigned R2 part
  URLs inside a foreground service - closes the mobile/desktop upload speed
  gap (1 connection ≈ 9 Mbps vs 5 connections ≈ uplink ~20 Mbps). The web
  frontend already prefers it and falls back to the stock uploader on shells
  without it, so no coordination is needed: just build + roll out. Compiled
  clean 2026-08-06; not yet exercised on a physical device - verify one real
  upload (progress notification, lock the screen mid-upload, and a Start Over
  cancel) on the internal track before promoting.
