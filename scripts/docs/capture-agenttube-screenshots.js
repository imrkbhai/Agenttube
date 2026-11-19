#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const sharp = require('sharp');

const ROOT = path.resolve(__dirname, '..', '..');
const OUT_DIR = path.join(ROOT, 'assets', 'screenshots');
const DASHBOARD_URL = (process.env.AGENTTUBE_DASHBOARD_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
const MAX_BYTES = 400 * 1024;
const DEMO_CREATORS = [{
  id: 'demo-creator',
  name: 'Example Creator',
  channel_url: 'https://www.youtube.com/@examplecreator',
  permission: 'Permission recorded',
  niche: 'Gaming'
}];
const DEMO_JOBS = [
  {
    id: 'demo-2026-02',
    created_at: '2026-11-20T10:30:00Z',
    kind: 'video',
    source_url: 'https://www.youtube.com/watch?v=example',
    title: 'The best moments from this week',
    creator: 'Example Creator',
    state: 'running',
    stage: "Transcribing on this computer (whisper 'small')",
    counts: { rejected: 0, pending_review: 0, approved: 0, total: 0 }
  },
  {
    id: 'demo-2026-01',
    created_at: '2026-11-19T15:15:00Z',
    kind: 'video',
    source_url: 'https://www.youtube.com/watch?v=example2',
    title: 'A surprising moment in the final round',
    creator: 'Example Creator',
    state: 'finished',
    stage: 'Done: 6 clips ready for review',
    counts: { rejected: 0, pending_review: 0, approved: 6, total: 6 }
  }
];

async function waitForDashboard() {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${DASHBOARD_URL}/api/creators`);
      if (response.ok) return;
    } catch {
      // Retry while the local dashboard starts.
    }
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`Agenttube dashboard is not reachable at ${DASHBOARD_URL}. Start it with npm run clip:dashboard first.`);
}

async function save(page, name) {
  const file = path.join(OUT_DIR, name);
  await page.screenshot({ path: file, type: 'png', fullPage: true });
  const size = fs.statSync(file).size;
  if (size > MAX_BYTES) {
    const buffer = await sharp(file)
      .resize({ width: 1440, withoutEnlargement: true })
      .png({ compressionLevel: 9, palette: true, quality: 90 })
      .toBuffer();
    fs.writeFileSync(file, buffer);
  }
  console.log(`${name}: ${(fs.statSync(file).size / 1024).toFixed(0)} KB`);
}

async function main() {
  await waitForDashboard();
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, colorScheme: 'dark' });
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route('**/api/creators', route => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(DEMO_CREATORS)
    }));
    await page.route('**/api/jobs', route => route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(DEMO_JOBS)
    }));
    await page.goto(DASHBOARD_URL, { waitUntil: 'domcontentloaded' });
    await page.locator('#creators .creator').first().waitFor({ state: 'visible', timeout: 10000 });

    await save(page, 'overview.png');
    await page.locator('.studio-subnav button[data-studio-view="videos"]').click();
    await page.locator('#jobs tr.job').first().waitFor({ state: 'visible', timeout: 10000 });
    await save(page, 'review.png');
    await page.locator('.studio-subnav button[data-studio-view="home"]').click();
    await page.setViewportSize({ width: 390, height: 844 });
    await save(page, 'operator.png');

    if (pageErrors.length) throw new Error(`Browser errors: ${pageErrors.join('; ')}`);
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error.message);
  process.exit(1);
});
