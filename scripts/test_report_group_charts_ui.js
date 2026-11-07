/* Dependency-free UI contract checks: mocked DOM, no browser/database/network. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'static/js/report-group-charts.js'), 'utf8');
const centerScript = fs.readFileSync(path.join(root, 'static/js/report-center.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'participants/templates/participants/report_center.html'), 'utf8');
const partial = fs.readFileSync(path.join(root, 'participants/templates/participants/_report_group_comparison.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/css/report-group-charts.css'), 'utf8');
const keys = ['meeting', 'circle', 'achievement', 'activity', 'submitters', 'net_average'];
const labels = ['حضور اللقاء', 'حضور الحلقة', 'إنجاز الحلقة', 'إنجاز الفعالية', 'الطلاب ذوو تسليم', 'متوسط صافي النقاط'];
const arNumber = value => new Intl.NumberFormat('ar-SA', {maximumFractionDigits: 2}).format(value);
let assertions = 0;

class Element {
  constructor(tag = 'div', attrs = {}) {
    this.tagName = tag;
    this.attrs = {...attrs};
    this.className = attrs.class || '';
    this.name = attrs.name || '';
    this.type = attrs.type || '';
    this.value = attrs.value || '';
    this.disabled = Boolean(attrs.disabled);
    this.checked = Boolean(attrs.checked);
    this.hidden = Boolean(attrs.hidden);
    this.required = false;
    this.dataset = {};
    this.listeners = {};
    this.children = [];
    this._text = '';
    this.styles = {};
    this.style = {setProperty: (name, value) => { this.styles[name] = String(value); }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { throw Error('Untrusted content must never be parsed as HTML: ' + value); }
  get innerHTML() { throw Error('HTML reads are outside the contract'); }
  appendChild(child) {
    if (child.tagName === '#fragment') {
      for (const item of child.children) this.children.push(item);
      child.children = [];
    } else this.children.push(child);
    return child;
  }
  replaceChildren(...children) {
    this.children = [];
    this._text = '';
    for (const child of children) this.appendChild(child);
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name, extras = {}) {
    const event = {target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extras};
    for (const callback of this.listeners[name] || []) callback(event);
    return event;
  }
  closest() { return null; }
}

function payload(pointsByKey = {}) {
  return {
    scope_note: 'البيئة الحالية وحالة الحساب الحالية؛ الفترة المطبقة فقط.',
    series: keys.map((key, index) => ({
      key, label: labels[index], unit: key === 'net_average' ? 'points' : 'percent',
      description: 'تعريف المؤشر ' + labels[index],
      points: pointsByKey[key] || [
        {name: 'باء', value: 50, numerator: 1, denominator: 2},
        {name: 'ألف', value: 0, numerator: 0, denominator: 2},
        {name: 'تاء', value: 100, numerator: 2, denominator: 2},
        {name: 'ثاء — بيئة بلا بيانات', value: null, numerator: 0, denominator: 0},
      ].map(point => ({...point, ...(key === 'net_average' && point.value !== null ? {value: point.numerator} : {})})),
    })),
  };
}

function setup({data = payload(), raw, missing = [], noRoot = false, center = true} = {}) {
  const nodes = {};
  for (const id of ['report-group-comparison', 'report-group-comparison-data', 'rgc-controls', 'rgc-metric',
    'rgc-sort', 'rgc-figure', 'rgc-fallback', 'rgc-metric-title', 'rgc-description', 'rgc-scale-note',
    'rgc-axis-layout', 'rgc-axis', 'rgc-axis-unit', 'rgc-rows', 'rgc-message', 'rgc-status',
    'rgc-stale', 'rc-pending', 'rc-submit-status', 'rc-date-range']) nodes[id] = new Element('div', {id});
  for (const id of ['rgc-controls', 'rgc-figure', 'rgc-stale', 'rgc-message', 'rc-pending']) nodes[id].hidden = true;
  nodes['rgc-metric'].disabled = nodes['rgc-sort'].disabled = true;
  nodes['rgc-sort'].value = 'name';
  nodes['report-group-comparison-data'].textContent = raw ?? JSON.stringify(data);
  nodes['rc-date-range'].querySelector = () => null;
  const form = new Element('form');
  form.dataset.filtersValid = 'true';
  const fields = [
    new Element('select', {name: 'period', type: 'select-one', value: 'week'}),
    new Element('input', {name: 'start_date', type: 'date', value: '2026-11-01'}),
    new Element('input', {name: 'end_date', type: 'date', value: '2026-11-07'}),
    new Element('select', {name: 'group', type: 'select-one', value: ''}),
    new Element('input', {name: 'reports', type: 'checkbox', value: 'groups', checked: true}),
    new Element('input', {name: 'include_private', type: 'checkbox', value: 'on'}),
  ];
  const buttons = [new Element('button', {type: 'submit'}), new Element('button', {type: 'submit', name: 'format', value: 'pdf'})];
  const exportLabel = new Element('span', {'data-export-label': 'PDF'});
  form.elements = [...fields, ...buttons];
  form.elements.namedItem = name => fields.find(field => field.name === name) || null;
  form.querySelectorAll = selector => selector === '[data-export-label]' ? [exportLabel] : buttons;
  nodes['report-center-form'] = form;
  const window = new Element('window');
  window.setTimeout = () => 1;
  window.clearTimeout = () => {};
  window.confirm = () => true;
  const created = [];
  let forbiddenCalls = 0;
  const forbidden = () => { forbiddenCalls++; throw Error('Network/persistence is forbidden'); };
  const document = {
    getElementById: id => (noRoot && id === 'report-group-comparison') || missing.includes(id) ? null : nodes[id] || null,
    createElement: tag => { created.push(tag); return new Element(tag); },
    createDocumentFragment: () => new Element('#fragment'),
  };
  const context = {document, window, Intl, fetch: forbidden, XMLHttpRequest: forbidden,
    localStorage: {getItem: forbidden, setItem: forbidden}, navigator: {sendBeacon: forbidden}};
  vm.createContext(context);
  if (center) vm.runInContext(centerScript, context);
  assert.equal(nodes['rgc-controls'].hidden, true, 'Controls are hidden before successful init');
  assert.equal(nodes['rgc-metric'].disabled, true);
  vm.runInContext(script, context);
  return {
    nodes, window, form, fields, created, exportLabel,
    get forbiddenCalls() { return forbiddenCalls; },
    get rows() { return nodes['rgc-rows'].children; },
    row(name) { return this.rows.find(row => findClass(row, 'rgc-name').textContent === name); },
    choose(key) { nodes['rgc-metric'].value = key; nodes['rgc-metric'].dispatch('change'); },
    sort(value) { nodes['rgc-sort'].value = value; nodes['rgc-sort'].dispatch('change'); },
    field(name) { return fields.find(field => field.name === name); },
    change(name, value, event = 'change') {
      this.field(name).value = value;
      form.dispatch(event, {target: this.field(name)});
    },
  };
}

function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }
function findClass(node, name) { return descendants(node).find(item => item.className.split(' ').includes(name)); }
function names(f) { return f.rows.map(row => findClass(row, 'rgc-name').textContent); }
function snapshot(f) {
  return JSON.stringify(f.form.elements.map(field => [field.name, field.value, field.checked, field.disabled, field.required]));
}
function axisValues(f) { return f.nodes['rgc-axis'].children.map(tick => Number(tick.getAttribute('data-value'))); }
function barGeometry(f, name) {
  const row = f.row(name);
  const track = findClass(row, 'rgc-track');
  const bar = findClass(row, 'rgc-bar');
  return {zero: parseFloat(track.styles['--rgc-zero']), left: bar && parseFloat(bar.styles['--rgc-left']), width: bar && parseFloat(bar.styles['--rgc-width'])};
}
function near(actual, expected) { assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`); }
function test(name, callback) { callback(); assertions++; console.log('PASS ' + name); }

test('template boundary, single escaped payload, outside GET form and existing fallback tables', () => {
  assert.equal((template.match(/include "participants\/_report_group_comparison.html"/g) || []).length, 1);
  assert.match(template, /if report.key == 'groups' and group_comparison/);
  const includePosition = template.indexOf('include "participants/_report_group_comparison.html"');
  assert.ok(includePosition > template.indexOf('</form>'));
  assert.ok(includePosition > template.indexOf('{% if filters_valid %}'));
  assert.ok(includePosition < template.indexOf('{% for section in report.sections %}'));
  assert.match(template, /report-center\.js' %\}" defer>[\s\S]*report-group-charts\.js' %\}" defer>/);
  assert.match(template, /css\/report-group-charts\.css/);
  assert.match(partial, /group_comparison\|json_script:"report-group-comparison-data"/);
  assert.match(partial, /\{\{ group_comparison.scope_note \}\}/);
  assert.equal((partial.match(/<select\b/g) || []).length, 2);
  for (const select of partial.match(/<select\b[^>]*>/g)) {
    assert.match(select, /disabled/);
    assert.doesNotMatch(select, /\bname=|\bform=/);
  }
  assert.match(partial, /id="rgc-controls" hidden/);
  assert.match(partial, /role="region"[\s\S]*aria-describedby=/);
  assert.match(partial, /aria-live="polite"/);
  assert.match(partial, /الجداول أدناه/);
  assert.match(partial, /آخر نتائج معروضة/);
  assert.doesNotMatch(partial, /\|safe|autoescape off|<canvas|<svg|cdn|<form/i);
  assert.doesNotMatch(script, /innerHTML|insertAdjacentHTML|fetch\(|XMLHttpRequest|localStorage|FormData|URLSearchParams|location\.|form\.elements|form\.submit|requestSubmit|window\.[A-Za-z]+\s*=/);
});

test('light RTL layout, physical LTR chart coordinates, touch and no viewport/fixed chart sizing', () => {
  assert.match(css, /background: var\(--white\)/);
  assert.match(css, /background: var\(--sand\)/);
  assert.match(css, /var\(--font-display\)/);
  assert.match(css, /min-height: 44px/);
  assert.match(css, /minmax\(0, 1fr\)/);
  assert.match(css, /white-space: normal;\s*overflow-wrap: anywhere/);
  assert.match(css, /\.rgc-track\s*\{[^}]*width: 100%;[^}]*direction: ltr/);
  assert.match(css, /left: var\(--rgc-left\)/);
  assert.match(css, /width: var\(--rgc-width\)/);
  assert.match(css, /\.rgc-bar--negative\s*\{[^}]*var\(--alert\)/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /prefers-reduced-motion/);
  assert.doesNotMatch(css, /\b\d+(?:\.\d+)?(?:vw|vmin|vmax)\b|text-overflow: ellipsis|max-height|overflow-y:|animation:(?! none)/);
  assert.doesNotMatch(css, /\.wrap\s*\{/);
});

test('successful progressive init, all six indicators and no form mutation', () => {
  const f = setup();
  assert.equal(f.nodes['report-group-comparison'].dataset.state, 'ready');
  assert.equal(f.nodes['rgc-controls'].hidden, false);
  assert.equal(f.nodes['rgc-metric'].disabled, false);
  assert.equal(f.nodes['rgc-sort'].disabled, false);
  assert.equal(f.nodes['rgc-figure'].hidden, false);
  assert.equal(f.nodes['rgc-fallback'].hidden, true);
  assert.deepEqual(f.nodes['rgc-metric'].children.map(option => option.value), keys);
  const original = snapshot(f);
  for (const [index, key] of keys.entries()) {
    f.choose(key);
    assert.equal(f.nodes['rgc-metric-title'].textContent, labels[index]);
    assert.match(f.nodes['rgc-description'].textContent, /تعريف المؤشر/);
    assert.equal(f.rows.length, 4);
    assert.equal(snapshot(f), original);
    assert.equal(f.nodes['rc-pending'].hidden, true);
    assert.equal(f.form.getAttribute('aria-busy'), null);
  }
  assert.equal(f.forbiddenCalls, 0);
});

test('fixed 0/50/100 percentage axis, real zero vs missing denominator and source counts', () => {
  const f = setup();
  assert.deepEqual(axisValues(f), [0, 50, 100]);
  const tickPositions = f.nodes['rgc-axis'].children.map(tick => tick.styles['--rgc-position']);
  assert.deepEqual(tickPositions, ['0%', '50%', '100%']);
  near(barGeometry(f, 'باء').width, 50);
  near(barGeometry(f, 'تاء').width, 100);
  assert.equal(findClass(f.row('ألف'), 'rgc-bar'), undefined);
  assert.equal(findClass(f.row('ألف'), 'rgc-value').textContent, arNumber(0) + ' ٪');
  assert.equal(findClass(f.row('ثاء — بيئة بلا بيانات'), 'rgc-bar'), undefined);
  assert.equal(findClass(f.row('ثاء — بيئة بلا بيانات'), 'rgc-zero'), undefined);
  assert.match(f.row('ثاء — بيئة بلا بيانات').textContent, /غير متاح — المقام صفر/);
  assert.match(f.row('باء').textContent, /سجل لقاء مسجل/);
  assert.ok(f.row('باء').textContent.includes(arNumber(1) + ' من ' + arNumber(2)));
  f.choose('submitters');
  assert.match(f.row('باء').textContent, /طالب في النطاق الحالي/);
  assert.doesNotMatch(f.row('باء').textContent, /سجل لقاء/);
  for (const row of f.rows) assert.equal(findClass(row, 'rgc-track').getAttribute('aria-hidden'), 'true');
});

test('mixed signed -8/+12 net axis has exact 40% zero and distinct signed bars/labels', () => {
  const f = setup({data: payload({net_average: [
    {name: 'سالب', value: -8, numerator: -16, denominator: 2},
    {name: 'موجب', value: 12, numerator: 36, denominator: 3},
    {name: 'صفر', value: 0, numerator: 0, denominator: 4},
    {name: 'فارغة', value: null, numerator: 0, denominator: 0},
  ]})});
  f.choose('net_average');
  assert.deepEqual(axisValues(f), [-8, 0, 12]);
  assert.deepEqual(barGeometry(f, 'سالب'), {zero: 40, left: 0, width: 40});
  assert.deepEqual(barGeometry(f, 'موجب'), {zero: 40, left: 40, width: 60});
  assert.ok(findClass(f.row('سالب'), 'rgc-bar--negative'));
  assert.ok(findClass(f.row('موجب'), 'rgc-bar--points'));
  assert.ok(findClass(f.row('سالب'), 'rgc-value--negative'));
  assert.ok(findClass(f.row('سالب'), 'rgc-value').textContent.includes('-'));
  assert.ok(findClass(f.row('سالب'), 'rgc-value').textContent.includes(arNumber(-8)));
  assert.ok(findClass(f.row('سالب'), 'rgc-counts').textContent.includes(arNumber(-16)));
  assert.ok(findClass(f.row('موجب'), 'rgc-counts').textContent.includes(arNumber(36)));
  assert.match(f.nodes['rgc-scale-note'].textContent, /يسار الصفر/);
  assert.equal(findClass(f.row('صفر'), 'rgc-bar'), undefined);
});

test('one-sided signed axes always include zero, decimals stay at two or fewer', () => {
  for (const values of [[-8, -4], [4, 12]]) {
    const f = setup({data: payload({net_average: values.map((value, index) => ({name: String(index), value, numerator: value, denominator: 1}))})});
    f.choose('net_average');
    assert.deepEqual(axisValues(f), values[0] < 0 ? [-8, 0] : [0, 12]);
    near(barGeometry(f, '0').zero, values[0] < 0 ? 100 : 0);
  }
  const f = setup({data: payload({circle: [{name: 'دقة', value: 33.333333, numerator: 1, denominator: 3}]})});
  f.choose('circle');
  assert.equal(findClass(f.row('دقة'), 'rgc-value').textContent, arNumber(33.333333) + ' ٪');
  near(barGeometry(f, 'دقة').width, 33.333333);
  assert.deepEqual(axisValues(f), [0, 50, 100]);
});

test('all-zero, all-unavailable and zero-groups are honest, with no fabricated bars', () => {
  for (const key of keys) {
    const f = setup({data: payload({[key]: [
      {name: 'صفر', value: 0, numerator: 0, denominator: 2},
      {name: 'بدون طلاب', value: null, numerator: 0, denominator: 0},
    ]})});
    f.choose(key);
    assert.equal(f.nodes['rgc-message'].hidden, false);
    assert.match(f.nodes['rgc-message'].textContent, /تساوي صفرًا/);
    assert.ok(f.rows.every(row => !findClass(row, 'rgc-bar')));
    assert.deepEqual(axisValues(f), key === 'net_average' ? [0, 1] : [0, 50, 100]);
    if (key === 'net_average') assert.match(f.nodes['rgc-scale-note'].textContent, /محور توضيحي/);
  }
  const absent = [{name: 'فارغة', value: null, numerator: 0, denominator: 0}];
  const f = setup({data: payload(Object.fromEntries(keys.map(key => [key, absent])))});
  for (const key of keys) {
    f.choose(key);
    assert.equal(f.nodes['rgc-axis-layout'].hidden, true);
    assert.match(f.nodes['rgc-message'].textContent, /لا تتوافر بيانات/);
    assert.equal(findClass(f.rows[0], 'rgc-bar'), undefined);
    assert.match(f.rows[0].textContent, /غير متاح/);
  }
  const empty = setup({data: payload(Object.fromEntries(keys.map(key => [key, []])))});
  for (const key of keys) {
    empty.choose(key);
    assert.equal(empty.rows.length, 0);
    assert.equal(empty.nodes['rgc-axis-layout'].hidden, true);
    assert.match(empty.nodes['rgc-message'].textContent, /لا توجد بيئات مطابقة/);
  }
});

test('highest sort has null last, deterministic name ties, original payload unchanged', () => {
  const points = [
    {name: 'ثاء', value: null, numerator: 0, denominator: 0},
    {name: 'باء', value: 50, numerator: 1, denominator: 2},
    {name: 'ألف', value: 50, numerator: 1, denominator: 2},
    {name: 'جيم', value: 0, numerator: 0, denominator: 2},
  ];
  const data = payload({meeting: points});
  const original = JSON.stringify(data);
  const f = setup({data});
  const applied = snapshot(f);
  f.sort('highest');
  assert.deepEqual(names(f), ['ألف', 'باء', 'جيم', 'ثاء']);
  f.sort('name');
  const collator = new Intl.Collator('ar', {numeric: true});
  assert.deepEqual(names(f), points.map(point => point.name).sort((a, b) => collator.compare(a, b) || (a < b ? -1 : a > b ? 1 : 0)));
  assert.equal(f.nodes['report-group-comparison-data'].textContent, original);
  assert.equal(JSON.stringify(data), original);
  assert.equal(snapshot(f), applied);
  assert.equal(f.nodes['rc-pending'].hidden, true);
});

test('53 complete groups and a late extreme stay on one common axis after sorting', () => {
  const points = Array.from({length: 53}, (_, index) => ({
    name: 'بيئة ' + index + ' — اسم عربي طويل كامل لا يختصر ولا يحذف من المقارنة',
    value: index === 52 ? 1200 : index - 8,
    numerator: index === 52 ? 1200 : index - 8, denominator: 1,
  }));
  const f = setup({data: payload({net_average: points})});
  f.choose('net_average');
  assert.equal(f.rows.length, 53);
  assert.deepEqual(axisValues(f), [-8, 0, 1200]);
  assert.ok(findClass(f.nodes['rgc-axis'], 'rgc-axis--staggered'));
  assert.ok(findClass(f.nodes['rgc-axis'], 'rgc-tick--staggered'), 'A near-endpoint zero label has its own line without moving its coordinate');
  near(barGeometry(f, points[52].name).width, 1200 / 1208 * 100);
  near(barGeometry(f, points[0].name).zero, 8 / 1208 * 100);
  f.sort('highest');
  assert.equal(names(f)[0], points[52].name);
  assert.equal(f.rows.length, 53);
  assert.deepEqual(axisValues(f), [-8, 0, 1200]);
  assert.ok(names(f).every(name => name.includes('لا يختصر ولا يحذف')));
  assert.doesNotMatch(script, /\.slice\(0,\s*50|page_obj|report\.sections|querySelectorAll\([^)]*tr/);
});

test('pending input uses existing report state without pretending to apply a new period', () => {
  const f = setup();
  const originalRows = f.rows.slice();
  const originalData = f.nodes['report-group-comparison-data'].textContent;
  const originalTitle = f.nodes['rgc-metric-title'].textContent;
  assert.equal(f.nodes['rgc-stale'].hidden, true);
  f.change('group', '99', 'input');
  assert.equal(f.nodes['rc-pending'].hidden, false);
  assert.equal(f.nodes['rgc-stale'].hidden, false);
  assert.equal(f.nodes['report-group-comparison'].dataset.stale, 'true');
  assert.deepEqual(f.rows, originalRows, 'Editing filters must not redraw the old results as new data');
  assert.equal(f.nodes['rgc-metric-title'].textContent, originalTitle);
  assert.equal(f.nodes['report-group-comparison-data'].textContent, originalData);
  f.choose('net_average');
  assert.equal(f.nodes['rgc-stale'].hidden, false, 'Metric change does not clear pending filters');
  f.change('group', '');
  assert.equal(f.nodes['rgc-stale'].hidden, true);
  f.change('period', 'custom');
  assert.equal(f.field('start_date').disabled, false, 'Original report-center listener runs first');
  assert.equal(f.nodes['rgc-stale'].hidden, false);
  f.window.dispatch('pageshow');
  assert.equal(f.nodes['rgc-stale'].hidden, false);
  f.change('period', 'week');
  assert.equal(f.nodes['rgc-stale'].hidden, true);
  assert.equal(f.forbiddenCalls, 0);
});

test('untrusted names and labels are literal text, with no HTML execution or global helpers', () => {
  const hostile = '<img src=x onerror="window.attacked=true"> & <script>alert(1)</script> اسم كامل';
  const data = payload({meeting: [{name: hostile, value: 50, numerator: 1, denominator: 2}]});
  data.series[0].label = hostile;
  data.series[0].description = hostile;
  const f = setup({data});
  assert.equal(findClass(f.rows[0], 'rgc-name').textContent, hostile);
  assert.equal(f.nodes['rgc-metric-title'].textContent, hostile);
  assert.equal(f.nodes['rgc-description'].textContent, hostile);
  assert.equal(f.nodes['rgc-metric'].children[0].textContent, hostile);
  assert.equal(f.window.attacked, undefined);
  assert.ok(f.created.every(tag => ['span', 'option', 'li', 'div', 'small', 'bdi'].includes(tag)));
  assert.equal(f.forbiddenCalls, 0);
});

test('malformed JSON/shape, invalid numbers and missing nodes fail gracefully to tables', () => {
  for (const raw of ['{', 'null', '{}', '{"series":[]}', JSON.stringify({...payload(), series: [payload().series[0]]})]) {
    let f;
    assert.doesNotThrow(() => { f = setup({raw}); });
    assert.equal(f.nodes['report-group-comparison'].dataset.state, 'fallback');
    assert.equal(f.nodes['rgc-controls'].hidden, true);
    assert.equal(f.nodes['rgc-metric'].disabled, true);
    assert.equal(f.nodes['rgc-sort'].disabled, true);
    assert.equal(f.nodes['rgc-figure'].hidden, true);
    assert.match(f.nodes['rgc-fallback'].textContent, /الجداول أدناه/);
    assert.equal(f.forbiddenCalls, 0);
  }
  for (const change of [
    point => { point.value = '50'; },
    point => { point.value = 150; },
    point => { point.denominator = 0; point.value = 0; },
    point => { point.denominator = -1; },
    point => { point.name = {html: 'bad'}; },
  ]) {
    const data = payload();
    change(data.series[0].points[0]);
    assert.equal(setup({data}).nodes['report-group-comparison'].dataset.state, 'fallback');
  }
  const wrongUnit = payload();
  wrongUnit.series[0].unit = 'points';
  assert.equal(setup({data: wrongUnit}).nodes['report-group-comparison'].dataset.state, 'fallback');
  const unknown = payload();
  unknown.series[0].key = '__proto__';
  assert.equal(setup({data: unknown}).nodes['report-group-comparison'].dataset.state, 'fallback');
  assert.doesNotThrow(() => setup({missing: ['rgc-rows']}));
  assert.doesNotThrow(() => setup({missing: ['report-group-comparison-data']}));
  assert.doesNotThrow(() => setup({noRoot: true}));
  const runtime = setup();
  runtime.choose('unknown');
  assert.equal(runtime.nodes['report-group-comparison'].dataset.state, 'fallback');
  assert.equal(runtime.nodes['rgc-controls'].hidden, true);
});

console.log(`Report group charts UI: ${assertions} contract groups passed. No browser, database, dependencies, network, storage or production data used.`);
