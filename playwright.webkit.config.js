// TEMPORARY compat-audit config - real WebKit over the core user journeys.
const base = require('./playwright.config.js');
module.exports = { ...base, projects: [{ name: 'WebKit iPhone', use: { ...require('@playwright/test').devices['iPhone 13'] } }] };
