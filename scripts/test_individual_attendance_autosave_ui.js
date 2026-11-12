/* Actual Chromium + Django views/templates/CSRF + ephemeral SQLite :memory:.
 * Run: NODE_PATH=/tmp/mobile-ui-deps/node_modules node scripts/test_individual_attendance_autosave_ui.js
 * No dependency installation, production accounts, real DB, or external network.
 * Fault/race responses are selectively intercepted; default successful POSTs reach
 * the real view, with raw browser multipart forwarded by the finite Python helper.
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const project = path.resolve(__dirname, '..');
const kinds = ['supervisor_dashboard', 'quran_circle_attendance', 'weekly_activity_attendance'];
const widths = [360, 390, 1280];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const token = crypto.randomBytes(24).toString('hex');
let helper, browser, origin, metadata, serial = 0, passed = 0;
const evidence = [];

function multipart(text) {
  const data = {};
  for (const match of text.matchAll(/name="([^"]+)"\r\n\r\n([\s\S]*?)(?=\r\n--)/g)) {
    assert.equal(data[match[1]], undefined, `Duplicate multipart field: ${match[1]}`);
    data[match[1]] = match[2];
  }
  return data;
}
async function eventually(predicate, message, timeout = 10000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await delay(20);
  }
  assert.fail(message);
}
async function startHelper() {
  helper = spawn(process.env.PYTHON || path.join(project, '.venv/bin/python'),
    [path.join(__dirname, 'attendance_autosave_fixture.py'), '--token', token, '--max-seconds', '900'],
    {cwd: project, env: {...process.env, USE_POSTGRES:'False', DJANGO_SETTINGS_MODULE:'config.test_settings'}});
  let stderr = '';
  helper.stderr.on('data', chunk => { stderr += chunk.toString(); });
  const lines = readline.createInterface({input:helper.stdout});
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Fixture startup timeout: ${stderr}`)), 60000);
    lines.once('line', line => {clearTimeout(timer); try {resolve(JSON.parse(line));} catch (error) {reject(error);} });
    helper.once('exit', code => {clearTimeout(timer); reject(new Error(`Fixture exited ${code}: ${stderr}`));});
  });
  lines.close();
  assert.equal(ready.database, ':memory:');
  assert.equal(ready.settings, 'config.test_settings');
  assert.match(ready.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
  origin = ready.origin;
  metadata = await db();
}
async function db() {
  const response = await fetch(`${origin}/__fixture__/state?token=${token}`);
  assert.equal(response.status, 200);
  const result = await response.json();
  assert.equal(result.database, ':memory:');
  assert.equal(result.settings, 'config.test_settings');
  return result;
}
function fields(kind) {
  return kind === 'supervisor_dashboard' ? ['attended', 'early'] : kind === 'quran_circle_attendance' ? ['attended', 'achieved'] : ['attended'];
}
function dbRows(state, kind, date) {
  const source = kind === 'supervisor_dashboard' ? 'meeting' : kind === 'quran_circle_attendance' ? 'circle' : 'activity';
  const dateKey = kind === 'supervisor_dashboard' ? 'week_start_date' : 'date';
  return state[source].filter(row => row[dateKey] === date);
}
function assertIndividual(request, f, id, flags = {}) {
  assert.equal(request.method, 'POST');
  assert.equal(request.headers.accept, 'application/json');
  assert.match(request.headers['content-type'], /^multipart\/form-data; boundary=/);
  assert.equal(new URL(request.url).pathname, metadata.routes[f.kind]);
  assert.equal(request.data.partial_roster, '1');
  assert.equal(request.data.group, String(metadata.group));
  assert.equal(request.data.date, f.date);
  assert.ok(request.data.csrfmiddlewaretoken.length >= 32, 'Real CSRF token retained');
  const allowed = new Set(['partial_roster', 'group', 'date', 'csrfmiddlewaretoken', `prepared_${id}`, ...fields(f.kind).map(col => `${col}_${id}`)]);
  for (const key of Object.keys(request.data)) assert.ok(allowed.has(key), `Individual payload leaks ${key}`);
  assert.equal(request.data[`prepared_${id}`], '1');
  assert.deepEqual(Object.keys(request.data).filter(key => key.startsWith('prepared_')), [`prepared_${id}`]);
  for (const col of fields(f.kind)) assert.equal(request.data[`${col}_${id}`], flags[col] ? 'on' : undefined, `${col}: captured individual intent only`);
}
async function fixture(kind, width, options = {}) {
  // Every scenario/date is isolated; no ORM reset and no on-disk database.
  const date = new Date(Date.UTC(2027, 0, ++serial)).toISOString().slice(0, 10);
  const context = await browser.newContext({viewport:{width, height:844}, javaScriptEnabled:options.js !== false, serviceWorkers:'block'});
  const page = await context.newPage();
  const f = {kind, width, date, context, page, requests:[], completed:0, active:0, maxActive:0,
    held:false, releases:[], mode:'live', confirms:0, confirm:true, errors:[]};
  const activeRequests = new Map();
  page.on('response', response => {
    const record = activeRequests.get(response.request());
    if (record && record.active) {record.active = false; f.active--;}
  });
  page.on('pageerror', error => f.errors.push(error.message));
  page.on('dialog', async dialog => {f.confirms++; await (f.confirm ? dialog.accept() : dialog.dismiss());});
  await page.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.origin !== origin) return route.fulfill({status:200, body:''}); // Never contact fonts/CDNs/production.
    if (req.method() !== 'POST' || !Object.values(metadata.routes).includes(url.pathname)) return route.continue();
    const headers = req.headers();
    const data = (headers['content-type'] || '').includes('application/x-www-form-urlencoded')
      ? Object.fromEntries(new URLSearchParams(req.postData() || '')) : multipart(req.postData() || '');
    const record = {data, raw:req.postData(), headers, url:req.url(), method:req.method(), active:true};
    activeRequests.set(req, record);
    f.requests.push(record);
    f.active++; f.maxActive = Math.max(f.maxActive, f.active);
    try {
      if (f.held) await new Promise(resolve => f.releases.push(resolve));
      if (f.mode === 'live') {
        // Continue the actual browser request verbatim (including native no-JS
        // urlencoded POSTs). Await its response before counting the queue idle.
        const received = page.waitForResponse(response => response.request() === req);
        await route.continue();
        const response = await received;
        record.status = response.status();
        if ((response.headers()['content-type'] || '').includes('application/json')) record.ack = await response.json();
        return;
      }
      if (f.mode === 'network') return await route.abort('failed');
      if (f.mode === 'login') return await route.fulfill({status:302, headers:{location:'/accounts/login/supervisor/'}, body:''});
      if (f.mode === 'html') return await route.fulfill({status:200, contentType:'text/html', body:'<h1>Login</h1>'});
      if (f.mode === 'invalid-json') return await route.fulfill({status:200, contentType:'application/json', body:'{not JSON'});
      const ack = {success:true, saved_ids:Object.keys(record.data).filter(k => k.startsWith('prepared_')).map(k => Number(k.slice(9))), saved_note_ids:[], date:record.data.date, group_id:Number(record.data.group), saved_at:'18:40'};
      if (f.mode === 'missing-ids') delete ack.saved_ids;
      if (f.mode === 'empty-ids') ack.saved_ids = [];
      if (f.mode === 'wrong-ids') ack.saved_ids = [metadata.outside_id];
      if (f.mode === 'duplicate-ids') ack.saved_ids = [ack.saved_ids[0], ack.saved_ids[0]];
      if (f.mode === 'extra-ids') ack.saved_ids.push(metadata.outside_id);
      if (f.mode === 'ids-string') ack.saved_ids = String(ack.saved_ids[0]);
      if (f.mode === 'object-id') ack.saved_ids = [{id:ack.saved_ids[0]}];
      if (f.mode === 'unexpected-notes') ack.saved_note_ids = [metadata.ids[0]];
      if (f.mode === 'wrong-date') ack.date = '2020-01-01';
      if (f.mode === 'wrong-group') ack.group_id = Number(metadata.group) + 100;
      if (f.mode === 'bad-time') ack.saved_at = '25:61';
      if (f.mode === 'false-success') ack.success = false;
      if (f.mode === 'string-success') ack.success = 'true';
      if (f.mode === 'not-object') return await route.fulfill({status:200, contentType:'application/json', body:'null'});
      return await route.fulfill({status:f.mode === '403' ? 403 : f.mode === '500' ? 500 : 200, contentType:'application/json', body:JSON.stringify(ack)});
    } catch (error) {
      f.errors.push(error.message);
      await route.abort('failed').catch(() => {});
    } finally {
      if (record.active) {record.active = false; f.active--;}
      activeRequests.delete(req);
      f.completed++;
    }
  });
  f.row = id => page.locator(`tr[data-participant-id="${id}"]`);
  f.box = (id, col = 'attended') => f.row(id).locator(`input[data-col="${col}"]`);
  f.save = () => page.locator('[data-roster-form] [type="submit"]');
  f.bulk = col => page.locator(`.select-all[data-target="${col}"]:visible`).first().click();
  f.release = () => {const resolve = f.releases.shift(); assert.ok(resolve, 'Expected held request'); resolve();};
  f.waitHeld = n => eventually(() => f.releases.length >= n, 'Request was not held');
  f.quiet = async () => {
    await eventually(() => f.active === 0 && f.completed === f.requests.length, 'Requests did not settle');
    await delay(120);
    await eventually(() => f.active === 0, 'Queue did not settle');
    assert.deepEqual(f.errors, [], 'No uncaught JavaScript errors');
  };
  f.waitCount = n => eventually(() => f.requests.length >= n, `Expected ${n} POSTs; got ${f.requests.length}`);
  f.clean = () => page.waitForFunction(() => /:\s*0\s*$/.test(document.querySelector('[data-dirty-count]').textContent));
  f.reload = async () => {await page.reload(); if (options.js !== false) await page.waitForSelector('[data-mobile-initialized="true"]');};
  f.close = () => context.close();
  await page.goto(`${origin}${metadata.routes[kind]}?date=${date}&group=${metadata.group}`);
  if (options.js !== false) await page.waitForSelector('[data-mobile-initialized="true"]');
  f.baseline = await db();
  return f;
}
async function assertDB(f, expected, delta = {}) {
  const state = await db();
  const rows = dbRows(state, f.kind, f.date);
  assert.deepEqual(rows.map(row => row.participant_id).sort((a,b) => a-b), Object.keys(expected).map(Number).sort((a,b) => a-b), 'Only expected student rows persisted');
  for (const [id, flags] of Object.entries(expected)) {
    const row = rows.find(item => item.participant_id === Number(id));
    for (const col of fields(f.kind)) assert.equal(row[col === 'early' ? 'is_early' : col], Boolean(flags[col]), `DB ${f.kind} ${id} ${col}`);
  }
  for (const balance of state.balances) {
    const previous = f.baseline.balances.find(item => item.id === balance.id);
    const points = delta[balance.id] || 0;
    assert.equal(balance.points, previous.points + points, 'Exact points delta');
    assert.equal(balance.miles, previous.miles + points * 10, 'Exact miles delta');
    assert.equal(balance.purchase_points, previous.purchase_points + points, 'Exact purchase points delta');
  }
  assert.equal(state.notes.filter(row => row.meeting_date === f.date).length, 0, 'Autosave never stores notes');
  return state;
}
async function replay(f, request) {
  return f.page.evaluate(async ({url, data}) => {
    const body = new FormData();
    for (const [name, value] of Object.entries(data)) body.append(name, value);
    const response = await fetch(url, {method:'POST', headers:{Accept:'application/json'}, body, credentials:'same-origin'});
    return {status:response.status, body:await response.json()};
  }, {url:request.url, data:request.data});
}
async function successCase(label, fn) {
  if (process.env.AUTOSAVE_CASE && !label.includes(process.env.AUTOSAVE_CASE)) return;
  await fn();
  passed++; evidence.push(label);
  console.log(`PASS ${label}`);
}

(async () => {
  try {
    await startHelper();
    browser = await chromium.launch({executablePath:process.env.CHROMIUM || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
    const [a,b,c] = metadata.ids;
    for (const kind of kinds) for (const width of widths) {
      await successCase(`live basic check/uncheck/reload/idempotency ${kind} ${width}`, async () => {
        const f = await fixture(kind, width);
        try {
          await f.box(a).check(); await f.waitCount(1); await f.quiet(); await f.clean();
          assertIndividual(f.requests[0], f, a, {attended:true});
          const points = kind === 'supervisor_dashboard' ? 8 : kind === 'quran_circle_attendance' ? 3 : 10;
          const saved = await assertDB(f, {[a]:{attended:true}}, {[a]:points});
          await f.reload(); assert.equal(await f.box(a).isChecked(), true, 'Reload re-reads saved DB');
          assert.equal(await f.row(b).getAttribute('data-recorded'), 'false');
          const repeated = await replay(f, f.requests[0]); assert.equal(repeated.status, 200); assert.equal(repeated.body.success, true);
          const afterRepeat = await assertDB(f, {[a]:{attended:true}}, {[a]:points});
          assert.deepEqual(afterRepeat.ledger, saved.ledger, 'Identical POST cannot award twice');
          await f.box(a).uncheck(); await f.waitCount(3); await f.quiet(); await f.clean();
          assertIndividual(f.requests[2], f, a, {});
          await assertDB(f, {[a]:{}}, {}); await f.reload();
          assert.equal(await f.box(a).isChecked(), false); assert.equal(await f.row(a).getAttribute('data-recorded'), 'true', 'Negative record survives reload');
        } finally {await f.close();}
      });
      if (kind !== 'weekly_activity_attendance') await successCase(`live secondary flags ${kind} ${width}`, async () => {
        const f = await fixture(kind, width);
        try {
          const secondary = fields(kind)[1];
          await f.box(a, secondary).check(); await f.waitCount(1); await f.quiet(); await f.clean();
          const flags = kind === 'supervisor_dashboard' ? {attended:true, early:true} : {achieved:true};
          assert.equal(f.requests.length, 1, 'Early coupling is one consistent POST');
          assertIndividual(f.requests[0], f, a, flags);
          await assertDB(f, {[a]:flags}, {[a]:kind === 'supervisor_dashboard' ? 10 : 2});
          await f.reload(); assert.equal(await f.box(a, secondary).isChecked(), true);
          assert.equal(await f.box(a).isChecked(), kind === 'supervisor_dashboard', 'Circle achievement does not imply attendance');
          await f.box(a, secondary).uncheck(); await f.waitCount(2); await f.quiet(); await f.clean();
          const after = kind === 'supervisor_dashboard' ? {attended:true} : {};
          assertIndividual(f.requests[1], f, a, after);
          await assertDB(f, {[a]:after}, {[a]:kind === 'supervisor_dashboard' ? 8 : 0});
          await f.reload(); assert.equal(await f.box(a, secondary).isChecked(), false);
          if (kind === 'quran_circle_attendance') {
            await f.box(a).check(); await f.quiet(); await f.waitCount(3);
            await f.box(a, 'achieved').check(); await f.waitCount(4); await f.quiet(); await f.clean();
            await assertDB(f, {[a]:{attended:true, achieved:true}}, {[a]:5});
            await f.reload(); assert.equal(await f.box(a).isChecked(), true); assert.equal(await f.box(a, 'achieved').isChecked(), true);
          }
        } finally {await f.close();}
      });
      await successCase(`live bulk manual then individual ${kind} ${width}`, async () => {
        const f = await fixture(kind, width);
        try {
          await f.page.locator('#roster-search').fill('9000000001');
          await f.bulk('attended'); await delay(350);
          assert.equal(f.requests.length, 0, 'Bulk must not send a POST');
          assert.equal(await f.box(c).isChecked(), true, 'Hidden students included');
          await assertDB(f, {}, {});
          await f.save().click(); await f.waitCount(1); await f.quiet(); await f.clean();
          for (const id of [a,b,c]) assert.equal(f.requests[0].data[`prepared_${id}`], '1');
          const points = kind === 'supervisor_dashboard' ? 8 : kind === 'quran_circle_attendance' ? 3 : 10;
          await assertDB(f, {[a]:{attended:true}, [b]:{attended:true}, [c]:{attended:true}}, {[a]:points,[b]:points,[c]:points});
          await f.reload(); for (const id of [a,b,c]) assert.equal(await f.box(id).isChecked(), true);
          await f.box(a).uncheck(); await f.waitCount(2); await f.quiet(); await f.clean();
          assertIndividual(f.requests[1], f, a, {});
          await assertDB(f, {[a]:{},[b]:{attended:true},[c]:{attended:true}}, {[b]:points,[c]:points});
          await f.reload(); assert.equal(await f.box(a).isChecked(), false); assert.equal(await f.box(b).isChecked(), true);
          await f.bulk('attended'); await delay(350); assert.equal(f.requests.length, 2);
          await f.bulk('attended'); await delay(350); assert.equal(f.requests.length, 2, 'Unselect-all does not autosave');
          await f.save().click(); await f.waitCount(3); await f.quiet(); await f.clean();
          await assertDB(f, {[a]:{},[b]:{},[c]:{}}, {}); await f.reload();
          for (const id of [a,b,c]) assert.equal(await f.box(id).isChecked(), false);
        } finally {await f.close();}
      });
      await successCase(`live individual isolation before manual bulk ${kind} ${width}`, async () => {
        const f = await fixture(kind, width);
        try {
          const bulkCol = fields(kind)[1] || 'attended';
          await f.bulk(bulkCol); await delay(350); assert.equal(f.requests.length, 0);
          if (kind === 'supervisor_dashboard') await f.box(a, 'early').uncheck();
          else if (kind === 'quran_circle_attendance') await f.box(a).check();
          else await f.box(a).uncheck();
          await f.waitCount(1); await f.quiet();
          const flags = kind === 'quran_circle_attendance' ? {attended:true} : {};
          assertIndividual(f.requests[0], f, a, flags);
          const points = kind === 'quran_circle_attendance' ? 3 : 0;
          await assertDB(f, {[a]:flags}, {[a]:points});
          assert.equal(await f.box(b, bulkCol).isChecked(), true, 'Other students keep manual bulk drafts');
          assert.match(await f.page.locator('[data-dirty-count]').textContent(), /[23]/);
          // Reload deliberately discards unapproved bulk, and proves it never hit DB.
          await f.reload();
          assert.equal(await f.box(a).isChecked(), kind === 'quran_circle_attendance');
          assert.equal(await f.box(b, bulkCol).isChecked(), false);
          assert.equal(await f.row(b).getAttribute('data-recorded'), 'false');
        } finally {await f.close();}
      });
    }
    // Every desktop/mobile bulk column toggles all hidden rows without any request.
    for (const kind of kinds) for (const width of widths) await successCase(`all bulk columns no autosave ${kind} ${width}`, async () => {
      const f = await fixture(kind, width);
      try {
        await f.page.locator('#roster-search').fill('9000000001');
        for (const col of fields(kind)) {
          await f.bulk(col); await delay(350); assert.equal(f.requests.length, 0);
          for (const id of [a,b,c]) assert.equal(await f.box(id,col).isChecked(), true);
          if (col === 'early') for (const id of [a,b,c]) assert.equal(await f.box(id).isChecked(), true);
          await f.bulk(col); await delay(350); assert.equal(f.requests.length, 0);
          for (const id of [a,b,c]) assert.equal(await f.box(id,col).isChecked(), false);
        }
        await assertDB(f, {}, {});
      } finally {await f.close();}
    });
    for (const kind of kinds) for (const width of widths) await successCase(`live fast held latest wins ${kind} ${width}`, async () => {
      const f = await fixture(kind, width);
      try {
        f.held = true;
        await f.box(a).check(); await f.waitHeld(1);
        await f.box(a).uncheck(); await f.box(b).check(); await f.box(c).check();
        await f.box(b).uncheck(); await f.box(c).uncheck(); await f.box(c).check();
        assert.equal(f.requests.length, 1, 'Global queue allows exactly one active POST');
        assert.equal(await f.box(a).isEnabled(), true, 'Individual input stays editable');
        assert.equal(await f.page.evaluate(() => {const event = new Event('beforeunload',{cancelable:true}); window.dispatchEvent(event); return event.defaultPrevented;}), true);
        f.release(); await f.waitHeld(1);
        assert.match(await f.row(a).locator('[data-row-state]').textContent(), /غير محفوظ|جار|معلق|تعذر/, 'Old ACK cannot confirm newer unchecked state');
        assert.equal(f.maxActive, 1);
        f.held = false; f.release(); await f.quiet(); await f.clean();
        assert.equal(f.maxActive, 1, 'No parallel row POSTs');
        for (const request of f.requests) {
          const id = Number(Object.keys(request.data).find(k => k.startsWith('prepared_')).slice(9));
          assertIndividual(request, f, id, Object.fromEntries(fields(kind).map(col => [col,request.data[`${col}_${id}`] === 'on'])));
        }
        const points = kind === 'supervisor_dashboard' ? 8 : kind === 'quran_circle_attendance' ? 3 : 10;
        const state = await db();
        const finalRows = dbRows(state, kind, f.date);
        assert.equal(finalRows.find(row => row.participant_id === a).attended, false);
        assert.equal(finalRows.find(row => row.participant_id === c).attended, true);
        const optionalB = finalRows.find(row => row.participant_id === b);
        if (optionalB) assert.equal(optionalB.attended, false);
        const actual = Object.fromEntries(finalRows.map(row => [row.participant_id,{attended:row.attended}]));
        await assertDB(f, actual, {[c]:points});
        await f.reload(); assert.equal(await f.box(a).isChecked(), false); assert.equal(await f.box(b).isChecked(), false); assert.equal(await f.box(c).isChecked(), true);
      } finally {await f.close();}
    });
    for (const kind of kinds) await successCase(`bulk while auto held and manual interlock ${kind}`, async () => {
      const f = await fixture(kind, 390);
      try {
        f.held = true; await f.box(a).check(); await f.waitHeld(1);
        await f.bulk(fields(kind)[1] || 'attended'); await delay(350);
        assert.equal(f.requests.length, 1, 'Bulk while auto in flight remains draft');
        await f.save().click();
        assert.equal(await f.save().isDisabled(), true, 'Manual barrier locks duplicate submits while queued');
        await f.page.evaluate(() => document.querySelector('[data-roster-form]').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
        assert.equal(f.requests.length, 1, 'Programmatic duplicate submit cannot overlap');
        f.held = false; f.release(); await f.waitCount(2); await f.quiet(); await f.clean();
        assertIndividual(f.requests[0], f, a, {attended:true});
        assert.equal(await f.box(b,fields(kind)[1] || 'attended').isChecked(), true);
        const manual = f.requests[1].data;
        assert.equal(manual[`prepared_${b}`], '1'); assert.equal(manual[`prepared_${c}`], '1');
        assert.equal(f.maxActive, 1, 'Manual barrier waits for autosave ACK');
        await f.reload(); for (const id of [a,b,c]) assert.equal(await f.box(id, fields(kind)[1] || 'attended').isChecked(), true);
      } finally {await f.close();}
    });
    // Actual opt-in UI controls (read from the implemented templates/controller).
    const noticeSelector = '[data-autosave-feedback]';
    const retrySelector = '[data-autosave-retry]';
    for (const width of widths) await successCase(`ACK-only temporary top notification ${width}`, async () => {
      const f = await fixture('weekly_activity_attendance', width);
      try {
        f.held = true; await f.box(a).check(); await f.waitHeld(1);
        assert.doesNotMatch(await f.page.locator(noticeSelector).textContent(), /تم الحفظ بنجاح/);
        assert.equal(await f.row(a).getAttribute('data-recorded'), 'false');
        f.held = false; f.release(); await f.quiet(); await f.clean();
        assert.match(await f.page.locator(noticeSelector).textContent(), /تم الحفظ بنجاح/);
        const notice = await f.page.locator(noticeSelector).evaluate(node => {
          const rect=node.getBoundingClientRect(); const style=getComputedStyle(node);
          return {top:rect.top,bottom:rect.bottom,pointerEvents:style.pointerEvents,overflow:document.documentElement.scrollWidth>innerWidth};
        });
        assert.ok(notice.top >= 0 && notice.top < 140, 'Small notification at top');
        assert.ok(notice.bottom < 200, 'Notification does not occupy roster');
        assert.equal(notice.overflow, false);
        await f.page.waitForFunction(selector => {const node=document.querySelector(selector); return node.hidden || !node.textContent.includes('تم الحفظ بنجاح') || getComputedStyle(node).display==='none';}, noticeSelector, {timeout:10000});
      } finally {await f.close();}
    });
    const faults = ['403','500','login','html','network','missing-ids','empty-ids','wrong-ids','duplicate-ids','extra-ids','ids-string','object-id','unexpected-notes','wrong-date','wrong-group','bad-time','false-success','string-success','invalid-json','not-object'];
    for (const kind of kinds) for (const width of widths) for (const mode of faults) await successCase(`failure retry isolation ${kind} ${width} ${mode}`, async () => {
      const f = await fixture(kind, width);
      try {
        const bulkCol = fields(kind)[1] || 'attended';
        await f.bulk(bulkCol);
        if (kind === 'supervisor_dashboard') {
          await f.row(a).locator('summary').click(); await f.row(a).locator('textarea').fill('ملاحظة معلقة لا تدخل الحفظ الفردي');
        }
        f.mode = mode;
        if (kind === 'quran_circle_attendance') await f.box(a).check();
        else await f.box(a, bulkCol).uncheck();
        await f.waitCount(1); await f.quiet();
        const flags = kind === 'quran_circle_attendance' ? {attended:true} : {};
        assertIndividual(f.requests[0], f, a, flags);
        await f.page.waitForFunction(selector => {const node=document.querySelector(selector); return node && !node.hidden && getComputedStyle(node).display !== 'none';}, retrySelector);
        assert.match(await f.page.locator(noticeSelector).textContent(), /تعذر|فشل/);
        assert.doesNotMatch(await f.page.locator(noticeSelector).textContent(), /تم الحفظ بنجاح/);
        assert.equal(await f.row(a).getAttribute('data-recorded'), 'false', 'Malformed ACK must not update confirmed baseline');
        assert.match(await f.row(a).locator('[data-row-state]').textContent(), /غير محفوظ|تعذر|معلق/);
        assert.equal(await f.box(b,bulkCol).isChecked(), true);
        const retryMetrics = await f.page.locator(retrySelector).evaluate(node => {
          const rect = node.getBoundingClientRect();
          return {height:rect.height,width:rect.width,overflow:document.documentElement.scrollWidth > innerWidth};
        });
        assert.ok(retryMetrics.height >= 44 && retryMetrics.width >= 44, 'Retry has 44px touch target');
        assert.equal(retryMetrics.overflow, false, 'Failure feedback does not overflow viewport');
        await assertDB(f, {}, {});
        f.mode = 'live'; await f.page.locator(retrySelector).click(); await f.waitCount(2); await f.quiet();
        assertIndividual(f.requests[1], f, a, flags);
        await assertDB(f, {[a]:flags}, {[a]:kind === 'quran_circle_attendance' ? 3 : 0});
        assert.equal(await f.box(b,bulkCol).isChecked(), true, 'Retry leaves bulk pending');
        if (kind === 'supervisor_dashboard') assert.equal(await f.row(a).locator('textarea').inputValue(), 'ملاحظة معلقة لا تدخل الحفظ الفردي');
        await f.reload(); assert.equal(await f.box(b,bulkCol).isChecked(), false);
      } finally {await f.close();}
    });
    await successCase('note interlocks held note request and pending autosave', async () => {
      const f = await fixture('supervisor_dashboard', 390);
      try {
        await f.row(a).locator('summary').click(); await f.row(a).locator('textarea').fill('ملاحظة مستقلة');
        f.held = true; await f.row(a).locator('.note-save-button').click(); await f.waitHeld(1);
        await f.box(b).check(); await delay(350);
        assert.equal(f.requests.length, 1, 'Autosave cannot race an independent note request');
        assert.equal(await f.row(a).locator('.note-save-button').isDisabled(), true, 'No duplicate note request');
        f.held = false; f.release(); await f.waitCount(2); await f.quiet(); await f.clean();
        assert.equal(f.requests[0].data.action, 'save_note'); assert.equal(f.requests[0].data.prepared_1, undefined);
        assertIndividual(f.requests[1], f, b, {attended:true}); assert.equal(f.maxActive, 1);
        const state = await db(); assert.equal(state.notes.find(row => row.meeting_date === f.date).text, 'ملاحظة مستقلة');
        await f.reload(); assert.equal(await f.row(a).locator('textarea').inputValue(), 'ملاحظة مستقلة');
        assert.equal(await f.row(a).getAttribute('data-recorded'), 'false'); assert.equal(await f.box(b).isChecked(), true);
      } finally {await f.close();}
    });
    await successCase('auto held blocks notes, date/group and navigation cleanup', async () => {
      const f = await fixture('supervisor_dashboard', 390);
      try {
        await f.row(a).locator('summary').click(); await f.row(a).locator('textarea').fill('مسودة ملاحظة');
        f.held = true; await f.box(b).check(); await f.waitHeld(1);
        const canceled = await f.page.evaluate(() => {const e=new Event('submit',{bubbles:true,cancelable:true}); document.querySelector('form[method="get"]').dispatchEvent(e); return e.defaultPrevented;});
        assert.equal(canceled, true);
        assert.equal(await f.page.evaluate(() => {const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}), true, 'Pending auto/note guards navigation');
        await f.row(a).locator('.note-save-button').click(); await delay(350);
        assert.equal(f.requests.length, 1, 'Independent note cannot overlap active autosave');
        assert.equal(await f.row(a).locator('.note-save-button').isDisabled(), true);
        f.held = false; f.release(); await f.waitCount(2); await f.quiet(); await f.clean();
        assertIndividual(f.requests[0], f, b, {attended:true});
        assert.equal(f.requests[1].data.action, 'save_note');
        assert.equal(f.maxActive, 1);
        assert.equal(await f.page.evaluate(() => {const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}), false);
      } finally {await f.close();}
    });
    for (const kind of kinds) for (const width of widths) await successCase(`no-JS real manual persistence ${kind} ${width}`, async () => {
      const f = await fixture(kind, width, {js:false});
      try {
        await f.box(a).check(); await delay(350); assert.equal(f.requests.length, 0);
        await Promise.all([f.page.waitForNavigation(), f.save().click()]);
        await f.quiet();
        assert.equal(f.requests[0].status, 302, `Native POST failed: ${JSON.stringify(f.requests[0])}; ${await f.page.content()}`);
        assert.equal(new URL(f.page.url()).pathname, metadata.routes[kind], 'No-JS save returns to actual roster, not login');
        assert.equal(f.requests[0].data.partial_roster, undefined, 'Legacy no-JS full-roster POST preserved');
        assert.equal(await f.box(a).isChecked(), true); await f.reload(); assert.equal(await f.box(a).isChecked(), true);
        const state = await db(); assert.equal(dbRows(state, kind, f.date).find(row => row.participant_id === a).attended, true);
      } finally {await f.close();}
    });
    await successCase('real CSRF rejection has no DB side effects', async () => {
      const f = await fixture('weekly_activity_attendance', 390);
      try {
        const request = {url:`${origin}${metadata.routes[f.kind]}`, data:{group:String(metadata.group),date:f.date,partial_roster:'1',[`prepared_${a}`]:'1',[`attended_${a}`]:'on'}};
        const result = await f.page.evaluate(async ({url,data}) => {const body=new FormData();Object.entries(data).forEach(([k,v])=>body.append(k,v));const response=await fetch(url,{method:'POST',body,headers:{Accept:'application/json'}});return response.status;}, request);
        assert.equal(result, 403); await assertDB(f, {}, {});
      } finally {await f.close();}
    });
    const final = await db();
    assert.equal(final.balances.find(row => row.id === metadata.outside_id).points, 0);
    assert.equal(final.ledger.some(row => row.participant_id === metadata.outside_id), false);
    console.log(`Individual attendance autosave UI: ${passed} cases passed; actual Chromium 360/390/1280; Django config.test_settings SQLite :memory:; real POST/CSRF/DB reload/points idempotency, intercepted faults and serialized races. No external requests or production writes.`);
  } finally {
    if (browser) await browser.close();
    if (helper && helper.exitCode === null) {
      const exited = new Promise(resolve => helper.once('exit', resolve));
      helper.kill('SIGTERM'); await Promise.race([exited, delay(3000)]);
      if (helper.exitCode === null) {helper.kill('SIGKILL'); await exited;}
    }
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
