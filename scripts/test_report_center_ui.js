/* Dependency-free contract checks. No browser, server, database or network. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'static/js/report-center.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'participants/templates/participants/report_center.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/css/report-center.css'), 'utf8');

class Element {
  constructor(attrs = {}) {
    this.attrs = {...attrs};
    this.name = attrs.name || '';
    this.type = attrs.type || '';
    this.value = attrs.value || '';
    this.disabled = Boolean(attrs.disabled);
    this.checked = Boolean(attrs.checked);
    this.required = false;
    this.hidden = false;
    this.open = false;
    this.textContent = '';
    this.listeners = {};
    this.dataset = {};
    this.details = null;
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name, fields = {}) {
    const event = {target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields};
    for (const callback of this.listeners[name] || []) callback(event);
    return event;
  }
  closest(selector) { return selector === 'details' ? this.details : null; }
}

function setup({valid = true, period = 'week', dateError = false, missingNodes = false} = {}) {
  const form = new Element();
  form.dataset.filtersValid = String(valid);
  form.attrs.method = 'get';
  form.attrs.action = '/participants/reports/';
  const controls = [
    new Element({name: 'period', type: 'select-one', value: period}),
    new Element({name: 'start_date', type: 'date', value: '2026-11-01'}),
    new Element({name: 'end_date', type: 'date', value: '2026-11-07'}),
    new Element({name: 'group', type: 'select-one', value: ''}),
    new Element({name: 'academic_stage', type: 'select-one', value: ''}),
    new Element({name: 'participant', type: 'select-one', value: ''}),
    new Element({name: 'account_status', type: 'select-one', value: 'active'}),
    new Element({name: 'detail', type: 'select-one', value: 'summary'}),
    new Element({name: 'points_basis', type: 'select-one', value: 'recorded'}),
    new Element({name: 'task', type: 'select-one', value: ''}),
    new Element({name: 'include_private', type: 'checkbox', value: 'on'}),
    ...['summary', 'students', 'attendance', 'tasks', 'groups'].map(value =>
      new Element({name: 'reports', type: 'checkbox', value, checked: value === 'summary'})),
  ];
  const view = new Element({type: 'submit'});
  const pdf = new Element({name: 'format', type: 'submit', value: 'pdf', disabled: !valid});
  const xlsx = new Element({name: 'format', type: 'submit', value: 'xlsx', disabled: !valid});
  pdf.attrs.formaction = xlsx.attrs.formaction = '/participants/reports/export/';
  const buttons = [view, pdf, xlsx];
  form.elements = controls.concat(buttons);
  form.elements.namedItem = name => form.elements.find(field => field.name === name) || null;
  const labels = ['PDF', 'Excel'].map(value => new Element({'data-export-label': value}));
  const pending = new Element();
  const status = new Element();
  const dateRange = new Element();
  dateRange.querySelector = () => dateError ? new Element() : null;
  const nodes = {'report-center-form': form, 'rc-pending': pending, 'rc-submit-status': status, 'rc-date-range': dateRange};
  form.querySelectorAll = selector => selector === '[data-export-label]' ? labels : buttons;
  const window = new Element();
  const timers = new Map();
  let nextTimer = 1;
  let confirmResult = true;
  const confirmations = [];
  window.confirm = text => { confirmations.push(text); return confirmResult; };
  window.setTimeout = (callback, delay) => { const id = nextTimer++; timers.set(id, {callback, delay}); return id; };
  window.clearTimeout = id => timers.delete(id);
  const forbidden = () => { throw Error('UI must not perform network requests or persist settings'); };
  vm.runInNewContext(script, {
    document: {getElementById: id => missingNodes && id !== 'report-center-form' ? null : nodes[id] || null},
    window, fetch: forbidden, XMLHttpRequest: forbidden, localStorage: {setItem: forbidden},
  });
  return {
    form, controls, buttons, view, pdf, xlsx, labels, pending, status, dateRange, window, timers, confirmations,
    field: name => controls.find(field => field.name === name),
    report: value => controls.find(field => field.name === 'reports' && field.value === value),
    change(name, value) { const field = this.field(name); field.value = value; form.dispatch('change', {target: field}); },
    setConfirm(value) { confirmResult = value; },
    finishExport() { const timer = [...timers.values()][0]; assert.ok(timer); timer.callback(); timers.clear(); },
  };
}

function successfulQuery(fixture, submitter) {
  const query = new URLSearchParams();
  for (const field of fixture.form.elements) {
    if (!field.name || field.disabled || field.type === 'submit') continue;
    if (['checkbox', 'radio'].includes(field.type) && !field.checked) continue;
    query.append(field.name, field.value);
  }
  if (submitter.name && !submitter.disabled) query.append(submitter.name, submitter.value);
  return query;
}

function testTemplateContract() {
  assert.match(template, /extends "participants\/app_base.html"/);
  assert.match(template, /\.wrap\{width:100%;max-width:100%;min-width:0\}/);
  assert.match(template, /method="get"[\s\S]*?action="\{% url 'participants:report_center' %\}"/);
  assert.equal((template.match(/<form\b/g) || []).length, 1, 'View and exports share one form');
  assert.equal((template.match(/formaction="\{% url 'participants:report_center_export' %\}"/g) || []).length, 2);
  assert.match(template, /name="format" value="pdf"/);
  assert.match(template, /name="format" value="xlsx"/);
  assert.doesNotMatch(template, /csrf_token|method="post"|\|safe|autoescape off|<canvas|cdn/i);
  const fields = ['period', 'start_date', 'end_date', 'group', 'academic_stage', 'participant', 'account_status', 'detail', 'points_basis', 'task', 'include_private', 'reports'];
  for (const field of fields) {
    assert.match(template, new RegExp('form\\.' + field + '\\b'), field + ' is rendered');
    assert.match(template, new RegExp('form\\.' + field + '\\.errors'), field + ' errors are rendered');
    assert.match(template, new RegExp('form\\.' + field + '\\.auto_id \\}\\}_error'), field + ' errors have accessible IDs');
  }
  assert.match(template, /form.non_field_errors/);
  assert.match(template, /<details class="rc-advanced"/);
  assert.match(template, /<fieldset class="rc-report-selector"/);
  assert.match(template, /for option in form.reports/);
  assert.match(template, /option.tag/);
  assert.match(template, /الهوية والجوال والملاحظات في التقرير والتصدير/);
  assert.match(template, /if not filters_valid %\}disabled/);
  assert.match(template, /if filters_valid %\}[\s\S]*?for report in reports/);
  assert.match(template, /for item in filter_labels/);
  assert.match(template, /generated_at\|date/);
  assert.match(template, /for section in report.sections/);
  assert.match(template, /for column in section.columns/);
  assert.match(template, /for row in section.rows/);
  assert.match(template, /for cell in row.cells/);
  assert.match(template, /if forloop.first and row.detail_url/);
  assert.match(template, /href="\{\{ row.detail_url \}\}"/);
  assert.match(template, /cell\|default_if_none:"—"/);
  assert.match(template, /section.page_obj.paginator.count/);
  assert.equal((template.match(/pagination_query \}\}&amp;/g) || []).length, 2);
  assert.match(template, /page=\{\{ section.page_obj.next_page_number \}\}#reports-results/);
  assert.match(template, /id="reports-results"/);
  assert.match(template, /لا توجد سجلات مطابقة/);
  assert.match(template, /\{% empty %\}/);
  assert.match(template, /<noscript>/);
  assert.doesNotMatch(template, /report\.key == '(store|elite|followup|messages)'/);
  assert.doesNotMatch(css, /\b\d+(?:\.\d+)?(?:vw|vmin|vmax)\b/);
  assert.match(css, /\.rc-table-scroll\s*\{[^}]*max-width: 100%;[^}]*overflow-x: auto;/);
  assert.match(css, /min-height: 44px/);
  assert.match(css, /\.rc-table th,[\s\S]*?overflow-wrap: anywhere;[\s\S]*?white-space: normal;/);
  assert.match(css, /minmax\(0, 1fr\)/);
  assert.match(css, /var\(--font-display\)/);
  assert.match(css, /:focus-visible/);
  assert.doesNotMatch(script, /innerHTML|insertAdjacentHTML|fetch\(|XMLHttpRequest|localStorage|form\.submit\(|requestSubmit\(|location\./);
}

function testDatesAndPending() {
  const f = setup();
  assert.equal(f.field('start_date').disabled, true);
  assert.equal(f.field('start_date').required, false);
  assert.equal(f.dateRange.hidden, true);
  assert.equal(f.pending.hidden, true);
  assert.equal(f.field('include_private').checked, false);
  assert.deepEqual(successfulQuery(f, f.pdf).getAll('reports'), ['summary']);
  assert.equal(successfulQuery(f, f.pdf).has('start_date'), false);
  assert.equal(successfulQuery(f, f.pdf).has('include_private'), false);
  f.change('period', 'custom');
  assert.equal(f.field('start_date').disabled, false);
  assert.equal(f.field('end_date').required, true);
  assert.equal(f.dateRange.hidden, false);
  assert.equal(f.pending.hidden, false);
  assert.equal(f.labels[0].textContent, 'PDF بالإعدادات الحالية');
  assert.equal(successfulQuery(f, f.pdf).get('start_date'), '2026-11-01');
  f.change('period', 'week');
  assert.equal(f.pending.hidden, true, 'Reverting to applied settings clears pending notification');
  assert.equal(f.labels[1].textContent, 'Excel');
  f.change('group', '27');
  assert.equal(f.pending.hidden, false);
  assert.equal(f.form.getAttribute('aria-busy'), null, 'Changing filters does not submit');
  assert.equal(f.confirmations.length, 0);
  f.change('group', '');
  assert.equal(f.pending.hidden, true);
  f.field('include_private').checked = true;
  f.form.dispatch('input');
  assert.equal(f.pending.hidden, false);
  assert.equal(successfulQuery(f, f.xlsx).get('include_private'), 'on');
  f.field('include_private').checked = false;
  f.form.dispatch('change');
  assert.equal(f.pending.hidden, true);
  for (const value of ['month', 'all']) {
    f.change('period', value);
    assert.equal(f.field('end_date').disabled, true);
  }
  const custom = setup({period: 'custom'});
  assert.equal(custom.field('start_date').required, true);
  assert.equal(custom.pending.hidden, true);
  const errors = setup({valid: false, dateError: true});
  assert.equal(errors.dateRange.hidden, false, 'Server date errors stay visible');
}

function testNativeExports() {
  const f = setup();
  f.report('students').checked = true;
  f.report('groups').checked = true;
  f.form.dispatch('change');
  f.change('group', '42');
  f.setConfirm(false);
  const cancel = f.form.dispatch('submit', {submitter: f.pdf});
  assert.equal(cancel.defaultPrevented, true);
  assert.equal(f.form.getAttribute('aria-busy'), null);
  assert.equal(f.status.textContent, '');
  assert.match(f.confirmations[0], /الإعدادات الحالية والتقارير المختارة/);
  f.setConfirm(true);
  const event = f.form.dispatch('submit', {submitter: f.xlsx});
  assert.equal(event.defaultPrevented, false, 'Native GET is allowed');
  const query = successfulQuery(f, f.xlsx);
  assert.deepEqual(query.getAll('reports'), ['summary', 'students', 'groups']);
  assert.equal(query.get('group'), '42', 'Current unsent filters, not old URL data');
  assert.equal(query.get('format'), 'xlsx', 'Submitter remains successful after busy state');
  assert.equal(f.xlsx.getAttribute('formaction'), '/participants/reports/export/');
  assert.equal(f.form.getAttribute('action'), '/participants/reports/');
  for (const button of f.buttons) assert.equal(button.disabled, false);
  assert.equal(f.form.getAttribute('aria-busy'), 'true');
  assert.match(f.status.textContent, /جارٍ طلب التصدير/);
  assert.doesNotMatch(f.status.textContent, /تم التصدير|نجاح/);
  assert.equal(f.form.dispatch('submit', {submitter: f.view}).defaultPrevented, true, 'Duplicate submit is blocked');
  f.finishExport();
  assert.equal(f.form.getAttribute('aria-busy'), 'false');
  assert.equal(f.pending.hidden, false, 'Export never pretends to apply displayed results');
  assert.match(f.status.textContent, /لم تتغير النتائج المعروضة/);
  assert.equal(f.form.dispatch('submit', {submitter: f.pdf}).defaultPrevented, false);
  assert.equal(successfulQuery(f, f.pdf).get('format'), 'pdf');
  const unchanged = setup();
  assert.equal(unchanged.form.dispatch('submit', {submitter: unchanged.pdf}).defaultPrevented, false);
  assert.equal(unchanged.confirmations.length, 0);
  const empty = setup();
  empty.report('summary').checked = false;
  empty.form.dispatch('change');
  empty.form.dispatch('submit', {submitter: empty.pdf});
  assert.deepEqual(successfulQuery(empty, empty.pdf).getAll('reports'), [], 'No silent default replaces empty selection; server validates');
}

function testSubmissionAndRecovery() {
  const f = setup();
  f.change('detail', 'detailed');
  assert.equal(f.form.dispatch('submit', {submitter: f.view}).defaultPrevented, false);
  assert.equal(successfulQuery(f, f.view).has('format'), false);
  assert.match(f.status.textContent, /جارٍ التحقق/);
  assert.equal(f.confirmations.length, 0);
  f.window.dispatch('pageshow');
  assert.equal(f.form.getAttribute('aria-busy'), 'false');
  assert.equal(f.status.textContent, '');
  assert.equal(f.pending.hidden, false, 'Back-forward restoration retains pending edits');
  const invalid = setup({valid: false});
  invalid.change('group', '42');
  assert.equal(invalid.pdf.disabled, true, 'Correction requires valid manual view before export');
  assert.equal(invalid.form.dispatch('submit', {submitter: invalid.pdf}).defaultPrevented, true);
  assert.match(invalid.status.textContent, /عرض التقرير/);
  assert.equal(invalid.form.dispatch('submit', {submitter: invalid.view}).defaultPrevented, false);
  invalid.window.dispatch('pageshow');
  assert.equal(invalid.pdf.disabled, true, 'Busy recovery preserves server-invalid export lock');
  const advanced = new Element();
  invalid.field('participant').details = advanced;
  invalid.form.dispatch('invalid', {target: invalid.field('participant')});
  assert.equal(advanced.open, true);
  const legacy = setup();
  legacy.pdf.dispatch('click');
  assert.equal(legacy.form.dispatch('submit').defaultPrevented, false);
  assert.match(legacy.status.textContent, /التصدير/);
  const implicit = setup();
  implicit.pdf.dispatch('click'); // A previously invalid click never reached submission.
  assert.equal(implicit.form.dispatch('submit', {submitter: null}).defaultPrevented, false);
  assert.match(implicit.status.textContent, /جارٍ التحقق/, 'Standard null submitter must not reuse a stale export click');
  const invalidClick = setup();
  invalidClick.pdf.dispatch('click');
  invalidClick.form.dispatch('invalid', {target: invalidClick.field('start_date')});
  invalidClick.form.dispatch('submit');
  assert.match(invalidClick.status.textContent, /جارٍ التحقق/, 'Invalid native controls clear the legacy click fallback');
  const download = setup();
  download.form.dispatch('submit', {submitter: download.pdf});
  assert.equal(download.timers.size, 1);
  download.window.dispatch('pageshow');
  assert.equal(download.timers.size, 0, 'Back-forward restoration clears a pending download timer');
  const protectedEvent = setup();
  protectedEvent.form.dispatch('submit', {submitter: protectedEvent.view, defaultPrevented: true});
  assert.equal(protectedEvent.form.getAttribute('aria-busy'), null);
  assert.doesNotThrow(() => setup({missingNodes: true}));
  assert.doesNotThrow(() => vm.runInNewContext(script, {document: {getElementById: () => null}}));
}

testTemplateContract();
testDatesAndPending();
testNativeExports();
testSubmissionAndRecovery();
console.log('Report centre UI: template/context/escaping, RTL/touch/contained tables, dates, pending edits, repeated reports, current GET exports/format/formaction, invalid-state lock, busy and recovery passed. No browser or database used.');
