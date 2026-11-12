/* One set of fields for desktop/mobile. Individual autosave is explicitly opt-in. */
(function () {
  "use strict";
  var list = function (nodes) { return Array.prototype.slice.call(nodes); };
  var requests = globalThis.RahhalRosterRequests;
  if (!requests) {
    var tail = Promise.resolve();
    requests = globalThis.RahhalRosterRequests = {
      enqueue: function (run) {
        var result = tail.then(run);
        tail = result.catch(function () {});
        return result;
      }
    };
  }

  function initializeRoster(root) {
    var form = root.querySelector("[data-roster-form]");
    if (!form || root.getAttribute("data-mobile-initialized") === "true") return;
    root.setAttribute("data-mobile-initialized", "true");
    var autosave = root.hasAttribute("data-individual-autosave") &&
      ["meeting", "circle", "activity"].indexOf(root.getAttribute("data-roster-kind")) !== -1;
    var rows = list(form.querySelectorAll("tr[data-participant-id]"));
    var search = root.querySelector("#roster-search");
    var filter = root.querySelector("[data-preparation-filter]");
    var remaining = root.querySelector("[data-remaining-count]");
    var empty = root.querySelector("#roster-empty");
    var bar = form.querySelector("[data-roster-savebar]");
    var dirtyCount = root.querySelector("[data-dirty-count]");
    var status = root.querySelector("[data-roster-status]");
    var saveButton = form.querySelector('[type="submit"]');
    var saveLabel = saveButton ? saveButton.textContent : "حفظ التحضير";
    var feedback = autosave ? root.querySelector("[data-autosave-feedback]") : null;
    var feedbackText = feedback && feedback.querySelector("[data-autosave-message]");
    var retryButton = feedback && feedback.querySelector("[data-autosave-retry]");
    var topbar = document.querySelector(".app-topbar");
    if (feedback && topbar) {
      // In the topbar's flex flow, never an overlay over roster/navigation controls.
      topbar.appendChild(feedback);
      topbar.classList.add("roster-autosave-topbar");
      document.body.classList.add("roster-autosave-page");
    }
    var saving = false;
    var autoBusy = false;
    var autoTimer = null;
    var noticeTimer = null;
    var autoFailed = false;
    var manualFailed = false;
    var bulkDepth = 0;
    var allowNavigation = false;
    var failureText = "تعذر تأكيد الحفظ. اختياراتك باقية؛ تحقق من الاتصال والجلسة ثم أعد المحاولة.";
    var states = rows.map(function (row) {
      var controls = list(row.querySelectorAll('input[type="checkbox"][data-col], [data-elite-trip-status]'));
      var note = row.querySelector('textarea[name^="note_"]');
      return {
        row: row, id: row.getAttribute("data-participant-id"), controls: controls,
        recorded: row.getAttribute("data-recorded") === "true", explicit: false, negative: false,
        revision: 0, baseline: snapshot(controls), note: note, noteBaseline: note ? note.value : "",
        manualUnconfirmed: false, noteUnconfirmed: false,
        // Three independent lanes per field. A bulk draft never replaces an auto intent;
        // an individual edit replaces only that field's draft, not other columns.
        fields: controls.map(function (control) {
          return { control: control, confirmed: valueOf(control), auto: null, manual: null, version: 0 };
        })
      };
    });

    function valueOf(control) { return control.type === "checkbox" ? control.checked : control.value; }
    function snapshot(controls) { return controls.map(valueOf); }
    function same(first, second) {
      return first.length === second.length && first.every(function (value, index) { return value === second[index]; });
    }
    function rowHasAuto(state) { return state.fields.some(function (field) { return !!field.auto; }); }
    function hasAuto() { return autosave && states.some(rowHasAuto); }
    function attendanceDirty(state) {
      var select = state.controls.filter(function (control) { return control.hasAttribute("data-elite-trip-status"); })[0];
      if (select && !select.value) return false;
      return rowHasAuto(state) || state.manualUnconfirmed || state.negative || !same(snapshot(state.controls), state.baseline) || (!state.recorded && state.explicit);
    }
    function noteDirty(state) { return state.note && (state.noteUnconfirmed || state.note.value !== state.noteBaseline); }
    function isPrepared(state) { return state.recorded || state.explicit; }
    function hasDirty() { return states.some(function (state) { return attendanceDirty(state) || noteDirty(state); }); }
    function noteBusy() { return Number(root.getAttribute("data-note-saving") || 0) > 0; }
    function unsettled() { return hasDirty() || saving || autoBusy || autoTimer !== null || autoFailed || manualFailed || noteBusy(); }
    function message(text, failed) {
      if (!status) return;
      status.textContent = text;
      status.setAttribute("data-error", String(!!failed));
      measureSpace();
    }
    function topMessage(text, kind) {
      if (!feedback) return;
      if (noticeTimer !== null) { window.clearTimeout(noticeTimer); noticeTimer = null; }
      feedback.setAttribute("data-state", kind);
      if (feedbackText) feedbackText.textContent = text;
      if (retryButton) {
        retryButton.hidden = kind !== "failed" || !hasAuto();
        retryButton.disabled = saving || autoBusy;
      }
    }
    function showWork() {
      if (autoFailed || manualFailed) {
        message(failureText, true);
        topMessage("تعذر تأكيد الحفظ", "failed");
      } else if (saving) {
        message("جارٍ حفظ التعديلات…");
        topMessage("جارٍ الحفظ…", "pending");
      } else if (autoBusy || autoTimer !== null || hasAuto()) {
        message("جارٍ حفظ التغييرات الفردية…");
        topMessage("جارٍ الحفظ…", "pending");
      } else if (noteBusy()) {
        message("جارٍ حفظ الملاحظة…");
        topMessage("جارٍ حفظ الملاحظة…", "pending");
      } else if (hasDirty()) {
        message(autosave ? "التعديلات الجماعية والملاحظات تُعتمد بزر الحفظ." : "تعديلات تنتظر زر الحفظ.");
        topMessage("", "idle");
      } else if (status && status.textContent.indexOf("جارٍ") === 0) {
        message("");
        topMessage("", "idle");
      }
    }
    function savedNotice(result, attendance) {
      // A valid ACK belongs to the sent version only. Never imply latest/drafts saved.
      if (unsettled()) { showWork(); return; }
      message((attendance ? "تم حفظ التحضير" : "تم حفظ الملاحظات دون تغيير التحضير") + " — الساعة " + result.saved_at);
      if (feedback) {
        topMessage("تم الحفظ بنجاح", "saved");
        noticeTimer = window.setTimeout(function () {
          noticeTimer = null;
          topMessage("", "idle");
        }, 2500);
      }
    }
    function refresh() {
      var unprepared = 0;
      var dirty = 0;
      var visible = 0;
      var query = search ? search.value.trim().toLocaleLowerCase("ar") : "";
      states.forEach(function (state) {
        var prepared = isPrepared(state);
        var attendanceChanged = attendanceDirty(state);
        var changed = attendanceChanged || noteDirty(state);
        if (!prepared) unprepared += 1;
        if (changed) dirty += 1;
        state.row.classList.toggle("roster-row--dirty", !!changed);
        state.row.setAttribute("data-prepared", String(prepared));
        var badge = state.row.querySelector("[data-row-state]");
        if (badge) badge.textContent = attendanceChanged ? "تحضير غير محفوظ" : (state.recorded ? "محفوظ" : (prepared ? "محدّد" : "لم يُحدّد"));
        var matches = (state.row.getAttribute("data-search") || "").indexOf(query) !== -1;
        state.row.hidden = !(matches && (!filter || filter.value !== "unprepared" || !prepared));
        if (!state.row.hidden) visible += 1;
      });
      if (remaining) remaining.textContent = "المتبقون دون تحديد: " + unprepared;
      if (dirtyCount) dirtyCount.textContent = "طلاب بتعديلات غير محفوظة: " + dirty;
      if (empty) empty.hidden = visible !== 0;
      // Autosave pages allow manual save to queue behind earlier auto/note work.
      if (saveButton) saveButton.disabled = saving || (!autosave && noteBusy()) || (dirty === 0 && !autoFailed && !manualFailed);
      root.setAttribute("data-autosave-pending", String(!!(autoBusy || autoTimer !== null || hasAuto())));
      root.setAttribute("data-autosave-failed", String(autoFailed));
      measureSpace();
    }

    root.addEventListener("roster:bulk-start", function () { bulkDepth += 1; });
    root.addEventListener("roster:bulk-end", function () { bulkDepth = Math.max(0, bulkDepth - 1); refresh(); });
    states.forEach(function (state) {
      state.fields.forEach(function (field) {
        var control = field.control;
        control.addEventListener("change", function (event) {
          if (saving) return;
          state.revision += 1;
          field.version += 1;
          var intent = { value: valueOf(control), version: field.version };
          var bulk = bulkDepth > 0 || (event.detail && event.detail.rosterSource === "bulk");
          if (autosave && !bulk && control.type === "checkbox") {
            field.auto = intent;
            field.manual = null;
          } else field.manual = intent;
          if (control.hasAttribute("data-elite-trip-status")) {
            state.explicit = !!control.value;
            state.negative = false;
          } else if (state.controls.some(function (box) { return box.checked; })) {
            state.explicit = true;
            state.negative = false;
          }
          if (!autoFailed && !manualFailed) message("");
          topMessage("", "idle");
          if (autosave && !bulk) scheduleAuto();
          refresh();
          showWork();
        });
      });
      var negativeButton = state.row.querySelector("[data-explicit-negative]");
      if (negativeButton) negativeButton.addEventListener("click", function () {
        if (saving) return;
        state.controls.forEach(function (control) {
          if (control.type === "checkbox") control.checked = false;
          else control.value = "absent";
        });
        state.explicit = true;
        state.negative = true;
        state.controls.forEach(function (control) {
          control.dispatchEvent(new CustomEvent("change", { bubbles: true, detail: { rosterSource: "individual" } }));
        });
        refresh();
        showWork();
      });
      if (state.note) state.note.addEventListener("input", function () {
        if (!autoFailed && !manualFailed) message("");
        topMessage("", "idle");
        refresh();
        showWork();
      });
    });
    if (search) search.addEventListener("input", refresh);
    if (filter) filter.addEventListener("change", refresh);

    // Existing note-only payload/redirect contract stays independent of attendance.
    root.addEventListener("roster:note-saved", function (event) {
      var detail = event.detail || {};
      states.forEach(function (state) {
        if (state.note && state.note === detail.field && typeof detail.value === "string") {
          state.noteBaseline = detail.value;
          state.noteUnconfirmed = false;
        }
      });
      manualFailed = states.some(function (state) { return state.manualUnconfirmed || state.noteUnconfirmed; });
      refresh();
      showWork();
    });
    root.addEventListener("roster:note-request", function () { refresh(); showWork(); });

    function capturedRow(state, individual) {
      return {
        state: state, revision: state.revision,
        value: state.fields.map(function (field) { return individual ? (field.auto ? field.auto.value : field.confirmed) : valueOf(field.control); }),
        auto: state.fields.map(function (field) { return field.auto; }),
        manual: state.fields.map(function (field) { return field.manual; })
      };
    }
    function appendAttendance(data, item) {
      data.set("prepared_" + item.state.id, "1");
      item.state.fields.forEach(function (field, index) {
        // Missing checkbox means false in the backend: always construct every flag.
        if (field.control.type !== "checkbox" || item.value[index]) data.set(field.control.name, field.control.type === "checkbox" ? (field.control.value || "on") : item.value[index]);
        else data.delete(field.control.name);
      });
    }
    function individualData() {
      // Never copy the form: no notes, bulk drafts or other participants may leak.
      var data = new FormData();
      ["csrfmiddlewaretoken", "date", "group"].forEach(function (name) {
        var control = form.querySelector('[name="' + name + '"]');
        if (control) data.set(name, control.value);
      });
      data.set("partial_roster", "1");
      return data;
    }
    function acknowledged(ids, sent) {
      if (!Array.isArray(ids) || ids.length !== sent.length) return false;
      var expected = sent.map(function (item) { return item.state.id; });
      var seen = [];
      return ids.every(function (id) {
        var valid = (typeof id === "number" && Number.isSafeInteger(id) && id > 0) || (typeof id === "string" && /^[1-9]\d*$/.test(id));
        var key = String(id);
        if (!valid || expected.indexOf(key) === -1 || seen.indexOf(key) !== -1) return false;
        seen.push(key);
        return true;
      });
    }
    function post(data, attendance, notes) {
      var sentDate = data.get("date");
      var sentGroup = data.get("group");
      return fetch(form.action, {
        method: "POST", body: data, headers: { Accept: "application/json" }, credentials: "same-origin"
      }).then(function (response) {
        if (!response.ok || response.redirected || !/^application\/json(?:\s*;|$)/i.test(response.headers.get("Content-Type") || "")) throw new Error("invalid_response");
        return response.json();
      }).then(function (result) {
        if (!result || result.success !== true || result.date !== sentDate ||
            !/^[1-9]\d*$/.test(String(result.group_id)) || String(result.group_id) !== String(sentGroup) ||
            typeof result.saved_at !== "string" || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(result.saved_at) ||
            !acknowledged(result.saved_ids, attendance) || !acknowledged(result.saved_note_ids, notes)) throw new Error("unconfirmed_save");
        return result;
      });
    }
    function acknowledgeAttendance(sent, individual) {
      sent.forEach(function (item) {
        var state = item.state;
        state.baseline = item.value.slice();
        state.recorded = true;
        state.row.setAttribute("data-recorded", "true");
        if (!individual) state.manualUnconfirmed = false;
        state.fields.forEach(function (field, index) {
          field.confirmed = item.value[index];
          // Object identity is the captured field version, NOT the current UI value.
          if (field.auto && field.auto === item.auto[index]) field.auto = null;
          if (!individual && field.manual === item.manual[index]) field.manual = null;
        });
        if (state.revision === item.revision) { state.explicit = false; state.negative = false; }
      });
    }
    function scheduleAuto() {
      if (!autosave || saving || autoBusy || autoFailed || autoTimer !== null || !hasAuto()) return;
      // 0ms groups the synchronous early->attended coupling into a single snapshot.
      autoTimer = window.setTimeout(function () { autoTimer = null; saveIndividual(); }, 0);
    }
    function saveIndividual() {
      if (saving || autoBusy || autoFailed || !hasAuto()) { refresh(); return; }
      autoBusy = true;
      refresh();
      showWork();
      requests.enqueue(function () {
        var data = individualData();
        var sent = states.filter(rowHasAuto).slice(0, 1).map(function (state) { return capturedRow(state, true); });
        sent.forEach(function (item) { appendAttendance(data, item); });
        var result = null;
        return post(data, sent, []).then(function (response) {
          acknowledgeAttendance(sent, true);
          result = response;
        }).catch(function () {
          // A network failure cannot prove the DB did not commit. Keep latest intents.
          autoFailed = true;
        }).finally(function () {
          autoBusy = false;
          scheduleAuto();
          refresh();
          if (result) savedNotice(result, true);
          else showWork();
        });
      });
    }
    if (retryButton) retryButton.addEventListener("click", function () {
      if (saving || autoBusy || !hasAuto()) return;
      autoFailed = false;
      scheduleAuto();
      refresh();
      showWork();
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (saving || (!autosave && noteBusy()) || !unsettled()) return;
      if (!form.reportValidity()) return;
      // Manual barrier: stop scheduling auto, retain earlier queued auto/note tasks,
      // then capture ALL dirty rows/notes only after their acknowledgements finish.
      if (autoTimer !== null) { window.clearTimeout(autoTimer); autoTimer = null; }
      saving = true;
      var disabled = list(root.querySelectorAll("input, select, textarea, button")).map(function (control) {
        // An earlier note task's temporary disable must not survive this barrier.
        var previous = control.disabled && control.getAttribute("data-note-request-pending") !== "true";
        control.disabled = true;
        return { control: control, previous: previous };
      });
      form.setAttribute("aria-busy", "true");
      if (saveButton) saveButton.textContent = "جارٍ الحفظ…";
      refresh();
      showWork();
      requests.enqueue(function () {
        var sentAttendance = [];
        var sentNotes = [];
        // Disabled fields cannot be read via FormData(form); snapshot explicitly.
        var data = individualData();
        states.forEach(function (state) {
          if (attendanceDirty(state)) {
            var item = capturedRow(state, false);
            sentAttendance.push(item);
            appendAttendance(data, item);
          }
          if (noteDirty(state)) {
            data.set(state.note.name, state.note.value);
            sentNotes.push({ state: state, value: state.note.value });
          }
        });
        var result = null;
        var nothingToSave = !sentAttendance.length && !sentNotes.length;
        var work = nothingToSave ? Promise.resolve() : post(data, sentAttendance, sentNotes);
        return work.then(function (response) {
          if (!nothingToSave) {
            acknowledgeAttendance(sentAttendance, false);
            sentNotes.forEach(function (item) { item.state.noteBaseline = item.value; item.state.noteUnconfirmed = false; });
            result = response;
            manualFailed = states.some(function (state) { return state.manualUnconfirmed || state.noteUnconfirmed; });
            autoFailed = false;
          }
        }).catch(function () {
          sentAttendance.forEach(function (item) { item.state.manualUnconfirmed = true; });
          sentNotes.forEach(function (item) { item.state.noteUnconfirmed = true; });
          manualFailed = true;
          if (hasAuto()) autoFailed = true;
        }).finally(function () {
          saving = false;
          form.setAttribute("aria-busy", "false");
          disabled.forEach(function (item) { item.control.disabled = item.previous; });
          if (saveButton) saveButton.textContent = saveLabel;
          scheduleAuto();
          refresh();
          if (result) savedNotice(result, !!sentAttendance.length);
          else if (nothingToSave && !unsettled()) { message(""); topMessage("", "idle"); }
          else showWork();
        });
      });
    });

    window.addEventListener("beforeunload", function (event) {
      if (!allowNavigation && unsettled()) { event.preventDefault(); event.returnValue = ""; }
    });
    list(root.querySelectorAll('form[method="get"]')).forEach(function (dateForm) {
      dateForm.addEventListener("submit", function (event) {
        if (saving || autoBusy || autoTimer !== null || noteBusy()) {
          event.preventDefault();
          message("انتظر اكتمال طلب الحفظ قبل تغيير التاريخ أو البيئة.", true);
        } else if (unsettled()) {
          if (!window.confirm("لديك تعديلات غير محفوظة أو حفظ غير مؤكّد. هل تريد مغادرة الكشف وتغيير التاريخ أو البيئة دون تأكيد حفظها؟")) event.preventDefault();
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
    window.addEventListener("load", measureSpace);
    refresh();
  }
  function initialize() { list(document.querySelectorAll("[data-mobile-attendance]")).forEach(initializeRoster); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
