/* Same-origin native POST forms: no alternate grading endpoint or AJAX success guess. */
(function () {
  'use strict';
  var modal = document.getElementById('preview-modal');
  var content = document.getElementById('modal-content');
  var actions = document.getElementById('modal-review-actions');
  var closeButton = document.getElementById('modal-close-btn');
  var title = document.getElementById('preview-title');
  var status = document.getElementById('preview-status');
  var opener = null;
  var previousOverflow = '';

  function confirmGradeChange(form) {
    // A simple featured adjustment is already an absolute yes/no command;
    // only the full, reasoned revision needs an extra confirmation step.
    if (!form.classList.contains('grade-revision-form')) return true;
    var award = form.getAttribute('data-award');
    var historical = form.querySelector('[name="previous_award"]');
    var grade = form.querySelector('[name="grade"]');
    if (!award && historical) award = historical.value;
    var newAward = grade && {featured: 12, accepted: 10, rejected: 0}[grade.value];
    var note = 'يُطبّق فرق المنحة فقط على الأرصدة الحالية وفق قواعدها.';
    if (['0', '10', '12'].indexOf(award) !== -1 && typeof newAward === 'number') {
      var delta = newAward - Number(award);
      note = 'المنحة: ' + award + ' ← ' + newAward + ' نقطة؛ الفرق: ' +
        (delta > 0 ? '+' : '') + delta + ' نقطة على الأرصدة الحالية وفق قواعدها.';
    }
    return window.confirm('هل تؤكد تعديل التقييم مباشرة؟ ' + note + ' يبقى عمل الطالب محفوظًا ويُسجّل سبب التعديل.');
  }

  function bindReviewForm(form, inPreview) {
    var inFlight = false;
    form.addEventListener('submit', function (event) {
      if (event.defaultPrevented) return;
      if (inFlight) {
        event.preventDefault();
        return;
      }
      // Native clicks/requestSubmit validate first; also guard a synthetic
      // submit event before showing confirmation, locking or sending status.
      if (!form.checkValidity()) {
        event.preventDefault();
        form.reportValidity();
        return;
      }
      if (!confirmGradeChange(form)) {
        event.preventDefault();
        return;
      }
      inFlight = true;
      form.setAttribute('aria-busy', 'true');
      // Do not disable successful controls or the submitter: native POST must
      // retain CSRF, version, action, chosen fields and any submitter payload.
      form.querySelectorAll('button[type="submit"]').forEach(function (button) {
        button.setAttribute('aria-disabled', 'true');
      });
      var sending = 'جارٍ إرسال القرار للتحقق والحفظ؛ ستظهر النتيجة بعد تحميل الصفحة.';
      if (inPreview && status) {
        status.textContent = sending;
      } else {
        var progress = form.querySelector('.review-submit-status');
        if (!progress) {
          progress = document.createElement('p');
          progress.className = 'review-submit-status';
          progress.setAttribute('role', 'status');
          progress.setAttribute('aria-live', 'polite');
          form.appendChild(progress);
        }
        progress.textContent = sending;
      }
      // No fetch, optimistic state change or success message. Native navigation
      // exposes the server's saved result, validation error or revoked access.
    });
  }

  function bindFeaturedCheckboxes(root) {
    root.querySelectorAll('.featured-checkbox-label input[type="checkbox"]').forEach(function (checkbox) {
      checkbox.addEventListener('keydown', function (event) {
        // Space toggles. Enter never implicitly accepts in either location.
        if (event.key === 'Enter') event.preventDefault();
      });
    });
  }

  function copyFormValues(original, copy) {
    var originals = original.querySelectorAll('input, select, textarea');
    copy.querySelectorAll('input, select, textarea').forEach(function (field, index) {
      var source = originals[index];
      if (field.tagName === 'SELECT') {
        field.selectedIndex = source.selectedIndex;
        Array.prototype.forEach.call(field.options, function (option, optionIndex) {
          option.selected = source.options[optionIndex].selected;
        });
      } else if (field.type !== 'file') {
        field.value = source.value;
      }
      if (field.type === 'checkbox' || field.type === 'radio') {
        field.checked = source.checked;
        field.indeterminate = source.indeterminate;
      }
    });
  }

  function remapPreviewIds(copy) {
    var ids = Object.create(null);
    var reserved = Object.create(null);
    copy.querySelectorAll('[id]').forEach(function (field) {
      var oldId = field.id;
      var prefix = 'preview-' + oldId;
      var newId = prefix;
      var suffix = 1;
      while (document.getElementById(newId) || reserved[newId]) {
        newId = prefix + '-' + suffix++;
      }
      ids[oldId] = newId;
      reserved[newId] = true;
      field.id = newId;
    });
    // Preserve native details state and aria-expanded if present, and remap
    // all local ID references (including summary aria-controls and hints).
    copy.querySelectorAll('[for], [aria-controls], [aria-labelledby], [aria-describedby], [aria-owns]').forEach(function (element) {
      ['for', 'aria-controls', 'aria-labelledby', 'aria-describedby', 'aria-owns'].forEach(function (attribute) {
        var value = element.getAttribute(attribute);
        if (!value) return;
        element.setAttribute(attribute, value.split(/\s+/).map(function (id) {
          return ids[id] || id;
        }).join(' '));
      });
    });
  }

  document.querySelectorAll('.review-actions form').forEach(function (form) {
    bindReviewForm(form, false);
  });
  bindFeaturedCheckboxes(document);

  function clearPreview() {
    // Removing media also stops audio/video playback, without parsing student HTML.
    content.replaceChildren();
    actions.replaceChildren();
    document.body.style.overflow = previousOverflow;
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  }

  function closePreview() {
    modal.close();
  }

  function addFile(url, type) {
    if (!url) return;
    var element;
    if (type === 'image') {
      element = document.createElement('img');
      element.alt = 'معاينة ملف التسليم';
    } else if (type === 'pdf') {
      element = document.createElement('iframe');
      element.title = 'معاينة ملف PDF للتسليم';
    } else if (type === 'video' || type === 'audio') {
      element = document.createElement(type);
      element.controls = true;
      element.preload = 'metadata';
    } else {
      element = document.createElement('p');
      element.textContent = 'لا يمكن معاينة هذا النوع من الملفات؛ استخدم رابط التحميل.';
    }
    if (['image', 'pdf', 'video', 'audio'].indexOf(type) !== -1) element.src = url;
    content.appendChild(element);
    var download = document.createElement('a');
    download.href = url;
    download.target = '_blank';
    download.rel = 'noopener';
    download.className = 'file-link';
    download.textContent = 'فتح الملف أو تحميله';
    content.appendChild(download);
  }

  function addReviewForms(button) {
    var row = button.closest('tr');
    var original = row && row.querySelector('.review-actions');
    if (!original) {
      var note = document.createElement('p');
      note.textContent = 'المعاينة فقط: القرار غير متاح لصلاحيتك أو لحالة هذا التسليم.';
      actions.appendChild(note);
      return;
    }
    // Clone server-rendered, already-authorized forms, including CSRF, legacy
    // choice, submission id and native POST target. Preserve list forms intact.
    var copy = original.cloneNode(true);
    // cloneNode does not reliably copy a runtime-selected option. Explicitly
    // preserve all current choices, reasons and checked states before re-ID.
    copyFormValues(original, copy);
    remapPreviewIds(copy);
    bindFeaturedCheckboxes(copy);
    copy.querySelectorAll('form').forEach(function (form) {
      bindReviewForm(form, true);
    });
    actions.appendChild(copy);
  }

  if (modal && content && actions && closeButton && title && status) {
    document.querySelectorAll('.preview-btn').forEach(function (button) {
      button.addEventListener('click', function () {
        content.replaceChildren();
        actions.replaceChildren();
        opener = button;
        title.textContent = 'معاينة: ' + (button.getAttribute('data-name') || '') + ' — ' + (button.getAttribute('data-title') || '');
        status.textContent = button.getAttribute('data-status') || '';
        addFile(button.getAttribute('data-url'), button.getAttribute('data-type'));
        var text = button.getAttribute('data-content');
        if (text) {
          var heading = document.createElement('h3');
          heading.textContent = 'نص التسليم';
          var pre = document.createElement('pre');
          pre.textContent = text;
          content.appendChild(heading);
          content.appendChild(pre);
        }
        if (!content.children.length) {
          var empty = document.createElement('p');
          empty.textContent = 'لا يوجد ملف أو نص في هذا التسليم.';
          content.appendChild(empty);
        }
        addReviewForms(button);
        previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        modal.showModal();
        closeButton.focus();
      });
    });
    closeButton.addEventListener('click', closePreview);
    modal.addEventListener('close', clearPreview);
    modal.addEventListener('cancel', function (event) {
      // Escape follows the same cleanup/focus-restoration path as close.
      event.preventDefault();
      closePreview();
    });
    modal.addEventListener('click', function (event) {
      if (event.target === modal) closePreview();
    });
    modal.addEventListener('keydown', function (event) {
      if (event.key !== 'Tab') return;
      var focusable = Array.prototype.filter.call(
        modal.querySelectorAll('button, summary, a[href], input, select, textarea, iframe, audio[controls], video[controls], [tabindex]'),
        function (element) {
          // Closed details content may still report layout rectangles in
          // Chromium, but only its summary belongs in the keyboard order.
          var closedDetails = element.closest('details:not([open])');
          if (closedDetails && element !== closedDetails.querySelector('summary')) return false;
          return !element.disabled && element.tabIndex >= 0 && element.getClientRects().length > 0;
        }
      );
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    });
  }

  // A single environment filter per task controls both lists and their counts.
  document.querySelectorAll('.task-review-card').forEach(function (card) {
    var checkboxes = card.querySelectorAll('.group-filter-checkbox');
    var submittedRows = card.querySelectorAll('.submissions-body tr');
    var missingRows = card.querySelectorAll('.not-submitted-list li[data-participant]');
    function filterRows(rows, selected, key) {
      var visible = 0;
      rows.forEach(function (row) {
        row.hidden = checkboxes.length > 0 && selected.indexOf(row.getAttribute('data-group') || '') === -1;
        if (!row.hidden) visible += 1;
      });
      var count = card.querySelector('[data-count="' + key + '"]');
      var empty = card.querySelector('[data-empty="' + key + '"]');
      if (count) count.textContent = String(visible);
      if (empty) empty.hidden = visible > 0 || rows.length === 0;
    }
    function applyFilters() {
      var selected = [];
      checkboxes.forEach(function (checkbox) { if (checkbox.checked) selected.push(checkbox.value); });
      filterRows(submittedRows, selected, 'submitted');
      filterRows(missingRows, selected, 'not-submitted');
    }
    checkboxes.forEach(function (checkbox) { checkbox.addEventListener('change', applyFilters); });
    [['.group-filter-all', true], ['.group-filter-none', false]].forEach(function (setting) {
      var button = card.querySelector(setting[0]);
      if (button) button.addEventListener('click', function () {
        checkboxes.forEach(function (checkbox) { checkbox.checked = setting[1]; });
        applyFilters();
      });
    });
    applyFilters();

    var table = card.querySelector('.submissions-table');
    var body = card.querySelector('.submissions-body');
    if (!table || !body) return;
    var currentSort = {col: null, dir: null};
    table.querySelectorAll('th.sortable').forEach(function (header) {
      header.addEventListener('click', function () {
        var col = parseInt(header.getAttribute('data-col'), 10);
        var dir = currentSort.col === col && currentSort.dir === 'asc' ? 'desc' : 'asc';
        currentSort = {col: col, dir: dir};
        table.querySelectorAll('th.sortable').forEach(function (h) {
          h.classList.remove('sort-asc', 'sort-desc');
          h.removeAttribute('aria-sort');
        });
        header.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
        header.setAttribute('aria-sort', dir === 'asc' ? 'ascending' : 'descending');
        Array.prototype.slice.call(body.querySelectorAll('tr')).sort(function (a, b) {
          var av = a.children[col].textContent.trim().toLocaleLowerCase('ar');
          var bv = b.children[col].textContent.trim().toLocaleLowerCase('ar');
          return av.localeCompare(bv, 'ar') * (dir === 'asc' ? 1 : -1);
        }).forEach(function (row) { body.appendChild(row); });
      });
    });
  });
})();
