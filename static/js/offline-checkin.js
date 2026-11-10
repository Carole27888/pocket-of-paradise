/* Offline-tolerant manifest check-in: a failed check-in POST is queued in localStorage and replayed when the
   connection returns. Only the check-in POST is queued: guest data is never written to the device. */
(function () {
  "use strict";
  const KEY = "ds-checkin-queue";
  const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (_) { return []; } };
  const write = (q) => { try { localStorage.setItem(KEY, JSON.stringify(q)); } catch (_) { /* private mode: nothing queued */ } };
  const csrf = () => (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  const toast = (t, l) => (window.dsToast ? window.dsToast(t, l) : null);

  async function flush() {
    let q = read();
    if (!q.length || !navigator.onLine) return;
    const left = [];
    for (const item of q) {
      try {
        const body = new FormData();
        if (item.undo) body.append("undo", "1");
        const r = await fetch(item.url, { method: "POST", body, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" } });
        if (!r.ok && r.status >= 500) left.push(item);
      } catch (_) { left.push(item); }
    }
    write(left);
    if (left.length < q.length) { toast((q.length - left.length) + " check-in(s) synced.", "success"); if (!left.length && document.querySelector("[data-manifest]")) setTimeout(() => location.reload(), 800); }
  }

  document.addEventListener("htmx:sendError", (e) => {
    const cfg = e.detail && e.detail.requestConfig;
    if (!cfg || !/\/manifest\/\d+\/check\/$/.test(cfg.path || "")) return;
    const undo = cfg.parameters && cfg.parameters.undo === "1";
    const q = read();
    if (!q.some((i) => i.url === cfg.path)) q.push({ url: cfg.path, undo: !!undo, at: Date.now() });
    write(q);
    const row = e.detail.elt && e.detail.elt.closest("tr");
    if (row) row.classList.add("row-selected");
    toast("You're offline: check-in saved and will sync when you're back online.", "warning");
  });
  window.addEventListener("online", flush);
  document.addEventListener("DOMContentLoaded", flush);
})();
