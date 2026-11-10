/* Posting calendar: drag a post chip (or a draft) onto a day to reschedule it. The time of day is kept. */
(function () {
  var cal = document.getElementById("post-cal");
  if (!cal || !cal.dataset.canMove) return;
  var dragId = null;
  var csrf = function () { var t = document.querySelector("[name=csrfmiddlewaretoken]"); return t ? t.value : ""; };
  document.addEventListener("dragstart", function (e) {
    var chip = e.target.closest && e.target.closest("[data-post][draggable=true]");
    if (!chip) return;
    dragId = chip.dataset.post;
    e.dataTransfer.setData("text/x-post", dragId); e.dataTransfer.effectAllowed = "move";
    setTimeout(function () { chip.classList.add("pc-dragging"); }, 0);
  });
  document.addEventListener("dragend", function (e) {
    dragId = null;
    document.querySelectorAll(".pc-dragging").forEach(function (c) { c.classList.remove("pc-dragging"); });
    document.querySelectorAll(".pc-over").forEach(function (c) { c.classList.remove("pc-over"); });
  });
  cal.addEventListener("dragover", function (e) {
    if (!dragId) return;
    var day = e.target.closest(".cal-d"); if (!day) return;
    e.preventDefault();
    document.querySelectorAll(".pc-over").forEach(function (c) { if (c !== day) c.classList.remove("pc-over"); });
    day.classList.add("pc-over");
  });
  cal.addEventListener("drop", function (e) {
    var day = e.target.closest(".cal-d");
    if (!day || !dragId) return;
    e.preventDefault();
    var f = new FormData(); f.append("date", day.dataset.day);
    fetch(cal.dataset.reschedule.replace(/0\/reschedule\/$/, dragId + "/reschedule/"), { method: "POST", body: f, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (j.ok) location.reload(); else alert(j.error || "Couldn't move that post."); });
  });
})();
