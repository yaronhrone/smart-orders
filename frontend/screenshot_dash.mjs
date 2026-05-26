import { chromium } from 'playwright';
const browser = await chromium.launch({ 
  headless: true,
  executablePath: process.env.HOME + '/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe'
});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto('http://localhost:3000');
await page.evaluate((token) => { localStorage.setItem('token', token); }, 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc5NzY0MDI3LCJpYXQiOjE3Nzk3NjA0MjcsImp0aSI6ImIxNTI3M2ZlYzlkMjQ5ZGViZTE0NGE5NmUwYzQzODRkIiwidXNlcl9pZCI6Mn0.waVorRgEG3luvXiL9015UDnXPhINz-y7pXKsslxsPTo');
await page.goto('http://localhost:3000/dashboard');
await page.waitForLoadState('networkidle');
await page.screenshot({ path: 'dashboard.png', fullPage: true });
await browser.close();
console.log('done');
