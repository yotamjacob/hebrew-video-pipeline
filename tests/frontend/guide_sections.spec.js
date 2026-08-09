// New-feature guide sections (profiles, effects) and their info-dots
// (2026-08-09), including the context-aware editor dot.
const { test, expect } = require('@playwright/test');
const { mockAllApis, bootApp } = require('./helpers');

test('profiles + effects guide sections render with localized bodies', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await page.click('#tabGuide');
  await page.click('#gsec-profiles .guide-sec-head');
  await expect(page.locator('#gsec-profiles .guide-sec-text')).toContainText('פרופיל');
  await page.click('#gsec-effects .guide-sec-head');
  await expect(page.locator('#gsec-effects .guide-sec-text')).toContainText('זום');
});

test('the profiles info-dot deep-links to its guide section', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await page.click('#profileRow .info-dot');
  await expect(page.locator('#guideView')).toBeVisible();
  await expect(page.locator('#gsec-profiles')).toHaveClass(/open/);
});

test('the editor info-dot opens the guide for the ACTIVE editor tab', async ({ page }) => {
  await bootApp(page);
  await mockAllApis(page);
  await page.evaluate(() => { switchEditorTab('effects'); openGuideEditorSection(); });
  await expect(page.locator('#guideView')).toBeVisible();
  await expect(page.locator('#gsec-effects')).toHaveClass(/open/);
  await page.evaluate(() => { returnFromGuide(); switchEditorTab('captions'); openGuideEditorSection(); });
  await expect(page.locator('#guideView')).toBeVisible();
  await expect(page.locator('#gsec-captions')).toHaveClass(/open/);
});
