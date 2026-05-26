import { chromium } from 'playwright';
const browser = await chromium.launch({ 
  headless: true,
  executablePath: process.env.HOME + '/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe'
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto('http://localhost:3000/login');
await page.waitForLoadState('networkidle');
await page.screenshot({ path: 'login.png' });
await page.fill('input[type="email"]', 'test@test.com');
await page.fill('input[type="password"]', 'testpass');
await page.screenshot({ path: 'login_filled.png' });
await browser.close();
console.log('done');
