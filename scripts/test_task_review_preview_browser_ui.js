/*
 * Synthetic Chromium UI checks only: actual local JS/CSS, explicitly fabricated
 * server-authorized source forms, no Django/DB/HTTP server or live application URL.
 * Run: NODE_PATH=/tmp/rahhal-mobile-ui-qa/node_modules node scripts/test_task_review_preview_browser_ui.js
 * Uses existing test-only Playwright and /usr/bin/chromium; installs no project dependencies.
 */
'use strict';
const nativeAssert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'static/js/task-review-preview.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/css/task-review-preview.css'), 'utf8');
const template = fs.readFileSync(path.join(root, 'participants/templates/participants/weekly_task_review.html'), 'utf8');
const base = fs.readFileSync(path.join(root, 'participants/templates/participants/app_base.html'), 'utf8');
const baseCss = base.match(/<style>([\s\S]*?)<\/style>/)[1];
const templateCss = template.match(/{% block extra_style %}([\s\S]*?){% endblock %}/)[1];
let assertions = 0;
let cases = 0;
let blockedRequests = 0;
const failures = [];
const assert = new Proxy(nativeAssert, {
  get(target, key) {
    return typeof target[key] === 'function' ? (...args) => { assertions += 1; return target[key](...args); } : target[key];
  },
});
const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const literal = '<img src=x onerror="window.studentCodeRan=true"> & نص الطالب الآمن';
const target = 'https://synthetic.invalid/never-submit/custom-review/?task=old&version=7';
const csrf = 'SYNTHETIC-CSRF-not-a-real-token';
const version = 'SYNTHETIC-signed-review-version:12:7';
const hidden = (name, value) => `<input type="hidden" name="${name}" value="${escape(value)}">`;
function sourceForms({mode = 'pending', legacy = false, historical = false, featuredChecked = false, featuredValue = 'on', unavailable = '', canAccept = true, award = '10'} = {}) {
  if (mode === 'readonly') return '';
  const form = (className, action, controls) => `<form method="post" action="${escape(target)}" class="${className}"${mode === 'cancel' ? ` data-award="${historical ? '' : award}"` : ''}>${hidden('csrfmiddlewaretoken', csrf)}${hidden('action', action)}${hidden('submission_id', '12')}${hidden('review_version', version)}${controls}</form>`;
  if (mode === 'cancel' || mode === 'legacy-revision') {
    const awardId = mode === 'cancel' ? 'cancel-award-12' : 'previous-award-12';
    const previous = historical ? `<label class="field" for="${awardId}">قيمة المنحة السابقة<select id="${awardId}" name="previous_award" required><option value="">حدد ما صُرف دون تخمين</option><option value="0">0</option><option value="10">10</option><option value="12">12</option></select></label>` : '';
    const legacyControls = mode === 'legacy-revision' ? '<select name="grade" required><option value="rejected">رفض</option></select><textarea name="reason" required maxlength="2000">سبب محفوظ في نسخة قديمة</textarea>' : hidden('confirm', 'yes');
    const noScript = mode === 'cancel' ? '<noscript><label><input type="checkbox" required> أؤكد إلغاء التقييم وسحب منحة المهمة المحتسبة.</label></noscript>' : '';
    return `<div class="review-actions">${form(mode === 'cancel' ? 'grade-cancellation-form' : 'grade-revision-form', mode === 'cancel' ? 'cancel_grade' : 'revise_grade', previous + legacyControls + noScript + `<button type="submit" class="btn ${mode === 'cancel' ? 'btn-cancel-grade ' : ''}btn-sm">${mode === 'cancel' ? 'إلغاء التقييم' : 'حفظ تعديل التقييم'}</button>`)}</div>`;
  }
  const choice = legacy ? '<label class="field" for="historical-award-12">تحقق من المنحة القديمة<select id="historical-award-12" name="historical_award" required><option value="">هل صُرفت سابقًا؟</option><option value="already_awarded">نعم</option><option value="not_awarded">لا</option></select></label>' : '';
  // The absent-value variant explicitly tests the browser's default "on".
  const accept = canAccept ? form('accept-form', 'accept', choice + `<label class="featured-checkbox-label" for="featured-12"><input id="featured-12" type="checkbox" name="is_featured"${featuredValue === null ? '' : ` value="${escape(featuredValue)}"`}${featuredChecked ? ' checked' : ''}>متميز</label><button type="submit" class="btn btn-accept btn-sm">قبول</button>`) : '<span class="rejection-note">تسليم قديم: يلزم تحقق المشرف العام.</span>';
  const reject = form('reject-form', 'reject', '<input type="text" name="rejection_reason" class="reject-reason-input" aria-label="سبب الرفض (اختياري)" placeholder="سبب الرفض (اختياري)"><button type="submit" class="btn btn-reject btn-sm">رفض</button>');
  const returned = unavailable ? `<div class="return-unavailable"><button type="button" class="btn btn-return btn-sm" disabled>إعادة للتعديل</button><p class="rejection-note">${escape(unavailable)}</p></div>` : form('return-form', 'return_for_revision', '<label for="revision-note-12">ملاحظة التعديل</label><textarea id="revision-note-12" name="revision_note" class="return-note-input" rows="2" maxlength="2000" required aria-label="ملاحظة التعديل" placeholder="ملاحظة التعديل (مطلوبة)"></textarea><button type="submit" class="btn btn-return btn-sm">إعادة للتعديل</button>');
  return `<div class="review-actions">${accept}${reject}${returned}</div>`;
}
function fixtureHtml(options = {}) {
  const mediaType = options.type || 'text';
  const mediaUrl = options.url === undefined ? '' : options.url;
  const status = options.state || (options.mode === 'cancel' || options.mode === 'legacy-revision' ? 'مقبولة' : 'قيد المراجعة');
  return `<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>SYNTHETIC review fixture — never production</title><style>${baseCss}\n${templateCss}\n${css}</style></head><body class="sidebar-collapsed" style="overflow:auto"><main class="wrap"><section class="card task-review-card" data-task="1"><h2>مهمة مصطنعة للاختبار فقط</h2><div class="group-filter-actions"><button type="button" class="select-all group-filter-all">تحديد الكل</button><button type="button" class="select-all group-filter-none">إلغاء تحديد الكل</button></div><div class="group-filter-checkboxes"><label class="group-filter-item"><input type="checkbox" class="group-filter-checkbox" value="ألف" checked>ألف</label><label class="group-filter-item"><input type="checkbox" class="group-filter-checkbox" value="باء" checked>باء</label></div><h3>من سلّموا <span data-count="submitted"></span></h3><div class="table-scroll"><table class="submissions-table"><thead><tr><th class="sortable" data-col="0" data-sort="text">المشارك</th><th>البيئة</th><th>تاريخ الرفع</th><th>العمل</th><th>الحالة</th><th>الإجراء</th></tr></thead><tbody class="submissions-body"><tr data-group="ألف" data-submission="12"><td>باء طالب باسم كامل طويل محفوظ دون قص</td><td>ألف</td><td>تاريخ مصطنع</td><td><div class="file-actions"><button type="button" id="synthetic-preview" class="btn-inline preview-btn" data-url="${escape(mediaUrl)}" data-type="${escape(mediaType)}" data-content="${escape(literal)}" data-name="${escape('<script>اسم الطالب</script>')}" data-title="مهمة مصطنعة" data-status="${escape(status)}">معاينة</button></div></td><td>${escape(status)}</td><td id="synthetic-source">${sourceForms(options)}</td></tr><tr data-group="باء" data-submission="13"><td>ألف طالب</td><td>باء</td><td>تاريخ مصطنع</td><td></td><td>قيد المراجعة</td><td id="synthetic-other-source"><div class="review-actions"><form class="accept-form" method="post" action="${escape(target)}">${hidden('csrfmiddlewaretoken', 'OTHER-SYNTHETIC-CSRF')}${hidden('submission_id', '13')}<label class="featured-checkbox-label" for="featured-13"><input id="featured-13" type="checkbox" name="is_featured">متميز</label><button type="submit" class="btn btn-accept btn-sm">قبول</button></form></div></td></tr></tbody></table></div><p data-empty="submitted" hidden>لا تسليمات</p><h3>من لم يسلّموا <span data-count="not-submitted"></span></h3><ul class="not-submitted-list"><li data-group="باء" data-participant="14">طالب مصطنع لم يسلّم</li></ul><p data-empty="not-submitted" hidden>لا طلاب</p></section></main><dialog id="preview-modal" class="task-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="preview-title" dir="rtl"><header class="task-preview-header"><h2 id="preview-title">معاينة</h2><button type="button" class="btn-inline" id="modal-close-btn" aria-label="إغلاق المعاينة">×</button></header><p id="preview-help">بيانات مصطنعة: القرار من النموذج المصرح نفسه.</p><p id="preview-status" role="status" aria-live="polite"></p><div class="task-preview-layout"><div id="modal-content"></div><aside id="modal-review-actions" aria-label="قرار مراجعة التسليم"></aside></div></dialog><script>
window.captured = []; window.submitEvents = []; window.invalidFields = []; window.confirmMessages = []; window.studentCodeRan = false;
window.fetch = () => { throw new Error('Network fetch forbidden in synthetic fixture'); };
window.XMLHttpRequest = function () { throw new Error('XHR forbidden in synthetic fixture'); };
navigator.sendBeacon = () => { throw new Error('Beacon forbidden in synthetic fixture'); };
HTMLFormElement.prototype.submit = function () { throw new Error('Unintercepted native submit forbidden'); };
window.__syntheticNativeConfirm ||= window.confirm.bind(window);
window.confirm = message => { window.confirmMessages.push(message); return window.__syntheticNativeConfirm(message); };
document.addEventListener('invalid', event => { window.invalidFields.push(event.target.name); }, true);
document.addEventListener('submit', event => {
  const form = event.target;
  const preventedByApplication = event.defaultPrevented;
  // Let application confirmation handlers run first. No native request is ever allowed,
  // even if the application regresses and forgets to prevent a rejected confirmation.
  event.preventDefault();
  window.submitEvents.push({preventedByApplication, action: form.querySelector('[name="action"]')?.value});
  if (!preventedByApplication) window.captured.push({method: form.method, target: form.getAttribute('action'), submitter: event.submitter ? {type: event.submitter.type, name: event.submitter.name, value: event.submitter.value} : null, entries: [...new FormData(form, event.submitter)]});
});
${script}
</script></body></html>`;
}
async function snapshot(page) {
  return page.evaluate(() => ['synthetic-source', 'synthetic-other-source'].map(id => {
    const root = document.getElementById(id);
    return {html: root.innerHTML, fields: [...root.querySelectorAll('input,select,textarea')].map(field => ({name: field.name, value: field.value, checked: field.checked}))};
  }));
}
async function data(page) {
  return page.evaluate(() => ({captured: window.captured, events: window.submitEvents, invalid: window.invalidFields, confirms: window.confirmMessages, status: document.getElementById('preview-status').textContent}));
}
async function mount(page, options = {}) {
  await page.setContent(fixtureHtml(options), {waitUntil: 'domcontentloaded'});
  const before = await snapshot(page);
  await page.locator('#synthetic-preview').click();
  assert.equal(await page.locator('#preview-modal').evaluate(node => node.open), true);
  assert.equal(await page.evaluate(() => document.activeElement.id), 'modal-close-btn');
  return before;
}
async function assertPayload(page, index, action, extras = {}) {
  const result = await data(page);
  const captured = result.captured[index];
  assert.ok(captured, `Native successful controls captured for ${action}`);
  assert.equal(captured.method, 'post');
  assert.equal(captured.target, target);
  assert.deepEqual(Object.fromEntries(captured.entries), {csrfmiddlewaretoken: csrf, action, submission_id: '12', review_version: version, ...extras});
  assert.equal(captured.submitter.type, 'submit');
  assert.equal(captured.entries.filter(([name]) => name === 'is_featured').length, extras.is_featured ? 1 : 0, 'Exactly the existing checkbox supplies featured data');
  assert.match(result.status, /جارٍ إرسال القرار/);
  assert.doesNotMatch(result.status, /تم قبول|تم حفظ|تم إلغاء/, 'The synthetic interception must not become an application success message');
}
async function assertLayout(page, {featured = false} = {}) {
  const geometry = await page.evaluate(() => {
    const dialog = document.getElementById('preview-modal');
    const r = dialog.getBoundingClientRect();
    const targets = [...dialog.querySelectorAll('button, .featured-checkbox-label, textarea, select, input[type="text"]')].filter(node => node.getClientRects().length && !node.disabled).map(node => {
      const rect = node.getBoundingClientRect();
      return {selector: node.className || node.tagName, width: rect.width, height: rect.height, left: rect.left, right: rect.right};
    });
    const label = dialog.querySelector('.featured-checkbox-label');
    return {viewport: innerWidth, documentWidth: document.documentElement.scrollWidth, dialog: {left: r.left, right: r.right, clientWidth: dialog.clientWidth, scrollWidth: dialog.scrollWidth}, targets, labelHeight: label?.getBoundingClientRect().height};
  });
  assert.ok(geometry.documentWidth <= geometry.viewport + 1, `Page overflow: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.dialog.left >= -1 && geometry.dialog.right <= geometry.viewport + 1, `Dialog exceeds viewport: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.dialog.scrollWidth <= geometry.dialog.clientWidth + 1, `Dialog content overflow: ${JSON.stringify(geometry)}`);
  for (const item of geometry.targets) {
    assert.ok(item.height >= 44 - 0.1, `Touch target below 44px: ${JSON.stringify(item)}`);
    assert.ok(item.width >= 44 - 0.1, `Touch target narrower than 44px: ${JSON.stringify(item)}`);
  }
  if (featured) assert.ok(geometry.labelHeight >= 44 - 0.1, 'Whole featured label is the 44px checkbox touch target');
}
async function confirmNext(page, accepted, action) {
  const handler = dialog => {
    assert.equal(dialog.type(), 'confirm');
    return accepted ? dialog.accept() : dialog.dismiss();
  };
  page.once('dialog', handler);
  await action();
}
async function run(name, callback) {
  cases += 1;
  try {
    await callback(); console.log(`ok ${cases} - ${name}`);
  } catch (error) {
    failures.push({name, message: error.message});
    console.error(`not ok ${cases} - ${name}\n${error.stack}`);
  }
}

(async () => {
  const browser = await chromium.launch({executablePath: '/usr/bin/chromium', headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const networkAttempts = [];
  const pageErrors = [];
  try {
    for (const width of [360, 390, 1280]) {
      const context = await browser.newContext({viewport: {width, height: 900}, serviceWorkers: 'block'});
      const page = await context.newPage();
      // Defence in depth: every attempted request, including accidental localhost
      // or application POST, is aborted before reaching any HTTP service.
      await page.route('**/*', route => {
        const request = route.request();
        networkAttempts.push({method: request.method(), url: request.url()});
        blockedRequests += 1;
        return route.abort('blockedbyclient');
      });
      page.on('pageerror', error => pageErrors.push(error.message));
      const prefix = `${width}px`;
      await run(`${prefix}: label, checkbox and Space only toggle checked state`, async () => {
        const before = await mount(page, {featuredValue: null});
        const box = page.locator('#modal-review-actions input[name="is_featured"]');
        const label = page.locator('#modal-review-actions .featured-checkbox-label');
        assert.equal(await box.isChecked(), false);
        assert.equal(await box.getAttribute('value'), null);
        assert.equal(await box.inputValue(), 'on');
        assert.equal(await page.locator('#modal-review-actions .accept-form button[type="submit"]').count(), 1);
        assert.equal(await page.locator('#modal-review-actions button[name="is_featured"], #modal-review-actions .btn-featured').count(), 0);
        const status = (await data(page)).status;
        await label.click(); assert.equal(await box.isChecked(), true);
        await label.click(); assert.equal(await box.isChecked(), false);
        await box.click(); assert.equal(await box.isChecked(), true);
        await box.focus(); await page.keyboard.press('Space'); assert.equal(await box.isChecked(), false);
        await page.keyboard.press('Space'); assert.equal(await box.isChecked(), true);
        await page.keyboard.press('Enter'); assert.equal(await box.isChecked(), true, 'Enter must not implicitly accept');
        const result = await data(page);
        assert.equal(result.events.length, 0); assert.equal(result.captured.length, 0); assert.equal(result.confirms.length, 0);
        assert.equal(result.status, status);
        assert.deepEqual(await snapshot(page), before, 'Preview choice does not affect either list row');
        assert.equal(await box.getAttribute('id'), 'preview-featured-12');
        assert.equal(await label.getAttribute('for'), 'preview-featured-12');
        await assertLayout(page, {featured: true});
      });
      await run(`${prefix}: the single accept submitter sends absent/on checkbox payload`, async () => {
        const before = await mount(page, {featuredValue: null});
        const accept = page.locator('#modal-review-actions .accept-form button[type="submit"]');
        await accept.click(); await assertPayload(page, 0, 'accept');
        await page.locator('#modal-review-actions .featured-checkbox-label').click();
        await accept.click(); await assertPayload(page, 1, 'accept', {is_featured: 'on'});
        assert.equal((await data(page)).captured.length, 2);
        assert.deepEqual(await snapshot(page), before);
      });
      await run(`${prefix}: explicit source checked state is retained and clone edits are discarded`, async () => {
        await page.setContent(fixtureHtml(), {waitUntil: 'domcontentloaded'});
        const sourceBox = page.locator('#synthetic-source input[name="is_featured"]');
        await sourceBox.check(); // Current source property changed outside the preview, not a checked attribute.
        assert.equal(await sourceBox.getAttribute('checked'), null);
        const before = await snapshot(page);
        await page.locator('#synthetic-preview').click();
        const box = page.locator('#modal-review-actions input[name="is_featured"]');
        assert.equal(await box.isChecked(), true);
        await box.uncheck();
        assert.equal(await page.locator('#synthetic-source input[name="is_featured"]').isChecked(), true);
        assert.deepEqual(await snapshot(page), before);
        await page.keyboard.press('Escape');
        await page.locator('#synthetic-preview').click();
        assert.equal(await box.isChecked(), true);
        assert.equal((await data(page)).events.length, 0);
      });
      await run(`${prefix}: optional reject reason is blank or literal without mutating source`, async () => {
        const before = await mount(page);
        const reason = page.locator('#modal-review-actions input[name="rejection_reason"]');
        assert.equal(await reason.getAttribute('required'), null);
        await page.locator('#modal-review-actions .reject-form button').click();
        await assertPayload(page, 0, 'reject', {rejection_reason: ''});
        await reason.fill('سبب اختباري <script> حرفي');
        await page.locator('#modal-review-actions .reject-form button').click();
        await assertPayload(page, 1, 'reject', {rejection_reason: 'سبب اختباري <script> حرفي'});
        assert.deepEqual(await snapshot(page), before);
      });
      await run(`${prefix}: required return textarea, maxlength 2000 and valid native payload`, async () => {
        const before = await mount(page);
        const note = page.locator('#modal-review-actions textarea[name="revision_note"]');
        assert.equal(await note.getAttribute('required'), '');
        assert.equal(await note.getAttribute('maxlength'), '2000');
        assert.equal(await note.getAttribute('id'), 'preview-revision-note-12');
        assert.equal(await page.locator('#modal-review-actions label[for="preview-revision-note-12"]').count(), 1);
        const status = (await data(page)).status;
        await page.locator('#modal-review-actions .return-form button').click();
        let result = await data(page);
        assert.equal(result.captured.length, 0); assert.equal(result.events.length, 0);
        assert.ok(result.invalid.includes('revision_note'));
        assert.equal(result.status, status);
        assert.equal(await note.evaluate(node => node.validity.valueMissing), true);
        await note.fill('x'.repeat(1999));
        await note.press('End'); await page.keyboard.type('yyy');
        assert.equal((await note.inputValue()).length, 2000, 'Native keyboard input enforces maxlength');
        await note.fill('يرجى إكمال التفاصيل المطلوبة.');
        await page.locator('#modal-review-actions .return-form button').click();
        await assertPayload(page, 0, 'return_for_revision', {revision_note: 'يرجى إكمال التفاصيل المطلوبة.'});
        assert.deepEqual(await snapshot(page), before);
        await assertLayout(page, {featured: true});
      });
      await run(`${prefix}: historical accept validation and custom target/CSRF/version survive cloning`, async () => {
        const before = await mount(page, {legacy: true});
        const select = page.locator('#modal-review-actions select[name="historical_award"]');
        assert.equal(await select.getAttribute('id'), 'preview-historical-award-12');
        assert.equal(await page.locator('#modal-review-actions label[for="preview-historical-award-12"]').count(), 1);
        await page.locator('#modal-review-actions .accept-form button').click();
        assert.equal((await data(page)).captured.length, 0);
        assert.ok((await data(page)).invalid.includes('historical_award'));
        await select.selectOption('not_awarded');
        await page.locator('#modal-review-actions .accept-form button').click();
        await assertPayload(page, 0, 'accept', {historical_award: 'not_awarded'});
        await select.selectOption('already_awarded');
        await page.locator('#modal-review-actions input[name="is_featured"]').check();
        await page.locator('#modal-review-actions .accept-form button').click();
        await assertPayload(page, 1, 'accept', {historical_award: 'already_awarded', is_featured: 'on'});
        assert.deepEqual(await snapshot(page), before);
        await assertLayout(page, {featured: true});
      });
      await run(`${prefix}: GENERAL cancel confirmation rejects then accepts without fake success`, async () => {
        const before = await mount(page, {mode: 'cancel'});
        const button = page.locator('#modal-review-actions .grade-cancellation-form button');
        assert.equal(await button.textContent(), 'إلغاء التقييم');
        assert.equal(await page.locator('#modal-review-actions .accept-form, #modal-review-actions .reject-form, #modal-review-actions .return-form').count(), 0);
        const initialStatus = (await data(page)).status;
        await confirmNext(page, false, () => button.click());
        let result = await data(page);
        assert.equal(result.captured.length, 0);
        assert.equal(result.events.at(-1).preventedByApplication, true);
        assert.equal(result.status, initialStatus);
        assert.match(result.confirms.at(-1), /قيد المراجعة/); assert.match(result.confirms.at(-1), /منحة|نقاط/);
        assert.equal(result.confirms.length, 1, 'Cloned cancellation confirms exactly once');
        assert.ok(result.confirms[0].includes('(10 نقطة)'));
        await confirmNext(page, true, () => button.click());
        await assertPayload(page, 0, 'cancel_grade', {confirm: 'yes'});
        assert.deepEqual(await snapshot(page), before);
        await button.focus(); await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'modal-close-btn');
        await page.keyboard.press('Shift+Tab');
        assert.equal(await page.evaluate(() => document.activeElement.closest('form')?.className), 'grade-cancellation-form');
        await assertLayout(page);
      });
      await run(`${prefix}: historical cancel blocks empty previous_award before confirm and retains token`, async () => {
        const before = await mount(page, {mode: 'cancel', historical: true});
        const button = page.locator('#modal-review-actions .grade-cancellation-form button');
        const select = page.locator('#modal-review-actions select[name="previous_award"]');
        assert.equal(await select.getAttribute('required'), '');
        assert.equal(await select.getAttribute('id'), 'preview-cancel-award-12');
        assert.equal(await page.locator('#modal-review-actions label[for="preview-cancel-award-12"]').count(), 1);
        await button.click();
        let result = await data(page);
        assert.equal(result.captured.length, 0); assert.equal(result.confirms.length, 0);
        assert.ok(result.invalid.includes('previous_award'));
        await select.selectOption('12');
        await confirmNext(page, false, () => button.click());
        assert.equal((await data(page)).captured.length, 0);
        await confirmNext(page, true, () => button.click());
        await assertPayload(page, 0, 'cancel_grade', {confirm: 'yes', previous_award: '12'});
        assert.ok((await data(page)).confirms.at(-1).includes('(12 نقطة)'));
        assert.deepEqual(await snapshot(page), before);
        await assertLayout(page);
      });
      await run(`${prefix}: known 0/10/12 awards and all GENERAL final states use exact one-time confirmation`, async () => {
        for (const [award, state] of [['0', 'مرفوضة'], ['10', 'مقبولة'], ['12', 'مميزة'], ['0', 'مُعادة للتعديل']]) {
          const before = await mount(page, {mode: 'cancel', award, state});
          const form = page.locator('#modal-review-actions .grade-cancellation-form');
          assert.equal(await form.getAttribute('data-award'), award);
          assert.equal(await form.locator('select[name="previous_award"]').count(), 0);
          assert.equal(await form.locator('noscript input').count(), 0, 'Script-enabled noscript fallback does not create a validation checkbox');
          const button = form.locator('button');
          await confirmNext(page, false, () => button.click());
          const result = await data(page);
          assert.equal(result.confirms.length, 1);
          assert.equal(result.captured.length, 0);
          assert.equal(result.events.length, 1);
          assert.equal(result.events[0].preventedByApplication, true);
          if (award === '0') assert.match(result.confirms[0], /لا توجد منحة/);
          else assert.ok(result.confirms[0].includes('(' + award + ' نقطة)'));
          assert.doesNotMatch(result.status, /جارٍ إرسال|تم إلغاء/);
          await confirmNext(page, true, () => button.click());
          await assertPayload(page, 0, 'cancel_grade', {confirm: 'yes'});
          assert.deepEqual(await snapshot(page), before);
        }
      });
      await run(`${prefix}: cached legacy revision form keeps list and preview confirmation`, async () => {
        await page.setContent(fixtureHtml({mode: 'legacy-revision'}), {waitUntil: 'domcontentloaded'});
        await confirmNext(page, false, () => page.locator('#synthetic-source .grade-revision-form button').click());
        assert.equal((await data(page)).captured.length, 0);
        assert.equal((await data(page)).events.at(-1).preventedByApplication, true);
        const before = await snapshot(page);
        await page.locator('#synthetic-preview').click();
        const button = page.locator('#modal-review-actions .grade-revision-form button');
        const initialStatus = (await data(page)).status;
        await confirmNext(page, false, () => button.click());
        assert.equal((await data(page)).status, initialStatus);
        assert.match((await data(page)).confirms.at(-1), /تعديل التقييم/);
        await confirmNext(page, true, () => button.click());
        await assertPayload(page, 0, 'revise_grade', {grade: 'rejected', reason: 'سبب محفوظ في نسخة قديمة'});
        assert.deepEqual(await snapshot(page), before);
      });
      await run(`${prefix}: unauthorized and final non-GENERAL previews invent no forms`, async () => {
        for (const state of ['قيد المراجعة: بلا تفويض', 'مقبولة', 'مميزة', 'مرفوضة', 'مُعادة للتعديل']) {
          await mount(page, {mode: 'readonly', state});
          assert.equal(await page.locator('#modal-review-actions form').count(), 0);
          assert.match(await page.locator('#modal-review-actions').textContent(), /المعاينة فقط/);
          assert.equal((await data(page)).captured.length, 0);
          await assertLayout(page);
        }
        await mount(page, {canAccept: false});
        assert.equal(await page.locator('#modal-review-actions .accept-form').count(), 0);
        assert.equal(await page.locator('#modal-review-actions .reject-form, #modal-review-actions .return-form').count(), 2);
        assert.match(await page.locator('#modal-review-actions').textContent(), /يلزم تحقق المشرف العام/);
      });
      await run(`${prefix}: expired and inactive return is disabled, with visible reason and no POST`, async () => {
        for (const unavailable of ['انتهى موعد التسليم؛ لا يمكن الإعادة للتعديل.', 'المهمة غير نشطة؛ لا يمكن الإعادة للتعديل.']) {
          const before = await mount(page, {unavailable});
          const disabled = page.locator('#modal-review-actions .return-unavailable button');
          assert.equal(await disabled.isDisabled(), true);
          assert.equal(await disabled.getAttribute('type'), 'button');
          assert.equal(await disabled.isVisible(), true);
          assert.equal(await page.locator('#modal-review-actions .return-form').count(), 0);
          assert.ok((await page.locator('#modal-review-actions').textContent()).includes(unavailable));
          assert.equal(await page.locator('#modal-review-actions .rejection-note').isVisible(), true);
          assert.equal(await page.locator('#modal-review-actions .accept-form, #modal-review-actions .reject-form').count(), 2);
          // Dispatch tests that even an artificial click on the disabled marker has no handler.
          await disabled.dispatchEvent('click');
          assert.equal((await data(page)).captured.length, 0);
          assert.deepEqual(await snapshot(page), before);
          await assertLayout(page, {featured: true});
        }
      });
      await run(`${prefix}: native Escape cleans media/actions, restores focus, filter counts and sort`, async () => {
        const before = await mount(page);
        const last = page.locator('#modal-review-actions .return-form button');
        await last.focus(); await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'modal-close-btn');
        await page.keyboard.press('Shift+Tab');
        assert.equal(await page.evaluate(() => document.activeElement.closest('form')?.className), 'return-form');
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.getElementById('preview-modal').open && document.getElementById('modal-content').children.length === 0 && document.getElementById('modal-review-actions').children.length === 0);
        assert.equal(await page.locator('#preview-modal').evaluate(node => node.open), false);
        assert.equal(await page.locator('#modal-content > *, #modal-review-actions > *').count(), 0);
        assert.equal(await page.evaluate(() => document.body.style.overflow), 'auto');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'synthetic-preview');
        assert.deepEqual(await snapshot(page), before);
        assert.equal(await page.locator('[data-count="submitted"]').textContent(), '2');
        assert.equal(await page.locator('[data-count="not-submitted"]').textContent(), '1');
        await page.locator('.group-filter-checkbox[value="باء"]').uncheck();
        assert.equal(await page.locator('[data-count="submitted"]').textContent(), '1');
        assert.equal(await page.locator('[data-count="not-submitted"]').textContent(), '0');
        assert.equal(await page.locator('tr[data-submission="13"]').isVisible(), false);
        assert.equal(await page.locator('[data-empty="not-submitted"]').isVisible(), true);
        await page.locator('.group-filter-none').click();
        assert.equal(await page.locator('[data-count="submitted"]').textContent(), '0');
        assert.equal(await page.locator('[data-empty="submitted"]').isVisible(), true);
        await page.locator('.group-filter-all').click();
        assert.equal(await page.locator('[data-count="submitted"]').textContent(), '2');
        const sort = page.locator('th.sortable'); await sort.click();
        assert.equal(await page.locator('.submissions-body tr').first().getAttribute('data-submission'), '13');
        assert.equal(await sort.getAttribute('aria-sort'), 'ascending');
        await sort.click();
        assert.equal(await page.locator('.submissions-body tr').first().getAttribute('data-submission'), '12');
        assert.equal(await sort.getAttribute('aria-sort'), 'descending');
        await page.locator('#synthetic-preview').click(); await page.locator('#modal-close-btn').click();
        assert.equal(await page.locator('#preview-modal').evaluate(node => node.open), false);
        assert.equal(await page.evaluate(() => document.activeElement.id), 'synthetic-preview');
      });
      await run(`${prefix}: literal student HTML plus image/PDF/audio/video/download and cleanup`, async () => {
        for (const type of ['image', 'pdf', 'audio', 'video', 'unknown']) {
          const url = `https://synthetic.invalid/blocked-media/file.${type}`;
          await mount(page, {mode: 'readonly', type, url});
          assert.equal(await page.locator('#modal-content pre').textContent(), literal);
          assert.equal(await page.locator('#modal-content pre > *').count(), 0);
          assert.ok((await page.locator('#preview-title').textContent()).includes('<script>اسم الطالب</script>'));
          assert.equal(await page.locator('#preview-title script').count(), 0);
          assert.equal(await page.evaluate(() => window.studentCodeRan), false);
          const download = page.locator('#modal-content a');
          assert.equal(await download.getAttribute('target'), '_blank');
          assert.equal(await download.getAttribute('rel'), 'noopener');
          assert.equal(await download.getAttribute('href'), url);
          if (type !== 'unknown') {
            const media = page.locator('#modal-content ' + ({image: 'img', pdf: 'iframe'}[type] || type));
            assert.equal(await media.getAttribute('src'), url);
            if (type === 'pdf') assert.match(await media.getAttribute('title'), /PDF/);
            if (type === 'audio' || type === 'video') assert.equal(await media.evaluate(node => node.controls), true);
          } else assert.match(await page.locator('#modal-content').textContent(), /لا يمكن معاينة/);
          await assertLayout(page);
          await page.keyboard.press('Escape');
          await page.waitForFunction(() => !document.getElementById('preview-modal').open && document.getElementById('modal-content').children.length === 0 && document.getElementById('modal-review-actions').children.length === 0);
          assert.equal(await page.locator('#modal-content > *, #modal-review-actions > *').count(), 0);
        }
      });
      await context.close();
    }
    assert.deepEqual(pageErrors, [], 'No runtime errors in actual application JavaScript');
    assert.equal(networkAttempts.filter(request => request.method !== 'GET').length, 0, 'No POST or mutation request was attempted, even on synthetic origins');
    if (failures.length) {
      console.error(`Task review Chromium UI: ${cases - failures.length}/${cases} cases passed; ${assertions} assertions executed; ${failures.length} failures. All ${blockedRequests} synthetic media GET attempts aborted; zero POST/database access.`);
      throw new Error(failures.map(failure => `${failure.name}: ${failure.message}`).join('\n'));
    }
    console.log(`Task review Chromium UI: ${cases} cases and ${assertions} assertions passed at 360/390/1280px; ${blockedRequests} synthetic media GET attempts aborted; zero POST, real HTTP navigation, DB access, screenshots or new dependencies.`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
