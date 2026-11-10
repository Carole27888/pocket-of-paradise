/* Keep a title field in step with a chosen option until the person edits it themselves.
   <select data-autotitle-source> option labels look like "REF · Client · What they asked for"; the target gets "Client · What they asked for". */
(function () {
  function sync(src) {
    var target = document.querySelector("[data-autotitle-target]");
    if (!target || target.dataset.touched) return;
    var label = (src.options[src.selectedIndex] || {}).text || "";
    var parts = label.split(" · ");
    target.value = src.value && parts.length > 2 ? parts.slice(1).join(" · ") : "";
  }
  document.addEventListener("input", function (e) { if (e.target.matches("[data-autotitle-target]")) e.target.dataset.touched = "1"; });
  document.addEventListener("change", function (e) { if (e.target.matches("[data-autotitle-source]")) sync(e.target); });
  document.addEventListener("DOMContentLoaded", function () { var s = document.querySelector("[data-autotitle-source]"); if (s && s.value) sync(s); });
})();
