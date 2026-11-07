/* Dependency-free synthetic DOM contract checks; no server, database, network or production writes. */
'use strict';
const nativeAssert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/js/task-review-preview.js'), 'utf8');
let assertions = 0;
let fixtures = 0;
const assert = new Proxy(nativeAssert, {
  get(target, key) {
    return typeof target[key] === 'function' ? (...args) => { assertions += 1; return target[key](...args); } : target[key];
  },
});
let currentDocument;

function matchesSimple(node, selector) {
  const tag = selector.match(/^[a-z]+/i);
  if (tag && node.tagName !== tag[0].toLowerCase()) return false;
  const id = selector.match(/#([\w-]+)/);
  if (id && node.id !== id[1]) return false;
  for (const match of selector.matchAll(/\.([\w-]+)/g)) {
    if (!node.className.split(/\s+/).includes(match[1])) return false;
  }
  for (const match of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    const value = node.getAttribute(match[1]);
    if (value === null || (match[2] !== undefined && value !== match[2])) return false;
  }
  return true;
}
function matches(node, selector) {
  const parts = selector.trim().split(/\s+/);
  if (!matchesSimple(node, parts.pop())) return false;
  let ancestor = node.parentElement;
  while (parts.length) {
    const part = parts.pop();
    while (ancestor && !matchesSimple(ancestor, part)) ancestor = ancestor.parentElement;
    if (!ancestor) return false;
    ancestor = ancestor.parentElement;
  }
  return true;
}
class Element {
  constructor(tag, attrs = {}, text = '') {
    this.tagName = tag.toLowerCase(); this.attrs = {...attrs}; this.children = [];
    this.parentElement = null; this.listeners = {}; this.style = {}; this.hidden = Object.hasOwn(attrs, 'hidden');
    this.disabled = Object.hasOwn(attrs, 'disabled'); this.checked = Object.hasOwn(attrs, 'checked'); this.open = false; this._text = text;
    this.classList = {
      add: (...names) => { this.className = [...new Set(this.className.split(/\s+/).filter(Boolean).concat(names))].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(' '); },
      contains: name => this.className.split(/\s+/).includes(name),
    };
  }
  get id() { return this.attrs.id || ''; } set id(value) { this.attrs.id = value; }
  get className() { return this.attrs.class || ''; } set className(value) { this.attrs.class = value; }
  get name() { return this.attrs.name || ''; } set name(value) { this.attrs.name = value; }
  get value() {
    if (Object.hasOwn(this.attrs, 'value')) return this.attrs.value;
    if (this.type === 'checkbox') return 'on'; // Native browser default, even without a value attribute.
    return this.tagName === 'textarea' ? this._text : '';
  }
  set value(value) { this.attrs.value = value; }
  get type() { return this.attrs.type || ''; } set type(value) { this.attrs.type = value; }
  get htmlFor() { return this.attrs.for || ''; } set htmlFor(value) { this.attrs.for = value; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = value; this.replaceChildren(); }
  set innerHTML(value) { throw Error('Student content must not be parsed as HTML: ' + value); }
  get isConnected() { return this === currentDocument.body || Boolean(this.parentElement && this.parentElement.isConnected); }
  get tabIndex() { return this.attrs.tabindex === undefined ? (this.type === 'hidden' ? -1 : 0) : Number(this.attrs.tabindex); }
  set tabIndex(value) { this.attrs.tabindex = String(value); }
  set controls(value) { if (value) this.attrs.controls = ''; else delete this.attrs.controls; }
  get controls() { return Object.hasOwn(this.attrs, 'controls'); }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name, fields = {}) {
    const event = {target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...fields};
    for (const callback of this.listeners[name] || []) callback(event);
    return event;
  }
  appendChild(child) { child.remove(); child.parentElement = this; this.children.push(child); return child; }
  insertBefore(child, before) { child.remove(); child.parentElement = this; const index = this.children.indexOf(before); this.children.splice(index < 0 ? this.children.length : index, 0, child); return child; }
  remove() { if (this.parentElement) { const index = this.parentElement.children.indexOf(this); this.parentElement.children.splice(index, 1); this.parentElement = null; } }
  replaceChildren(...children) { for (const child of this.children) child.parentElement = null; this.children = []; for (const child of children) this.appendChild(child); }
  querySelectorAll(selector) {
    const selectors = selector.split(','); const result = [];
    const walk = node => { for (const child of node.children) { if (selectors.some(item => matches(child, item))) result.push(child); walk(child); } };
    walk(this); return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { let element = this; while (element) { if (matches(element, selector)) return element; element = element.parentElement; } return null; }
  cloneNode(deep) { const clone = new Element(this.tagName, this.attrs, this._text); clone.checked = this.checked; clone.disabled = this.disabled; clone.hidden = this.hidden; if (deep) for (const child of this.children) clone.appendChild(child.cloneNode(true)); return clone; }
  focus() { currentDocument.activeElement = this; }
  getClientRects() { return this.hidden || this.type === 'hidden' ? [] : [{}]; }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatch('close'); }
}
function add(parent, tag, attrs, text) { return parent.appendChild(new Element(tag, attrs, text)); }
function snapshot(node) {
  return JSON.stringify({tag: node.tagName, attrs: node.attrs, checked: node.checked, disabled: node.disabled, hidden: node.hidden, text: node._text, children: node.children.map(child => JSON.parse(snapshot(child)))});
}
function setup({review = true, legacy = false, accept = true, featuredChecked = false, unavailable = '', mode = 'pending', historical = false, award = '10', type = 'image', url = '/media/preview.jpg', text = '<img src=x onerror="alert(1)"> & نص آمن', target = '/participants/tasks/review/?task=1'} = {}) {
  fixtures += 1;
  const body = new Element('body'); body.style.overflow = 'auto';
  currentDocument = {
    body, activeElement: null,
    querySelectorAll: selector => body.querySelectorAll(selector),
    getElementById: id => body.querySelector('#' + id),
    createElement: tag => new Element(tag),
  };
  const card = add(body, 'section', {class: 'task-review-card', 'data-task': '1'});
  const all = add(card, 'button', {class: 'group-filter-all'});
  const none = add(card, 'button', {class: 'group-filter-none'});
  const alpha = add(card, 'input', {class: 'group-filter-checkbox', value: 'ألف'}); alpha.checked = true;
  const beta = add(card, 'input', {class: 'group-filter-checkbox', value: 'باء'}); beta.checked = true;
  const count = add(card, 'span', {'data-count': 'submitted'});
  const missingCount = add(card, 'span', {'data-count': 'not-submitted'});
  const submittedEmpty = add(card, 'p', {'data-empty': 'submitted'});
  const missingEmpty = add(card, 'p', {'data-empty': 'not-submitted'});
  const table = add(card, 'table', {class: 'submissions-table'});
  const head = add(table, 'thead'); const headRow = add(head, 'tr');
  const sort = add(headRow, 'th', {class: 'sortable', 'data-col': '0', 'data-sort': 'text'});
  const tbody = add(table, 'tbody', {class: 'submissions-body'});
  const row = add(tbody, 'tr', {'data-group': 'ألف', 'data-submission': '12'});
  add(row, 'td', {}, 'باء طالب'); add(row, 'td', {}, 'ألف');
  const fileCell = add(row, 'td');
  const button = add(fileCell, 'button', {class: 'preview-btn', type: 'button', 'data-url': url, 'data-type': type, 'data-content': text, 'data-name': '<script>اسم الطالب</script>', 'data-title': 'مهمة اختبار', 'data-status': mode === 'pending' ? 'قيد المراجعة' : 'مقبولة'});
  let original;
  if (review) {
    original = add(add(row, 'td'), 'div', {class: 'review-actions'});
    function form(action, className) {
      const form = add(original, 'form', {method: 'post', action: target, class: className});
      add(form, 'input', {name: 'csrfmiddlewaretoken', value: 'csrf-fixture', type: 'hidden'});
      add(form, 'input', {name: 'action', value: action, type: 'hidden'});
      add(form, 'input', {name: 'submission_id', value: '12', type: 'hidden'});
      return form;
    }
    if (mode === 'pending') {
      if (accept) {
        const accepted = form('accept', 'accept-form');
        if (legacy) {
          const label = add(accepted, 'label', {for: 'historical-award-12'});
          const select = add(label, 'select', {id: 'historical-award-12', name: 'historical_award', required: ''});
          select.value = 'not_awarded';
        }
        const featured = add(accepted, 'label', {class: 'featured-checkbox-label', for: 'featured-12'}, 'متميز');
        const checkbox = add(featured, 'input', {id: 'featured-12', type: 'checkbox', name: 'is_featured'});
        checkbox.checked = featuredChecked;
        add(accepted, 'button', {type: 'submit'}, 'قبول');
      } else add(original, 'span', {class: 'rejection-note'}, 'يلزم تحقق المشرف العام');
      const rejected = form('reject', 'reject-form');
      add(rejected, 'input', {type: 'text', name: 'rejection_reason', value: 'سبب الرفض'});
      add(rejected, 'button', {type: 'submit'}, 'رفض');
      if (unavailable) {
        const marker = add(original, 'div', {class: 'return-unavailable'});
        add(marker, 'button', {type: 'button', class: 'btn btn-return btn-sm', disabled: ''}, 'إعادة للتعديل');
        add(marker, 'p', {class: 'rejection-note'}, unavailable);
      } else {
        const returned = form('return_for_revision', 'return-form');
        const label = add(returned, 'label', {for: 'revision-note-12'}, 'ملاحظة التعديل');
        add(label, 'textarea', {id: 'revision-note-12', name: 'revision_note', required: '', maxlength: '2000', rows: '2', class: 'return-note-input'}, 'ملاحظة');
        add(returned, 'button', {type: 'submit'}, 'إعادة للتعديل');
      }
    } else {
      const canceled = form(mode === 'legacy-revision' ? 'revise_grade' : 'cancel_grade', mode === 'legacy-revision' ? 'grade-revision-form' : 'grade-cancellation-form');
      if (mode !== 'legacy-revision') canceled.setAttribute('data-award', historical ? '' : award);
      add(canceled, 'input', {type: 'hidden', name: 'review_version', value: 'signed-version-fixture:12:7'});
      if (mode !== 'legacy-revision') add(canceled, 'input', {type: 'hidden', name: 'confirm', value: 'yes'});
      if (historical) {
        const awardId = mode === 'legacy-revision' ? 'previous-award-12' : 'cancel-award-12';
        const label = add(canceled, 'label', {for: awardId}, 'المنحة السابقة');
        const select = add(label, 'select', {id: awardId, name: 'previous_award', required: ''});
        select.value = '12';
      }
      if (mode === 'legacy-revision') {
        add(canceled, 'select', {name: 'grade', required: '', value: 'rejected'});
        add(canceled, 'textarea', {name: 'reason', required: '', maxlength: '2000'}, 'سبب محفوظ قديم');
      }
      add(canceled, 'button', {type: 'submit'}, mode === 'legacy-revision' ? 'حفظ تعديل التقييم' : 'إلغاء التقييم');
    }
  }
  const otherRow = add(tbody, 'tr', {'data-group': 'باء', 'data-submission': '13'});
  add(otherRow, 'td', {}, 'ألف طالب'); add(otherRow, 'td', {}, 'باء');
  const otherOriginal = add(add(otherRow, 'td'), 'div', {class: 'review-actions'});
  const otherForm = add(otherOriginal, 'form', {class: 'accept-form', method: 'post'});
  add(otherForm, 'input', {id: 'featured-13', type: 'checkbox', name: 'is_featured'});
  const missing = add(card, 'ul', {class: 'not-submitted-list'});
  const missingRow = add(missing, 'li', {'data-group': 'باء', 'data-participant': '14'});
  const modal = add(body, 'dialog', {id: 'preview-modal'});
  const title = add(modal, 'h2', {id: 'preview-title'});
  const close = add(modal, 'button', {id: 'modal-close-btn', type: 'button'});
  const status = add(modal, 'p', {id: 'preview-status'});
  const content = add(modal, 'div', {id: 'modal-content'});
  const actions = add(modal, 'aside', {id: 'modal-review-actions'});
  const confirms = [];
  const confirmation = {result: true};
  vm.runInNewContext(source, {
    document: currentDocument,
    window: {confirm: message => { confirms.push(message); return confirmation.result; }},
    fetch: () => { throw Error('Preview must not call AJAX'); },
    XMLHttpRequest: function () { throw Error('Preview must not call AJAX'); },
  });
  return {body, card, button, original, otherOriginal, modal, title, close, status, content, actions, row, otherRow, missingRow, alpha, beta, all, none, count, missingCount, submittedEmpty, missingEmpty, sort, tbody, confirms, confirmation};
}
function successfulControls(form, submitter) {
  const payload = {};
  for (const field of form.querySelectorAll('input, select, textarea')) {
    if (!field.disabled && field.name && (field.type !== 'checkbox' || field.checked)) payload[field.name] = field.value;
  }
  if (submitter && submitter.name && !submitter.disabled) payload[submitter.name] = submitter.value;
  return payload;
}
function assertNative(form, action, target = '/participants/tasks/review/?task=1') {
  assert.equal(form.getAttribute('method'), 'post');
  assert.equal(form.getAttribute('action'), target);
  const payload = successfulControls(form);
  assert.equal(payload.csrfmiddlewaretoken, 'csrf-fixture');
  assert.equal(payload.submission_id, '12');
  assert.equal(payload.action, action);
}
function assertFocusCycle(fixture, last) {
  last.focus();
  assert.equal(fixture.modal.dispatch('keydown', {key: 'Tab'}).defaultPrevented, true);
  assert.equal(currentDocument.activeElement, fixture.close);
  assert.equal(fixture.modal.dispatch('keydown', {key: 'Tab', shiftKey: true}).defaultPrevented, true);
  assert.equal(currentDocument.activeElement, last);
}

const fixture = setup({legacy: true});
const originalSnapshot = snapshot(fixture.original);
const otherSnapshot = snapshot(fixture.otherOriginal);
fixture.button.focus(); fixture.button.dispatch('click');
assert.equal(fixture.modal.open, true);
assert.equal(currentDocument.activeElement, fixture.close);
assert.equal(fixture.body.style.overflow, 'hidden');
assert.match(fixture.title.textContent, /<script>اسم الطالب<\/script>/);
assert.equal(fixture.title.children.length, 0, 'Student name is literal text, never parsed HTML');
assert.equal(fixture.content.querySelector('img').src, '/media/preview.jpg');
assert.equal(fixture.content.querySelector('pre').textContent, '<img src=x onerror="alert(1)"> & نص آمن');
assert.equal(fixture.content.querySelector('pre').children.length, 0);
assert.equal(fixture.content.querySelector('a').target, '_blank');
assert.equal(fixture.content.querySelector('a').rel, 'noopener');
const copiedAccept = fixture.actions.querySelector('.accept-form');
const copiedReject = fixture.actions.querySelector('.reject-form');
const copiedReturn = fixture.actions.querySelector('.return-form');
assert.ok(copiedAccept && copiedReject && copiedReturn, 'All authorized pending forms are cloned');
assertNative(copiedAccept, 'accept'); assertNative(copiedReject, 'reject'); assertNative(copiedReturn, 'return_for_revision');
assert.ok(fixture.original.querySelector('.return-form'), 'Original return form remains in the list');
assert.ok(fixture.actions.querySelector('.featured-checkbox-label'), 'Featured label is retained rather than replaced by a submitter');
assert.equal(copiedAccept.querySelector('.btn-featured'), null);
assert.equal(copiedAccept.querySelector('button[name="is_featured"]'), null, 'No featured submitter can make an implicit decision');
assert.equal(copiedAccept.querySelectorAll('button[type="submit"]').length, 1, 'Acceptance has exactly one submit button');
assert.equal(copiedAccept.querySelector('select').id, 'preview-historical-award-12');
assert.equal(copiedAccept.querySelector('label').htmlFor, 'preview-historical-award-12');
assert.equal(fixture.original.querySelector('select').id, 'historical-award-12');
const checkbox = copiedAccept.querySelector('input[name="is_featured"]');
assert.equal(checkbox.checked, false, 'No implicit featured choice');
assert.equal(checkbox.id, 'preview-featured-12');
assert.equal(copiedAccept.querySelector('.featured-checkbox-label').htmlFor, checkbox.id);
assert.equal(checkbox.getAttribute('value'), null, 'Source need not have an explicit value attribute');
assert.equal(checkbox.value, 'on');
const normal = copiedAccept.querySelector('button[type="submit"]');
assert.equal(checkbox.dispatch('keydown', {key: 'Enter'}).defaultPrevented, true, 'Enter on featured checkbox cannot implicitly accept');
assert.deepEqual(successfulControls(copiedAccept, normal), {
  csrfmiddlewaretoken: 'csrf-fixture', action: 'accept', submission_id: '12', historical_award: 'not_awarded',
});
const beforeToggle = fixture.status.textContent;
// This mock supplies the checked change; real label/Space activation and native
// validation are exercised separately in the Chromium harness.
for (const checked of [true, false, true]) {
  checkbox.checked = checked;
  checkbox.dispatch('change'); checkbox.dispatch('click'); checkbox.dispatch('keydown', {key: ' '});
  assert.equal(checkbox.checked, checked);
  assert.equal(fixture.status.textContent, beforeToggle, 'Toggling is not a POST or sending status');
  assert.equal(fixture.confirms.length, 0);
}
assert.equal(successfulControls(copiedAccept, normal).is_featured, 'on');
assert.equal(successfulControls(copiedReject).rejection_reason, 'سبب الرفض');
copiedReject.querySelector('input[name="rejection_reason"]').value = '';
assert.equal(successfulControls(copiedReject).rejection_reason, '', 'Reason remains optional');
const returnNote = copiedReturn.querySelector('textarea');
assert.equal(returnNote.name, 'revision_note');
assert.equal(returnNote.getAttribute('required'), '');
assert.equal(returnNote.getAttribute('maxlength'), '2000');
assert.equal(returnNote.id, 'preview-revision-note-12');
assert.equal(copiedReturn.querySelector('label').htmlFor, returnNote.id);
returnNote.value = 'ملاحظة جديدة';
assert.equal(successfulControls(copiedReturn).revision_note, 'ملاحظة جديدة');
assert.equal(snapshot(fixture.original), originalSnapshot, 'Editing the clone never writes back to the source row');
assert.equal(snapshot(fixture.otherOriginal), otherSnapshot, 'Another row remains unchanged');
assert.equal(copiedAccept.dispatch('submit', {submitter: normal}).defaultPrevented, false, 'Confirmed decision retains native POST navigation');
assert.match(fixture.status.textContent, /جارٍ إرسال القرار/);
assert.match(fixture.status.textContent, /إن انتهت الجلسة/);
assert.doesNotMatch(fixture.status.textContent, /تم قبول|تم حفظ|تم إلغاء/, 'No premature success');
assertFocusCycle(fixture, copiedReturn.querySelector('button'));
assert.equal(fixture.modal.dispatch('cancel').defaultPrevented, true, 'Escape handled through native dialog cancel');
assert.equal(fixture.modal.open, false);
assert.equal(fixture.content.children.length, 0);
assert.equal(fixture.actions.children.length, 0);
assert.equal(fixture.body.style.overflow, 'auto');
assert.equal(currentDocument.activeElement, fixture.button);
assert.equal(snapshot(fixture.original), originalSnapshot);
fixture.button.dispatch('click');
assert.equal(fixture.actions.querySelector('input[name="is_featured"]').checked, false, 'Reopening reclones source state, not discarded preview choice');
fixture.close.dispatch('click');
assert.equal(currentDocument.activeElement, fixture.button);
assert.equal(fixture.modal.open, false);

assert.equal(fixture.count.textContent, '2');
assert.equal(fixture.missingCount.textContent, '1');
fixture.beta.checked = false; fixture.beta.dispatch('change');
assert.equal(fixture.row.hidden, false);
assert.equal(fixture.otherRow.hidden, true);
assert.equal(fixture.missingRow.hidden, true);
assert.equal(fixture.count.textContent, '1');
assert.equal(fixture.missingCount.textContent, '0');
assert.equal(fixture.missingEmpty.hidden, false);
fixture.none.dispatch('click');
assert.equal(fixture.row.hidden, true);
assert.equal(fixture.count.textContent, '0');
assert.equal(fixture.submittedEmpty.hidden, false);
fixture.all.dispatch('click');
assert.equal(fixture.missingRow.hidden, false);
assert.equal(fixture.count.textContent, '2');
fixture.sort.dispatch('click');
assert.equal(fixture.tbody.children[0], fixture.otherRow);
assert.equal(fixture.sort.getAttribute('aria-sort'), 'ascending');
fixture.sort.dispatch('click');
assert.equal(fixture.tbody.children[0], fixture.row);
assert.equal(fixture.sort.getAttribute('aria-sort'), 'descending');

const checkedSource = setup({featuredChecked: true});
checkedSource.button.dispatch('click');
const checkedClone = checkedSource.actions.querySelector('input[name="is_featured"]');
assert.equal(checkedClone.checked, true, 'An explicitly checked source state is preserved');
checkedClone.checked = false; checkedClone.dispatch('change');
assert.equal(checkedSource.original.querySelector('input[name="is_featured"]').checked, true);
assert.equal(successfulControls(checkedSource.actions.querySelector('.accept-form')).is_featured, undefined);

for (const unavailable of ['انتهى موعد التسليم؛ لا يمكن الإعادة للتعديل.', 'المهمة غير نشطة؛ لا يمكن الإعادة للتعديل.']) {
  const expired = setup({unavailable}); expired.button.dispatch('click');
  assert.equal(expired.actions.querySelector('.return-form'), null, 'No invented return POST form');
  const unavailableButton = expired.actions.querySelector('.return-unavailable button');
  assert.ok(unavailableButton);
  assert.equal(unavailableButton.disabled, true);
  assert.equal(unavailableButton.type, 'button');
  assert.match(expired.actions.textContent, /انتهى موعد|غير نشطة/);
  assert.ok(expired.actions.querySelector('.accept-form'));
  assert.ok(expired.actions.querySelector('.reject-form'));
  assertFocusCycle(expired, expired.actions.querySelector('.reject-form button'));
}

for (const mode of ['cancel', 'legacy-revision']) {
  const cancellation = setup({mode, historical: true, target: '/synthetic/custom-review/?task=old&version=7'});
  const before = snapshot(cancellation.original);
  const sourceForm = cancellation.original.querySelector('form');
  cancellation.confirmation.result = false;
  assert.equal(sourceForm.dispatch('submit').defaultPrevented, true, 'List forms also require confirmation');
  cancellation.button.dispatch('click');
  const form = cancellation.actions.querySelector('form');
  assertNative(form, mode === 'cancel' ? 'cancel_grade' : 'revise_grade', '/synthetic/custom-review/?task=old&version=7');
  assert.equal(successfulControls(form).review_version, 'signed-version-fixture:12:7');
  assert.equal(form.querySelector('select[name="previous_award"]').getAttribute('required'), '');
  const awardId = mode === 'legacy-revision' ? 'preview-previous-award-12' : 'preview-cancel-award-12';
  assert.equal(form.querySelector('select[name="previous_award"]').id, awardId);
  assert.equal(form.querySelector('label').htmlFor, awardId);
  if (mode === 'cancel') {
    assert.equal(successfulControls(form).confirm, 'yes');
    assert.equal(form.querySelector('button').textContent, 'إلغاء التقييم');
    assert.equal(cancellation.actions.querySelector('.accept-form'), null);
    assert.equal(cancellation.actions.querySelector('.return-form'), null);
  }
  const initialStatus = cancellation.status.textContent;
  assert.equal(form.dispatch('submit').defaultPrevented, true);
  assert.equal(cancellation.status.textContent, initialStatus, 'Canceling confirmation must not show sending status');
  assert.doesNotMatch(cancellation.status.textContent, /جارٍ إرسال|تم حفظ|تم إلغاء/);
  if (mode === 'cancel') {
    assert.match(cancellation.confirms.at(-1), /قيد المراجعة/);
    assert.match(cancellation.confirms.at(-1), /منحة|نقاط/);
  } else assert.match(cancellation.confirms.at(-1), /تعديل التقييم/);
  cancellation.confirmation.result = true;
  assert.equal(form.dispatch('submit').defaultPrevented, false);
  assert.match(cancellation.status.textContent, /جارٍ إرسال القرار/);
  assert.doesNotMatch(cancellation.status.textContent, /تم إلغاء|تم حفظ|تم قبول/);
  assert.equal(snapshot(cancellation.original), before);
  assertFocusCycle(cancellation, form.querySelector('button'));
  cancellation.modal.dispatch('cancel');
  assert.equal(cancellation.actions.children.length, 0);
  assert.equal(currentDocument.activeElement, cancellation.button);
}

for (const award of ['0', '10', '12']) {
  const known = setup({mode: 'cancel', award}); known.button.dispatch('click');
  const form = known.actions.querySelector('.grade-cancellation-form');
  assert.equal(form.getAttribute('data-award'), award);
  assert.equal(form.querySelector('[name="previous_award"]'), null);
  known.confirmation.result = false;
  assert.equal(form.dispatch('submit').defaultPrevented, true);
  assert.equal(known.confirms.length, 1, 'Cloned form confirms exactly once');
  if (award === '0') assert.match(known.confirms[0], /لا توجد منحة/);
  else assert.ok(known.confirms[0].includes('(' + award + ' نقطة)'));
  assert.doesNotMatch(known.status.textContent, /جارٍ إرسال|تم إلغاء/);
}

for (const type of ['pdf', 'audio', 'video']) {
  const media = setup({type, url: '/media/preview.' + type, review: false});
  media.button.dispatch('click');
  const element = media.content.querySelector(type === 'pdf' ? 'iframe' : type);
  assert.equal(element.src, '/media/preview.' + type);
  if (type === 'pdf') assert.match(element.title, /PDF/);
  else assert.equal(element.controls, true);
  assert.equal(media.content.querySelectorAll('pre').length, 1, 'File AND text coexist for every media type');
  assert.equal(media.actions.querySelectorAll('form').length, 0, 'Unauthorized/final read-only preview has no invented tools');
  assert.match(media.actions.textContent, /المعاينة فقط/);
  media.modal.dispatch('click');
  assert.equal(media.modal.open, false);
}
const textOnly = setup({url: '', type: 'text', text: 'نص فقط', review: false});
textOnly.button.dispatch('click');
assert.equal(textOnly.content.querySelector('pre').textContent, 'نص فقط');
assert.equal(textOnly.content.querySelector('img'), null);
const empty = setup({url: '', text: '', review: false}); empty.button.dispatch('click');
assert.match(empty.content.textContent, /لا يوجد ملف أو نص/);
const unknown = setup({type: '', text: '', review: false}); unknown.button.dispatch('click');
assert.match(unknown.content.textContent, /لا يمكن معاينة/);
assert.ok(unknown.content.querySelector('a'));
const legacyGroup = setup({accept: false}); legacyGroup.button.dispatch('click');
assert.equal(legacyGroup.actions.querySelector('.accept-form'), null);
assert.equal(legacyGroup.actions.querySelector('button[name="is_featured"]'), null);
assert.ok(legacyGroup.actions.querySelector('.reject-form'));
assert.ok(legacyGroup.actions.querySelector('.return-form'));
assert.match(legacyGroup.actions.textContent, /يلزم تحقق المشرف العام/);
console.log(`Task review Node UI: ${assertions} assertions across ${fixtures} synthetic fixtures passed (featured checkbox only, native POST/CSRF, return validation attributes, cancel/legacy confirmations, immutable source rows, media/text, Escape/focus, filters/counts/sorting; no network/database).`);
