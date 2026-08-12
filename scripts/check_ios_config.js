#!/usr/bin/env node
// Fails the iOS build if the copied Capacitor config carries a server.url.
//
// Android deliberately ships `server.url: https://hebrew-pipeline.app` so every
// `vercel --prod` reaches installed apps instantly. iOS must NOT: an app whose
// entire UI is a remote website is what App Store Guideline 4.2 calls a web
// clipping, and it is the single biggest rejection risk for a WebView app.
// capacitor.config.js already branches on the CLI target, so this is the
// second line of defence - it checks the ARTIFACT, not the intent, and catches
// a hand-edited config or a sync run through some other path.
//
// Run automatically by `npm run sync:ios`.
const fs = require('fs');
const path = require('path');

const configPath = path.join(__dirname, '..', 'ios', 'App', 'App', 'capacitor.config.json');

if (!fs.existsSync(configPath)) {
  console.error(`[ios-config] ${configPath} is missing - run "npx cap sync ios" first.`);
  process.exit(1);
}

const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const url = config.server && config.server.url;

if (url) {
  console.error(
    `[ios-config] REFUSING: the iOS bundle points at ${url}.\n` +
    '              The App Store build must serve its own bundled assets.\n' +
    '              Re-run "npm run sync:ios" and do not set CAP_PLATFORM.');
  process.exit(1);
}

const publicDir = path.join(__dirname, '..', 'ios', 'App', 'App', 'public');
if (!fs.existsSync(path.join(publicDir, 'index.html'))) {
  console.error('[ios-config] REFUSING: no bundled web assets in ios/App/App/public.');
  process.exit(1);
}

// The bundle IS the app on iOS (no remote frontend), so a stale copy ships
// stale UI no footer tag can reveal. Compare the version stamp, not mtimes.
const versionOf = (p) => {
  const m = fs.readFileSync(p, 'utf8').match(/APP_VERSION\s*=\s*['"]([^'"]+)['"]/);
  return m && m[1];
};
const siteVer = versionOf(path.join(__dirname, '..', 'site', 'app.js'));
const bundleVer = versionOf(path.join(publicDir, 'app.js'));
if (!siteVer || siteVer !== bundleVer) {
  console.error(
    `[ios-config] REFUSING: bundled app.js is ${bundleVer || 'unversioned'} but site/app.js is ${siteVer}.\n` +
    '              Re-run "npm run sync:ios" so the archive ships the current frontend.');
  process.exit(1);
}

// NativeBillingPlugin is registered by AppViewController (capacitorDidLoad +
// registerPluginInstance) because it is not an SPM package and therefore never
// appears in packageClassList - the only list the stock bridge reads. If the
// storyboard stops instantiating the subclass, IAP silently dies. Assert it.
const storyboard = fs.readFileSync(
  path.join(__dirname, '..', 'ios', 'App', 'App', 'Base.lproj', 'Main.storyboard'), 'utf8');
if (!storyboard.includes('customClass="AppViewController"')) {
  console.error(
    '[ios-config] REFUSING: Main.storyboard no longer instantiates AppViewController -\n' +
    '              NativeBillingPlugin would never register and in-app purchase dies.');
  process.exit(1);
}

console.log(`[ios-config] OK - bundled ${bundleVer}, no server.url, billing bridge wired.`);
