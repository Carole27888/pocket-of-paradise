/* Sales pipeline board: drag a card to another column (touch users use the "Move to" menu on each card). */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const board = $("#board");
  if (!board) return;
  const csrf = () => ($('meta[name="csrf-token"]') || {}).content || "";
  const toast = (t, l) => (window.dsToast ? window.dsToast(t, l) : alert(t));
  let dragged = null;

  function fmt(n) {
    if (n >= 1e6) return "KES " + (n / 1e6).toFixed(n >= 1e7 ? 1 : 2).replace(/\.?0+$/, "") + "M";
    if (n >= 1e3) return "KES " + Math.round(n / 1e3) + "K";
    return "KES " + Math.round(n);
  }
  function refreshColumn(col) {
    const cards = $$(".kcard", col);
    const total = cards.reduce((s, c) => s + parseFloat(c.dataset.value || 0), 0);
    $(".count", col).textContent = cards.length;
    $(".total", col).textContent = fmt(total);
  }
  function refreshAll() { $$(".kcol", board).forEach(refreshColumn); }

  async function move(card, targetCol, reason) {
    const fromCol = card.closest(".kcol");
    if (fromCol === targetCol) return;
    const stage = targetCol.dataset.stage;
    if (stage === "lost" && reason === undefined) {
      reason = (window.prompt("Why was this lead lost? (e.g. price, chose another operator, dates)") || "").trim();
      if (!reason) return toast("A lost reason is required.", "warning");
    }
    const body = new FormData();
    body.append("stage", stage);
    if (reason) body.append("reason", reason);
    card.classList.add("busy");
    try {
      const r = await fetch(card.dataset.moveUrl, { method: "POST", body, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" } });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        const s = $(".kmove", card); if (s) s.value = fromCol.dataset.stage;
        if (data.need_quote && window.dsNeedQuote) { window.dsNeedQuote(data); return; }
        toast(data.error || "Couldn't move that lead.", "danger"); return;
      }
      if (stage === "lost") { card.remove(); } else { $(".kcol-body", targetCol).prepend(card); const s = $(".kmove", card); if (s) s.value = stage; }
      refreshColumn(fromCol); refreshColumn(targetCol);
      toast("Moved to " + data.label + ".");
    } catch (e) {
      toast("You appear to be offline.", "warning");
    } finally { card.classList.remove("busy"); }
  }

  board.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".kcard");
    if (!card || card.dataset.locked) return e.preventDefault();
    dragged = card; card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", card.dataset.id);
    $$(".kcol", board).forEach((c) => c.classList.add(c.dataset.droppable ? "drop-ok" : "drop-no"));
  });
  board.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null; $$(".kcol", board).forEach((c) => c.classList.remove("drop-ok", "drop-no"));
  });
  board.addEventListener("dragover", (e) => { const col = e.target.closest(".kcol"); if (col && dragged && col.dataset.droppable) e.preventDefault(); });
  board.addEventListener("drop", (e) => {
    const col = e.target.closest(".kcol");
    if (!col || !dragged) return;
    e.preventDefault();
    if (!col.dataset.droppable) return toast("Record a payment on the booking to move a deal there.", "warning");
    const card = dragged; dragged = null; move(card, col);
  });
  board.addEventListener("change", (e) => {
    const sel = e.target.closest(".kmove");
    if (!sel) return;
    const col = $(`.kcol[data-stage="${sel.value}"]`, board);
    if (col) move(sel.closest(".kcard"), col);
  });
  refreshAll();
})();
