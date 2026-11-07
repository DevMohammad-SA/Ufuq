/* One set of roster fields for desktop and mobile; opt-in partial saves only. */
(function () {
  "use strict";
  var list = function (nodes) { return Array.prototype.slice.call(nodes); };

  function initializeRoster(root) {
    var form = root.querySelector("[data-roster-form]");
    if (!form || root.getAttribute("data-mobile-initialized") === "true") return;
    root.setAttribute("data-mobile-initialized", "true");
    var rows = list(form.querySelectorAll("tr[data-participant-id]"));
    var search = root.querySelector("#roster-search");
    var filter = root.querySelector("[data-preparation-filter]");
    var remaining = root.querySelector("[data-remaining-count]");
    var empty = root.querySelector("#roster-empty");
    var bar = form.querySelector("[data-roster-savebar]");
    var dirtyCount = root.querySelector("[data-dirty-count]");
    var status = root.querySelector("[data-roster-status]");
    var saveButton = form.querySelector('[type="submit"]');
    var saving = false;
    var allowNavigation = false;
    var states = rows.map(function (row) {
      var controls = list(row.querySelectorAll('input[type="checkbox"][data-col], [data-elite-trip-status]'));
      var note = row.querySelector('textarea[name^="note_"]');
      return {
        row: row, id: row.getAttribute("data-participant-id"), controls: controls,
        recorded: row.getAttribute("data-recorded") === "true", explicit: false, negative: false,
        baseline: snapshot(controls), note: note, noteBaseline: note ? note.value : ""
      };
    });

    function snapshot(controls) {
      return controls.map(function (control) {
        return control.type === "checkbox" ? control.checked : control.value;
      });
    }
    function same(first, second) {
      return first.length === second.length && first.every(function (value, index) { return value === second[index]; });
    }
    function attendanceDirty(state) {
      var select = state.controls.filter(function (control) { return control.hasAttribute("data-elite-trip-status"); })[0];
      // An empty elite placeholder is not one of the four attendance states.
      if (select && !select.value) return false;
      return state.negative || !same(snapshot(state.controls), state.baseline) || (!state.recorded && state.explicit);
    }
    function noteDirty(state) { return state.note && state.note.value !== state.noteBaseline; }
    function isPrepared(state) { return state.recorded || state.explicit; }
    function hasDirty() { return states.some(function (state) { return attendanceDirty(state) || noteDirty(state); }); }
    function noteBusy() { return Number(root.getAttribute("data-note-saving") || 0) > 0; }
    function message(text, failed) {
      if (!status) return;
      status.textContent = text;
      status.setAttribute("data-error", String(!!failed));
      measureSpace();
    }
    function refresh() {
      var unprepared = 0;
      var dirty = 0;
      var visible = 0;
      var query = search ? search.value.trim().toLocaleLowerCase("ar") : "";
      states.forEach(function (state) {
        var prepared = isPrepared(state);
        var changed = attendanceDirty(state) || noteDirty(state);
        if (!prepared) unprepared += 1;
        if (changed) dirty += 1;
        state.row.classList.toggle("roster-row--dirty", !!changed);
        state.row.setAttribute("data-prepared", String(prepared));
        var badge = state.row.querySelector("[data-row-state]");
        if (badge) badge.textContent = attendanceDirty(state) ? "تحضير غير محفوظ" : (state.recorded ? "محفوظ" : (prepared ? "محدّد" : "لم يُحدّد"));
        var matches = (state.row.getAttribute("data-search") || "").indexOf(query) !== -1;
        state.row.hidden = !(matches && (!filter || filter.value !== "unprepared" || !prepared));
        if (!state.row.hidden) visible += 1;
      });
      if (remaining) remaining.textContent = "المتبقون دون تحديد: " + unprepared;
      if (dirtyCount) dirtyCount.textContent = "طلاب بتعديلات غير محفوظة: " + dirty;
      if (empty) empty.hidden = visible !== 0;
      if (saveButton) saveButton.disabled = saving || noteBusy() || dirty === 0;
      measureSpace();
    }

    states.forEach(function (state) {
      state.controls.forEach(function (control) {
        control.addEventListener("change", function () {
          if (control.hasAttribute("data-elite-trip-status")) {
            state.explicit = !!control.value;
            state.negative = false;
          } else {
            if (state.controls.some(function (box) { return box.checked; })) {
              state.explicit = true;
              state.negative = false;
            }
          }
          message("");
          refresh();
        });
      });
      var negativeButton = state.row.querySelector("[data-explicit-negative]");
      if (negativeButton) negativeButton.addEventListener("click", function () {
        if (saving) return;
        state.controls.forEach(function (control) {
          if (control.type === "checkbox") control.checked = false;
          else control.value = "absent";
        });
        state.controls.forEach(function (control) { control.dispatchEvent(new Event("change", { bubbles: true })); });
        state.explicit = true;
        state.negative = true;
        message("");
        refresh();
      });
      if (state.note) state.note.addEventListener("input", function () { message(""); refresh(); });
    });
    if (search) search.addEventListener("input", refresh);
    if (filter) filter.addEventListener("change", refresh);

    // A note-only request acknowledges the captured string, never a newer edit or attendance.
    root.addEventListener("roster:note-saved", function (event) {
      var detail = event.detail || {};
      states.forEach(function (state) {
        if (state.note && state.note === detail.field && typeof detail.value === "string") state.noteBaseline = detail.value;
      });
      refresh();
    });
    root.addEventListener("roster:note-request", refresh);

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (saving || noteBusy() || !hasDirty()) return;
      if (!form.reportValidity()) return;
      // Capture before disabling inputs: disabled fields are omitted by FormData.
      var data = new FormData(form);
      var sentAttendance = [];
      var sentNotes = [];
      data.set("partial_roster", "1");
      states.forEach(function (state) {
        data.delete("prepared_" + state.id);
        if (attendanceDirty(state)) {
          data.set("prepared_" + state.id, "1");
          sentAttendance.push({ state: state, value: snapshot(state.controls) });
        } else {
          state.controls.forEach(function (control) { data.delete(control.name); });
        }
        if (state.note) {
          if (noteDirty(state)) {
            data.set(state.note.name, state.note.value);
            sentNotes.push({ state: state, value: state.note.value });
          } else data.delete(state.note.name);
        }
      });
      var sentDate = data.get("date");
      var sentGroup = data.get("group");
      var disabled = list(root.querySelectorAll("input, select, textarea, button")).map(function (control) {
        var previous = control.disabled;
        control.disabled = true;
        return { control: control, previous: previous };
      });
      saving = true;
      form.setAttribute("aria-busy", "true");
      message("جارٍ حفظ التعديلات…");
      if (saveButton) saveButton.textContent = "جارٍ الحفظ…";

      function acknowledged(ids, sent) {
        return Array.isArray(ids) && sent.every(function (item) {
          return ids.some(function (id) { return String(id) === item.state.id; });
        });
      }
      fetch(form.action, {
        method: "POST", body: data, headers: { Accept: "application/json" }, credentials: "same-origin"
      }).then(function (response) {
        if (!response.ok || response.redirected || !/application\/json/i.test(response.headers.get("Content-Type") || "")) throw new Error("invalid_response");
        return response.json();
      }).then(function (result) {
        if (!result || result.success !== true || result.date !== sentDate || String(result.group_id) !== String(sentGroup) ||
            !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(result.saved_at || "") ||
            !acknowledged(result.saved_ids, sentAttendance) || !acknowledged(result.saved_note_ids, sentNotes)) throw new Error("unconfirmed_save");
        sentAttendance.forEach(function (item) {
          item.state.baseline = item.value;
          item.state.recorded = true;
          item.state.explicit = false;
          item.state.negative = false;
          item.state.row.setAttribute("data-recorded", "true");
        });
        sentNotes.forEach(function (item) { item.state.noteBaseline = item.value; });
        message((sentAttendance.length ? "تم حفظ التحضير" : "تم حفظ الملاحظات دون تغيير التحضير") + " — الساعة " + result.saved_at);
      }).catch(function () {
        message("تعذر تأكيد الحفظ. لم نفقد اختياراتك؛ تحقق من الاتصال والجلسة ثم حاول مجددًا.", true);
      }).finally(function () {
        saving = false;
        form.setAttribute("aria-busy", "false");
        disabled.forEach(function (item) { item.control.disabled = item.previous; });
        if (saveButton) saveButton.textContent = saveLabel;
        refresh();
      });
    });
    var saveLabel = saveButton ? saveButton.textContent : "حفظ التحضير";

    window.addEventListener("beforeunload", function (event) {
      if (!allowNavigation && (hasDirty() || saving || noteBusy())) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
    list(root.querySelectorAll('form[method="get"]')).forEach(function (dateForm) {
      dateForm.addEventListener("submit", function (event) {
        if (saving || noteBusy()) {
          event.preventDefault();
          message("انتظر اكتمال طلب الحفظ قبل تغيير التاريخ أو البيئة.", true);
        } else if (hasDirty()) {
          if (!window.confirm("لديك تعديلات غير محفوظة. هل تريد مغادرة الكشف وتغيير التاريخ أو البيئة دون حفظها؟")) event.preventDefault();
          else allowNavigation = true;
        }
      });
    });

    var nav = document.querySelector(".app-nav--bottom");
    function measureSpace() {
      if (!bar) return;
      var mobile = window.matchMedia("(max-width: 767px)").matches;
      var navHeight = mobile && nav ? nav.getBoundingClientRect().height : 0;
      root.style.setProperty("--roster-nav-height", navHeight + "px");
      root.style.setProperty("--roster-save-height", mobile ? bar.getBoundingClientRect().height + "px" : "0px");
    }
    if (window.ResizeObserver) {
      var observer = new window.ResizeObserver(measureSpace);
      if (nav) observer.observe(nav);
      if (bar) observer.observe(bar);
    }
    window.addEventListener("resize", measureSpace);
    window.addEventListener("orientationchange", measureSpace);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", measureSpace);
    // ResizeObserver-free browsers still measure at load, resize and every state/message change.
    window.addEventListener("load", measureSpace);
    refresh();
  }
  function initialize() { list(document.querySelectorAll("[data-mobile-attendance]")).forEach(initializeRoster); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
