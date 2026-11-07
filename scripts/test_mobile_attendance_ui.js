/* Synthetic, intercepted requests only: no server, database, or production writes.
 * Run: npm install --prefix /tmp/mobile-ui-deps playwright --no-audit --no-fund
 * NODE_PATH=/tmp/mobile-ui-deps/node_modules node scripts/test_mobile_attendance_ui.js
 * Uses .venv/bin/python and /usr/bin/chromium; override PYTHON/CHROMIUM if needed.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');
const project = path.resolve(__dirname, '..');
const rendered = spawnSync(process.env.PYTHON || path.join(project, '.venv/bin/python'), ['-c', `
import os,json,datetime
from types import SimpleNamespace as S
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
os.environ['SECRET_KEY']='synthetic-mobile-layout-key-not-for-deployment'
os.environ['DATABASE_URL']='sqlite:///:memory:'
import django
django.setup()
from django.template.loader import get_template
rows=[]
for i in range(1,4):
    user=S(full_name='الطالب '+str(i)+' عبدالله عبدالرحمن محمد صاحب الاسم الكامل الطويل',national_id='100000000'+str(i))
    participant=S(id=i,pk=i,user=user,phone='0500000000',miles=123,points=50,get_academic_stage_display=lambda:'المرحلة الدراسية الطويلة')
    rows.append(dict(participant=participant,recorded=i==2,attended=False,is_early=False,achieved=False,note='ملاحظة محفوظة' if i==2 else '',status='absent'))
ctx=dict(request=S(user=S(full_name='مشرف الاختبار')),group=S(id=7,pk=7,name='بيئة اختبار'),roster=rows,selected_date=datetime.date(2026,11,5),can_view_notes=True,show_group_selector=False,is_group_locked=True,csrf_token='fixture-csrf',navbar_items=[],status_choices=[('present','حاضر'),('excused','معتذر'),('absent','غائب'),('not_nominated','غير مرشح')],statistics=S(total=3,recorded=1,present=0,excused=0,absent=1,not_nominated=0))
templates=['supervisor_dashboard','quran_circle_attendance','weekly_activity_attendance','elite_trip_attendance']
result={name:get_template('participants/'+name+'.html').render(ctx) for name in templates}
ctx['can_view_notes']=False
result['meeting_without_notes']=get_template('participants/supervisor_dashboard.html').render(ctx)
print(json.dumps(result))
`], {cwd:project, encoding:'utf8'});
assert.equal(rendered.status, 0, rendered.stderr);
const html = JSON.parse(rendered.stdout);
const kinds = ['supervisor_dashboard', 'quran_circle_attendance', 'weekly_activity_attendance', 'elite_trip_attendance'];
const uris = Object.fromEntries(kinds.map(kind => [kind, html[kind].match(/<form data-roster-form[^>]*action="([^"]+)"/)[1]]));
function multipart(text) {
  const result = {};
  for (const match of text.matchAll(/name="([^"]+)"\r\n\r\n([\s\S]*?)(?=\r\n--)/g)) result[match[1]] = match[2];
  return result;
}
let passed = 0;
(async () => {
  // Chromium may follow an intercepted 302 without re-entering page.route.
  // This loopback-only server handles that final GET; it has no application/DB.
  const server=http.createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/html'});res.end('<p>Synthetic redirect target</p>');});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const origin='http://127.0.0.1:'+server.address().port;
  const browser = await chromium.launch({executablePath:process.env.CHROMIUM || '/usr/bin/chromium', headless:true, args:['--no-sandbox']});
  try {
    async function fixture(kind='supervisor_dashboard', width=390, options={}) {
      const context = await browser.newContext({viewport:{width,height:844}});
      const page = await context.newPage();
      const f = {context,page,requests:[], mode:'success', held:false, release:null, confirmations:0, confirm:true};
      if (options.noObserver) await page.addInitScript('window.ResizeObserver = undefined');
      await page.addInitScript(() => {
        window.__fetchResults=[];
        const fetchOriginal=window.fetch;
        window.fetch=(...args)=>fetchOriginal(...args).then(response=>{
          window.__fetchResults.push({ok:response.ok,url:response.url,redirected:response.redirected});
          return response;
        });
      });
      page.on('dialog', async dialog => { f.confirmations++; await (f.confirm ? dialog.accept() : dialog.dismiss()); });
      await page.route('**/*', async route => {
        const req = route.request();
        const url = new URL(req.url());
        if (req.method() === 'POST') {
          const data = multipart(req.postData() || '');
          f.requests.push({data,headers:req.headers(),url:req.url()});
          if (f.held) await new Promise(resolve => {f.release=resolve;});
          if (f.mode === 'network') return route.abort('failed');
          if (data.action === 'save_note') return route.fulfill({status:302,headers:{location:`${uris[kind]}?date=${data.date}&group=${data.group}`},body:''});
          if (f.mode === 'login') return route.fulfill({status:302,headers:{location:'/accounts/login/supervisor/'},body:''});
          if (f.mode === 'html') return route.fulfill({status:200,contentType:'text/html',body:'<h1>Login</h1>'});
          const response = {success:true,saved_ids:Object.keys(data).filter(k=>k.startsWith('prepared_')).map(k=>Number(k.slice(9))),saved_note_ids:Object.keys(data).filter(k=>k.startsWith('note_')).map(k=>Number(k.slice(5))),saved_at:'18:40',date:data.date,group_id:Number(data.group)};
          if (f.mode === 'missing-attendance') response.saved_ids=[];
          if (f.mode === 'missing-note') response.saved_note_ids=[];
          if (f.mode === 'wrong-date') response.date='2026-11-04';
          if (f.mode === 'wrong-group') response.group_id=8;
          if (f.mode === 'false-success') response.success=false;
          if (f.mode === 'bad-time') response.saved_at='not a time';
          return route.fulfill({status:f.mode==='http-error'?403:200,contentType:'application/json',body:JSON.stringify(response)});
        }
        if (url.origin !== origin) return route.fulfill({status:200,body:''});
        if (url.pathname.startsWith('/static/')) {
          const file = path.join(project, url.pathname.slice(1));
          if (!fs.existsSync(file)) return route.fulfill({status:404,body:''});
          return route.fulfill({path:file,contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'image/png'});
        }
        return route.fulfill({status:200,contentType:'text/html',body:html[options.noNotes?'meeting_without_notes':kind]});
      });
      await page.goto(origin+uris[kind]);
      await page.waitForSelector('[data-mobile-initialized="true"]');
      f.row = id => page.locator(`[data-participant-id="${id}"]`);
      f.save = () => page.locator('[data-roster-form] [type="submit"]');
      f.waitSaved = () => page.waitForFunction(()=>document.querySelector('[data-roster-status]').textContent.includes('تم حفظ'));
      f.close = () => context.close();
      return f;
    }
    for (const kind of kinds) for (const width of [360,390,1280]) {
      const f = await fixture(kind,width);
      for (const show of [false,true]) {
        if (show) await f.page.locator('#student-data-toggle').click();
        const metrics = await f.page.evaluate(() => {
          const table=document.querySelector('#roster-table');
          const rows=[...document.querySelectorAll('tr[data-participant-id]')];
          const bar=document.querySelector('[data-roster-savebar]').getBoundingClientRect();
          const nav=document.querySelector('.app-nav--bottom').getBoundingClientRect();
          const checks=[...document.querySelectorAll('.roster-check-label,.roster-negative,.elite-trip-status-select')];
          return {overflow:document.documentElement.scrollWidth>innerWidth,tableDisplay:getComputedStyle(table).display,rowDisplay:getComputedStyle(rows[0]).display,minimumControl:Math.min(...checks.map(x=>x.getBoundingClientRect().height)),barBottom:bar.bottom,navTop:nav.top,navHeight:nav.height,space:parseFloat(getComputedStyle(document.querySelector('[data-mobile-attendance]')).paddingBottom),barHeight:bar.height,uniqueNames:new Set([...document.querySelector('[data-roster-form]').elements].filter(x=>x.name).map(x=>x.name)).size,fields:[...document.querySelector('[data-roster-form]').elements].filter(x=>x.name).length};
        });
        assert.equal(metrics.overflow,false,`${kind} ${width} details=${show}: page overflow`);
        assert.equal(metrics.fields,metrics.uniqueNames,'No duplicate input names');
        if (width<768) {
          assert.equal(metrics.rowDisplay,'grid');
          assert.ok(metrics.minimumControl>=44,'44px mobile touch targets');
          assert.ok(Math.abs(metrics.barBottom-metrics.navTop)<=1,'Savebar directly above bottom nav');
          assert.ok(metrics.space>=metrics.barHeight+metrics.navHeight,'Actual bottom space reserved');
          await f.page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight));
          const end=await f.page.evaluate(()=>({last:document.querySelector('#roster-body').lastElementChild.getBoundingClientRect().bottom,bar:document.querySelector('[data-roster-savebar]').getBoundingClientRect().top}));
          assert.ok(end.last<end.bar,'Last row is not covered');
        } else assert.equal(metrics.tableDisplay,'table');
        passed++;
      }
      await f.close();
    }
    // All four forms: recorded negative is prepared; explicit negative only submits that row.
    for (const kind of kinds) {
      const f=await fixture(kind);
      assert.match(await f.page.locator('[data-remaining-count]').textContent(),/2/);
      assert.equal(await f.save().isDisabled(),true);
      await f.page.locator('[data-preparation-filter]').selectOption('unprepared');
      assert.equal(await f.row(2).isVisible(),false);
      await f.page.locator('#roster-search').fill('1000000003');
      assert.equal(await f.row(1).isVisible(),false);
      assert.equal(await f.row(3).isVisible(),true);
      await f.page.locator('#roster-search').fill('');
      await f.row(1).locator('[data-explicit-negative]').click();
      assert.equal(await f.row(1).isVisible(),false,'Explicit negative exits unprepared filter');
      assert.equal(f.requests.length,0,'No autosave');
      await f.save().click(); await f.waitSaved();
      const request=f.requests[0];
      assert.equal(request.data.partial_roster,'1');
      assert.equal(request.data.prepared_1,'1');
      assert.equal(request.data.prepared_2,undefined); assert.equal(request.data.prepared_3,undefined);
      assert.equal(request.data.note_1,undefined); assert.equal(request.data.note_2,undefined);
      assert.equal(request.headers.accept,'application/json');
      assert.equal(new URL(request.url).pathname,uris[kind]);
      if(kind==='elite_trip_attendance') assert.equal(request.data.status_1,'absent');
      assert.equal(await f.row(1).getAttribute('data-recorded'),'true');
      assert.equal(await f.row(3).getAttribute('data-recorded'),'false');
      assert.equal(await f.save().isDisabled(),true);
      passed++; await f.close();
    }
    // Bulk remains reachable when headers are hidden, includes filtered-out rows, and early implies attendance.
    for (const kind of kinds) {
      const f=await fixture(kind);
      await f.page.locator('#roster-search').fill('1000000001');
      if(kind==='elite_trip_attendance') {
        assert.equal(await f.row(1).locator('select').inputValue(),'');
        assert.equal(await f.row(1).locator('select option').count(),5);
        await f.page.locator('#elite-trip-bulk-status').selectOption('present');
        await f.page.locator('#elite-trip-bulk-apply').click();
        assert.equal(f.confirmations,1);
        assert.equal(await f.row(3).locator('select').inputValue(),'present');
        f.confirm=false;
        await f.page.locator('#elite-trip-bulk-status').selectOption('absent');
        await f.page.locator('#elite-trip-bulk-apply').click();
        assert.equal(await f.row(3).locator('select').inputValue(),'present');
      } else {
        const col=kind==='supervisor_dashboard'?'early':kind==='quran_circle_attendance'?'achieved':'attended';
        await f.page.locator(`.roster-mobile-bulk [data-target="${col}"]`).click();
        assert.equal(await f.row(3).locator(`[data-col="${col}"]`).isChecked(),true);
        if(col==='early') assert.equal(await f.row(3).locator('[data-col="attended"]').isChecked(),true);
      }
      assert.equal(f.requests.length,0);
      assert.match(await f.page.locator('[data-dirty-count]').textContent(),/3/);
      passed++; await f.close();
    }
    // Every invalid acknowledgement retains ALL attendance and note baselines.
    for(const mode of ['missing-attendance','missing-note','wrong-date','wrong-group','false-success','bad-time','http-error','html','login','network']) {
      const f=await fixture(); f.mode=mode;
      await f.row(1).locator('[data-explicit-negative]').click();
      await f.row(1).locator('summary').click(); await f.row(1).locator('textarea').fill('تعديل جديد');
      await f.save().click();
      await f.page.waitForFunction(()=>document.querySelector('[data-roster-status]').getAttribute('data-error')==='true');
      assert.equal(await f.row(1).getAttribute('data-recorded'),'false');
      assert.equal(await f.row(1).locator('textarea').inputValue(),'تعديل جديد');
      assert.equal(await f.save().isEnabled(),true);
      assert.match(await f.page.locator('[data-dirty-count]').textContent(),/1/);
      f.mode='success'; await f.save().click(); await f.waitSaved();
      assert.equal(f.requests[1].data.prepared_1,'1'); assert.equal(f.requests[1].data.note_1,'تعديل جديد');
      passed++; await f.close();
    }
    // Note-only FormData never prepares the previously unrecorded student.
    {
      const f=await fixture();
      await f.row(1).locator('summary').click(); await f.row(1).locator('textarea').fill('ملاحظة فقط');
      await f.save().click(); await f.waitSaved();
      assert.equal(f.requests[0].data.note_1,'ملاحظة فقط');
      assert.equal(f.requests[0].data.prepared_1,undefined);
      assert.equal(f.requests[0].data.note_2,undefined);
      assert.equal(await f.row(1).getAttribute('data-recorded'),'false');
      assert.match(await f.page.locator('[data-remaining-count]').textContent(),/2/);
      passed++; await f.close();
    }
    // Old note-only button: capture string; a subsequent edit survives acknowledgement.
    {
      const f=await fixture(); f.held=true;
      await f.row(1).locator('summary').click(); await f.row(1).locator('textarea').fill('أرسلت');
      await f.row(1).locator('.note-save-button').click();
      await f.page.waitForFunction(()=>document.querySelector('[data-note-saving]').getAttribute('data-note-saving')==='1');
      await f.row(1).locator('textarea').fill('تعديل أثناء الطلب');
      assert.equal(await f.save().isDisabled(),true);
      while(!f.release) await new Promise(r=>setTimeout(r,10));
      f.held=false; f.release();
      await f.page.waitForFunction(()=>document.querySelector('[data-note-saving]').getAttribute('data-note-saving')==='0');
      assert.match(await f.row(1).locator('.note-save-status').textContent(),/تم حفظ/,JSON.stringify({requests:f.requests,responses:await f.page.evaluate(()=>window.__fetchResults)}));
      assert.equal(await f.row(1).locator('textarea').inputValue(),'تعديل أثناء الطلب');
      assert.equal(await f.row(1).getAttribute('data-recorded'),'false');
      assert.match(await f.page.locator('[data-dirty-count]').textContent(),/1/);
      await f.save().click(); await f.waitSaved();
      assert.equal(f.requests[1].data.note_1,'تعديل أثناء الطلب');
      assert.equal(f.requests[1].data.prepared_1,undefined);
      passed++; await f.close();
    }
    // Freeze fields, reject double submission, beforeunload/date guards and successful cleanup.
    {
      const f=await fixture();
      await f.row(1).locator('[data-explicit-negative]').click();
      assert.equal(await f.page.evaluate(()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}),true);
      f.confirm=false;
      const canceled=await f.page.evaluate(()=>{const e=new Event('submit',{bubbles:true,cancelable:true});document.querySelector('form[method="get"]').dispatchEvent(e);return e.defaultPrevented;});
      assert.equal(canceled,true);
      f.held=true; await f.save().click();
      while(!f.release) await new Promise(r=>setTimeout(r,10));
      assert.equal(await f.row(1).locator('input').first().isDisabled(),true);
      assert.equal(await f.page.locator('[name="date"]').first().isDisabled(),true);
      await f.page.evaluate(()=>document.querySelector('[data-roster-form]').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
      assert.equal(f.requests.length,1);
      f.held=false; f.release(); await f.waitSaved();
      assert.equal(await f.row(1).locator('input').first().isEnabled(),true);
      assert.equal(await f.page.evaluate(()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}),false);
      passed++; await f.close();
    }
    // Independent successful old note-save resets only its note baseline.
    {
      const f=await fixture();
      await f.row(1).locator('summary').click(); await f.row(1).locator('textarea').fill('ملاحظة مستقلة');
      await f.row(1).locator('.note-save-button').click();
      await f.page.waitForFunction(()=>document.querySelector('.note-save-status').textContent.includes('تم حفظ'));
      assert.equal(await f.save().isDisabled(),true);
      assert.equal(await f.row(1).getAttribute('data-prepared'),'false');
      assert.equal(await f.row(1).getAttribute('data-recorded'),'false');
      passed++; await f.close();
    }
    {
      const f=await fixture('supervisor_dashboard',390,{noNotes:true,noObserver:true});
      assert.equal(await f.page.locator('textarea,.attendance-note,.note-save-button').count(),0,'Unauthorized notes are absent');
      await f.page.setViewportSize({width:360,height:844});
      await f.page.waitForTimeout(50);
      const measured=await f.page.evaluate(()=>({nav:document.querySelector('.app-nav--bottom').getBoundingClientRect().height,offset:parseFloat(getComputedStyle(document.querySelector('[data-mobile-attendance]')).getPropertyValue('--roster-nav-height'))}));
      assert.equal(measured.nav,measured.offset,'ResizeObserver-free fallback measures nav');
      passed++; await f.close();
    }
    console.log(`Mobile attendance UI: ${passed} targeted Chromium checks passed (four actual templates; 360/390/1280; details, recorded negatives, partial/note-only payloads, bulk/early, strict failures, concurrency and navigation guards).`);
  } finally { await browser.close(); await new Promise(resolve=>server.close(resolve)); }
})().catch(error=>{console.error(error);process.exitCode=1;});
