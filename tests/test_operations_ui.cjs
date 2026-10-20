// Start tests/ui_fixture_server.py first. This script mutates ONLY the fixture on 5090.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.FIA_TEST_URL || 'http://127.0.0.1:5090';
if (new URL(base).port !== '5090') throw new Error('Only run against the isolated fixture on port 5090.');
const output = process.env.FIA_SCREENSHOTS || path.join(require('node:os').tmpdir(), 'fia-operations-ui');

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    async function login(code, remember = false) {
      await page.goto(base + '/login');
      await page.locator('[name=mssv]').fill(code);
      await page.locator('[name=password]').fill('ui-test-password');
      if (remember) await page.locator('[name=remember]').check();
      await Promise.all([page.waitForURL('**/home'), page.locator('button[type=submit]').click()]);
    }
    async function logout() {
      await Promise.all([page.waitForURL('**/login'), page.locator('form.live-logout-form button').click()]);
    }
    async function post(url, values) {
      return page.evaluate(async ({ url, values }) => {
        const data = new URLSearchParams(values);
        data.set('csrf_token', document.querySelector('meta[name=csrf-token]').content);
        const response = await fetch(url, { method: 'POST', body: data });
        return { status: response.status, text: await response.text() };
      }, { url, values });
    }
    async function screens(route, name) {
      const response = await page.goto(base + route);
      assert.equal(response.status(), 200);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: width === 390 ? 844 : 1050 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), route);
        await page.screenshot({ path: path.join(output, name + '-' + width + '.png'), fullPage: true });
      }
      await page.setViewportSize({ width: 1440, height: 1050 });
    }
    await login('QA001', true);
    const deadline = new Date(Date.now() + 86400000).toISOString().slice(0, 16);
    const created = await post('/event/1/task/new', { ten_task: 'Browser notification task',
      assignee_mssv: 'QA002', ban_id: '1', deadline, mo_ta: 'Isolated browser test' });
    assert.equal(created.status, 200);
    assert.match(created.text, /Browser notification task/);
    await screens('/operations', 'inbox');
    await screens('/mail-deliveries/', 'mail');
    assert.match(await page.locator('#view').innerText(), /Đã bỏ qua/);
    await logout();
    await page.goto(base + '/profile');
    assert.match(page.url(), /\/login/); // remember cookie must have been removed
    await login('QA002');
    await screens('/notifications', 'notifications');
    assert.match(await page.locator('#view').innerText(), /Browser notification task/);
    await page.locator('form[action="/notifications/read-all"] button').click();
    assert.match(await page.locator('#view').innerText(), /0 chưa đọc/);
    assert.equal((await page.goto(base + '/operations')).status(), 403);
    await page.goto(base + '/ai');
    await page.locator('#chat-message').fill('tổng đã thu');
    await page.locator('#ai-chat-form button[type=submit]').click();
    await page.waitForFunction(() => document.querySelector('#chat-stream-status')?.getAttribute('aria-busy') === 'false'
      && document.querySelector('.chat-ai a[href$="/sources"]'));
    await page.reload();
    const source = page.locator('.chat-ai a[href$="/sources"]').last();
    assert.equal(await source.count(), 1);
    await source.click();
    assert.match(await page.locator('#view').innerText(), /SHA-256/);
    await screens(new URL(page.url()).pathname, 'sources');
    assert.deepEqual(errors, []);
    console.log('PASS: new inbox/mail/notification/source pages desktop+mobile, task notification, mark read, member denial, durable AI source, remember logout');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
