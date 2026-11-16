/* Actual Chromium + real Django middleware/CSRF/views + SQLite :memory:.
 * NODE_PATH=/tmp/mobile-ui-deps/node_modules node scripts/test_elite_trip_ui.js
 * Successes are NEVER fulfilled by fake endpoints. One final-request transport
 * fault is deliberately held/aborted and reported separately, not counted as DB
 * success. Every scenario has a private browser context/session and own trip.
 */
'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const crypto = require('node:crypto');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
let playwright;
try { playwright = require('playwright'); }
catch { playwright = require('/tmp/mobile-ui-deps/node_modules/playwright'); }
const {chromium} = playwright;
const root = path.resolve(__dirname, '..');
const token = crypto.randomBytes(24).toString('hex');
let helper, browser, origin, metadata;
let browserCases = 0, faultCases = 0;
const evidence = [];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function state() {
  const response = await fetch(`${origin}/__fixture__/state?token=${token}`);
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.database, ':memory:');
  assert.equal(data.settings, 'config.test_settings');
  return data;
}
async function start() {
  helper = spawn(process.env.PYTHON || path.join(root, '.venv/bin/python'),
    [path.join(__dirname, 'elite_trip_fixture.py'), '--token', token, '--max-seconds', '900'],
    {cwd:root, env:{...process.env, DJANGO_SETTINGS_MODULE:'config.test_settings', USE_POSTGRES:'False'}});
  let stderr = '';
  helper.stderr.on('data', chunk => {stderr += chunk.toString(); process.stderr.write(chunk);});
  const lines = readline.createInterface({input:helper.stdout});
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error(`Fixture startup timed out: ${stderr}`)), 60000);
    lines.once('line', line => {clearTimeout(timer); try {resolve(JSON.parse(line));} catch (e) {reject(e);} });
    helper.once('exit', code => {clearTimeout(timer); reject(Error(`Fixture exited ${code}: ${stderr}`));});
  });
  lines.close();
  assert.match(ready.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
  assert.equal(ready.database, ':memory:');
  origin = ready.origin;
  metadata = await state();
  assert.ok(metadata.middleware.includes('participants.elite_trip_views.EliteTripPrivateResponseMiddleware'),
    'Parent-owned actual middleware must be integrated; no test override');
  assert.ok(metadata.middleware.indexOf('participants.elite_trip_views.EliteTripPrivateResponseMiddleware') <
    metadata.middleware.indexOf('django.middleware.csrf.CsrfViewMiddleware'));
  browser = await chromium.launch({executablePath:process.env.CHROMIUM || '/usr/bin/chromium',
    headless:true, args:['--no-sandbox','--disable-dev-shm-usage']});
}
async function context(actor, width=390, dpr=1) {
  const ctx = await browser.newContext({viewport:{width,height:844}, deviceScaleFactor:dpr,
    hasTouch:width < 500, serviceWorkers:'block'});
  ctx.setDefaultTimeout(30000);
  ctx.setDefaultNavigationTimeout(30000);
  await ctx.route('**/*', route => {
    const u = new URL(route.request().url());
    return u.origin === origin || ['data:', 'blob:'].includes(u.protocol) ? route.continue() : route.abort();
  });
  const response = await ctx.request.post(`${origin}/__fixture__/login?token=${token}&actor=${actor}`);
  assert.equal(response.status(), 200);
  assert.doesNotMatch(response.headers()['set-cookie'], /(?:Domain|Expires|Max-Age)=None/i);
  const session = (await ctx.cookies(origin)).find(cookie => cookie.name === 'sessionid');
  assert.ok(session, 'Chromium must accept a real authenticated session cookie');
  assert.equal(session.httpOnly, true);
  assert.equal(session.sameSite, 'Lax');
  return ctx;
}
function privateHeaders(headers) {
  assert.match(headers['cache-control'] || '', /private/);
  assert.match(headers['cache-control'] || '', /no-store/);
  assert.match(headers['cdn-cache-control'] || '', /no-store/);
}
async function responseOK(response, label) {
  assert.ok(response, `${label} must receive an actual HTTP response`);
  if (response.status() >= 400) {
    const headers=await response.request().allHeaders();
    throw Error(`${label}: HTTP ${response.status()} ${response.url()} Origin=${headers.origin || '(absent)'} Referrer-Policy=${response.headers()['referrer-policy'] || '(absent)'}\n${(await response.text()).slice(0,4000)}`);
  }
  privateHeaders(response.headers());
}
async function navigate(page, url) {
  await responseOK(await page.goto(url), 'Navigation');
  if (new URL(url).pathname.startsWith('/participants/') && !url.includes('elite-trip-attendance')) {
    const csrf = (await page.context().cookies(origin)).find(cookie => cookie.name === 'csrftoken');
    if (await page.locator('[name=csrfmiddlewaretoken]').count()) assert.ok(csrf, 'Real CSRF cookie required');
  }
}
async function submit(page, button, expectedStatus) {
  const [response] = await Promise.all([
    page.waitForResponse(response => response.request().method() === 'POST' &&
      new URL(response.url()).origin === origin), button.click(),
  ]);
  if (expectedStatus !== undefined) {
    assert.equal(response.status(), expectedStatus, (await response.text()).slice(0,4000));
    privateHeaders(response.headers());
  } else await responseOK(response, 'Form POST');
  await page.waitForLoadState('domcontentloaded');
  return response;
}
async function layout(page, width, label) {
  const dimensions = await page.evaluate(() => ({vw:innerWidth,
    body:document.body.scrollWidth, html:document.documentElement.scrollWidth}));
  assert.equal(dimensions.vw, width);
  assert.ok(dimensions.body <= width + 1 && dimensions.html <= width + 1,
    `${label} has horizontal body scroll at ${width}: ${JSON.stringify(dimensions)}`);
}
async function noDomainChanges(before) {
  const after = await state();
  assert.deepEqual(after.balances, before.balances);
  assert.equal(after.ledger, before.ledger);
  assert.equal(after.attendance, before.attendance);
}
async function createAndApprove(page, width, serial, scope='') {
  await navigate(page, origin + metadata.routes.elite_trip_list);
  await layout(page, width, 'list');
  const before = await state();
  const create = page.locator('form').filter({has:page.locator('[name=action][value=create]')}).locator('button[type=submit]');
  assert.equal(await create.count(), 1, 'Contract form must contain exactly one create submit button');
  await submit(page, create);
  await page.waitForURL(/\/elite-trips\/\d+\/edit\//);
  const tripId = Number(page.url().match(/elite-trips\/(\d+)/)[1]);
  await layout(page, width, 'draft');
  assert.equal(await page.locator('[name=selected_ids]:checked').count(), 26);
  assert.equal((await state()).consents.length, before.consents.length);
  const values = {title:`رحلة متصفح مصطنعة ${serial}`,date:'2027-02-12',time:'10:00',location:'مكان مصطنع طويل لاختبار الواجهة',
    fee:'0',meeting_point:'نقطة تجمع تركيبية',departure_time:'09:00',return_time:'18:00',
    instructions:'تعليمات مصطنعة\nسطر ثان',details:'تفاصيل مصطنعة'};
  for (const [key,value] of Object.entries(values)) await page.locator(`[name=${key}]`).fill(value);
  // Individual selection/reason remain global even when search/status hides rows.
  const a14 = metadata.participants.find(p => p.actor === 'a-14');
  const a0 = metadata.participants.find(p => p.actor === 'a-0');
  await page.locator(`[name=selected_ids][value="${a14.id}"]`).check();
  await page.locator(`[name=reason_${a14.id}]`).fill('اختيار يدوي مصطنع');
  const row = page.locator('[data-roster-row]').filter({has:page.locator(`[name=selected_ids][value="${a14.id}"]`)});
  assert.ok((await row.getAttribute('class')).includes('is-selected'));
  await page.locator('[data-roster-search]').fill('nothing-synthetic-matches');
  assert.equal(await page.locator('[data-roster-row]:visible').count(), 0);
  assert.equal(await page.locator(`[name=selected_ids][value="${a0.id}"]`).isChecked(), true);
  await page.locator('[data-roster-search]').fill('');
  await page.locator('[data-roster-status]').selectOption('selected');
  assert.equal(await page.locator('[name=selected_ids]:checked').count(), 27);
  await page.locator('[data-roster-status]').selectOption('');
  await page.locator('[name=scope_group]').selectOption(scope);
  assert.equal(await page.locator('[name=selected_ids]:disabled').count(), 0,
    'Scope/filter cannot disable hidden global draft choices');
  await submit(page, page.getByRole('button', {name:'حفظ المسودة',exact:true}));
  let current = await state();
  let trip = current.trips.find(t => t.id === tripId);
  assert.equal(trip.status, 'draft');
  assert.equal(current.nominations.find(n => n.trip_id === tripId && n.participant_id === a14.id).reason,
    'اختيار يدوي مصطنع');
  assert.ok(current.nominations.filter(n => n.trip_id === tripId).every(n => !n.notified_at));
  await page.locator('[data-saved-review-link]').click();
  await page.waitForURL(/\/review\//);
  await layout(page, width, 'approval review');
  assert.equal(await page.locator('[data-final-button]').isDisabled(), true);
  assert.equal((await state()).trips.find(t => t.id === tripId).status, 'draft');
  await page.locator('[data-final-confirm]').check();
  await submit(page, page.locator('[data-final-button]'));
  current = await state();
  trip = current.trips.find(t => t.id === tripId);
  assert.equal(trip.status, 'approved');
  assert.equal(current.audits.filter(a => a.trip_id === tripId && a.event === 'approved').length, 1);
  await noDomainChanges(before);
  return tripId;
}
async function draw(page, pointerType='mouse') {
  const canvas = page.locator('[data-signature-canvas]');
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  assert.ok(box && box.width >= 100 && box.height >= 50);
  if (pointerType === 'mouse') {
    await page.mouse.move(box.x + box.width * .1, box.y + box.height * .6);
    await page.mouse.down();
    for (const [x,y] of [[.2,.2],[.4,.8],[.6,.3],[.85,.65]])
      await page.mouse.move(box.x + box.width*x, box.y + box.height*y, {steps:5});
    await page.mouse.up();
  } else {
    // Chrome DevTools sends actual browser touch input (not fake canvas pixels).
    const cdp = await page.context().newCDPSession(page);
    const point = (x,y) => ({x:box.x+box.width*x,y:box.y+box.height*y,id:1,radiusX:2,radiusY:2,force:1});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[point(.1,.6)]});
    for (const [x,y] of [[.2,.2],[.4,.8],[.6,.3],[.85,.65]])
      await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[point(x,y)]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
    await cdp.detach();
  }
  assert.match(await page.locator('[name=signature]').inputValue(), /^data:image\/png;base64,/);
}
async function consentFlow(tripId, width, serial, decision, actor='a-0', notes='<script>window.guardianXss=1</script>\nملاحظة ثانية') {
  const ctx = await context(actor, width, width < 500 ? 3 : 2);
  try {
    const page = await ctx.newPage();
    const errors=[]; page.on('pageerror', error => errors.push(error.message));
    await navigate(page, origin + metadata.routes.student_trips);
    await layout(page, width, 'student cards');
    assert.equal(await page.evaluate(() => devicePixelRatio), width < 500 ? 3 : 2);
    const current = await state();
    const participantId=metadata.participants.find(p => p.actor===actor).id;
    const nomination=current.nominations.find(n => n.trip_id===tripId && n.participant_id===participantId);
    const invitation=`/participants/trip-invitations/${nomination.id}/`;
    await navigate(page, origin + invitation);
    await layout(page, width, 'invitation');
    assert.equal(await page.locator('[name=decision]:checked').count(), 0);
    assert.equal(await page.locator('[name=guardian_relation]').inputValue(), '');
    await page.locator(`[name=decision][value=${decision}]`).check();
    await page.locator('[name=guardian_name]').fill(`ولي مصطنع ${serial}`);
    await page.locator('[name=guardian_relation]').selectOption('guardian');
    await page.locator('[name=notes]').fill(notes);
    await page.locator('[name=declaration_accepted]').check();
    await draw(page,width<500?'touch':'mouse');
    const beforeRotation=await page.locator('[name=signature]').inputValue();
    await page.setViewportSize({width:844,height:width});
    await delay(50);
    assert.equal(await page.locator('[name=signature]').inputValue(),beforeRotation,'Rotate must not clear drawn signature');
    await page.setViewportSize({width,height:844});
    await layout(page,width,'rotated invitation');
    await page.locator('[data-signature-clear]').click();
    assert.equal(await page.locator('[name=signature]').inputValue(),'');
    await draw(page,width<500?'touch':'mouse');
    const before=await state();
    await submit(page, page.locator('form[data-signature-form] button[type=submit]'));
    assert.equal(await page.locator('[name=review_token]').count(),1);
    assert.equal((await state()).consents.length,before.consents.length,'Review is not a saved consent');
    await layout(page,width,'guardian confirmation');
    assert.equal(await page.evaluate(() => window.guardianXss),undefined);
    assert.equal(await page.locator('[data-final-button]').isDisabled(),true);
    await page.locator('[data-final-confirm]').check();
    await submit(page, page.locator('[data-final-button]'));
    await page.waitForURL(/\/trip-consents\/\d+\/$/);
    const saved=await state();
    const consent=saved.consents.find(c => c.nomination_id===nomination.id);
    assert.ok(consent,'Only actual DB persistence confirms success');
    assert.equal(consent.decision,decision);
    assert.equal(consent.verification_method,'account_declaration');
    assert.equal(consent.notes.replace(/\r\n/g,'\n'),notes.replace(/\r\n/g,'\n'));
    await layout(page,width,'saved consent');
    await responseOK(await page.reload(), 'Saved consent reload');
    assert.ok((await page.locator('body').innerText()).includes(consent.reference));
    assert.equal(await page.evaluate(() => window.guardianXss),undefined);
    assert.equal((await state()).consents.filter(c=>c.nomination_id===nomination.id).length,1);
    await noDomainChanges(before);
    assert.deepEqual(errors,[]);
    browserCases++;
    evidence.push({scenario:'real create/save/approve/review/final/reload',width,decision,tripId,reference:consent.reference});
    console.log(`PASS actual consent ${width}px ${decision} trip=${tripId}`);
  } finally {await ctx.close();}
}
async function followAndPDF(tripId, width) {
  const ctx=await context('general',width,width<500?3:2);
  try {
    const page=await ctx.newPage();
    const before=await state();
    const rows=before.nominations.filter(n=>n.trip_id===tripId&&n.is_selected&&n.frozen_member);
    const consents=before.consents.filter(c=>rows.some(n=>n.id===c.nomination_id));
    const url=`${origin}/participants/elite-trips/${tripId}/responses/`;
    await navigate(page,url);
    await layout(page,width,'follow all');
    assert.equal(await page.locator('.et-response-table tbody tr').count(),rows.length);
    const kpis=await page.locator('.et-kpi strong').allTextContents();
    assert.deepEqual(kpis.map(Number),[rows.length,consents.filter(c=>c.decision==='yes').length,
      consents.filter(c=>c.decision==='no').length,rows.length-consents.length]);
    assert.equal(await page.evaluate(()=>window.guardianXss),undefined);
    assert.ok((await page.locator('.et-response-table').innerText()).includes('بانتظار رد ولي الأمر'));
    for(const consent of consents) {
      const row=page.locator('.et-response-table tbody tr').filter({has:page.locator(`a[href="/participants/trip-consents/${consent.id}/"]`)});
      assert.equal((await row.locator('.et-prewrap').innerText()).replace(/\r\n/g,'\n'),(consent.notes||'لا توجد ملاحظات').replace(/\r\n/g,'\n'));
      assert.equal(await row.locator('.et-prewrap').evaluate(e=>getComputedStyle(e).whiteSpace),'pre-wrap');
    }
    for(const status of ['yes','no','pending']) {
      await page.locator('[name=status]').selectOption(status);
      await Promise.all([page.waitForURL(u=>u.searchParams.get('status')===status),
        page.getByRole('button',{name:'تطبيق فلاتر العرض',exact:true}).click()]);
      const count=status==='pending'?rows.length-consents.length:consents.filter(c=>c.decision===status).length;
      assert.equal(await page.locator('.et-kpi strong').allTextContents().then(v=>JSON.stringify(v)),JSON.stringify(kpis));
      assert.equal(await page.locator('.et-response-table tbody tr').count(),Math.max(count,1));
      if(!count) assert.match(await page.locator('.et-response-table tbody').innerText(),/لا توجد نتائج/);
      await layout(page,width,`follow ${status}`);
    }
    await navigate(page,url);
    const target=rows.find(n=>consents.some(c=>c.nomination_id===n.id));
    await page.locator('[name=group]').selectOption(String(target.group_snapshot_id));
    await page.locator('[name=q]').fill(`P${target.participant_id}`);
    await Promise.all([page.waitForURL(u=>u.searchParams.get('q')===`P${target.participant_id}`),
      page.getByRole('button',{name:'تطبيق فلاتر العرض',exact:true}).click()]);
    const needle=`P${target.participant_id}`.toLowerCase();
    const matching=rows.filter(n=>n.group_snapshot_id===target.group_snapshot_id &&
      (`P${n.participant_id}`.toLowerCase().includes(needle)||n.student_name.toLowerCase().includes(needle)));
    assert.equal(await page.locator('.et-response-table tbody tr').count(),matching.length);
    assert.deepEqual(await page.locator('.et-kpi strong').allTextContents(),kpis);
    const consent=consents.find(c=>c.nomination_id===target.id);
    await page.locator(`a[href="/participants/trip-consents/${consent.id}/"]`).click();
    await page.waitForURL(new RegExp(`/trip-consents/${consent.id}/$`));
    await layout(page,width,'manager preview');
    assert.ok((await page.locator('body').innerText()).includes(consent.reference));
    assert.equal(await page.evaluate(()=>window.guardianXss),undefined);
    const [download]=await Promise.all([page.waitForEvent('download'),
      page.locator(`a[href="/participants/trip-consents/${consent.id}/pdf/"]`).click()]);
    assert.equal(await download.failure(),null);
    assert.ok(download.suggestedFilename().includes(consent.reference));
    const bytes=await require('node:fs/promises').readFile(await download.path());
    assert.equal(bytes.subarray(0,5).toString(),'%PDF-');
    assert.ok(bytes.length>1000,'Native renderer must create a real PDF, not an empty success');
    const pdf=await ctx.request.get(`${origin}/participants/trip-consents/${consent.id}/pdf/`);
    assert.equal(pdf.status(),200); privateHeaders(pdf.headers());
    assert.match(pdf.headers()['content-type'],/application\/pdf/);
    assert.equal((await pdf.body()).subarray(0,5).toString(),'%PDF-');
    assert.deepEqual((await state()).external_network_attempts,[],'PDF must not attempt external network/DNS');
    await noDomainChanges(before);
    browserCases++; evidence.push({scenario:'follow all/status/group/code filters, literal notes/XSS, native PDF without network',width,tripId,pdfBytes:bytes.length});
    console.log(`PASS actual follow/PDF ${width}px trip=${tripId}`);
  } finally {await ctx.close();}
}
async function scopeCards(tripId,width,single) {
  const current=await state();
  const trip=current.trips.find(t=>t.id===tripId);
  for(const actor of ['a-14','a-15',...(single?['b-0']:[])]) {
    const ctx=await context(actor,width);
    try {
      const page=await ctx.newPage();
      await navigate(page,origin+metadata.routes.student_trips);
      await layout(page,width,`cards ${actor}`);
      const p=metadata.participants.find(p=>p.actor===actor);
      const n=current.nominations.find(n=>n.trip_id===tripId&&n.participant_id===p.id);
      const card=page.locator('.et-trip-card').filter({has:page.locator(`#et-trip-card-${n.id}`)});
      if(actor==='b-0') {
        assert.equal(await card.count(),0,'Outside single-group snapshot must see no card');
        assert.equal((await page.locator('body').innerText()).includes(trip.title),false);
      } else {
        assert.equal(await card.count(),1);
        assert.equal(n.is_selected,actor==='a-14');
        assert.equal(await card.locator('a[href*="trip-invitations"]').count(),actor==='a-14'?1:0);
        assert.match(await card.innerText(),actor==='a-14'?/تهانينا! تم ترشيحك/:/غير مرشح لهذه الرحلة/);
        if(actor==='a-15') {
          assert.ok((await card.getAttribute('class')).includes('et-trip-card--unselected'));
          assert.match(await card.innerText(),/الفرصة أمامك! اجتهد/);
          assert.equal(await card.locator('form').count(),0);
        }
      }
      if(actor!=='a-14') {
        const denied=await page.goto(`${origin}/participants/trip-invitations/${n.id}/`);
        assert.equal(denied.status(),404);privateHeaders(denied.headers());
        assert.equal(await page.locator('[data-signature-form]').count(),0);
        assert.equal((await page.locator('body').innerText()).includes(trip.location),false);
      }
    } finally {await ctx.close();}
  }
  browserCases++; evidence.push({scenario:'manual nomination green / unselected red / outside-scope denied',width,tripId,single});
  console.log(`PASS actual cards/scope ${width}px trip=${tripId}`);
}
async function noSuccessOnFinalFailure(tripId) {
  const ctx=await context('a-1',390,3);
  try {
    let page=await ctx.newPage();
    const current=await state();
    const p=metadata.participants.find(p=>p.actor==='a-1');
    const n=current.nominations.find(n=>n.trip_id===tripId&&n.participant_id===p.id);
    await navigate(page, `${origin}/participants/trip-invitations/${n.id}/`);
    await page.locator('[name=decision][value=no]').check();
    await page.locator('[name=guardian_name]').fill('ولي حالة انقطاع مصطنع');
    await page.locator('[name=guardian_relation]').selectOption('mother');
    await page.locator('[name=declaration_accepted]').check();
    await draw(page,'touch');
    await submit(page, page.locator('form[data-signature-form] button[type=submit]'));
    let held, release;
    const reached=new Promise(resolve=>{held=resolve;});
    const gate=new Promise(resolve=>{release=resolve;});
    await page.route(`**/trip-invitations/${n.id}/`,async route=>{
      if(route.request().method()==='POST'&&route.request().postData()?.includes('action=submit')) {
        held();await gate;return route.abort('failed');
      }
      return route.continue();
    });
    let captureStatus;
    const statusCaptured=new Promise(resolve=>{captureStatus=resolve;});
    await page.exposeFunction('captureActualPendingStatus',message=>captureStatus(message));
    await page.evaluate(()=>{
      const form=document.querySelector('[data-final-button]').form;
      // Run after the application's real submit listener, before native navigation.
      form.addEventListener('submit',()=>window.captureActualPendingStatus(
        form.querySelector('[data-submit-status]').textContent));
    });
    await page.locator('[data-final-confirm]').check();
    const clicking=page.locator('[data-final-button]').click({noWaitAfter:true});
    await Promise.race([reached,delay(10000).then(()=>{throw Error('Final request was not held');})]);
    try {
      const message=await Promise.race([statusCaptured,delay(10000).then(()=>{throw Error('Actual pending status was not captured');})]);
      assert.match(message,/لا توجد نتيجة مؤكدة/);
      assert.doesNotMatch(message,/تم الحفظ|تم حفظ|حُفظ|تم إرسال القرار بنجاح/);
      // Inspect the real DB after releasing the aborted native navigation below.
      // A held Chromium connection can monopolize the single-thread WSGI fixture.
    } finally {release();}
    await clicking.catch(()=>{});
    await page.unroute(`**/trip-invitations/${n.id}/`);
    // Close the aborted browser document before reopening in the SAME session.
    // Its delayed chrome-error navigation must not interrupt the recovery GET.
    await page.close();
    page=await ctx.newPage();
    await navigate(page, `${origin}/participants/trip-invitations/${n.id}/`);
    assert.equal(await page.locator('[name=decision]:checked').count(),0);
    assert.equal((await state()).consents.filter(c=>c.nomination_id===n.id).length,0);
    faultCases++;evidence.push({scenario:'held/aborted final transport is not saved',width:390});
    await page.locator('[name=decision][value=no]').check();
    await page.locator('[name=guardian_name]').fill('ولي رفض رمز مراجعة مصطنع');
    await page.locator('[name=guardian_relation]').selectOption('father');
    await page.locator('[name=declaration_accepted]').check();
    await draw(page,'touch');
    await submit(page, page.locator('form[data-signature-form] button[type=submit]'));
    await page.locator('[name=review_token]').evaluate(e=>{e.value='invalid-real-final-token';});
    await page.locator('[data-final-confirm]').check();
    await submit(page,page.locator('[data-final-button]'),400);
    assert.equal((await state()).consents.filter(c=>c.nomination_id===n.id).length,0);
    assert.doesNotMatch(await page.locator('body').innerText(),/تم الحفظ|تم حفظ|تم إرسال القرار بنجاح/);
    browserCases++; evidence.push({scenario:'actual server rejects invalid final token HTTP400, no saved consent',width:390});
    console.log('PASS actual final rejection and separate aborted transport');
  } finally {await ctx.close();}
}
(async()=>{
  try {
    await start();
    let firstTrip;
    for (const [serial,width] of [360,390,1280].entries()) {
      const ctx=await context('general',width);
      let tripId;
      try {tripId=await createAndApprove(await ctx.newPage(),width,serial+1,serial===1?String(metadata.groups[0]):'');}
      finally {await ctx.close();}
      if (!firstTrip) firstTrip=tripId;
      await consentFlow(tripId,width,serial+1,serial%2?'no':'yes');
      if(width===390) await consentFlow(tripId,width,'empty-notes','yes','a-2','');
      await followAndPDF(tripId,width);
      await scopeCards(tripId,width,serial===1);
    }
    await noSuccessOnFinalFailure(firstTrip);
    // Route variations have already used all/single scopes, create/edit/review,
    // student cards/invitation/confirmation/detail. Old attendance navigation stays.
    const general=await context('general',1280);
    try {
      const page=await general.newPage();
      await page.goto(origin+metadata.routes.elite_trip_attendance);
      assert.equal(await page.locator(`a[href="${metadata.routes.elite_trip_attendance}"]`).count()>0,true);
      await layout(page,1280,'old attendance route');
      browserCases++;
    } finally {await general.close();}
    const final=await state();
    assert.deepEqual(final.balances,final.baseline);
    assert.equal(final.ledger,0);assert.equal(final.attendance,0);
    assert.deepEqual(final.external_network_attempts,[]);
    assert.equal(final.consents.length,4);
    assert.equal(browserCases,12);
    assert.equal(faultCases,1);
    console.log(JSON.stringify({browserCases,faultCases,mockedSuccessEndpoints:0,evidence},null,2));
  } finally {
    if(browser) await browser.close();
    if(helper&&!helper.killed) {
      helper.kill('SIGTERM');
      await Promise.race([new Promise(resolve=>helper.once('exit',resolve)),delay(3000)]);
      if(helper.exitCode===null) helper.kill('SIGKILL');
    }
  }
})().catch(error=>{
  console.error(error.stack);
  console.error(JSON.stringify({browserCases,faultCases,mockedSuccessEndpoints:0,evidence,failure:error.message}));
  process.exitCode=1;
});
