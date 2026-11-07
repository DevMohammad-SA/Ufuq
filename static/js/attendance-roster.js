(function () {
  "use strict";

  function toArray(nodeList) {
    return Array.prototype.slice.call(nodeList);
  }

  function getSortValue(row, columnIndex, type) {
    var cell = row.children[columnIndex];
    var rawValue = cell ? (cell.getAttribute("data-sort-value") || cell.textContent || "") : "";
    var value = rawValue.trim();

    if (type === "number") {
      var numberValue = parseFloat(value.replace(/[^\d.-]/g, ""));
      return isNaN(numberValue) ? -Infinity : numberValue;
    }

    return value.toLocaleLowerCase("ar");
  }

  function initRoster(root) {
    var table = root.querySelector("#roster-table");
    var tbody = root.querySelector("#roster-body");
    if (!table || !tbody) return;

    var emptyMessage = root.querySelector("#roster-empty");
    var searchInput = root.querySelector("#roster-search");
    var dataToggle = root.querySelector("#student-data-toggle");
    var currentSort = { column: null, direction: null };

    function toggleStudentData(show) {
      root.classList.toggle("student-data-visible", show);
      toArray(table.querySelectorAll(".student-extra")).forEach(function (cell) {
        cell.hidden = !show;
      });

      if (!dataToggle) return;
      dataToggle.setAttribute("aria-expanded", String(show));
      dataToggle.textContent = show
        ? (dataToggle.getAttribute("data-hide-label") || "إخفاء بيانات الطلاب")
        : (dataToggle.getAttribute("data-show-label") || "إظهار بيانات الطلاب");
    }

    if (dataToggle) {
      toggleStudentData(false);
      dataToggle.addEventListener("click", function () {
        toggleStudentData(dataToggle.getAttribute("aria-expanded") !== "true");
      });
    }

    if (searchInput && !root.hasAttribute("data-mobile-attendance")) {
      searchInput.addEventListener("input", function () {
        var query = searchInput.value.trim().toLocaleLowerCase("ar");
        var visibleRows = 0;

        toArray(tbody.querySelectorAll("tr")).forEach(function (row) {
          var isMatch = (row.getAttribute("data-search") || "").indexOf(query) !== -1;
          row.hidden = !isMatch;
          if (isMatch) visibleRows += 1;
        });

        if (emptyMessage) emptyMessage.hidden = visibleRows !== 0;
      });
    }

    function makeEarlyAttendanceAlsoAttended(earlyBox) {
      if (!earlyBox.checked) return;
      var row = earlyBox.closest("tr");
      var attendedBox = row && row.querySelector('input[type="checkbox"][data-col="attended"]');
      if (attendedBox && !attendedBox.checked) {
        attendedBox.checked = true;
        attendedBox.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    toArray(root.querySelectorAll('input[type="checkbox"][data-col="early"]')).forEach(function (earlyBox) {
      earlyBox.addEventListener("change", function () {
        makeEarlyAttendanceAlsoAttended(earlyBox);
      });
    });

    toArray(root.querySelectorAll(".select-all")).forEach(function (button) {
      button.addEventListener("click", function () {
        var column = button.getAttribute("data-target");
        /* لا نقيّد الاستعلام بالصفوف المرئية: يظل سلوك "تحديد الكل" شاملًا للبحث. */
        var boxes = toArray(root.querySelectorAll('input[type="checkbox"][data-col="' + column + '"]'));
        if (!boxes.length) return;

        var allChecked = boxes.every(function (box) { return box.checked; });
        boxes.forEach(function (box) {
          box.checked = !allChecked;
          box.dispatchEvent(new Event("change", { bubbles: true }));
        });

        if (column === "early") {
          boxes.forEach(makeEarlyAttendanceAlsoAttended);
        }
      });
    });

    toArray(root.querySelectorAll(".note-save-button")).forEach(function (button) {
      button.addEventListener("click", function () {
        var form = button.closest("form");
        var field = document.getElementById(button.getAttribute("data-note-field"));
        var status = button.parentElement.querySelector(".note-save-status");
        if (!form || !field || !status || button.disabled || form.getAttribute("aria-busy") === "true") return;
        if (!field.reportValidity()) return;
        var sentValue = field.value;
        var sentDate = form.querySelector('[name="date"]').value;
        var sentGroup = form.querySelector('[name="group"]').value;
        var data = new FormData();
        data.append("csrfmiddlewaretoken", form.querySelector('[name="csrfmiddlewaretoken"]').value);
        data.append("action", "save_note");
        data.append("participant_id", button.getAttribute("data-participant"));
        data.append("note_text", sentValue);
        data.append("date", sentDate);
        data.append("group", sentGroup);
        root.setAttribute("data-note-saving", String(Number(root.getAttribute("data-note-saving") || 0) + 1));
        button.disabled = true;
        root.dispatchEvent(new CustomEvent("roster:note-request"));
        status.textContent = "جارٍ حفظ الملاحظة…";
        fetch(form.action, {method: "POST", body: data, credentials: "same-origin"})
          .then(function (response) {
            var target = new URL(response.url);
            if (!response.ok || !response.redirected || target.pathname !== new URL(form.action).pathname ||
                target.searchParams.get("date") !== sentDate || target.searchParams.get("group") !== sentGroup) {
              throw new Error("note_save_failed");
            }
            status.textContent = "تم حفظ الملاحظة دون تغيير التحضير.";
            root.dispatchEvent(new CustomEvent("roster:note-saved", { detail: { field: field, value: sentValue } }));
          })
          .catch(function () { status.textContent = "تعذر حفظ الملاحظة. حدّث الصفحة وتحقق من الصلاحية أو طول النص."; })
          .finally(function () {
            button.disabled = false;
            root.setAttribute("data-note-saving", String(Math.max(0, Number(root.getAttribute("data-note-saving") || 0) - 1)));
            root.dispatchEvent(new CustomEvent("roster:note-request"));
          });
      });
    });

    toArray(table.querySelectorAll("th.sortable")).forEach(function (header) {
      header.addEventListener("click", function () {
        var columnIndex = parseInt(header.getAttribute("data-col"), 10);
        var type = header.getAttribute("data-sort") || "text";
        if (isNaN(columnIndex)) return;

        var direction = "asc";
        if (currentSort.column === columnIndex && currentSort.direction === "asc") {
          direction = "desc";
        }
        currentSort = { column: columnIndex, direction: direction };

        toArray(table.querySelectorAll("th.sortable")).forEach(function (sortableHeader) {
          sortableHeader.classList.remove("sort-asc", "sort-desc");
          sortableHeader.setAttribute("aria-sort", "none");
        });
        header.classList.add(direction === "asc" ? "sort-asc" : "sort-desc");
        header.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");

        var rows = toArray(tbody.querySelectorAll("tr"));
        rows.sort(function (firstRow, secondRow) {
          var firstValue = getSortValue(firstRow, columnIndex, type);
          var secondValue = getSortValue(secondRow, columnIndex, type);
          if (firstValue < secondValue) return direction === "asc" ? -1 : 1;
          if (firstValue > secondValue) return direction === "asc" ? 1 : -1;
          return 0;
        });
        rows.forEach(function (row) { tbody.appendChild(row); });
      });
      header.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          header.click();
        }
      });
    });
  }

  function initialize() {
    toArray(document.querySelectorAll(".attendance-roster")).forEach(initRoster);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
