/* Roles & Access board: drag a person card to another role column (executives only; the server enforces the rules). */
(function () {
  "use strict";
  const board = document.getElementById("roles-board");
  if (!board) return;
  const csrf = () => ($('meta[name="csrf-token"]') || {}).content || "";
  function $(s, r) { return (r || document).querySelector(s); }
  const toast = (t, l) => (window.dsToast ? window.dsToast(t, l) : alert(t));
  let dragged = null;
  const refresh = () => board.querySelectorAll(".kcol").forEach((c) => { const n = c.querySelectorAll(".person").length; c.querySelector(".count").textContent = n;
    const note = c.querySelector(".empty-note"); if (note) note.style.display = n ? "none" : ""; });

  board.addEventListener("dragstart", (e) => { const c = e.target.closest(".person"); if (!c) return; dragged = c; c.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", c.dataset.user);
    board.querySelectorAll(".kcol").forEach((k) => k.classList.add(k.dataset.droppable ? "drop-ok" : "drop-no")); });
  board.addEventListener("dragend", () => { if (dragged) dragged.classList.remove("dragging"); dragged = null; board.querySelectorAll(".kcol").forEach((k) => k.classList.remove("drop-ok", "drop-no")); });
  board.addEventListener("dragover", (e) => { const col = e.target.closest(".kcol"); if (col && dragged && col.dataset.droppable) e.preventDefault(); });
  board.addEventListener("drop", async (e) => {
    const col = e.target.closest(".kcol"); if (!col || !dragged) return; e.preventDefault();
    const card = dragged, from = card.dataset.from, to = col.dataset.role; dragged = null;
    if (from === to) return;
    if (!confirm("Change " + card.querySelector(".t").textContent + " from " + from + " to " + to + "?")) return;
    const body = new FormData(); body.append("user", card.dataset.user); body.append("from", from); body.append("to", to);
    const r = await fetch(board.dataset.url, { method: "POST", body, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" } });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.ok) { toast(data.error || "Couldn't change that role.", "danger"); return; }
    col.querySelector(".kcol-body").prepend(card); card.dataset.from = to; refresh(); toast("Role changed and logged.");
  });
  refresh();
})();
