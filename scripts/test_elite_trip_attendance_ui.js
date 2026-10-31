/* Behavioral checks for elite-trip roster controls, with no browser or production writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function control(attributes = {}) {
  return {
    attributes, listeners: {}, value: '', selectedIndex: 0, options: [], textContent: '', hidden: true, disabled: false,
    getAttribute(name) { return this.attributes[name]; },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  };
}

function select(value, selectedIndex) {
  const item = control();
  item.options = [
    {value: '', text: '— اختر الحالة —'},
    {value: 'present', text: 'حاضر'},
    {value: 'excused', text: 'معتذر'},
    {value: 'absent', text: 'غائب'},
    {value: 'not_nominated', text: 'غير مرشح'},
  ];
  item.value = value;
  item.selectedIndex = selectedIndex;
  return item;
}

(function () {
  const first = select('present', 1);
  const second = select('absent', 3);
  const bulkStatus = select('', 0);
  const bulkApply = control();
  const feedback = control();
  const root = {
    querySelector(selector) {
      return {
        '#elite-trip-bulk-status': bulkStatus,
        '#elite-trip-bulk-apply': bulkApply,
        '#elite-trip-bulk-feedback': feedback,
      }[selector] || null;
    },
    querySelectorAll(selector) {
      return selector === '[data-elite-trip-status]' ? [first, second] : [];
    },
  };
  const confirmations = [];
  let confirmed = true;
  const context = {
    window: {confirm(message) { confirmations.push(message); return confirmed; }},
    document: {readyState: 'complete', querySelectorAll: () => [root]},
  };

  vm.runInNewContext(fs.readFileSync('static/js/elite-trip-attendance.js', 'utf8'), context);
  assert.equal(first.attributes['data-status'], 'present');
  assert.equal(second.attributes['data-status'], 'absent');
  assert.equal(bulkApply.disabled, true);

  first.value = 'not_nominated';
  first.listeners.change();
  assert.equal(first.attributes['data-status'], 'not_nominated');

  bulkStatus.value = 'excused';
  bulkStatus.selectedIndex = 2;
  bulkStatus.listeners.change();
  assert.equal(bulkApply.disabled, false);
  bulkApply.listeners.click();
  assert.equal(first.value, 'excused');
  assert.equal(second.value, 'excused');
  assert.equal(first.attributes['data-status'], 'excused');
  assert.match(feedback.textContent, /تم تعيين حالة «معتذر» لجميع الطلاب/);
  assert.match(confirmations[0], /2 طالبًا/);

  bulkStatus.value = 'present';
  bulkStatus.selectedIndex = 1;
  bulkStatus.listeners.change();
  confirmed = false;
  bulkApply.listeners.click();
  assert.equal(first.value, 'excused');
  assert.equal(second.value, 'excused');
  assert.match(feedback.textContent, /لم تُغيّر حالات الطلاب/);
  console.log('Elite-trip UI: immediate individual select state, confirmed bulk assignment, and cancellation passed.');
})();
