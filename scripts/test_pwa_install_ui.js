/* Dependency-free Node/VM contracts. Synthetic DOM only: no HTTP, accounts or database.
 * Run: node scripts/test_pwa_install_ui.js
 */
'use strict';
const nativeAssert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const rootPath = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(rootPath, 'static/js/pwa-install.js'), 'utf8');
const template = fs.readFileSync(path.join(rootPath, 'templates/pwa/_install.html'), 'utf8');
const head = fs.readFileSync(path.join(rootPath, 'templates/pwa/_head.html'), 'utf8');
const css = fs.readFileSync(path.join(rootPath, 'static/css/pwa-install.css'), 'utf8');
let assertions = 0;
let cases = 0;
let fixtures = 0;
const assert = new Proxy(nativeAssert, {
  get(target, key) {
    return typeof target[key] === 'function' ? (...args) => { assertions += 1; return target[key](...args); } : target[key];
  },
});
class Events {
  constructor() { this.listeners = {}; }
  addEventListener(type, callback, options = {}) {
    (this.listeners[type] ||= []).push({callback, once: Boolean(options.once)});
  }
  dispatch(type, fields = {}) {
    const event = fields.type === type ? fields : {type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields};
    for (const entry of [...(this.listeners[type] || [])]) {
      if (entry.once) this.listeners[type] = this.listeners[type].filter(item => item !== entry);
      entry.callback(event);
    }
    return event;
  }
}
class Element extends Events {
  constructor(tag, attrs, document) {
    super(); this.tagName = tag.toLowerCase(); this.attrs = attrs; this.document = document;
    this.children = []; this.parentElement = null; this.hidden = Object.hasOwn(attrs, 'hidden');
    this.disabled = Object.hasOwn(attrs, 'disabled'); this.open = Object.hasOwn(attrs, 'open'); this._text = '';
  }
  append(child) { child.parentElement = this; this.children.push(child); }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = value; this.children = []; }
  set innerHTML(value) { throw new Error('PWA must never parse HTML: ' + value); }
  querySelectorAll(selector) {
    const attribute = selector.match(/^\[([\w-]+)\]$/)?.[1];
    if (!attribute) throw new Error('Unexpected synthetic selector: ' + selector);
    const found = [];
    const visit = element => { for (const child of element.children) { if (Object.hasOwn(child.attrs, attribute)) found.push(child); visit(child); } };
    visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { this.document.activeElement = this; }
  click() { if (!this.disabled) return this.dispatch('click'); }
}
function parse(html, document) {
  const body = new Element('body', {}, document);
  const stack = [body];
  for (const token of html.matchAll(/<[^>]+>|[^<]+/g)) {
    const value = token[0];
    if (value.startsWith('</')) { stack.pop(); continue; }
    if (value.startsWith('<')) {
      const tag = value.match(/^<([\w-]+)/)?.[1];
      if (!tag) continue;
      const attrs = {};
      const attributeText = value.slice(tag.length + 1, -1);
      for (const match of attributeText.matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
        attrs[match[1]] = match[2] ?? match[3] ?? match[4] ?? '';
      }
      const element = new Element(tag, attrs, document);
      stack.at(-1).append(element);
      if (!['meta', 'link', 'input', 'br', 'img'].includes(tag)) stack.push(element);
    } else stack.at(-1)._text += value;
  }
  return body;
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const settle = () => new Promise(resolve => setImmediate(resolve));
function setup(options = {}) {
  fixtures += 1;
  const document = new Events(); document.readyState = options.loading ? 'loading' : 'interactive';
  document.activeElement = null;
  const dom = parse(options.empty ? '' : options.malformed ? '<div data-pwa-install></div>' : template, document);
  document.querySelectorAll = selector => dom.querySelectorAll(selector);
  const window = new Events();
  window.location = {protocol: options.protocol || 'https:', hostname: options.hostname || 'synthetic.invalid', reload() { throw Error('Reload forbidden'); }};
  window.isSecureContext = options.secure ?? true;
  const media = new Events(); media.matches = options.standalone ?? false;
  media.addListener = callback => media.addEventListener('change', callback);
  if (options.legacyMedia) media.addEventListener = undefined;
  if (options.legacyMedia) media.addListener = callback => Events.prototype.addEventListener.call(media, 'change', callback);
  const queries = [];
  if (!options.noMedia) window.matchMedia = query => { queries.push(query); return media; };
  const registrations = [];
  const registration = deferred();
  const navigator = {standalone: options.iosStandalone ?? false};
  if (!options.noWorker) navigator.serviceWorker = {
    register(url, settings) {
      registrations.push({url, settings: {...settings}});
      if (options.registerThrows) throw Error('Synthetic registration failure');
      return options.registerPending ? registration.promise : options.registerReject ? Promise.reject(Error('Synthetic registration failure')) : Promise.resolve({});
    },
  };
  const forbidden = [];
  const trap = (object, name) => Object.defineProperty(object, name, {get() { forbidden.push(name); throw Error('Forbidden PWA access: ' + name); }});
  for (const key of ['fetch', 'XMLHttpRequest', 'caches', 'localStorage', 'sessionStorage', 'Notification', 'PushManager']) trap(window, key);
  for (const key of ['credentials', 'userAgent', 'sendBeacon']) trap(navigator, key);
  for (const key of ['body', 'cookie']) trap(document, key);
  const context = vm.createContext({window, document, navigator, Promise, WeakSet});
  const runScript = () => vm.runInContext(source, context, {filename: 'pwa-install.js'});
  if (!options.noJS) runScript();
  const root = dom.querySelector('[data-pwa-install]');
  const get = attribute => root?.querySelector('[' + attribute + ']');
  const button = get('data-pwa-install-button');
  return {
    dom, document, window, navigator, root, button, media, registrations, registration, forbidden, queries, runScript,
    help: get('data-pwa-install-help'), summary: get('data-pwa-install-summary'), instructions: get('data-pwa-install-instructions'), status: get('data-pwa-install-status'),
    ready() { document.readyState = 'interactive'; document.dispatch('DOMContentLoaded'); },
    change(matches) { media.matches = matches; media.dispatch('change'); },
    promptEvent({result, throws = false, rejects = false, omitChoice = false} = {}) {
      const choice = deferred(); const calls = [];
      const event = {prompt() { calls.push('prompt'); if (throws) throw Error('Synthetic prompt failure'); if (rejects) return Promise.reject(Error('Synthetic prompt failure')); return Promise.resolve(result); }};
      if (!omitChoice) event.userChoice = choice.promise;
      const delivered = window.dispatch('beforeinstallprompt', event);
      return {event: delivered, choice, calls};
    },
    click() { return button.click(); },
  };
}
async function run(name, callback) {
  cases += 1;
  await callback();
  console.log(`ok ${cases} - ${name}`);
}
function noSuccess(fixture) {
  assert.doesNotMatch(fixture.status.textContent, /تم التثبيت|تم تثبيت|تم الحفظ/);
  assert.equal(fixture.root.hidden, false);
}
function outsideBlocks(templateText, target) {
  let stack = [];
  for (const token of templateText.matchAll(/{%\s*(block\b[^%]*|endblock\b[^%]*|include\b[^%]*)%}|<script\b[^>]*>/g)) {
    const value = token[0];
    if (value.includes(target)) return stack.length === 0;
    if (/^{%\s*block\b/.test(value)) stack.push(value);
    if (/^{%\s*endblock\b/.test(value)) stack.pop();
  }
  return false;
}

(async () => {
  await run('actual partial: no-JS native details, one CTA, Arabic platform instructions and safe root link', () => {
    const f = setup({noJS: true});
    assert.equal(f.button.hidden, true); assert.equal(f.button.getAttribute('type'), 'button');
    assert.equal(f.summary.textContent, 'تثبيت رحّال'); assert.equal(f.help.tagName, 'details'); assert.equal(f.help.open, false);
    assert.equal(f.root.hidden, false); assert.equal(f.status.hidden, true);
    assert.equal(f.instructions.getAttribute('tabindex'), '-1');
    assert.equal(f.status.getAttribute('role'), 'status'); assert.equal(f.status.getAttribute('aria-live'), 'polite');
    for (const text of ['Android', 'Chrome', 'الثلاث نقاط', 'تثبيت التطبيق', 'إضافة إلى الشاشة الرئيسية', 'iPhone', 'Safari', 'مشاركة', 'فتح كتطبيق ويب', 'Edge', 'شريط العنوان', 'معاينة مضمّنة', 'اتصالًا بالإنترنت', 'خارج التصفح الخاص']) assert.ok(f.instructions.textContent.includes(text), text);
    const link = f.root.querySelector('[data-pwa-open-site]');
    assert.equal(link.getAttribute('href'), '/'); assert.equal(link.getAttribute('target'), '_blank'); assert.equal(link.getAttribute('rel'), 'noopener noreferrer');
    assert.doesNotMatch(template, /<form|<script|<style|onclick=|APK|Google Play|App Store/i);
    assert.equal(f.registrations.length, 0);
  });
  await run('metadata: exact manifest, icons, identity, theme and versioned CSS; no inline app logic', () => {
    assert.match(head, /rel="manifest" href="{% url 'pwa_manifest' %}"/);
    for (const [name, content] of [['theme-color', '#F7F3EA'], ['application-name', 'رحّال'], ['apple-mobile-web-app-title', 'رحّال'], ['mobile-web-app-capable', 'yes'], ['apple-mobile-web-app-capable', 'yes'], ['apple-mobile-web-app-status-bar-style', 'default']]) {
      assert.ok(head.includes(`name="${name}" content="${content}"`));
    }
    assert.match(head, /rel="apple-touch-icon" sizes="180x180" href="{% static 'pwa\/apple-touch-icon.png' %}"/);
    assert.match(head, /rel="icon" type="image\/png" sizes="192x192" href="{% static 'pwa\/icon-192.png' %}"/);
    assert.match(head, /css\/pwa-install.css' %}\?v=20261108-pwa1/);
    assert.doesNotMatch(head, /<script|<style|<form/);
  });
  await run('three parent templates: shared includes and deferred script cannot be replaced by children', () => {
    for (const filename of ['templates/home.html', 'participants/templates/participants/app_base.html', 'accounts/templates/base.html']) {
      const base = fs.readFileSync(path.join(rootPath, filename), 'utf8');
      assert.equal(base.match(/include "pwa\/_head.html"/g)?.length, 1, filename);
      assert.equal(base.match(/include "pwa\/_install.html"/g)?.length, 1, filename);
      assert.equal(base.match(/js\/pwa-install.js/g)?.length, 1, filename);
      assert.ok(base.indexOf('pwa/_head.html') > base.indexOf('</title>'));
      assert.ok(base.indexOf('pwa/_head.html') < base.indexOf('</head>'));
      const scriptTag = base.match(/<script\b[^>]*pwa-install\.js[^>]*>/)?.[0] || '';
      assert.match(scriptTag, /\bdefer\b/);
      assert.match(scriptTag, /src="{% static 'js\/pwa-install.js' %}\?v=20261108-pwa1"/);
      assert.equal(outsideBlocks(base, 'pwa/_install.html'), true);
      assert.equal(outsideBlocks(base, 'pwa-install.js'), true);
      const prefix = base.slice(0, base.indexOf('pwa/_install.html'));
      assert.ok(prefix.lastIndexOf('<form') <= prefix.lastIndexOf('</form>'), 'Invite outside native forms');
      if (filename.startsWith('accounts')) assert.ok(prefix.indexOf('{% block form_content %}{% endblock %}') > -1);
      if (filename.startsWith('participants')) assert.match(base, /<div class="wrap">\s*{% include "pwa\/_install.html" %}\s*{% block content %}/);
      if (filename.startsWith('templates/')) assert.ok(base.indexOf('pwa/_install.html') < base.indexOf('<div class="stat-strip">'));
    }
  });
  await run('scoped CSS: hidden wins, targets 44px, no fixed/viewport-sized elements or sibling effects', () => {
    assert.match(css, /\.pwa-install\[hidden\],\s*\.pwa-install \[hidden\]\s*{\s*display: none !important;/);
    assert.match(css, /min-height: 44px/); assert.match(css, /min-width: 44px/);
    assert.doesNotMatch(css, /position:\s*(fixed|sticky)|\b\d*(?:vw|vmin|vmax)\b|\.wrap|\.auth-|\.app-nav|\bbody\b/);
    const clean = css.replace(/\/\*[\s\S]*?\*\//g, '');
    for (const match of clean.matchAll(/([^{}]+)\{/g)) {
      for (const selector of match[1].split(',')) assert.ok(selector.trim().startsWith('.pwa-install'), selector);
    }
  });
  await run('enhancement reveals non-submit button, renames summary and registers secure root worker once', async () => {
    const f = setup(); await settle();
    assert.equal(f.button.hidden, false); assert.equal(f.summary.textContent, 'طريقة التثبيت');
    assert.equal(f.help.open, false); assert.equal(f.status.hidden, true);
    assert.deepEqual(f.registrations, [{url: '/service-worker.js', settings: {scope: '/', updateViaCache: 'none'}}]);
    assert.deepEqual(f.queries, ['(display-mode: standalone)']);
    assert.equal(f.window.listeners.beforeinstallprompt.length, 1);
    f.runScript(); f.ready(); f.ready();
    assert.equal(f.registrations.length, 1); assert.equal(f.button.listeners.click.length, 1);
    assert.equal(f.window.listeners.appinstalled.length, 1); assert.deepEqual(f.forbidden, []);
  });
  await run('event captured before DOM ready; never prompts automatically, init once', async () => {
    const f = setup({loading: true});
    const p = f.promptEvent();
    assert.equal(p.event.defaultPrevented, true); assert.equal(p.calls.length, 0); assert.equal(f.button.hidden, true);
    f.ready(); f.ready(); f.runScript();
    assert.equal(f.button.hidden, false); assert.equal(f.button.listeners.click.length, 1);
    assert.equal(p.calls.length, 0); f.click(); assert.equal(p.calls.length, 1);
    p.choice.resolve({outcome: 'dismissed'}); await settle();
    noSuccess(f); assert.deepEqual(f.forbidden, []);
  });
  await run('unusable event is not prevented; manual click opens native details and focuses instructions', () => {
    const f = setup(); const invalid = f.window.dispatch('beforeinstallprompt', {prompt: null});
    assert.equal(invalid.defaultPrevented, false);
    const click = f.click(); assert.equal(click.defaultPrevented, false);
    assert.equal(f.help.open, true); assert.equal(f.document.activeElement, f.instructions);
    assert.equal(f.button.disabled, false); assert.equal(f.status.hidden, false); noSuccess(f);
  });
  await run('one prompt per user click/event; repeated pending clicks and repeated consumed dispatch cannot reprompt', async () => {
    const f = setup(); const p = f.promptEvent();
    assert.equal(p.calls.length, 0); f.click(); f.click(); f.button.dispatch('click');
    assert.equal(p.calls.length, 1); assert.equal(f.button.disabled, true);
    p.choice.resolve({outcome: 'dismissed'}); await settle();
    assert.equal(f.button.disabled, false); assert.equal(f.help.open, true); noSuccess(f);
    f.window.dispatch('beforeinstallprompt', p.event); f.click();
    assert.equal(p.calls.length, 1); assert.equal(f.document.activeElement, f.instructions);
  });
  await run('cancel clears old prompt, then a genuinely new event can be accepted without fake installation', async () => {
    const f = setup(); const canceled = f.promptEvent(); f.click();
    canceled.choice.resolve({outcome: 'dismissed'}); await settle();
    f.help.open = false; f.click(); assert.equal(canceled.calls.length, 1); assert.equal(f.help.open, true);
    const accepted = f.promptEvent(); f.click(); accepted.choice.resolve({outcome: 'accepted'}); await settle();
    assert.equal(accepted.calls.length, 1); assert.equal(f.button.disabled, false);
    assert.match(f.status.textContent, /وافق المتصفح.*أكمل خطوات التثبيت/); noSuccess(f);
    f.click(); assert.equal(accepted.calls.length, 1); assert.equal(f.help.open, true);
  });
  await run('appinstalled hides invitation and invalidates late choice; script rerun cannot resurrect it', async () => {
    const f = setup(); const p = f.promptEvent(); f.click();
    f.window.dispatch('appinstalled');
    assert.equal(f.root.hidden, true); assert.equal(f.button.disabled, false); assert.equal(f.help.open, false);
    assert.equal(f.status.textContent, '');
    p.choice.resolve({outcome: 'accepted'}); await settle();
    assert.equal(f.root.hidden, true); assert.equal(f.status.textContent, ''); f.runScript(); f.button.dispatch('click');
    assert.equal(p.calls.length, 1); assert.equal(f.promptEvent().event.defaultPrevented, false);
  });
  await run('standalone display and iOS navigator.standalone hide UI but still register worker', async () => {
    for (const options of [{standalone: true}, {iosStandalone: true}, {iosStandalone: true, noMedia: true}]) {
      const f = setup(options); await settle();
      assert.equal(f.root.hidden, true); assert.equal(f.registrations.length, 1);
      const p = f.promptEvent(); f.button.dispatch('click');
      assert.equal(p.event.defaultPrevented, false); assert.equal(p.calls.length, 0);
      assert.deepEqual(f.forbidden, []);
    }
  });
  await run('display-mode change hides invite, clears pending state and restores manual help on exit', async () => {
    for (const legacyMedia of [false, true]) {
      const f = setup({legacyMedia}); const p = f.promptEvent(); f.click();
      f.change(true); assert.equal(f.root.hidden, true); assert.equal(f.button.disabled, false);
      p.choice.resolve({outcome: 'accepted'}); await settle(); assert.equal(f.status.textContent, '');
      f.change(false); assert.equal(f.root.hidden, false); f.click();
      assert.equal(p.calls.length, 1); assert.equal(f.help.open, true); noSuccess(f);
    }
  });
  await run('prompt throws/rejects and choice rejects: manual Arabic help, no false success or blocked form', async () => {
    for (const options of [{throws: true}, {rejects: true}, {}]) {
      const f = setup(); const p = f.promptEvent(options); f.click();
      if (!options.throws && !options.rejects) { await settle(); p.choice.reject(Error('Synthetic choice failure')); }
      await settle();
      assert.equal(p.calls.length, 1); assert.equal(f.button.disabled, false); assert.equal(f.help.open, true);
      assert.equal(f.document.activeElement, f.instructions); assert.match(f.status.textContent, /تعذّر.*طريقة التثبيت/);
      noSuccess(f); f.click(); assert.equal(p.calls.length, 1); assert.deepEqual(f.forbidden, []);
    }
  });
  await run('unknown/missing choice cannot become installed; prompt-return accepted still remains provisional', async () => {
    for (const result of [undefined, {outcome: 'unknown'}, {outcome: 'accepted'}]) {
      const f = setup(); const p = f.promptEvent({omitChoice: true, result}); f.click(); await settle();
      assert.equal(f.button.disabled, false); noSuccess(f);
      if (result?.outcome === 'accepted') assert.match(f.status.textContent, /وافق المتصفح/);
      else assert.equal(f.help.open, true);
    }
  });
  await run('secure origin gate: HTTPS/localhost IPv4/IPv6 only in a secure context', async () => {
    for (const [protocol, hostname, secure, expected] of [
      ['https:', 'synthetic.invalid', true, 1], ['https:', 'synthetic.invalid', false, 0],
      ['http:', 'synthetic.invalid', false, 0], ['http:', 'synthetic.invalid', true, 0],
      ['http:', 'localhost', true, 1], ['http:', 'demo.localhost', true, 1], ['http:', 'localhost', false, 0],
      ['http:', '127.0.0.1', true, 1], ['http:', '127.0.0.2', true, 1], ['http:', '[::1]', true, 1],
      ['http:', '::1', true, 1], ['http:', 'localhost.evil.invalid', true, 0],
      ['http:', '192.168.1.5', true, 0], ['file:', '', true, 0], ['data:', '', true, 0],
    ]) {
      const f = setup({protocol, hostname, secure}); await settle();
      assert.equal(f.registrations.length, expected, `${protocol}//${hostname} secure=${secure}`);
      assert.equal(f.root.hidden, false); f.click(); assert.equal(f.help.open, true);
    }
    const f = setup({noWorker: true}); f.click(); assert.equal(f.help.open, true); assert.equal(f.registrations.length, 0);
  });
  await run('registration failure is quiet, nonblocking and retains manual path even before DOM init', async () => {
    for (const options of [{registerThrows: true}, {registerReject: true}, {registerThrows: true, loading: true}, {registerReject: true, loading: true}]) {
      const f = setup(options); await settle(); if (options.loading) f.ready();
      assert.equal(f.button.disabled, false); assert.equal(f.help.open, false);
      assert.match(f.status.textContent, /متابعة استخدام رحّال.*طريقة التثبيت/); noSuccess(f);
      f.click(); assert.equal(f.help.open, true); assert.equal(f.document.activeElement, f.instructions);
      assert.deepEqual(f.forbidden, []);
    }
  });
  await run('late registration failure cannot overwrite pending prompt/accepted message or reveal standalone invite', async () => {
    const f = setup({registerPending: true}); const p = f.promptEvent(); f.click();
    f.registration.reject(Error('Synthetic delayed failure')); await settle();
    assert.match(f.status.textContent, /أكمل طلب التثبيت/);
    p.choice.resolve({outcome: 'accepted'}); await settle(); assert.match(f.status.textContent, /وافق المتصفح/); noSuccess(f);
    const standalone = setup({standalone: true, registerReject: true}); await settle();
    assert.equal(standalone.root.hidden, true); assert.equal(standalone.status.textContent, '');
  });
  await run('absence or incomplete component does not break host page; no unsafe APIs or form access in source', async () => {
    for (const options of [{empty: true}, {malformed: true}]) {
      const f = setup(options); await settle();
      f.window.dispatch('appinstalled'); f.change(true); f.change(false); f.runScript();
      assert.equal(f.registrations.length, 1); assert.deepEqual(f.forbidden, []);
    }
    assert.doesNotMatch(source, /\b(?:fetch|XMLHttpRequest|sendBeacon|requestPermission|reload|skipWaiting)\s*\(/);
    assert.doesNotMatch(source, /\b(?:localStorage|sessionStorage|caches|Notification|PushManager|SyncManager|userAgent|credentials)\b/);
    assert.doesNotMatch(source, /document\.(?:body|cookie)|innerHTML|\.reset\(|\.submit\(/);
  });
  console.log(`PWA install Node UI: ${cases} cases, ${assertions} assertions across ${fixtures} synthetic fixtures passed; no network, database, account data, dependencies or OS installation.`);
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
