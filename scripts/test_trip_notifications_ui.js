/* Synthetic Node/VM UI contracts only; no browser, HTTP server, sessions or DB.
 * All response objects below are synthetic inputs, NEVER backend persistence evidence.
 * Run: node scripts/test_trip_notifications_ui.js
 */
'use strict';
const nativeAssert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const rootPath = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(rootPath, 'static/js/trip-notifications.js'), 'utf8');
const partial = fs.readFileSync(path.join(rootPath, 'participants/templates/participants/elite_trips/_notifications.html'), 'utf8');
const css = fs.readFileSync(path.join(rootPath, 'static/css/trip-notifications.css'), 'utf8');
let assertions = 0, cases = 0, fixtures = 0;
const assert = new Proxy(nativeAssert, {get(target, key) {
  return typeof target[key] === 'function' ? (...args) => {assertions++; return target[key](...args);} : target[key];
}});
class Events {
  constructor() { this.listeners = {}; }
  addEventListener(name, fn, options = {}) { (this.listeners[name] ||= []).push({fn, once: Boolean(options.once)}); }
  dispatch(name, fields = {}) {
    const event = {type: name, defaultPrevented: false, preventDefault() {this.defaultPrevented = true;}, ...fields};
    for (const entry of [...(this.listeners[name] || [])]) {
      if (entry.once) this.listeners[name] = this.listeners[name].filter(e => e !== entry);
      entry.fn(event);
    }
    return event;
  }
}
function simpleMatch(node, selector) {
  const tag = selector.match(/^[a-z]+/i)?.[0];
  if (tag && node.tagName !== tag.toUpperCase()) return false;
  const id = selector.match(/#([\w-]+)/)?.[1];
  if (id && node.getAttribute('id') !== id) return false;
  for (const match of selector.matchAll(/\.([\w-]+)/g)) if (!node.className.split(/\s+/).includes(match[1])) return false;
  for (const match of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    if (node.getAttribute(match[1]) === null || (match[2] !== undefined && node.getAttribute(match[1]) !== match[2])) return false;
  }
  return true;
}
class Element extends Events {
  constructor(tag, document) {
    super(); this.tagName = tag.toUpperCase(); this.document = document;
    this.attrs = {}; this.children = []; this.parentElement = null; this.hidden = false;
    this.disabled = false; this.value = ''; this._text = ''; this.className = '';
    this.dataset = new Proxy({}, {
      get: (_, key) => this.getAttribute('data-' + key.replace(/[A-Z]/g, ch => '-' + ch.toLowerCase())),
      set: (_, key, value) => {this.setAttribute('data-' + key.replace(/[A-Z]/g, ch => '-' + ch.toLowerCase()), value); return true;}
    });
    this.classList = {toggle: (name, on) => {
      const all = this.className.split(/\s+/).filter(Boolean).filter(cls => cls !== name);
      if (on) all.push(name); this.className = all.join(' ');
    }};
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  set innerHTML(value) { throw Error('Forbidden HTML interpretation: ' + value); }
  setAttribute(key, value) { this.attrs[key] = String(value); if (key === 'class') this.className = String(value); }
  getAttribute(key) { return key === 'class' ? this.className : Object.hasOwn(this.attrs, key) ? this.attrs[key] : null; }
  appendChild(child) {
    if (child.tagName === '#FRAGMENT') { for (const entry of [...child.children]) this.appendChild(entry); }
    else { child.parentElement = this; this.children.push(child); }
    return child;
  }
  replaceChildren(...children) { this.children = []; this._text = ''; children.forEach(child => this.appendChild(child)); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.parentElement = null; }
  matches(selector) { return selector.split(',').some(part => simpleMatch(this, part.trim())); }
  closest(selector) { for (let n = this; n; n = n.parentElement) if (n.matches(selector)) return n; return null; }
  contains(node) { for (let n = node; n; n = n.parentElement) if (n === this) return true; return false; }
  querySelectorAll(selector) {
    const found = [];
    const match = node => selector.split(',').some(part => {
      const chunks = part.trim().split(/\s+/);
      if (!simpleMatch(node, chunks.pop())) return false;
      let ancestor = node.parentElement;
      while (chunks.length) {
        const wanted = chunks.pop();
        while (ancestor && !simpleMatch(ancestor, wanted)) ancestor = ancestor.parentElement;
        if (!ancestor) return false;
        ancestor = ancestor.parentElement;
      }
      return true;
    });
    const visit = node => { for (const child of node.children) {if (match(child)) found.push(child); visit(child);} };
    visit(this); return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() {this.document.activeElement = this;}
}
const origin = 'https://synthetic.invalid';
const tripsPath = '/participants/my-trips/';
const feedPath = tripsPath + 'notifications/';
const created = '2026-11-16T11:00:00.123+03:00';
const readStamp = '2026-11-16T11:10:00+03:00';
function notification(id = 1, overrides = {}) {
  return {id, kind: 'nominated', message: 'تم ترشيحك للرحلة المصطنعة', created_at: created,
    read_at: null, url: tripsPath + '#trip-' + id, read_url: feedPath + id + '/read/', ...overrides};
}
function payload(notifications = [notification()], overrides = {}) {
  return {ok: true, notifications, unread_count: notifications.filter(n => n.read_at === null).length,
    attention_count: 1, ...overrides};
}
function deferred() {let resolve, reject; const promise = new Promise((yes, no) => {resolve = yes; reject = no;}); return {promise, resolve, reject};}
const settle = () => new Promise(resolve => setImmediate(resolve));
function setup(options = {}) {
  fixtures++;
  const initial = options.initial || payload();
  const document = new Events(); document.readyState = options.loading ? 'loading' : 'complete';
  document.visibilityState = options.hidden ? 'hidden' : 'visible'; document.activeElement = null;
  const make = (tag, attrs = {}, text) => {
    const node = new Element(tag, document); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text; return node;
  };
  document.createElement = tag => make(tag);
  document.createDocumentFragment = () => make('#fragment');
  const dom = make('body');
  const root = make('section', {'data-trip-notifications': '', 'data-trips-url': options.tripsURL || tripsPath,
    'data-notifications-url': options.feedURL || feedPath, 'data-unread-count': initial.unread_count,
    'data-attention-count': initial.attention_count}); dom.appendChild(root);
  for (const [tag, attr] of [['ul','data-notifications-list'],['p','data-notifications-empty'],['span','data-notifications-count'],
    ['p','data-notifications-status'],['button','data-notifications-refresh']]) root.appendChild(make(tag, {[attr]: ''}));
  const refresh = root.querySelector('[data-notifications-refresh]'); refresh.hidden = true;
  const csrf = make('div', {'data-notifications-csrf': ''});
  const field = make('input', {name: 'csrfmiddlewaretoken'}); field.value = 'synthetic-csrf-only'; csrf.appendChild(field); root.appendChild(csrf);
  root.appendChild(make('script', {id: 'trip-notifications-initial'}, JSON.stringify(initial.notifications)));
  const navs = [];
  for (const cls of ['side-link', 'side-link', 'app-nav__link']) {
    const link = make('a', {href: tripsPath, class: cls}); link.appendChild(make('span', {class: 'app-nav__badge'}, '8'));
    dom.appendChild(link); navs.push(link);
  }
  const other = make('a', {href:'/participants/tasks/', class:'side-link'});
  other.appendChild(make('span', {class:'app-nav__badge'}, '9')); dom.appendChild(other);
  document.querySelectorAll = selector => options.absent ? [] : dom.querySelectorAll(selector);
  const window = new Events(); window.location = {href: origin + tripsPath, origin};
  window.self = window; window.top = window;
  if (options.frame) {
    window.top = {};
    window.frameElement = options.frameInaccessible ? null : {hidden: Boolean(options.frameHidden), getClientRects: () => options.frameZero ? [] : [{}]};
    window.getComputedStyle = () => ({display: options.frameDisplay || 'block', visibility: options.frameVisibility || 'visible'});
  }
  let now = 0, serial = 0;
  const timers = new Map();
  window.setTimeout = (fn, delay) => { const id = ++serial; timers.set(id, {fn, due:now + delay, delay}); return id; };
  window.clearTimeout = id => timers.delete(id);
  const requests = [];
  window.AbortController = AbortController;
  window.fetch = (url, settings) => {
    const gate = deferred(); requests.push({url, settings, gate}); return gate.promise;
  };
  if (options.noFetch) window.fetch = undefined;
  const forbidden = [];
  for (const target of [window, document]) for (const key of ['localStorage','sessionStorage','caches','cookie']) {
    Object.defineProperty(target, key, {get() {forbidden.push(key); throw Error('Forbidden private state API');}});
  }
  const context = vm.createContext({window, document, URL, URLSearchParams, Date, Set, Number, Object, Array, JSON, Promise});
  const boot = () => vm.runInContext(source, context, {filename:'trip-notifications.js'});
  boot();
  const f = {root, dom, document, window, requests, timers, forbidden, navs, other, boot,
    list: root.querySelector('[data-notifications-list]'), status: root.querySelector('[data-notifications-status]'), refresh,
    badge: root.querySelector('[data-notifications-count]'),
    row(id = 1) { return this.list.querySelector('[data-notification-id="' + id + '"]'); },
    form(id = 1) { return this.row(id)?.querySelector('form[data-notification-read]') || null; },
    read(id = 1) { return root.dispatch('submit', {target:this.form(id)}); },
    manual() { return refresh.dispatch('click'); },
    focus() { window.dispatch('focus'); },
    visibility(hidden) { document.visibilityState = hidden ? 'hidden' : 'visible'; document.dispatch('visibilitychange'); },
    async tick(ms) {
      const goal = now + ms;
      while (true) {
        const next = [...timers].filter(([,t]) => t.due <= goal).sort((a,b) => a[1].due-b[1].due)[0];
        if (!next) break;
        now = next[1].due; timers.delete(next[0]); next[1].fn(); await settle();
      }
      now = goal; await settle();
    },
    async reply(data = payload(), settings = {}, index = requests.length - 1) {
      const req = requests[index]; assert.ok(req, 'Synthetic request exists');
      const response = {status:200, ok:true, type:'basic', redirected:false, url:req.url,
        headers:{get: () => 'application/json; charset=utf-8'}, json: async () => data, ...settings};
      req.gate.resolve(response); await settle();
    },
    async fail(index = requests.length - 1) {requests[index].gate.reject(Error('Synthetic network fault')); await settle();}
  };
  return f;
}
async function run(name, fn) {cases++; await fn(); console.log(`ok ${cases} - ${name}`);}
function unchanged(f, text, unread = '1') {
  assert.equal(f.list.textContent, text); assert.equal(f.badge.textContent, unread);
  assert.ok(f.form()); assert.equal(f.form().querySelector('button').disabled, false);
  assert.doesNotMatch(f.status.textContent, /^تم تأكيد الاطلاع/);
}
(async () => {
  await run('actual templates: escaped text, native POST + CSRF, only two scoped integrations and unchanged card states', () => {
    assert.match(partial, /{{ notification.message }}/);
    assert.doesNotMatch(partial, /\|safe|autoescape off|onclick=/);
    assert.match(partial, /method="post" action="{{ notification.read_url }}"/);
    assert.match(partial, /{% csrf_token %}/); assert.match(partial, /name="action" value="read"/);
    assert.match(partial, /trip_notifications\|json_script:/); assert.match(partial, /aria-live="polite"/);
    for (const filename of ['participants/templates/participants/participant_dashboard.html','participants/templates/participants/elite_trips/student_trips.html']) {
      const text = fs.readFileSync(path.join(rootPath, filename), 'utf8');
      assert.equal(text.match(/include "participants\/elite_trips\/_notifications.html"/g)?.length, 1);
      assert.match(text, /css\/trip-notifications.css' %}\?v=20261116-notify1/);
      assert.match(text, /js\/trip-notifications.js' %}\?v=20261116-notify1" defer/);
      assert.match(text, /{% block scripts %}\s*{{ block.super }}/);
    }
    const cards = fs.readFileSync(path.join(rootPath, 'participants/templates/participants/elite_trips/_student_cards.html'), 'utf8');
    assert.match(cards, /id="trip-{{ card.trip_id(?:\|unlocalize)? }}"/);
    assert.match(cards, /et-trip-card--unselected/); assert.match(cards, /تهانينا! تم ترشيحك/); assert.match(cards, /الفرصة أمامك! اجتهد/);
    assert.match(css, /min-height: 44px/); assert.match(css, /min-width: 44px/);
    assert.match(css, /overflow-wrap: anywhere/); assert.match(css, /border-inline-start/);
    assert.doesNotMatch(css, /position:\s*(fixed|sticky)|\b\d+(?:vw|vmin|vmax)\b/);
    assert.doesNotMatch(source, /innerHTML|localStorage|sessionStorage|sendBeacon|XMLHttpRequest|window\.location\s*=/);
  });
  await run('initial context rendered once; first automatic GET only at 30s, private no-cache request policy', async () => {
    const f = setup(); assert.equal(f.requests.length, 0); assert.equal(f.refresh.hidden, false);
    assert.equal(f.badge.textContent, '1'); assert.match(f.list.textContent, /غير مقروء/);
    await f.tick(29999); assert.equal(f.requests.length, 0); await f.tick(1); assert.equal(f.requests.length, 1);
    const request = f.requests[0]; assert.equal(request.url, origin + feedPath);
    assert.equal(request.settings.method, 'GET'); assert.equal(request.settings.body, undefined);
    assert.equal(request.settings.credentials, 'same-origin'); assert.equal(request.settings.cache, 'no-store');
    assert.equal(request.settings.redirect, 'manual'); assert.equal(request.settings.headers.Accept, 'application/json');
    await f.reply(); assert.deepEqual(f.forbidden, []);
  });
  await run('literal hostile text, all four kinds, read/unread states and safe ISO timestamps', () => {
    const hostile = '<img src=x onerror="globalThis.pwned=1"> & <script>bad()</script>\nسطر';
    const items = ['nominated','not_nominated','closed','cancelled'].map((kind,i) => notification(i+1, {kind, message:hostile, read_at:i===3?readStamp:null}));
    const f = setup({initial:payload(items)});
    assert.equal(f.list.querySelectorAll('li').length, 4); assert.equal(f.list.querySelectorAll('img').length, 0);
    assert.equal(f.list.querySelectorAll('script').length, 0);
    assert.equal(f.list.querySelector('.trip-notifications__message').textContent, hostile);
    for (const label of ['تم الترشيح','لم يتم الترشيح','أُغلقت الرحلة','أُلغيت الرحلة','غير مقروء','مقروء']) assert.ok(f.list.textContent.includes(label));
    assert.equal(f.list.querySelectorAll('time').length, 5);
    assert.equal(f.list.querySelector('time').getAttribute('datetime'), created); assert.equal(f.form(4), null);
  });
  await run('successful GET refresh never submits or acknowledges reading; attention count is server distinct-trip count', async () => {
    const f = setup(); f.focus(); assert.equal(f.requests.length, 1);
    const data = payload([notification(1),notification(2)], {unread_count:2, attention_count:1});
    await f.reply(data);
    assert.equal(f.badge.textContent,'2'); for (const nav of f.navs) assert.equal(nav.querySelector('.app-nav__badge').textContent,'1');
    assert.equal(f.other.querySelector('.app-nav__badge').textContent,'9'); assert.equal(f.requests.every(r=>r.settings.method==='GET'),true);
    assert.ok(f.form(1)); assert.ok(f.form(2)); assert.match(f.status.textContent,/وصلت/);
  });
  await run('strict schema rejects malformed counts, duplicate IDs, unknown kinds, dates and over-20 payloads without replacing list', async () => {
    const invalid = [
      {ok:false}, {unread_count:'1'}, {attention_count:-1}, {attention_count:1.5}, {notifications:{}},
      {notifications:Array.from({length:21},(_,i)=>notification(i+1))}, {notifications:[notification(),notification()]},
      {notifications:[notification(0)]}, {notifications:[notification(1,{kind:'__proto__'})]},
      {notifications:[notification(1,{message:{html:'unsafe'}})]}, {notifications:[notification(1,{message:'a'.repeat(601)})]},
      {notifications:[notification(1,{created_at:'not-a-date'})]}, {notifications:[notification(1,{read_at:undefined})]}
    ];
    for (const bad of invalid) {
      const f=setup(); const before=f.list.textContent; f.manual(); await f.reply({...payload(),...bad});
      unchanged(f,before); assert.match(f.status.textContent,/تعذّر تحديث/);
    }
  });
  await run('same-origin exact known URL boundaries reject external, script, credentials, altered paths and query redirects', async () => {
    const badDetails=['https://other.invalid'+tripsPath+'#trip-1','//other.invalid/path','javascript:bad()',
      origin+'/participants/tasks/#trip-1',tripsPath+'?next=https://other.invalid#trip-1',tripsPath+'#wrong',
      'https://user:password@synthetic.invalid'+tripsPath+'#trip-1','\\\\other.invalid/path'];
    const badReads=['https://other.invalid/read',feedPath+'2/read/',feedPath+'1/read/?next=/','/participants/tasks/'];
    for (const [key,values] of [['url',badDetails],['read_url',badReads]]) for (const value of values) {
      const f=setup(); const before=f.list.textContent; f.manual(); await f.reply(payload([notification(1,{[key]:value})]));
      unchanged(f,before);
    }
    for (const options of [{feedURL:'https://other.invalid/notifications/'},{feedURL:'/participants/tasks/'},{tripsURL:'/participants/tasks/'}]) {
      const f=setup(options); assert.equal(f.requests.length,0); assert.equal(f.refresh.hidden,true); assert.match(f.status.textContent,/تعذّر/);
    }
  });
  await run('GET network/JSON HTTP failure preserves current list/counts and permits manual retry', async () => {
    for (const http of [false,true]) {
      const f=setup(); const before=f.list.textContent; f.manual();
      if(http) await f.reply(payload(),{status:500,ok:false}); else await f.fail();
      unchanged(f,before); assert.equal(f.refresh.disabled,false); f.manual(); await f.reply(); assert.match(f.status.textContent,/محدثة/);
    }
  });
  await run('POST transport failure retains unread notification, enables retry and never automatically retries POST', async () => {
    const f=setup(); const before=f.list.textContent; const event=f.read(); assert.equal(event.defaultPrevented,true);
    assert.equal(f.form().querySelector('button').disabled,true); assert.equal(f.requests[0].settings.method,'POST');
    assert.equal(f.requests[0].settings.body.get('action'),'read'); assert.equal(f.requests[0].settings.body.get('csrfmiddlewaretoken'),'synthetic-csrf-only');
    await f.fail(); unchanged(f,before); assert.match(f.status.textContent,/تعذّر تأكيد/);
    await f.tick(30000); assert.equal(f.requests.length,2); assert.equal(f.requests[1].settings.method,'GET'); await f.reply();
    assert.equal(f.requests.filter(r=>r.settings.method==='POST').length,1); f.read(); assert.equal(f.requests.length,3);
  });
  await run('server ok with a contradictory unread target or invalid stamp is not a saved read', async () => {
    for (const data of [payload(),payload([notification(1,{read_at:'bad'})])]) {
      const f=setup(); const before=f.list.textContent; f.read(); await f.reply(data); unchanged(f,before);
      assert.match(f.status.textContent,/تعذّر تأكيد/); assert.equal(f.form().querySelector('button').disabled,false);
    }
  });
  await run('validated exact POST ok ACK may move the target outside capped list; no invented read stamp or client reordering', async () => {
    const f=setup(); f.read();
    const data=payload([notification(7),notification(3)],{unread_count:2,attention_count:1});
    await f.reply(data);
    assert.equal(f.form(1),null); assert.match(f.status.textContent,/^تم تأكيد الاطلاع/);
    assert.deepEqual(f.list.children.map(row=>row.getAttribute('data-notification-id')),['7','3']);
    assert.equal(f.list.querySelectorAll('.trip-notifications__read-stamp').length,0);
    assert.equal(f.badge.textContent,'2');
  });
  await run('matching POST ACK + server timestamp alone marks read, updates both badges and restores focused row', async () => {
    const f=setup(); f.form().querySelector('button').focus(); f.read();
    assert.ok(f.row().className.includes('is-unread')); assert.equal(f.badge.textContent,'1');
    await f.reply(payload([notification(1,{read_at:readStamp})],{unread_count:0,attention_count:0}));
    assert.equal(f.form(),null); assert.equal(f.row().className.includes('is-unread'),false); assert.match(f.status.textContent,/^تم تأكيد الاطلاع/);
    assert.equal(f.row().querySelectorAll('time')[1].getAttribute('datetime'),readStamp); assert.equal(f.badge.textContent,'0');
    for(const nav of f.navs) assert.equal(nav.querySelector('.app-nav__badge'),null);
    assert.equal(f.document.activeElement,f.row());
    f.manual(); await f.reply(payload([notification(1,{read_at:readStamp}),notification(2)],{unread_count:1,attention_count:1}));
    for(const nav of f.navs) assert.equal(nav.querySelector('.app-nav__badge').textContent,'1');
  });
  await run('single-flight across GET, focus, visibility, manual button and read; duplicate submits make one POST', async () => {
    const f=setup(); f.manual(); f.focus(); f.manual(); f.visibility(false); f.read();
    assert.equal(f.requests.length,1); assert.equal(f.requests[0].settings.method,'GET'); await f.reply();
    f.read(); f.read(); f.manual(); f.focus(); assert.equal(f.requests.length,2); assert.equal(f.requests[1].settings.method,'POST');
    await f.reply(payload([notification(1,{read_at:readStamp})],{unread_count:0}));
    assert.equal(f.requests.filter(r=>r.settings.method==='POST').length,1);
  });
  await run('hidden page schedules no poll; visibility/focus refresh on return and one interval thereafter', async () => {
    const f=setup({hidden:true}); await f.tick(90000); assert.equal(f.requests.length,0);
    f.focus(); f.manual(); assert.equal(f.requests.length,0); f.visibility(false); assert.equal(f.requests.length,1); await f.reply();
    f.visibility(true); assert.equal(f.timers.size,0); await f.tick(90000); assert.equal(f.requests.length,1);
    f.visibility(false); assert.equal(f.requests.length,2); await f.reply(); await f.tick(30000); assert.equal(f.requests.length,3);
  });
  await run('hidden/zero-size/inaccessible iframe has no poll; visible iframe uses normal interval', async () => {
    for(const options of [{frameHidden:true},{frameZero:true},{frameDisplay:'none'},{frameVisibility:'hidden'},{frameInaccessible:true}]) {
      const f=setup({frame:true,...options}); await f.tick(60000); f.focus(); assert.equal(f.requests.length,0);
    }
    const f=setup({frame:true}); await f.tick(30000); assert.equal(f.requests.length,1); await f.reply();
  });
  await run('401/403/login redirect/non-JSON/changed response URL stop updates without external redirect or fake success', async () => {
    const errors=[{status:401,ok:false},{status:403,ok:false},{status:302,ok:false}, {type:'opaqueredirect',status:0,ok:false},
      {redirected:true,url:origin+'/accounts/login/'},{headers:{get:()=> 'text/html'}},{url:'https://other.invalid/notifications/'},
      {url:origin+'/accounts/login/'}];
    for(const settings of errors) {
      const f=setup(); const before=f.list.textContent; f.manual(); await f.reply(payload(),settings);
      unchanged(f,before); assert.match(f.status.textContent,/توقف تحديث/); assert.equal(f.refresh.disabled,true);
      assert.equal(f.timers.size,0); await f.tick(90000); f.focus(); f.manual(); assert.equal(f.requests.length,1);
    }
    const f=setup(); const before=f.list.textContent; f.read(); await f.reply(payload(),{status:403,ok:false});
    unchanged(f,before); f.read(); assert.equal(f.requests.length,1);
  });
  await run('pagehide cancels interval/aborts in-flight and ignores late response; BFCache pageshow refreshes', async () => {
    const f=setup(); const before=f.list.textContent; f.read(); f.window.dispatch('pagehide');
    assert.equal(f.requests[0].settings.signal.aborted,true); assert.equal(f.timers.size,1, 'Only request deadline remains until settlement');
    await f.reply(payload([notification(1,{read_at:readStamp})],{unread_count:0})); unchanged(f,before);
    await f.tick(90000); f.focus(); assert.equal(f.requests.length,1);
    f.window.dispatch('pageshow',{persisted:true}); assert.equal(f.requests.length,2); await f.reply();
  });
  await run('bounded request deadline aborts; failure cannot leave a read button disabled', async () => {
    const f=setup(); const before=f.list.textContent; f.read(); await f.tick(20000);
    assert.equal(f.requests[0].settings.signal.aborted,true); await f.fail(); unchanged(f,before);
    assert.equal(f.requests.filter(r=>r.settings.method==='POST').length,1);
  });
  await run('no fetch supports native CSRF POST fallback; duplicate boot and delayed DOM initialize only once', async () => {
    const f=setup({noFetch:true}); const form=f.form(); assert.equal(form.getAttribute('method'),'post');
    assert.equal(form.getAttribute('action'),origin+feedPath+'1/read/'); assert.equal(form.querySelector('input[name="csrfmiddlewaretoken"]').value,'synthetic-csrf-only');
    assert.equal(form.querySelector('input[name="action"]').value,'read'); assert.equal(f.read().defaultPrevented,false); assert.equal(f.refresh.hidden,true);
    const ready=setup({loading:true}); assert.equal(ready.requests.length,0); ready.document.dispatch('DOMContentLoaded'); ready.boot();
    assert.equal(ready.root.listeners.submit.length,1); assert.equal(ready.window.listeners.focus.length,1);
    assert.equal([...ready.timers.values()].filter(t=>t.delay===30000).length,1);
    const absent=setup({absent:true}); assert.equal(absent.requests.length,0); assert.equal(absent.timers.size,0);
  });
  console.log(`Trip notifications synthetic UI: ${cases} cases, ${assertions} assertions, ${fixtures} fixtures passed. No HTTP server, browser, external sessions, production or backend persistence verification.`);
})().catch(error => {console.error(error.stack); process.exitCode=1;});
