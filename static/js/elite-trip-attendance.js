/* Interactions unique to the data-only elite-trip roster. */
(function () {
  "use strict";

  function toArray(nodeList) {
    return Array.prototype.slice.call(nodeList);
  }

  function selectedLabel(select) {
    var option = select.options[select.selectedIndex];
    return option ? option.text : select.value;
  }

  function initializeRoster(root) {
    var statusSelects = toArray(root.querySelectorAll("[data-elite-trip-status]"));
    var bulkSelect = root.querySelector("#elite-trip-bulk-status");
    var bulkApply = root.querySelector("#elite-trip-bulk-apply");
    var feedback = root.querySelector("#elite-trip-bulk-feedback");

    if (!statusSelects.length) return;

    function updateSelectAppearance(select) {
      select.setAttribute("data-status", select.value);
    }

    function showFeedback(message) {
      if (!feedback) return;
      feedback.textContent = message;
      feedback.hidden = false;
    }

    statusSelects.forEach(function (select) {
      updateSelectAppearance(select);
      select.addEventListener("change", function () {
        updateSelectAppearance(select);
      });
    });

    if (!bulkSelect || !bulkApply) return;

    function updateBulkAvailability() {
      bulkApply.disabled = !bulkSelect.value;
    }

    bulkSelect.addEventListener("change", function () {
      updateBulkAvailability();
      if (feedback) feedback.hidden = true;
    });

    bulkApply.addEventListener("click", function () {
      var status = bulkSelect.value;
      if (!status) {
        showFeedback("اختر حالة أولًا قبل تطبيقها على الطلاب.");
        return;
      }

      var label = selectedLabel(bulkSelect);
      var confirmed = window.confirm(
        "تأكيد تعيين حالة «" + label + "» لجميع الطلاب (" + statusSelects.length + " طالبًا)؟\n" +
        "يمكنك تعديل أي حالة فردية قبل حفظ الكشف النهائي."
      );
      if (!confirmed) {
        showFeedback("لم تُغيّر حالات الطلاب.");
        return;
      }

      statusSelects.forEach(function (select) {
        select.value = status;
        updateSelectAppearance(select);
      });
      showFeedback("تم تعيين حالة «" + label + "» لجميع الطلاب. راجع الحالات ثم احفظ الكشف.");
    });

    updateBulkAvailability();
  }

  function initialize() {
    toArray(document.querySelectorAll(".attendance-roster--elite-trip")).forEach(initializeRoster);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize);
  } else {
    initialize();
  }
})();
