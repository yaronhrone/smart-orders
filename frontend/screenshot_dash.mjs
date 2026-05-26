import { chromium } from 'playwright';
const browser = await chromium.launch({ 
  headless: true,
  executablePath: process.env.HOME + '/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe'
});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// Set token in localStorage before navigating
await page.goto('http://localhost:3000');
await page.evaluate((token) => {
  localStorage.setItem('token', token);
}, 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc5NzYxNzU1LCJpYXQiOjE3Nzk3NTgxNTUsImp0aSI6ImY5YzQ2Y2JmYTZjMzRiMjViOGQxY2NhZjFmNTFlOTliIiwidXNlcl9pZCI6Mn0.dyhFHHQlC1trLnYG9TeUSxJfkPPedqcgIANcLFiXDDk');

await page.goto('http://localhost:3000/dashboard');
await page.waitForLoadState('networkidle');
await page.screenshot({ path: 'dashboard.png', fullPage: true });

await browser.close();
console.log('done');
