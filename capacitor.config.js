// Capacitor config — JS (not JSON) because the two platforms load their web
// assets differently and the config has to branch on the target:
//
//   Android → server.url = https://hebrew-pipeline.app (the "remote frontend"
//             model, 2026-07-14): every `vercel --prod` reaches installed apps
//             instantly and a new AAB is only needed for native changes.
//   iOS     → NO server.url: the assets in `webDir` are BUNDLED into the IPA.
//             Apple's Guideline 4.2 treats an app that is only a remote URL as
//             a web clipping, which is the single biggest rejection risk for a
//             WebView app, so the App Store build ships its own copy and each
//             UI change goes out as a new build.
//
// The platform is read from the CLI argv (`cap sync ios`, `cap copy ios`, ...)
// rather than an env var on purpose: an env var that someone forgets to set
// would silently ship an iOS binary pointing at the live site, which is the
// exact failure this split exists to prevent. `CAP_PLATFORM` stays supported
// for tooling that invokes the CLI programmatically.
// `scripts/check_ios_config.js` re-asserts the result on the copied file.

const platform =
  process.env.CAP_PLATFORM ||
  (process.argv.includes('ios') ? 'ios'
    : process.argv.includes('android') ? 'android'
      : '');

const REMOTE_URL = 'https://hebrew-pipeline.app';

/** @type {import('@capacitor/cli').CapacitorConfig} */
const config = {
  appId: 'com.heb.pipeline',
  appName: 'Pipeline',
  webDir: 'site',
  backgroundColor: '#E8DFD3',
  server: {
    androidScheme: 'https',
    // iOS bundles its assets; every other target keeps the remote frontend.
    ...(platform === 'ios' ? {} : { url: REMOTE_URL }),
  },
  android: {
    allowMixedContent: false,
  },
  ios: {
    // Keeps the sand background behind the WebView during rotation/overscroll
    // instead of the default black flash.
    backgroundColor: '#E8DFD3',
    contentInset: 'never',
  },
  plugins: {
    SplashScreen: {
      launchAutoHide: false,
      backgroundColor: '#E8DFD3',
      androidSplashResourceName: 'splash',
      androidScaleType: 'CENTER_CROP',
      showSpinner: true,
      spinnerColor: '#C4703F',
    },
  },
};

module.exports = config;
