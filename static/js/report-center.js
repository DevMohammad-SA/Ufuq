/* Progressive enhancement only: the server owns validation, results and GET exports. */
(function () {
  'use strict';

  var form = document.getElementById('report-center-form');
  if (!form) return;

  var period = form.elements.namedItem('period');
  var dates = ['start_date', 'end_date'].map(function (name) {
    return form.elements.namedItem(name);
  }).filter(Boolean);
  var dateRange = document.getElementById('rc-date-range');
  var pending = document.getElementById('rc-pending');
  var status = document.getElementById('rc-submit-status');
  var exportLabels = form.querySelectorAll('[data-export-label]');
  var buttons = form.querySelectorAll('button[type="submit"]');
  var filtersValid = form.dataset.filtersValid === 'true';
  var busy = false;
  var dirty = false;
  var lastSubmitter = null;
  var releaseTimer = null;

  function syncPeriod() {
    if (!period) return;
    var custom = period.value === 'custom';
    dates.forEach(function (field) {
      field.disabled = !custom;
      field.required = custom;
    });
    // Keep server-side date errors visible, even for a rejected non-custom query.
    var dateErrors = dateRange && dateRange.querySelector('.rc-field-error');
    if (dateRange) dateRange.hidden = !custom && !dateErrors;
  }

  function settingsSnapshot() {
    // Snapshot successful settings, not submit buttons or transient UI state.
    // Repeated "reports" controls stay repeated; their selections are never rewritten.
    return JSON.stringify(Array.from(form.elements).filter(function (field) {
      return field.name && !field.disabled &&
        !['submit', 'button', 'reset'].includes(field.type) &&
        (!['checkbox', 'radio'].includes(field.type) || field.checked);
    }).map(function (field) {
      return [field.name, field.value];
    }));
  }

  function syncPending() {
    dirty = settingsSnapshot() !== initialSettings;
    if (pending) pending.hidden = !dirty;
    exportLabels.forEach(function (label) {
      var format = label.getAttribute('data-export-label');
      label.textContent = format + (dirty ? ' بالإعدادات الحالية' : '');
    });
  }

  function setBusy(value) {
    busy = value;
    form.setAttribute('aria-busy', String(value));
    buttons.forEach(function (button) {
      // Never disable a successful submitter: "format" and formaction must survive.
      button.setAttribute('aria-disabled', String(value || button.disabled));
    });
  }

  function restoreAfterExport() {
    setBusy(false);
    if (status) status.textContent = 'أُرسل طلب التصدير. تحقق من تنزيل الملف أو رسالة المتصفح؛ لم تتغير النتائج المعروضة.';
  }

  function handleSubmit(event) {
    if (event.defaultPrevented) return;
    if (busy) {
      event.preventDefault();
      return;
    }
    var submitter = 'submitter' in event ? event.submitter : lastSubmitter;
    lastSubmitter = null;
    var isExport = submitter && submitter.name === 'format';
    if (isExport && !filtersValid) {
      event.preventDefault();
      if (status) status.textContent = 'صحح الأخطاء ثم اضغط «عرض التقرير» للتحقق قبل التصدير.';
      return;
    }
    syncPending();
    if (isExport && dirty && !window.confirm(
      'سيُصدَّر الملف بالإعدادات الحالية والتقارير المختارة في النموذج، وليس بفلاتر آخر نتائج معروضة. متابعة التصدير؟'
    )) {
      event.preventDefault();
      return;
    }
    setBusy(true);
    if (status) status.textContent = isExport ?
      'جارٍ طلب التصدير بالإعدادات الحالية. يتحقق الخادم منها قبل إصدار الملف.' :
      'جارٍ التحقق من الإعدادات وعرض التقارير…';
    // A native download may leave this document open; do not lock its controls forever.
    if (isExport) releaseTimer = window.setTimeout(restoreAfterExport, 8000);
  }

  syncPeriod();
  var initialSettings = settingsSnapshot();
  syncPending();
  form.addEventListener('input', syncPending);
  form.addEventListener('change', function () {
    syncPeriod();
    syncPending();
  });
  form.addEventListener('invalid', function (event) {
    lastSubmitter = null;
    var details = event.target.closest('details');
    if (details) details.open = true;
  }, true);
  buttons.forEach(function (button) {
    button.addEventListener('click', function () { lastSubmitter = button; });
  });
  form.addEventListener('submit', handleSubmit);
  window.addEventListener('pageshow', function () {
    if (releaseTimer) window.clearTimeout(releaseTimer);
    lastSubmitter = null;
    setBusy(false);
    if (status) status.textContent = '';
    syncPeriod();
    syncPending();
  });
})();
