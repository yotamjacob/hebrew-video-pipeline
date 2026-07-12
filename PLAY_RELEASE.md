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
3. Release name: `1.0 (1)`. Add brief release notes (e.g. "First internal build").
4. **Save** → **Review release** → **Start rollout to Internal testing**.
5. Under **Testers**, create an email list (add your own Gmail + testers), save.
6. Copy the **"Copy link"** join URL — open it on each tester's phone, accept,
   then install via the Play Store. No more sideloading.

## 4. Store listing (Main store listing)

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
- **Phone screenshots:** at least 2 (see step 5).

## 5. Screenshots (2–8 phone shots)

On your phone, in the app, capture these and transfer to your computer:
1. Upload screen, 2. Caption editor, 3. Options/toggles, 4. Result/download,
5. History. (Power + Volume-Down takes an Android screenshot.)
Play wants PNG/JPEG, 16:9 or 9:16, each side 320–3840 px — phone screenshots
qualify as-is.

## 6. Policy sections (App content — all required)

- **Privacy policy URL:** `https://site-theta-six-76.vercel.app/legal.html`
- **Data safety:** declare what the app collects. For this app:
  - **Personal info → Email address** — collected, for account
    management/app functionality; encrypted in transit; not shared; not for ads.
  - **App activity / Files → User videos & audio** — uploaded for processing
    (app functionality); not shared with third parties (except Metricool only
    when a user explicitly connects it to schedule a post).
  - **Passwords** — collected, stored hashed. Encrypted in transit.
  - No location, no advertising ID, no third-party analytics.
  - Users can request account/data deletion (state your support email).
- **Content rating:** fill the questionnaire → this app rates **Everyone**.
- **Target audience:** 18+ (or 13+); it is not directed at children.
- **Ads:** No.
- **Government app:** No. **Financial features:** No.

## 7. Roll out

Internal testing goes live within minutes (no full review). When you're ready
for the public, promote the same release: **Testing → Closed/Open testing** or
**Production → Create release** (production gets a full Google review, often a
few days for a first submission).

---

## Notes

- The app is a Capacitor shell around the web frontend in `site/`, calling the
  Modal API remotely. To update the app's UI you rebuild + upload a new AAB
  (bump `versionCode`). See `capacitor.config.json` and `CLAUDE.md`.
- Planned native upgrades (separate versions): background uploads, push
  notifications (needs a Firebase project), native share.
