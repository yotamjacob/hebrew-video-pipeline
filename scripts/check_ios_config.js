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

console.log('[ios-config] OK - assets are bundled and no server.url is set.');
