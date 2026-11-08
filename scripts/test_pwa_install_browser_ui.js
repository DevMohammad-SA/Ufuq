/* Native Chromium UI checks, fabricated forms only; no app URLs, OS install or database.
 * Run: NODE_PATH=/tmp/rahhal-mobile-ui-qa/node_modules node scripts/test_pwa_install_browser_ui.js
 * Uses existing test-only Playwright and /usr/bin/chromium. No dependency installation.
 */
'use strict';
const nativeAssert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'static/js/pwa-install.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/css/pwa-install.css'), 'utf8');
const partial = fs.readFileSync(path.join(root, 'templates/pwa/_install.html'), 'utf8');
const templates = {
  home: 'templates/home.html', app: 'participants/templates/participants/app_base.html', auth: 'accounts/templates/base.html',
};
const baseCss = Object.fromEntries(Object.entries(templates).map(([key, filename]) => {
  const source = fs.readFileSync(path.join(root, filename), 'utf8');
  return [key, source.match(/<style>([\s\S]*?)<\/style>/)[1].replace(/{%[^%]*%}/g, '')];
}));
const font = weight => fs.readFileSync(path.join(root, `static/fonts/tajawal/Tajawal-${weight === 700 ? 'Bold' : 'Regular'}.ttf`)).toString('base64');
const fonts = [400, 700].map(weight => `@font-face{font-family:Tajawal;font-style:normal;font-weight:${weight};src:url(data:font/ttf;base64,${font(weight)}) format('truetype');font-display:swap}`).join('\n');
const selectors = {
  root: '[data-pwa-install]', button: '[data-pwa-install-button]', summary: '[data-pwa-install-summary]',
  help: '[data-pwa-install-help]', instructions: '[data-pwa-install-instructions]', status: '[data-pwa-install-status]',
};
let cases = 0;
let assertions = 0;
let blockedRequests = 0;
const requests = [];
const runtimeErrors = [];
const failures = [];
const assert = new Proxy(nativeAssert, {
  get(target, key) {
    return typeof target[key] === 'function' ? (...args) => { assertions += 1; return target[key](...args); } : target[key];
  },
});
const form = '<form id="synthetic-form" method="post" action="https://synthetic.invalid/never-submit"><div class="field"><label for="synthetic-draft">مسودة مصطنعة فقط</label><textarea id="synthetic-draft" name="draft">اختيارات غير محفوظة — لا بيانات حساب حقيقية</textarea></div><input type="hidden" name="csrfmiddlewaretoken" value="SYNTHETIC-not-a-real-CSRF"><button type="submit" id="synthetic-submit" class="btn btn-gold">حفظ مصطنع</button></form>';
function content(surface) {
  if (surface === 'auth') return `<div class="auth-shell"><div class="auth-side">رحّال — جانب مصطنع</div><div class="auth-form-side">${form}${partial}</div></div>`;
  if (surface === 'app') return `<header class="app-topbar">رحّال — تطبيق مصطنع</header><aside class="app-sidebar">قائمة مصطنعة</aside><main class="wrap">${partial}${form}</main>`;
  return `<main class="wrap"><section class="hero"><div class="in"><h1>رحّال — مثال واجهة مصطنع</h1></div></section>${partial}<div class="stat-strip"><div class="card">إحصاءات مصطنعة</div></div>${form}</main>`;
}
function hooks(options) {
  return `
window.__pwaQA = {calls: [], submissions: 0, resets: 0, forbidden: [], media: new EventTarget(), lastEvent: null};
const qa = window.__pwaQA;
qa.media.matches = ${Boolean(options.standalone)};
const nativeMatchMedia = window.matchMedia.bind(window);
window.matchMedia = query => query === '(display-mode: standalone)' ? qa.media : nativeMatchMedia(query);
Object.defineProperty(navigator, 'standalone', {configurable: true, value: ${Boolean(options.iosStandalone)}});
qa.changeMode = matches => { qa.media.matches = matches; qa.media.dispatchEvent(new Event('change')); };
qa.dispatchPrompt = mode => {
  const event = new Event('beforeinstallprompt', {cancelable: true});
  if (mode !== 'unusable') {
    event.prompt = () => {
      qa.calls.push({userActivation: navigator.userActivation.isActive});
      if (mode === 'throw') throw new Error('Synthetic prompt error');
      if (mode === 'reject') return Promise.reject(new Error('Synthetic prompt rejection'));
      return Promise.resolve();
    };
    event.userChoice = new Promise((resolve, reject) => { qa.resolve = resolve; qa.reject = reject; });
  }
  qa.lastEvent = event;
  window.dispatchEvent(event);
  return event.defaultPrevented;
};
for (const name of ['fetch', 'XMLHttpRequest', 'localStorage', 'sessionStorage', 'caches', 'Notification', 'PushManager']) {
  Object.defineProperty(window, name, {configurable: true, get() { qa.forbidden.push(name); throw new Error('Forbidden fixture API: ' + name); }});
}
HTMLFormElement.prototype.submit = function () { throw new Error('Native POST forbidden'); };
HTMLFormElement.prototype.requestSubmit = function () { throw new Error('Native requestSubmit forbidden'); };
document.addEventListener('submit', event => { qa.submissions += 1; event.preventDefault(); }, true);
document.addEventListener('reset', event => { qa.resets += 1; event.preventDefault(); }, true);
`;
}
function fixtureHtml(surface, options = {}) {
  return `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SYNTHETIC PWA UI — never production</title><style>${fonts}\n${baseCss[surface]}\n${css}\n/* Stress hidden against a late host control display rule. */\n.pwa-install__button {display:inline-flex} textarea {box-sizing:border-box;max-width:100%;width:100%}</style></head><body>${content(surface)}${options.noJS ? '' : `<script>${hooks(options)}\n${script}</script>`}</body></html>`;
}
async function mount(page, surface = 'app', options = {}) {
  // setContent alone retains window globals/listeners: a blank document isolates each case.
  // about:blank makes no network request and is never an application/production URL.
  await page.goto('about:blank');
  await page.setContent(fixtureHtml(surface, options), {waitUntil: 'load'});
  await page.evaluate(() => document.fonts.ready);
  return snapshot(page);
}
async function snapshot(page) {
  return page.locator('#synthetic-form').evaluate(form => ({
    html: form.outerHTML,
    controls: [...form.elements].map(node => ({name: node.name, value: node.value, type: node.type, disabled: node.disabled})),
  }));
}
async function state(page) {
  return page.evaluate(() => ({
    calls: window.__pwaQA.calls, submissions: window.__pwaQA.submissions, resets: window.__pwaQA.resets,
    forbidden: window.__pwaQA.forbidden, hidden: document.querySelector('[data-pwa-install]').hidden,
    status: document.querySelector('[data-pwa-install-status]').textContent,
    open: document.querySelector('[data-pwa-install-help]').open,
    disabled: document.querySelector('[data-pwa-install-button]').disabled,
    focus: document.activeElement.hasAttribute('data-pwa-install-instructions'),
  }));
}
async function unchanged(page, before) {
  assert.deepEqual(await snapshot(page), before, 'Application does not read/reset/mutate synthetic form controls');
  const s = await state(page);
  assert.equal(s.submissions, 0); assert.equal(s.resets, 0); assert.deepEqual(s.forbidden, []);
}
async function geometry(page) {
  const result = await page.locator(selectors.root).evaluate(component => {
    const rect = component.getBoundingClientRect();
    const targets = [...component.querySelectorAll('button, summary, a')].filter(node => node.getClientRects().length).map(node => {
      const r = node.getBoundingClientRect();
      return {tag: node.tagName, width: r.width, height: r.height, left: r.left, right: r.right};
    });
    const style = getComputedStyle(component);
    return {viewport: innerWidth, documentWidth: document.documentElement.scrollWidth, componentWidth: component.clientWidth,
      contentWidth: component.scrollWidth, left: rect.left, right: rect.right, position: style.position,
      direction: style.direction, background: style.backgroundColor, font: style.fontFamily, targets};
  });
  assert.ok(result.documentWidth <= result.viewport + 1, `Page horizontal overflow: ${JSON.stringify(result)}`);
  assert.ok(result.left >= -1 && result.right <= result.viewport + 1, `Component exceeds content/viewport: ${JSON.stringify(result)}`);
  assert.ok(result.contentWidth <= result.componentWidth + 1, `Component overflow: ${JSON.stringify(result)}`);
  assert.equal(result.position, 'static'); assert.equal(result.direction, 'rtl'); assert.equal(result.background, 'rgb(255, 255, 255)');
  assert.match(result.font, /Tajawal/);
  for (const target of result.targets) {
    assert.ok(target.height >= 43.9, `Control below 44px: ${JSON.stringify(target)}`);
    assert.ok(target.width >= 43.9, `Control narrower than 44px: ${JSON.stringify(target)}`);
    assert.ok(target.left >= result.left - 1 && target.right <= result.right + 1, `Control outside component: ${JSON.stringify(target)}`);
  }
}
async function run(name, callback) {
  cases += 1;
  try { await callback(); console.log(`ok ${cases} - ${name}`); }
  catch (error) { failures.push({name, message: error.message}); console.error(`not ok ${cases} - ${name}\n${error.stack}`); }
}

(async () => {
  const browser = await chromium.launch({executablePath: '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  try {
    for (const width of [360, 390, 1280]) {
      const context = await browser.newContext({viewport: {width, height: 900}, serviceWorkers: 'block'});
      const page = await context.newPage();
      page.setDefaultTimeout(5000);
      await context.route('**/*', route => {
        const request = route.request(); requests.push({url: request.url(), method: request.method()}); blockedRequests += 1;
        return route.abort('blockedbyclient');
      });
      page.on('pageerror', error => runtimeErrors.push(error.message));
      const noJSContext = await browser.newContext({viewport: {width, height: 900}, javaScriptEnabled: false, serviceWorkers: 'block'});
      await noJSContext.route('**/*', route => {
        const request = route.request(); requests.push({url: request.url(), method: request.method()}); blockedRequests += 1;
        return route.abort('blockedbyclient');
      });
      const noJSPage = await noJSContext.newPage(); noJSPage.setDefaultTimeout(5000);
      for (const surface of ['home', 'app', 'auth']) {
        const prefix = `${width}px/${surface}`;
        await run(`${prefix}: compact white RTL component, actual partial, native controls outside POST form`, async () => {
          const before = await mount(page, surface);
          assert.equal(await page.locator(selectors.root).count(), 1);
          assert.equal(await page.locator(selectors.button).isVisible(), true);
          assert.equal(await page.locator(selectors.button).getAttribute('type'), 'button');
          assert.equal(await page.locator(selectors.summary).textContent(), 'طريقة التثبيت');
          assert.equal(await page.locator(selectors.help).evaluate(node => node.open), false);
          assert.equal(await page.locator(selectors.status).isVisible(), false);
          assert.equal(await page.locator(selectors.button).evaluate(node => node.form), null);
          assert.equal(await page.locator(selectors.root).evaluate(node => node.closest('form')), null);
          assert.equal(await page.locator('#synthetic-form [data-pwa-install]').count(), 0);
          const s = await state(page); assert.equal(s.calls.length, 0); assert.equal(s.hidden, false);
          await geometry(page); await unchanged(page, before);
        });
        await run(`${prefix}: native button/manual details focus, Tab and summary Space never submit/reset`, async () => {
          const before = await mount(page, surface);
          await page.locator(selectors.button).click();
          let s = await state(page); assert.equal(s.open, true); assert.equal(s.focus, true); assert.equal(s.calls.length, 0);
          assert.match(s.status, /خطوات المتصفح/); assert.doesNotMatch(s.status, /تم التثبيت|تم الحفظ/);
          await page.locator(selectors.summary).focus(); await page.keyboard.press('Space');
          assert.equal((await state(page)).open, false);
          await page.keyboard.press('Enter'); assert.equal((await state(page)).open, true);
          await page.keyboard.press('Tab');
          assert.equal(await page.evaluate(() => document.activeElement.hasAttribute('data-pwa-open-site')), true);
          await page.keyboard.press('Shift+Tab');
          assert.equal(await page.evaluate(() => document.activeElement.hasAttribute('data-pwa-install-summary')), true);
          await page.locator(selectors.button).focus(); await page.keyboard.press('Enter');
          assert.equal((await state(page)).focus, true);
          const link = page.locator('[data-pwa-open-site]');
          assert.equal(await link.getAttribute('href'), '/'); assert.equal(await link.getAttribute('target'), '_blank');
          assert.equal(await link.getAttribute('rel'), 'noopener noreferrer');
          for (const platform of ['Android', 'iPhone', 'Safari', 'Chrome', 'Edge', 'إنترنت']) assert.ok((await page.locator(selectors.instructions).textContent()).includes(platform));
          await geometry(page); await unchanged(page, before);
        });
        await run(`${prefix}: long Arabic/unbroken copy and larger text wrap without horizontal scroll`, async () => {
          const before = await mount(page, surface);
          await page.locator(selectors.button).click();
          await page.locator(selectors.root).evaluate(component => {
            const long = 'رحّال أقرب إليك — تعليمات عربية طويلة للاختبار فقط '.repeat(5);
            component.querySelector('.pwa-install__intro').textContent = long;
            component.querySelector('[data-pwa-install-button]').textContent = 'تثبيت رحّال '.repeat(5);
            component.querySelector('[data-pwa-install-summary]').textContent = 'طريقة التثبيت والإضافة إلى الشاشة الرئيسية '.repeat(4);
            component.querySelector('li').textContent += ' رابط_مصطنع_طويل_للاختبار_دون_شبكة'.repeat(20);
            component.style.fontSize = '28px';
          });
          await geometry(page); await unchanged(page, before);
        });
        await run(`${prefix}: JavaScript disabled, only native summary CTA, Enter/Space reveals all instructions`, async () => {
          await mount(noJSPage, surface, {noJS: true});
          const before = await snapshot(noJSPage);
          assert.equal(await noJSPage.locator(selectors.button).isVisible(), false);
          assert.equal(await noJSPage.locator(selectors.summary).textContent(), 'تثبيت رحّال');
          assert.equal(await noJSPage.locator(selectors.instructions).isVisible(), false);
          await noJSPage.locator(selectors.summary).focus(); await noJSPage.keyboard.press('Enter');
          assert.equal(await noJSPage.locator(selectors.help).evaluate(node => node.open), true);
          assert.equal(await noJSPage.locator(selectors.instructions).isVisible(), true);
          await geometry(noJSPage);
          await noJSPage.keyboard.press('Space');
          assert.equal(await noJSPage.locator(selectors.help).evaluate(node => node.open), false);
          assert.deepEqual(await snapshot(noJSPage), before);
          assert.equal(await noJSPage.locator(selectors.root).evaluate(node => node.closest('form')), null);
        });
      }
      await run(`${width}px: native click activates synthetic prompt once; pending repeat, cancel and new accepted event`, async () => {
        const before = await mount(page);
        assert.equal(await page.evaluate(() => window.__pwaQA.dispatchPrompt('normal')), true);
        assert.equal((await state(page)).calls.length, 0);
        await page.locator(selectors.button).click();
        let s = await state(page); assert.equal(s.calls.length, 1); assert.equal(s.calls[0].userActivation, true);
        assert.equal(s.disabled, true);
        await page.locator(selectors.button).dispatchEvent('click'); assert.equal((await state(page)).calls.length, 1);
        await page.evaluate(() => window.__pwaQA.resolve({outcome: 'dismissed'}));
        s = await state(page); assert.equal(s.disabled, false); assert.equal(s.open, true); assert.equal(s.focus, true);
        assert.doesNotMatch(s.status, /تم التثبيت/);
        await page.evaluate(() => window.dispatchEvent(window.__pwaQA.lastEvent));
        await page.locator(selectors.button).click(); assert.equal((await state(page)).calls.length, 1);
        await page.evaluate(() => window.__pwaQA.dispatchPrompt('normal'));
        await page.locator(selectors.button).click();
        await page.evaluate(() => window.__pwaQA.resolve({outcome: 'accepted'}));
        s = await state(page); assert.equal(s.calls.length, 2); assert.equal(s.hidden, false); assert.equal(s.disabled, false);
        assert.match(s.status, /وافق المتصفح.*أكمل خطوات التثبيت/); assert.doesNotMatch(s.status, /تم التثبيت/);
        await geometry(page); await unchanged(page, before);
      });
      await run(`${width}px: appinstalled clears pending and hides invite despite late accepted choice/duplicate init`, async () => {
        const before = await mount(page);
        await page.evaluate(() => window.__pwaQA.dispatchPrompt('normal'));
        await page.locator(selectors.button).click();
        await page.evaluate(() => window.dispatchEvent(new Event('appinstalled')));
        assert.equal(await page.locator(selectors.root).isVisible(), false);
        await page.evaluate(() => window.__pwaQA.resolve({outcome: 'accepted'}));
        await page.addScriptTag({content: script});
        await page.locator(selectors.button).dispatchEvent('click');
        const s = await state(page); assert.equal(s.hidden, true); assert.equal(s.disabled, false);
        assert.equal(s.calls.length, 1); assert.equal(s.status, '');
        await unchanged(page, before);
      });
      await run(`${width}px: standalone/iOS hide without prompting; display-mode change restores only manual path`, async () => {
        for (const options of [{standalone: true}, {iosStandalone: true}]) {
          const before = await mount(page, 'auth', options);
          assert.equal(await page.locator(selectors.root).isVisible(), false);
          assert.equal(await page.evaluate(() => window.__pwaQA.dispatchPrompt('normal')), false);
          await page.locator(selectors.button).dispatchEvent('click');
          assert.equal((await state(page)).calls.length, 0); await unchanged(page, before);
        }
        const before = await mount(page);
        await page.evaluate(() => window.__pwaQA.dispatchPrompt('normal')); await page.locator(selectors.button).click();
        await page.evaluate(() => window.__pwaQA.changeMode(true));
        assert.equal(await page.locator(selectors.root).isVisible(), false);
        await page.evaluate(() => window.__pwaQA.resolve({outcome: 'accepted'}));
        await page.evaluate(() => window.__pwaQA.changeMode(false));
        assert.equal(await page.locator(selectors.root).isVisible(), true);
        await page.locator(selectors.button).click(); const s = await state(page);
        assert.equal(s.calls.length, 1); assert.equal(s.open, true); assert.equal(s.focus, true);
        assert.doesNotMatch(s.status, /تم التثبيت/); await unchanged(page, before);
      });
      await run(`${width}px: invalid event, prompt throws/rejects and choice rejection all give usable Arabic help`, async () => {
        for (const mode of ['unusable', 'throw', 'reject', 'normal']) {
          const before = await mount(page);
          assert.equal(await page.evaluate(mode => window.__pwaQA.dispatchPrompt(mode), mode), mode !== 'unusable');
          await page.locator(selectors.button).click();
          if (mode === 'normal') await page.evaluate(() => window.__pwaQA.reject(new Error('Synthetic choice rejection')));
          await page.waitForFunction(() => !document.querySelector('[data-pwa-install-button]').disabled);
          const s = await state(page);
          assert.equal(s.open, true); assert.equal(s.focus, true); assert.equal(s.hidden, false);
          assert.equal(s.calls.length, mode === 'unusable' ? 0 : 1); assert.doesNotMatch(s.status, /تم التثبيت|تم الحفظ/);
          if (mode !== 'unusable') assert.match(s.status, /تعذّر.*طريقة التثبيت/);
          await geometry(page); await unchanged(page, before);
        }
      });
      await noJSContext.close(); await context.close();
    }
    assert.deepEqual(runtimeErrors, [], 'No actual PWA JavaScript runtime errors');
    assert.equal(requests.filter(request => request.method !== 'GET').length, 0, 'No attempted POST or mutation');
    assert.equal(requests.length, 0, 'setContent and embedded local fonts require zero network requests');
    if (failures.length) throw Error(failures.map(failure => `${failure.name}: ${failure.message}`).join('\n'));
    console.log(`PWA install Chromium UI: ${cases} cases and ${assertions} assertions passed at 360/390/1280px across home/app/auth CSS; ${blockedRequests} network attempts, zero POST, real URL navigation, OS install, database, screenshots or new dependencies.`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
