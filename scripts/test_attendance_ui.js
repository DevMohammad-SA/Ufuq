/* Behavioral checks with a small DOM contract, no browser or production writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function control(attributes = {}) {
  return {
    attributes, listeners: {}, hidden: true, checked: false, value: '', textContent: '', disabled: false,
    getAttribute(name) { return this.attributes[name]; },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    reportValidity() { return true; },
  };
}

(async function () {
  const extra = [control(), control()];
  const toggle = control({'aria-expanded':'false'});
  const status = control();
  const field = control(); field.value = 'ملاحظة محفوظة منفصلة';
  const button = control({'data-participant':'12', 'data-note-field':'note-12'});
  const early = control(); const attended = control();
  early.closest = () => ({querySelector: () => attended});
  const earlyAll = control({'data-target':'early'});
  const values = {'csrfmiddlewaretoken':'test-csrf', date:'2026-10-24', group:'3'};
  const form = {action:'https://example.test/participants/supervisor/dashboard/', querySelector(selector) {
    const key = selector.match(/name="([^"]+)"/)[1]; return {value:values[key]};
  }};
  button.closest = () => form;
  button.parentElement = {querySelector: () => status};
  const table = {querySelectorAll(selector) { return selector === '.student-extra' ? extra : []; }};
  const root = {
    querySelector(selector) { return {'#roster-table':table, '#roster-body':{}, '#student-data-toggle':toggle}[selector] || null; },
    querySelectorAll(selector) {
      if (selector === '.note-save-button') return [button];
      if (selector === '.select-all') return [earlyAll];
      if (selector.includes('data-col="early"')) return [early];
      return [];
    },
  };
  let lastRequest;
  let response = {ok:true, redirected:true, url:form.action+'?date=2026-10-24&group=3'};
  const context = {
    document:{readyState:'complete', querySelectorAll:()=>[root], getElementById:()=>field},
    FormData, URL,
    fetch: async (url, options) => {lastRequest={url, options};return response;},
  };
  vm.runInNewContext(fs.readFileSync('static/js/attendance-roster.js','utf8'), context);
  toggle.listeners.click();
  assert.equal(toggle.attributes['aria-expanded'],'true');
  assert.ok(extra.every(cell => !cell.hidden));
  toggle.listeners.click();
  assert.ok(extra.every(cell => cell.hidden));
  early.checked = true; early.listeners.change(); assert.equal(attended.checked, true);
  early.checked = false; earlyAll.listeners.click(); assert.equal(early.checked,true);assert.equal(attended.checked,true);
  button.listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  const sent = Object.fromEntries(lastRequest.options.body.entries());
  assert.equal(sent.action,'save_note'); assert.equal(sent.participant_id,'12');
  assert.equal(sent.note_text,field.value);assert.equal(sent.date,values.date);assert.equal(sent.group,values.group);
  assert.equal(sent.csrfmiddlewaretoken,values.csrfmiddlewaretoken);
  assert.equal(Object.keys(sent).length,6);
  assert.equal(lastRequest.options.credentials,'same-origin');
  assert.match(status.textContent,/تم حفظ الملاحظة دون تغيير التحضير/);
  response = {ok:true, redirected:true, url:'https://example.test/accounts/login/supervisor/?next=/participants/supervisor/dashboard/'};
  button.listeners.click();await new Promise(resolve=>setImmediate(resolve));
  assert.match(status.textContent,/تعذر حفظ الملاحظة/);assert.equal(button.disabled,false);
  console.log('Attendance UI: hidden-column toggle, early attendance, note-only payload and expired-session handling passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
