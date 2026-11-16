/* Private elite-trip forms use ordinary same-origin HTML POST + Django CSRF. */
(function () {
  'use strict';
  function all(selector, root) { return Array.from((root || document).querySelectorAll(selector)); }
  function text(element, value) { if (element) element.textContent = value; }
  function setupRoster(root) {
    var search = root.querySelector('[data-roster-search]');
    var filter = root.querySelector('[data-roster-status]');
    var scope = root.querySelector('[name=scope_group]');
    var rows = all('[data-roster-row]', root);
    function update() {
      var query = search ? search.value.trim().toLocaleLowerCase('ar') : '';
      var status = filter ? filter.value : '';
      var scopeId = scope ? scope.value : '';
      var selected = 0, scoped = 0, scopedSelected = 0, shown = 0;
      rows.forEach(function (row) {
        var checkbox = row.querySelector('[name=selected_ids]');
        var checked = !!(checkbox && checkbox.checked);
        var eligible = row.getAttribute('data-eligible') === 'true';
        var inScope = eligible && (!scopeId || row.getAttribute('data-group-id') === scopeId);
        var matches = (!query || row.getAttribute('data-search').toLocaleLowerCase('ar').includes(query)) &&
          (!status || (status === 'selected' ? checked : !checked));
        // Filtering never disables/removes any form control, including hidden choices.
        row.hidden = !matches;
        row.classList.toggle('is-selected', checked);
        text(row.querySelector('[data-selection-label]'), checked ? 'مرشح' : 'غير مرشح');
        text(row.querySelector('[data-row-scope]'), inScope ? 'ضمن نطاق الرحلة' : 'خارج النطاق الحالي');
        row.setAttribute('data-in-scope', String(inScope));
        if (checked) selected++;
        if (inScope) { scoped++; if (checked) scopedSelected++; }
        if (matches) shown++;
      });
      all('[data-roster-group]', root).forEach(function (group) {
        var groupRows = rows.filter(function (row) { return group.contains(row); });
        var count = groupRows.filter(function (row) { return row.querySelector('[name=selected_ids]').checked; }).length;
        text(group.querySelector('[data-group-selected]'), count);
        group.hidden = !groupRows.some(function (row) { return !row.hidden; });
      });
      text(root.querySelector('[data-total-selected]'), selected);
      text(root.querySelector('[data-scoped-selected]'), scopedSelected);
      text(root.querySelector('[data-scoped-total]'), scoped);
      text(root.querySelector('[data-visible-count]'), shown);
      var noResults = root.querySelector('[data-no-results]');
      if (noResults) noResults.hidden = shown !== 0;
      var label = scope && scope.options[scope.selectedIndex];
      text(root.querySelector('[data-scope-label]'), label ? label.textContent : 'جميع البيئات');
    }
    if (search) search.addEventListener('input', update);
    if (filter) filter.addEventListener('change', update);
    if (scope) scope.addEventListener('change', update);
    root.addEventListener('change', function (event) { if (event.target.name === 'selected_ids') update(); });
    var draft = root.querySelector('[data-draft-form]');
    var review = root.querySelector('[data-saved-review-link]');
    if (draft && review) {
      function dirty(event) {
        // Search filters do not invalidate the saved version.
        if (!event.target.name || event.target === search || event.target === filter) return;
        review.hidden = true;
        var notice = root.querySelector('[data-unsaved-review]');
        if (notice) notice.hidden = false;
      }
      draft.addEventListener('input', dirty);
      draft.addEventListener('change', dirty);
    }
    update();
  }
  function setupSignature(form) {
    var canvas = form.querySelector('[data-signature-canvas]');
    var hidden = form.querySelector('[name=signature]');
    var clear = form.querySelector('[data-signature-clear]');
    var status = form.querySelector('[data-signature-status]');
    var hint = form.querySelector('[data-signature-hint]');
    var restore = form.querySelector('[data-signature-restore]');
    var cursor = form.querySelector('[data-signature-cursor]');
    var ctx;
    try { ctx = canvas && canvas.getContext('2d'); } catch (error) { ctx = null; }
    if (!canvas || !hidden || !ctx) {
      text(status, 'تعذر تشغيل مساحة التوقيع. فعّل JavaScript واستخدم متصفحًا يدعم الرسم لإرسال الاستمارة.');
      all('button[type=submit]', form).forEach(function (button) { button.disabled = true; });
      form.addEventListener('submit', function (event) { event.preventDefault(); });
      return;
    }
    var reviewButton = form.querySelector('[data-signature-review-button]');
    if (reviewButton) reviewButton.disabled = false;
    // Intrinsic 1000 x 360 is set in HTML exactly once; never reset on resize/DPR change.
    var WIDTH = canvas.width, HEIGHT = canvas.height;
    var strokes = [], active = null, pen = false, base = null, restoring = false;
    var position = { x: WIDTH / 2, y: HEIGHT / 2 };
    function clampPoint(point) {
      return { x: Math.max(0, Math.min(WIDTH, point.x)), y: Math.max(0, Math.min(HEIGHT, point.y)) };
    }
    function eventPoint(event) {
      var rect = canvas.getBoundingClientRect();
      return clampPoint({ x: (event.clientX - rect.left) * WIDTH / rect.width,
        y: (event.clientY - rect.top) * HEIGHT / rect.height });
    }
    function meaningful() {
      if (base) return true; // Only a server-validated normalized PNG may restore the base.
      var length = 0;
      strokes.forEach(function (stroke) {
        for (var i = 1; i < stroke.length; i++) length += Math.hypot(stroke[i].x - stroke[i - 1].x, stroke[i].y - stroke[i - 1].y);
      });
      return length >= 25;
    }
    function updateCursor() {
      if (!cursor) return;
      cursor.style.left = (100 * position.x / WIDTH) + '%';
      cursor.style.top = (100 * position.y / HEIGHT) + '%';
      cursor.classList.toggle('is-drawing', pen);
    }
    function render() {
      ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, WIDTH, HEIGHT);
      if (base) ctx.drawImage(base, 0, 0, WIDTH, HEIGHT);
      ctx.strokeStyle = '#26312b'; ctx.lineWidth = 4; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      strokes.forEach(function (stroke) {
        if (stroke.length < 2) return;
        ctx.beginPath(); ctx.moveTo(stroke[0].x, stroke[0].y);
        for (var i = 1; i < stroke.length; i++) ctx.lineTo(stroke[i].x, stroke[i].y);
        ctx.stroke();
      });
      var valid = meaningful();
      if (hint) hint.hidden = valid || strokes.some(function (stroke) { return stroke.length > 1; });
      if (clear) clear.disabled = !base && !strokes.length;
      text(status, restoring ? 'جارٍ استعادة التوقيع للمراجعة…' : valid ? 'التوقيع مرسوم. راجعه أو امسحه قبل الانتقال؛ التحقق النهائي لدى الخادم.' : 'ارسم توقيعك بوضوح. المساحة الفارغة أو النقرة وحدها غير مقبولة.');
      updateCursor();
    }
    function sync() {
      // Cursor is a separate DOM overlay and can never be embedded in the PNG.
      render();
      hidden.value = meaningful() ? canvas.toDataURL('image/png') : '';
    }
    function start(point) { position = point; strokes.push([point]); render(); }
    function move(point) {
      position = point;
      if (strokes.length) strokes[strokes.length - 1].push(point);
      render();
    }
    function finish(event) {
      if (active === null || (event && event.pointerId !== active)) return;
      var released = active; active = null;
      if (canvas.hasPointerCapture && canvas.hasPointerCapture(released)) canvas.releasePointerCapture(released);
      sync();
    }
    canvas.addEventListener('pointerdown', function (event) {
      if (active !== null || event.button > 0 || restoring || form.getAttribute('data-waiting') === 'true') return;
      event.preventDefault(); pen = false; active = event.pointerId;
      canvas.focus({ preventScroll: true });
      if (canvas.setPointerCapture) canvas.setPointerCapture(active);
      start(eventPoint(event));
    });
    canvas.addEventListener('pointermove', function (event) {
      if (event.pointerId !== active) return;
      event.preventDefault(); move(eventPoint(event));
    });
    canvas.addEventListener('pointerup', finish);
    canvas.addEventListener('pointercancel', finish);
    canvas.addEventListener('lostpointercapture', finish);
    canvas.addEventListener('focus', function () { if (cursor) cursor.hidden = false; updateCursor(); });
    canvas.addEventListener('blur', function () { pen = false; if (cursor) cursor.hidden = true; sync(); });
    canvas.addEventListener('keydown', function (event) {
      if (restoring || active !== null || form.getAttribute('data-waiting') === 'true') return;
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault();
        if (event.repeat) return;
        pen = !pen;
        if (pen) start({ x: position.x, y: position.y }); else sync();
        text(status, pen ? 'القلم مفعّل: استخدم الأسهم لرسم التوقيع، والمسافة لإيقاف القلم.' : 'توقف الرسم. اضغط المسافة لبدء خط جديد، أو انتقل إلى المراجعة.');
        updateCursor(); return;
      }
      if (event.key === 'Escape') { pen = false; sync(); return; }
      var directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      if (!directions[event.key]) return;
      event.preventDefault();
      var delta = directions[event.key], step = event.shiftKey ? 16 : 5;
      var next = clampPoint({ x: position.x + delta[0] * step, y: position.y + delta[1] * step });
      if (pen) move(next); else { position = next; updateCursor(); }
    });
    if (clear) clear.addEventListener('click', function () {
      active = null; pen = false; strokes = []; base = null; hidden.value = '';
      if (restore) { restore.removeAttribute('src'); restore.hidden = true; }
      render(); canvas.focus({ preventScroll: true });
      text(status, 'مُسح التوقيع. ارسم توقيعًا جديدًا قبل المراجعة.');
    });
    var initial = restore && restore.getAttribute('src');
    if (initial && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(initial) && initial.length <= 256000) {
      restoring = true;
      var image = new Image();
      image.onload = function () { base = image; restoring = false; sync(); };
      image.onerror = function () { restoring = false; base = null; sync(); text(status, 'تعذر استعادة التوقيع. يرجى رسمه مجددًا.'); };
      image.src = initial;
    } else hidden.value = '';
    render();
    form.addEventListener('submit', function (event) {
      if (restoring) { event.preventDefault(); text(status, 'انتظر اكتمال استعادة التوقيع ثم راجعه.'); return; }
      active = null; pen = false;
      sync();
      if (!meaningful()) {
        event.preventDefault();
        text(status, 'لم تُرسل الاستمارة. ارسم توقيعًا واضحًا أولًا، ثم اضغط «مراجعة القرار».');
        canvas.focus({ preventScroll: true });
      }
    });
  }
  function setupPost(form) {
    var finalCheckbox = form.querySelector('[data-final-confirm]');
    var extraConfirm = form.querySelector('[data-extra-confirm]');
    var button = form.querySelector('[data-final-button]');
    var posting = false;
    var originals = new Map();
    var timeout = null;
    function updateFinal() { if (button) button.disabled = posting || !(finalCheckbox && finalCheckbox.checked) || !!(extraConfirm && !extraConfirm.checked); }
    if (finalCheckbox) finalCheckbox.addEventListener('change', updateFinal);
    if (extraConfirm) extraConfirm.addEventListener('change', updateFinal);
    updateFinal();
    function reset() {
      posting = false; form.removeAttribute('data-waiting'); form.removeAttribute('aria-busy');
      clearTimeout(timeout);
      originals.forEach(function (disabled, element) { element.disabled = disabled; });
      originals.clear(); updateFinal(); text(form.querySelector('[data-submit-status]'), '');
    }
    window.addEventListener('pageshow', reset);
    form.addEventListener('submit', function (event) {
      // Signature validation listeners run first and may prevent the native POST.
      if (event.defaultPrevented) return;
      if (posting) { event.preventDefault(); return; }
      if (finalCheckbox && !finalCheckbox.checked) {
        event.preventDefault(); finalCheckbox.focus(); return;
      }
      if (extraConfirm && !extraConfirm.checked) {
        event.preventDefault(); extraConfirm.focus(); return;
      }
      posting = true; form.setAttribute('data-waiting', 'true'); form.setAttribute('aria-busy', 'true');
      text(form.querySelector('[data-submit-status]'), 'جارٍ إرسال الطلب إلى الخادم. لا توجد نتيجة مؤكدة حتى تظهر استجابة الخادم.');
      // Disable buttons after the browser has collected successful form controls.
      setTimeout(function () {
        if (!posting) return;
        all('button', form).forEach(function (element) { originals.set(element, element.disabled); element.disabled = true; });
      }, 0);
      timeout = setTimeout(function () {
        if (!posting) return;
        text(form.querySelector('[data-submit-status]'), 'لم تصل نتيجة مؤكدة بعد. لا تعِد الإرسال أثناء الانتظار؛ عند العودة افحص السجل المحفوظ أولًا.');
      }, 20000);
    });
  }
  function init() {
    all('[data-elite-roster]').forEach(setupRoster);
    all('form[data-signature-form]').forEach(setupSignature);
    all('form[data-elite-post]').forEach(setupPost);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
