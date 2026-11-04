/* Dependency-free DOM contract checks; no server, database, network or production writes. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/js/task-review-preview.js'), 'utf8');
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
    this.parentElement = null; this.listeners = {}; this.style = {}; this.hidden = false;
    this.disabled = false; this.checked = false; this.open = false; this._text = text;
    this.classList = {
      add: (...names) => { this.className = [...new Set(this.className.split(/\s+/).filter(Boolean).concat(names))].join(' '); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(' '); },
    };
  }
  get id() { return this.attrs.id || ''; } set id(value) { this.attrs.id = value; }
  get className() { return this.attrs.class || ''; } set className(value) { this.attrs.class = value; }
  get name() { return this.attrs.name || ''; } set name(value) { this.attrs.name = value; }
  get value() { return this.attrs.value || ''; } set value(value) { this.attrs.value = value; }
  get type() { return this.attrs.type || ''; } set type(value) { this.attrs.type = value; }
  get htmlFor() { return this.attrs.for || ''; } set htmlFor(value) { this.attrs.for = value; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = value; this.replaceChildren(); }
  get isConnected() { return this === currentDocument.body || Boolean(this.parentElement && this.parentElement.isConnected); }
  get tabIndex() { return this.attrs.tabindex === undefined ? 0 : Number(this.attrs.tabindex); }
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
  getClientRects() { return this.hidden ? [] : [{}]; }
  showModal() { this.open = true; }
  close() { this.open = false; this.dispatch('close'); }
}
function add(parent, tag, attrs, text) { return parent.appendChild(new Element(tag, attrs, text)); }
function setup({review = true, legacy = false, accept = true, type = 'image', url = '/media/preview.jpg', text = '<img src=x onerror="alert(1)"> & نص آمن'} = {}) {
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
  const button = add(fileCell, 'button', {class: 'preview-btn', 'data-url': url, 'data-type': type, 'data-content': text, 'data-name': '<script>اسم الطالب</script>', 'data-title': 'مهمة اختبار', 'data-status': 'قيد المراجعة'});
  let original;
  if (review) {
    original = add(add(row, 'td'), 'div', {class: 'review-actions'});
    function form(action, className) {
      const form = add(original, 'form', {method: 'post', action: '/participants/tasks/review/?task=1', class: className});
      add(form, 'input', {name: 'csrfmiddlewaretoken', value: 'csrf-fixture', type: 'hidden'});
      add(form, 'input', {name: 'action', value: action, type: 'hidden'});
      add(form, 'input', {name: 'submission_id', value: '12', type: 'hidden'});
      return form;
    }
    if (accept) {
      const accepted = form('accept', 'accept-form');
      if (legacy) {
        const label = add(accepted, 'label', {for: 'historical-award-12'});
        const select = add(label, 'select', {id: 'historical-award-12', name: 'historical_award', required: ''});
        select.value = 'not_awarded';
      }
      const featured = add(accepted, 'label', {class: 'featured-checkbox-label'});
      const checkbox = add(featured, 'input', {type: 'checkbox', name: 'is_featured', value: 'on'}); checkbox.checked = true;
      add(accepted, 'button', {type: 'submit'}, 'قبول');
    } else add(original, 'span', {class: 'rejection-note'}, 'يلزم تحقق المشرف العام');
    const rejected = form('reject', 'reject-form');
    add(rejected, 'input', {type: 'text', name: 'rejection_reason', value: 'سبب الرفض'});
    add(rejected, 'button', {type: 'submit'}, 'رفض');
    const returned = form('return_for_revision', 'return-form');
    add(returned, 'textarea', {name: 'revision_note'}, 'ملاحظة');
    add(returned, 'button', {type: 'submit'}, 'إعادة للتعديل');
  }
  const otherRow = add(tbody, 'tr', {'data-group': 'باء'});
  add(otherRow, 'td', {}, 'ألف طالب'); add(otherRow, 'td', {}, 'باء');
  const missing = add(card, 'ul', {class: 'not-submitted-list'});
  const missingRow = add(missing, 'li', {'data-group': 'باء', 'data-participant': '14'});
  const modal = add(body, 'dialog', {id: 'preview-modal'});
  const title = add(modal, 'h2', {id: 'preview-title'});
  const close = add(modal, 'button', {id: 'modal-close-btn'});
  const status = add(modal, 'p', {id: 'preview-status'});
  const content = add(modal, 'div', {id: 'modal-content'});
  const actions = add(modal, 'aside', {id: 'modal-review-actions'});
  vm.runInNewContext(source, {document: currentDocument, fetch: () => { throw Error('Preview must not call AJAX'); }});
  return {body, card, button, original, modal, title, close, status, content, actions, row, otherRow, missingRow, alpha, beta, all, none, count, missingCount, submittedEmpty, missingEmpty, sort, tbody};
}
function successfulControls(form, submitter) {
  const payload = {};
  for (const field of form.querySelectorAll('input, select, textarea')) {
    if (!field.disabled && field.name && (field.type !== 'checkbox' || field.checked)) payload[field.name] = field.value;
  }
  if (submitter && submitter.name) payload[submitter.name] = submitter.value;
  return payload;
}

const fixture = setup({legacy: true});
fixture.button.focus(); fixture.button.dispatch('click');
assert.equal(fixture.modal.open, true);
assert.equal(currentDocument.activeElement, fixture.close);
assert.equal(fixture.body.style.overflow, 'hidden');
assert.match(fixture.title.textContent, /<script>اسم الطالب<\/script>/);
assert.equal(fixture.title.children.length, 0, 'Student name is literal text, never parsed HTML');
assert.equal(fixture.content.querySelector('img').src, '/media/preview.jpg');
assert.equal(fixture.content.querySelector('pre').textContent, '<img src=x onerror="alert(1)"> & نص آمن');
assert.equal(fixture.content.querySelector('pre').children.length, 0);
assert.equal(fixture.content.querySelector('a').getAttribute('target'), null); // property assigned, rather than parsed HTML
assert.equal(fixture.content.querySelector('a').target, '_blank');
assert.equal(fixture.content.querySelector('a').rel, 'noopener');
const copiedAccept = fixture.actions.querySelector('.accept-form');
const copiedReject = fixture.actions.querySelector('.reject-form');
assert.ok(copiedAccept && copiedReject);
assert.equal(copiedAccept.getAttribute('method'), 'post');
assert.equal(copiedAccept.getAttribute('action'), '/participants/tasks/review/?task=1');
assert.equal(fixture.actions.querySelector('.return-form'), null);
assert.ok(fixture.original.querySelector('.return-form'), 'Original return form stays in the list');
assert.ok(fixture.original.querySelector('.featured-checkbox-label'), 'Original featured checkbox stays unchanged');
assert.equal(fixture.actions.querySelector('.featured-checkbox-label'), null);
assert.equal(copiedAccept.querySelector('select').id, 'preview-historical-award-12');
assert.equal(copiedAccept.querySelector('label').htmlFor, 'preview-historical-award-12');
assert.equal(fixture.original.querySelector('select').id, 'historical-award-12');
const featured = copiedAccept.querySelector('.btn-featured');
const normal = copiedAccept.querySelectorAll('button')[1];
assert.equal(featured.textContent, 'متميز');
assert.deepEqual(successfulControls(copiedAccept, featured), {
  csrfmiddlewaretoken: 'csrf-fixture', action: 'accept', submission_id: '12', historical_award: 'not_awarded', is_featured: 'on',
});
assert.equal(successfulControls(copiedAccept, normal).is_featured, undefined, 'Normal acceptance cannot inherit checked featured checkbox');
assert.equal(successfulControls(copiedReject).rejection_reason, 'سبب الرفض');
copiedAccept.dispatch('submit');
assert.match(fixture.status.textContent, /جارٍ إرسال القرار/);
assert.match(fixture.status.textContent, /إن انتهت الجلسة/);
assert.doesNotMatch(fixture.status.textContent, /تم قبول|تم حفظ/, 'No premature AJAX success');
const last = copiedReject.querySelector('button'); last.focus();
assert.equal(fixture.modal.dispatch('keydown', {key: 'Tab'}).defaultPrevented, true);
assert.equal(currentDocument.activeElement, fixture.close);
assert.equal(fixture.modal.dispatch('keydown', {key: 'Tab', shiftKey: true}).defaultPrevented, true);
assert.equal(currentDocument.activeElement, last);
assert.equal(fixture.modal.dispatch('cancel').defaultPrevented, true, 'Escape handled through native dialog cancel');
assert.equal(fixture.modal.open, false);
assert.equal(fixture.content.children.length, 0);
assert.equal(fixture.actions.children.length, 0);
assert.equal(fixture.body.style.overflow, 'auto');
assert.equal(currentDocument.activeElement, fixture.button);
assert.ok(fixture.original.querySelector('.accept-form'));
fixture.button.dispatch('click'); fixture.close.dispatch('click');
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

for (const type of ['pdf', 'audio', 'video']) {
  const media = setup({type, url: '/media/preview.' + type, review: false});
  media.button.dispatch('click');
  const element = media.content.querySelector(type === 'pdf' ? 'iframe' : type);
  assert.equal(element.src, '/media/preview.' + type);
  if (type === 'pdf') assert.match(element.title, /PDF/);
  else assert.equal(element.controls, true);
  assert.equal(media.content.querySelectorAll('pre').length, 1, 'File AND text coexist for every media type');
  assert.equal(media.actions.querySelectorAll('form').length, 0, 'Read-only/nonpending preview has no decision tools');
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
assert.equal(legacyGroup.actions.querySelector('.btn-featured'), null);
assert.ok(legacyGroup.actions.querySelector('.reject-form'));
assert.match(legacyGroup.actions.textContent, /يلزم تحقق المشرف العام/);
console.log('Task review UI: file+text, original native POST/CSRF/legacy fields, featured/normal/reject, read-only states, Escape/focus, filters/counts and sorting passed.');
