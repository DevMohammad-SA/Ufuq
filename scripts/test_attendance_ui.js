/* Behavioral checks with a small DOM contract, no browser or production writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function classList() {
  const values = new Set();
  return {
    add(...items) { items.forEach(item => values.add(item)); },
    remove(...items) { items.forEach(item => values.delete(item)); },
    toggle(item, force) { if (force) values.add(item); else values.delete(item); },
    contains(item) { return values.has(item); },
  };
}

function control(attributes = {}) {
  return {
    attributes, listeners: {}, classList: classList(), hidden: false, checked: false,
    value: '', textContent: '', disabled: false,
    getAttribute(name) { return this.attributes[name]; },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    reportValidity() { return true; },
    click() { this.listeners.click(); },
  };
}

function fixture(kind, withNotes = kind === 'meeting') {
  const checks = kind === 'meeting' ? ['attended', 'early'] : kind === 'circle' ? ['attended', 'achieved'] : ['attended'];
  const extraCount = kind === 'activity' ? 3 : 5;
  const visibleCount = 1 + checks.length + Number(withNotes);
  const numericStart = visibleCount + extraCount - 2;
  const headers = [control({'data-col':'0', 'data-sort':'text'}), control({'data-col':String(numericStart), 'data-sort':'number'})];
  const rows = ['باسم الطويل', 'أحمد الكامل', 'خالد الطالب'].map((name, index) => {
    const row = control({'data-search':`${name} 80000000${index}`});
    row.children = Array.from({length:visibleCount + extraCount}, () => control());
    row.children[0].attributes['data-sort-value'] = name;
    // Name sorting must not use details/note text inside a cell.
    row.children[0].textContent = `ملاحظة فرز غير مرغوبة ${2 - index}`;
    row.children[numericStart].attributes['data-sort-value'] = String([100, 7, 40][index]);
    row.boxes = Object.fromEntries(checks.map(check => [check, control({'data-col':check})]));
    row.querySelector = selector => row.boxes[selector.match(/data-col="([^"]+)"/)[1]] || null;
    Object.values(row.boxes).forEach(box => { box.closest = () => row; });
    return row;
  });
  const body = {
    rows: [...rows],
    querySelectorAll() { return this.rows; },
    appendChild(row) { this.rows = this.rows.filter(item => item !== row); this.rows.push(row); },
  };
  const extras = Array.from({length:extraCount}, () => control());
  const toggle = control({'aria-expanded':'false', 'data-show-label':'إظهار بيانات الطلاب', 'data-hide-label':'إخفاء بيانات الطلاب'});
  const search = control();
  const empty = control(); empty.hidden = true;
  const selectAll = checks.map(check => control({'data-target':check}));
  const status = control();
  const field = control(); field.value = 'ملاحظة محفوظة منفصلة';
  const noteButton = control({'data-participant':'12', 'data-note-field':'note-12'});
  const values = {'csrfmiddlewaretoken':'test-csrf', date:'2026-10-24', group:'3'};
  const form = {action:'https://example.test/participants/supervisor/dashboard/', querySelector(selector) {
    const key = selector.match(/name="([^"]+)"/)[1]; return {value:values[key]};
  }};
  noteButton.closest = () => form;
  noteButton.parentElement = {querySelector: () => status};
  const table = {querySelectorAll(selector) { return selector === '.student-extra' ? extras : headers; }};
  const root = {
    classList: classList(),
    querySelector(selector) { return {'#roster-table':table, '#roster-body':body, '#student-data-toggle':toggle, '#roster-search':search, '#roster-empty':empty}[selector] || null; },
    querySelectorAll(selector) {
      if (selector === '.note-save-button') return withNotes ? [noteButton] : [];
      if (selector === '.select-all') return selectAll;
      const match = selector.match(/data-col="([^"]+)"/);
      return match ? rows.map(row => row.boxes[match[1]]).filter(Boolean) : [];
    },
  };
  const requests = [];
  let response = {ok:true, redirected:true, url:form.action+'?date=2026-10-24&group=3'};
  const context = {
    document:{readyState:'complete', querySelectorAll:()=>[root], getElementById:()=>field},
    FormData, URL,
    fetch: async (url, options) => {
      requests.push({url, options});
      if (response instanceof Error) throw response;
      return response;
    },
  };
  vm.runInNewContext(fs.readFileSync('static/js/attendance-roster.js','utf8'), context);
  return {root, rows, body, extras, toggle, search, empty, headers, selectAll, noteButton, field, status, requests, values,
    setResponse(value) { response = value; }};
}

function checkRoster(kind, withNotes) {
  const f = fixture(kind, withNotes);
  assert.ok(f.extras.every(cell => cell.hidden));
  assert.equal(f.toggle.attributes['aria-expanded'],'false');
  assert.equal(f.root.classList.contains('student-data-visible'),false);
  f.toggle.click();
  assert.equal(f.toggle.attributes['aria-expanded'],'true');
  assert.equal(f.toggle.textContent,'إخفاء بيانات الطلاب');
  assert.ok(f.extras.every(cell => !cell.hidden));
  assert.equal(f.root.classList.contains('student-data-visible'),true);

  f.search.value = '800000001'; f.search.listeners.input();
  assert.deepEqual(f.rows.map(row => row.hidden), [true, false, true]);
  f.selectAll[0].click();
  assert.ok(f.rows.every(row => row.boxes.attended.checked), 'Select all includes rows hidden by search');
  f.selectAll[0].click();
  assert.ok(f.rows.every(row => !row.boxes.attended.checked));
  f.search.value = 'لا يوجد'; f.search.listeners.input(); assert.equal(f.empty.hidden,false);
  f.search.value = ''; f.search.listeners.input(); assert.equal(f.empty.hidden,true);
  assert.ok(f.rows.every(row => !row.hidden));

  f.headers[0].click();
  assert.equal(f.body.rows[0], f.rows[1]);
  assert.equal(f.headers[0].attributes['aria-sort'],'ascending');
  f.headers[0].click();
  assert.equal(f.body.rows[0], f.rows[2]);
  assert.equal(f.headers[0].attributes['aria-sort'],'descending');
  let prevented = false;
  f.headers[1].listeners.keydown({key:'Enter', preventDefault() { prevented = true; }});
  assert.equal(prevented,true);
  assert.deepEqual(f.body.rows.map(row => row.children[Number(f.headers[1].attributes['data-col'])].attributes['data-sort-value']), ['7','40','100']);
  f.headers[1].listeners.keydown({key:' ', preventDefault() {}});
  assert.equal(f.body.rows[0],f.rows[0]);
  assert.equal(f.headers[0].attributes['aria-sort'],'none');
  f.toggle.click();
  assert.equal(f.toggle.textContent,'إظهار بيانات الطلاب');
  assert.ok(f.extras.every(cell => cell.hidden));
  assert.equal(f.root.classList.contains('student-data-visible'),false);
  assert.equal(f.requests.length,0, 'Toggle, search, sort, and bulk checks never POST');

  if (kind === 'meeting') {
    const early = f.rows[0].boxes.early;
    early.checked = true; early.listeners.change(); assert.equal(f.rows[0].boxes.attended.checked,true);
    early.checked = false; early.listeners.change(); assert.equal(f.rows[0].boxes.attended.checked,true);
    f.rows.forEach(row => { row.boxes.attended.checked = false; });
    f.selectAll[1].click();
    assert.ok(f.rows.every(row => row.boxes.early.checked && row.boxes.attended.checked));
    f.selectAll[1].click();
    assert.ok(f.rows.every(row => !row.boxes.early.checked && row.boxes.attended.checked));
  }
  if (kind === 'circle') {
    f.selectAll[1].click();
    assert.ok(f.rows.every(row => row.boxes.achieved.checked && !row.boxes.attended.checked));
  }
  return f;
}

(async function () {
  const f = checkRoster('meeting',true);
  checkRoster('meeting',false); // System has no notes; numeric indices remain valid.
  checkRoster('circle',false);
  checkRoster('activity',false);

  f.noteButton.click();
  assert.equal(f.noteButton.disabled,true);
  await new Promise(resolve => setImmediate(resolve));
  const sent = Object.fromEntries(f.requests[0].options.body.entries());
  assert.deepEqual(sent, {csrfmiddlewaretoken:f.values.csrfmiddlewaretoken, action:'save_note', participant_id:'12', note_text:f.field.value, date:f.values.date, group:f.values.group});
  assert.equal(f.requests[0].options.credentials,'same-origin');
  assert.equal(f.requests[0].options.method,'POST');
  assert.match(f.status.textContent,/تم حفظ الملاحظة دون تغيير التحضير/);
  assert.ok(f.rows.every(row => row.boxes.attended.checked && !row.boxes.early.checked));
  assert.equal(f.noteButton.disabled,false);

  for (const failure of [
    {ok:true, redirected:true, url:'https://example.test/accounts/login/supervisor/?next=/participants/supervisor/dashboard/'},
    {ok:false, redirected:false, url:'https://example.test/participants/supervisor/dashboard/'},
    new Error('network failure'),
  ]) {
    f.setResponse(failure); f.noteButton.click();
    await new Promise(resolve => setImmediate(resolve));
    assert.match(f.status.textContent,/تعذر حفظ الملاحظة/);
    assert.equal(f.noteButton.disabled,false);
  }
  const previousRequests = f.requests.length;
  f.field.reportValidity = () => false; f.noteButton.click();
  assert.equal(f.requests.length,previousRequests);
  console.log('Attendance UI: all three rosters, hidden-data widths, search, numeric/name/keyboard sorting, all-row selection, early attendance, independent achievement, note-only payload and failure handling passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
