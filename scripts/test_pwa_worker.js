/* Dependency-free service-worker contracts. No browser, network or app data. */
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'static/js/pwa-service-worker.js'), 'utf8');
const offlineHTML = fs.readFileSync(path.join(root, 'static/pwa/offline.html'), 'utf8');
const ORIGIN = 'https://rahhal.example';
const CACHE_NAME = 'rahhal-public-offline-v1';
const OFFLINE_URL = '/offline/';
const offlineText = 'أنت غير متصل بالإنترنت. عرض بياناتك وحفظ التغييرات يحتاجان إلى اتصال. لا تحفظ التغييرات دون اتصال.';
let groups = 0;

function response(body = offlineHTML, {status = 200, marker = 'public-v1', type = 'text/html; charset=utf-8', redirected = false} = {}) {
  const headers = {'Content-Type': type};
  if (marker !== null) headers['X-Rahhal-Offline'] = marker;
  const result = new Response(body, {status, headers});
  Object.defineProperty(result, 'redirected', {value: redirected});
  // Preserve the fixture's redirected state when cache clones it.
  const nativeClone = result.clone.bind(result);
  result.clone = () => {
    const clone = nativeClone();
    Object.defineProperty(clone, 'redirected', {value: redirected});
    return clone;
  };
  return result;
}

function setup({network = async () => response(), failStorage = false} = {}) {
  const listeners = {};
  const stores = new Map();
  const operations = {fetch: [], put: [], match: [], open: [], delete: [], requests: [], forbidden: 0};
  const forbidden = () => { operations.forbidden++; throw Error('Unexpected persistent/background/client operation'); };
  const cacheStorage = {
    async open(name) {
      operations.open.push(name);
      if (failStorage) throw Error('Cache storage unavailable');
      if (!stores.has(name)) stores.set(name, new Map());
      return {
        async put(key, value) {
          operations.put.push([name, key]);
          stores.get(name).set(key, value.clone());
        },
        async match(key) {
          operations.match.push([name, key]);
          return stores.get(name).get(key)?.clone();
        },
        add: forbidden, addAll: forbidden, delete: forbidden,
      };
    },
    async keys() { return Array.from(stores.keys()); },
    async delete(name) { operations.delete.push(name); return stores.delete(name); },
    match: forbidden,
  };
  class WorkerRequest extends Request {
    constructor(input, options) {
      super(new URL(input, ORIGIN), options);
      operations.requests.push(this);
    }
  }
  const context = {
    URL, Request: WorkerRequest, Response, Headers, caches: cacheStorage,
    fetch: async (...args) => { operations.fetch.push(args); return network(...args); },
    self: {
      location: {origin: ORIGIN},
      addEventListener(name, callback) {
        assert.ok(['install', 'activate', 'fetch'].includes(name), 'No sync/notification/message listener');
        assert.equal(listeners[name], undefined);
        listeners[name] = callback;
      },
      skipWaiting: forbidden,
      clients: {claim: forbidden, matchAll: forbidden},
      registration: {sync: {register: forbidden}, periodicSync: {register: forbidden}, showNotification: forbidden},
    },
    indexedDB: {open: forbidden}, localStorage: {getItem: forbidden, setItem: forbidden},
    setTimeout: forbidden, setInterval: forbidden, BroadcastChannel: forbidden,
  };
  vm.createContext(context);
  vm.runInContext(script, context, {filename: 'pwa-service-worker.js'});
  return {
    operations, stores,
    async lifecycle(name) {
      let pending;
      listeners[name]({waitUntil(promise) { assert.equal(pending, undefined); pending = promise; }});
      assert.ok(pending, 'Lifecycle work must be attached to waitUntil');
      return pending;
    },
    dispatch(request) {
      let pending;
      let called = false;
      listeners.fetch({request, respondWith(promise) {
        assert.equal(called, false, 'No double respondWith');
        called = true;
        pending = promise;
      }});
      return {called, pending};
    },
    seed(name, key, value) {
      if (!stores.has(name)) stores.set(name, new Map());
      stores.get(name).set(key, value);
    },
  };
}

function request(url = '/participants/dashboard/', overrides = {}) {
  const forbiddenBodyRead = () => { throw Error('Request body must not be inspected'); };
  return {
    url: new URL(url, ORIGIN).href, method: 'GET', mode: 'navigate', destination: 'document',
    credentials: 'include', headers: new Headers({'Cookie': 'sessionid=synthetic-private-session', 'Authorization': 'Bearer synthetic-token'}),
    text: forbiddenBodyRead, json: forbiddenBodyRead, arrayBuffer: forbiddenBodyRead,
    formData: forbiddenBodyRead, clone: forbiddenBodyRead, ...overrides,
  };
}

async function test(name, callback) {
  await callback();
  groups++;
  console.log('PASS ' + name);
}

(async () => {
  await test('only offline cache, lifecycle ownership and no persistent/background/client APIs', async () => {
    assert.match(script, /const CACHE_NAME = 'rahhal-public-offline-v1'/);
    assert.match(script, /const OFFLINE_URL = '\/offline\/'/);
    assert.doesNotMatch(script, /skipWaiting\s*\(|clients\.claim\s*\(|postMessage\s*\(|BroadcastChannel|indexedDB|localStorage|sessionStorage|Notification|pushManager|\.sync\.|setInterval|setTimeout|\.addAll\(|\.add\(/);
    const f = setup();
    assert.equal(f.operations.forbidden, 0);
    assert.equal(f.operations.fetch.length, 0, 'Evaluating the worker never fetches the dynamic root');
  });

  await test('installation fetches one fixed public HTML with omit credentials and reload, caches nothing else', async () => {
    const f = setup();
    await f.lifecycle('install');
    assert.equal(f.operations.fetch.length, 1);
    assert.equal(f.operations.requests.length, 1);
    const [[actual, options]] = f.operations.fetch;
    assert.equal(actual.url, ORIGIN + OFFLINE_URL);
    assert.equal(actual.method, 'GET');
    assert.equal(actual.credentials, 'omit');
    assert.equal(actual.cache, 'reload');
    assert.equal(actual.headers.get('Cookie'), null);
    assert.equal(actual.headers.get('Authorization'), null);
    assert.equal(options, undefined);
    assert.deepEqual(f.operations.put, [[CACHE_NAME, OFFLINE_URL]]);
    assert.deepEqual(Array.from(f.stores.get(CACHE_NAME).keys()), [OFFLINE_URL]);
    assert.equal(await f.stores.get(CACHE_NAME).get(OFFLINE_URL).clone().text(), offlineHTML);
    assert.equal(f.operations.forbidden, 0);
  });

  await test('redirected login, absent/wrong marker, non-HTML and HTTP errors are refused before cache access', async () => {
    for (const options of [
      {redirected: true}, {marker: null}, {marker: 'private'},
      {type: 'application/json'}, {type: 'text/html-not-really'}, {status: 403}, {status: 500},
    ]) {
      const f = setup({network: async () => response('<form>private login</form>', options)});
      await assert.rejects(f.lifecycle('install'), /non-public offline response/);
      assert.equal(f.operations.fetch.length, 1);
      assert.equal(f.operations.open.length, 0);
      assert.equal(f.operations.put.length, 0);
      assert.equal(f.stores.size, 0);
    }
    const f = setup({network: async () => { throw Error('Network disconnected'); }});
    await assert.rejects(f.lifecycle('install'), /Network disconnected/);
    assert.equal(f.operations.put.length, 0);
  });

  await test('POST/PUT/DELETE/HEAD/JSON/subresource/third-party/download requests are entirely unintercepted', async () => {
    const f = setup();
    const ignored = [
      request('/participants/tasks/submit/', {method: 'POST'}),
      request('/participants/supervisor/dashboard/', {method: 'POST'}),
      request('/participants/store/', {method: 'POST'}),
      request('/participants/dashboard/', {method: 'PUT'}),
      request('/participants/dashboard/', {method: 'DELETE'}),
      request('/participants/dashboard/', {method: 'HEAD'}),
      request('/participants/data/?format=json', {mode: 'same-origin', destination: ''}),
      request('/participants/reports/', {mode: 'cors', destination: '', headers: new Headers({'Accept': 'application/json'})}),
      request('https://other.example/private'),
      request('/static/app.js', {mode: 'no-cors', destination: 'script'}),
      request('/media/private.pdf', {mode: 'same-origin', destination: ''}),
      request('/download', {destination: ''}),
      request('/static/example.html'), request('/media/private.pdf'),
      request('/participants/reports/export/?format=xlsx'),
      request('/participants/data/export-pdf/'),
    ];
    for (const item of ignored) {
      const result = f.dispatch(item);
      assert.equal(result.called, false, 'No respondWith for ' + item.method + ' ' + item.url);
      assert.equal(result.pending, undefined);
    }
    assert.deepEqual(f.operations.fetch, []);
    assert.deepEqual(f.operations.put, []);
    assert.deepEqual(f.operations.open, []);
  });

  await test('authenticated navigation sends the ORIGINAL request and returns real response unchanged with zero caching', async () => {
    const real = response('<h1>synthetic account dashboard</h1>', {marker: null});
    const f = setup({network: async () => real});
    const original = request();
    const result = f.dispatch(original);
    assert.equal(result.called, true);
    assert.strictEqual(await result.pending, real);
    assert.strictEqual(f.operations.fetch[0][0], original);
    assert.equal(f.operations.fetch[0].length, 1, 'No altered credentials/options');
    assert.equal(original.credentials, 'include');
    assert.equal(original.headers.get('Cookie'), 'sessionid=synthetic-private-session');
    assert.equal(original.headers.get('Authorization'), 'Bearer synthetic-token');
    assert.deepEqual(f.operations.put, []);
    assert.deepEqual(f.operations.open, []);
  });

  await test('HTTP 400/403/500 and auth redirect/login are never masked or cached', async () => {
    for (const options of [{status: 400}, {status: 403}, {status: 500}, {redirected: true}]) {
      const real = response('<h1>real server response</h1>', {...options, marker: null});
      const f = setup({network: async () => real});
      f.seed(CACHE_NAME, OFFLINE_URL, response());
      assert.strictEqual(await f.dispatch(request()).pending, real);
      assert.equal(f.operations.fetch.length, 1);
      assert.deepEqual(f.operations.match, []);
      assert.deepEqual(f.operations.put, []);
    }
  });

  await test('only network error returns the fixed owned offline entry with no private lookup/retry/write', async () => {
    const f = setup({network: async () => { throw Error('Offline'); }});
    f.seed('other-app', OFFLINE_URL, response('unrelated content'));
    f.seed(CACHE_NAME, '/participants/dashboard/', response('synthetic private account'));
    f.seed(CACHE_NAME, OFFLINE_URL, response());
    const result = await f.dispatch(request()).pending;
    assert.equal(await result.text(), offlineHTML);
    assert.equal(result.headers.get('X-Rahhal-Offline'), 'public-v1');
    assert.equal(f.operations.fetch.length, 1);
    assert.deepEqual(f.operations.match, [[CACHE_NAME, OFFLINE_URL]]);
    assert.deepEqual(f.operations.put, []);
    assert.equal(f.operations.forbidden, 0);
  });

  await test('missing/corrupt/redirected cache cannot leak content; static Arabic notice is safe', async () => {
    for (const options of [undefined, {marker: null}, {marker: 'wrong'}, {type: 'application/json'}, {status: 500}, {redirected: true}]) {
      const f = setup({network: async () => { throw Error('Offline'); }});
      if (options) f.seed(CACHE_NAME, OFFLINE_URL, response('private sentinel must not leak', options));
      f.seed('another-app', OFFLINE_URL, response('another app must not leak'));
      const result = await f.dispatch(request()).pending;
      const body = await result.text();
      assert.equal(result.status, 503);
      assert.equal(result.headers.get('Cache-Control'), 'no-store');
      assert.equal(result.headers.get('X-Content-Type-Options'), 'nosniff');
      assert.equal(result.headers.get('Content-Type'), 'text/html; charset=utf-8');
      assert.ok(body.includes(offlineText));
      assert.match(body, /lang="ar" dir="rtl"/);
      assert.match(body, /location\.reload\(\)/);
      assert.match(body, /href="\/"/);
      assert.doesNotMatch(body, /private sentinel|another app|<form|<input|\bsrc=|https?:\/\//);
      assert.deepEqual(f.operations.put, []);
      assert.equal(f.operations.fetch.length, 1);
    }
  });

  await test('unavailable Cache Storage still returns static notice without dependencies', async () => {
    const f = setup({failStorage: true, network: async () => { throw Error('Offline'); }});
    const result = await f.dispatch(request()).pending;
    assert.equal(result.status, 503);
    assert.ok((await result.text()).includes(offlineText));
    assert.deepEqual(f.operations.put, []);
    assert.equal(f.operations.fetch.length, 1);
  });

  await test('activation deletes only older owned-prefix caches; current and unrelated remain', async () => {
    const f = setup();
    const names = [CACHE_NAME, 'rahhal-public-offline-v0', 'rahhal-public-offline-v-old',
      'other-app-cache', 'rahhal-private-v1', 'rahhal-public-offline', 'foreign-rahhal-public-offline-v0'];
    for (const name of names) f.seed(name, '/keep', response());
    await f.lifecycle('activate');
    assert.deepEqual(f.operations.delete.sort(), ['rahhal-public-offline-v-old', 'rahhal-public-offline-v0'].sort());
    assert.deepEqual(Array.from(f.stores.keys()).sort(), names.filter(name => !f.operations.delete.includes(name)).sort());
    assert.deepEqual(f.operations.fetch, []);
    assert.deepEqual(f.operations.put, []);
    assert.equal(f.operations.forbidden, 0, 'No skipWaiting/claim/reload/broadcast');
  });

  console.log(`PWA worker: ${groups} contract groups passed. No browser, database, network, storage credentials or production data used.`);
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
