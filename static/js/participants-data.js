(function () {
  "use strict";

  var search = document.getElementById("roster-search");
  var tbody = document.getElementById("roster-body");
  var table = document.getElementById("roster-table");
  var empty = document.getElementById("roster-empty");
  var exportBtn = document.getElementById("export-pdf-btn");
  var groupCheckboxes = document.querySelectorAll(".group-filter-checkbox");
  var selectAllBtn = document.getElementById("group-filter-all");
  var selectNoneBtn = document.getElementById("group-filter-none");
  var sortKeys = [
    "full_name", "group", "academic_stage", "miles", "points",
    "purchase_points", "circle_days_attended", "meetings_attended"
  ];
  var currentSort = { col: null, key: null, dir: null };
  var baseOrder = new WeakMap();

  if (tbody) {
    Array.prototype.forEach.call(tbody.querySelectorAll("tr"), function (row, index) {
      baseOrder.set(row, index);
    });
  }

  function renumberRows() {
    if (!tbody) return;
    Array.prototype.filter.call(tbody.querySelectorAll("tr"), function (row) {
      return !row.hidden;
    }).forEach(function (row, index) {
      var numberCell = row.querySelector(".row-number");
      if (numberCell) numberCell.textContent = index + 1;
    });
  }

  function getSelectedGroups() {
    var selected = [];
    groupCheckboxes.forEach(function (cb) {
      if (cb.checked) selected.push(cb.value);
    });
    return selected;
  }

  function applyFilters() {
    if (!tbody) return;
    var q = search ? search.value.trim().toLowerCase() : "";
    var selectedGroups = getSelectedGroups();
    var groupFilterActive = groupCheckboxes.length > 0;
    var visible = 0;
    Array.prototype.forEach.call(tbody.querySelectorAll("tr"), function (row) {
      var matchesSearch = !q || (row.getAttribute("data-search") || "").indexOf(q) !== -1;
      var matchesGroup = !groupFilterActive
        || selectedGroups.indexOf(row.getAttribute("data-group") || "") !== -1;
      row.hidden = !(matchesSearch && matchesGroup);
      if (!row.hidden) visible++;
    });
    if (empty) empty.hidden = visible !== 0;
    renumberRows();
  }

  if (search) search.addEventListener("input", applyFilters);
  groupCheckboxes.forEach(function (cb) {
    cb.addEventListener("change", applyFilters);
  });
  if (selectAllBtn) {
    selectAllBtn.addEventListener("click", function () {
      groupCheckboxes.forEach(function (cb) { cb.checked = true; });
      applyFilters();
    });
  }
  if (selectNoneBtn) {
    selectNoneBtn.addEventListener("click", function () {
      groupCheckboxes.forEach(function (cb) { cb.checked = false; });
      applyFilters();
    });
  }

  function getCellValue(row, colIndex, type) {
    var cell = row.children[colIndex];
    var raw = cell && cell.hasAttribute("data-sort-value")
      ? cell.getAttribute("data-sort-value") : (cell ? cell.textContent : "");
    var text = raw.trim();
    if (type === "number") {
      var number = parseFloat(text.replace(/[^\d.-]/g, ""));
      return isNaN(number) ? -Infinity : number;
    }
    return text.toLocaleLowerCase("ar");
  }

  function sortByHeader(header) {
    if (!tbody || !table) return;
    var colIndex = parseInt(header.getAttribute("data-col"), 10);
    var key = header.getAttribute("data-sort-key");
    var type = header.getAttribute("data-sort");
    if (sortKeys.indexOf(key) === -1 || isNaN(colIndex)) return;
    var dir = currentSort.col === colIndex && currentSort.dir === "asc" ? "desc" : "asc";
    currentSort = { col: colIndex, key: key, dir: dir };
    table.querySelectorAll("th.sortable").forEach(function (other) {
      other.classList.remove("sort-asc", "sort-desc");
      other.setAttribute("aria-sort", "none");
    });
    header.classList.add(dir === "asc" ? "sort-asc" : "sort-desc");
    header.setAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");

    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
    rows.sort(function (a, b) {
      var va = getCellValue(a, colIndex, type);
      var vb = getCellValue(b, colIndex, type);
      if (va < vb) return dir === "asc" ? -1 : 1;
      if (va > vb) return dir === "asc" ? 1 : -1;
      return baseOrder.get(a) - baseOrder.get(b);
    });
    rows.forEach(function (row) { tbody.appendChild(row); });
    renumberRows();
  }

  if (table) {
    table.querySelectorAll("th.sortable").forEach(function (header) {
      header.addEventListener("click", function () { sortByHeader(header); });
      header.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sortByHeader(header);
        }
      });
    });
  }

  if (exportBtn) {
    exportBtn.addEventListener("click", function (event) {
      event.preventDefault();
      var url = new URL(exportBtn.getAttribute("href"), window.location.href);
      url.searchParams.delete("group");
      getSelectedGroups().forEach(function (group) {
        url.searchParams.append("group", group);
      });
      var status = document.getElementById("participant-status-filter");
      if (status) url.searchParams.set("status", status.value);
      if (currentSort.key) {
        url.searchParams.set("sort_by", currentSort.key);
        url.searchParams.set("sort_dir", currentSort.dir);
      }
      // Search remains client-only; export does not submit student identifiers.
      window.location.href = url.toString();
    });
  }
  renumberRows();
})();
