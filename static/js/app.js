/* Pocket of Paradise Travel Company — app shell behaviour. Plain JS, no build step. */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function csrfToken() {
    const meta = $('meta[name="csrf-token"]');
    if (meta) return meta.content;
    const m = document.cookie.match(/(?:^|;\s*)dsa_csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  /* ---------------- toasts */
  function toast(text, level, action) {
    let box = $("#toasts");
    if (!box) {
      box = document.createElement("div");
      box.id = "toasts";
      box.className = "toasts";
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      document.body.appendChild(box);
    }
    const el = document.createElement("div");
    el.className = "toast " + (level || "success");
    const span = document.createElement("span");
    span.textContent = text;
    el.appendChild(span);
    const btn = document.createElement("button");
    btn.type = "button";
    if (action) {
      btn.textContent = action.label;
      btn.addEventListener("click", action.run);
    } else {
      btn.textContent = "×";
      btn.setAttribute("aria-label", "Dismiss");
      btn.addEventListener("click", () => el.remove());
    }
    el.appendChild(btn);
    box.appendChild(el);
    if (!action) setTimeout(() => el.remove(), 5000);
    return el;
  }
  window.dsToast = toast;

  /* ---------------- live alerts: a pop-up when a new notification arrives (checked every 30 s while the tab is open) */
  (function liveAlerts() {
    const dot = $("[data-notif-dot]");
    if (!dot) return;                                 // signed-out pages have no bell
    let newest = null;
    let busy = false;
    function check() {
      if (busy || document.hidden) return;
      busy = true;
      fetch("/notifications/live/" + (newest === null ? "" : "?after=" + newest), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (!d) return;
          (d.items || []).forEach((n) => {
            const el = toast(n.title + (n.body ? " · " + n.body : ""), n.level === "danger" ? "danger" : n.level === "warning" ? "warning" : "info",
              { label: "Open", run: () => { location.href = n.url; } });
            setTimeout(() => el.remove(), 12000);
          });
          newest = Math.max(newest || 0, d.newest || 0);
          dot.textContent = d.unread;
          dot.hidden = !d.unread;
        })
        .catch(() => {})
        .finally(() => { busy = false; });
    }
    check();
    setInterval(check, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
  })();

  /* ---------------- HTMX: CSRF on every request, toasts from HX-Trigger */
  document.addEventListener("htmx:configRequest", (e) => {
    e.detail.headers["X-CSRFToken"] = csrfToken();
  });
  document.body.addEventListener("toast", (e) => {
    const d = e.detail || {};
    toast(d.text || d.value || "Done", d.level || "success");
  });
  document.addEventListener("htmx:responseError", (e) => {
    const s = e.detail.xhr ? e.detail.xhr.status : 0;
    toast(s === 403 ? "Your role can't do that." : "Something went wrong (" + s + "). Please try again.", "danger");
  });
  document.addEventListener("htmx:sendError", () => toast("You appear to be offline.", "warning"));
  if (window.htmx) {
    htmx.config.historyCacheSize = 0;          // always refetch pages (fresh data, SW handles offline)
    htmx.config.defaultSwapStyle = "innerHTML";
  }

  /* Django messages rendered server-side become toasts */
  document.addEventListener("DOMContentLoaded", () => {
    $$("[data-toast]").forEach((el) => { toast(el.dataset.toast, el.dataset.level); el.remove(); });
  });

  /* ---------------- sidebar drawer */
  document.addEventListener("click", (e) => {
    const app = $(".app");
    if (!app) return;
    if (e.target.closest("[data-drawer-open]")) { app.classList.add("drawer-open"); e.preventDefault(); }
    else if (e.target.closest("[data-drawer-close]") || e.target.classList.contains("scrim")) app.classList.remove("drawer-open");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { const app = $(".app"); if (app) app.classList.remove("drawer-open"); }
  });

  /* ---------------- confirm buttons */
  document.addEventListener("submit", (e) => {
    const f = e.target.closest("form[data-confirm]");
    if (f && !window.confirm(f.dataset.confirm)) e.preventDefault();
  });

  /* ---------------- PWA: service worker + update toast */
  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;

  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((reg) => {
        const offer = (worker) => {
          toast("A new version is available", "info", {
            label: "Refresh",
            run: () => worker.postMessage({ type: "SKIP_WAITING" }),
          });
        };
        if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
        reg.addEventListener("updatefound", () => {
          const nw = reg.installing;
          if (!nw) return;
          nw.addEventListener("statechange", () => {
            if (nw.state === "installed" && navigator.serviceWorker.controller) offer(nw);
          });
        });
        setInterval(() => reg.update().catch(() => {}), 60 * 60 * 1000);
      }).catch(() => {});
      let reloading = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (reloading) return;
        reloading = true;
        location.reload();
      });
    });
  }

  /* ---------------- install button (Chrome / Edge / Samsung Internet) */
  let deferredPrompt = null;
  const installButtons = () => $$("[data-install]");
  const showInstall = (show) => installButtons().forEach((b) => b.classList.toggle("hidden", !show));
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (!isStandalone()) showInstall(true);
  });
  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    showInstall(false);
    toast("Pocket of Paradise installed. Open it from your home screen.", "success");
  });
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-install]");
    if (!btn || !deferredPrompt) return;
    deferredPrompt.prompt();
    try { await deferredPrompt.userChoice; } catch (_) { /* ignore */ }
    deferredPrompt = null;
    showInstall(false);
  });

  /* ---------------- iOS "Add to Home Screen" banner */
  const IOS_KEY = "ds-ios-banner-dismissed";
  function isIOSSafari() {
    const ua = navigator.userAgent;
    const iOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    const webkit = /WebKit/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
    return iOS && webkit;
  }
  function iosDismissedRecently() {
    try {
      const t = parseInt(localStorage.getItem(IOS_KEY) || "0", 10);
      return Date.now() - t < 14 * 24 * 3600 * 1000;
    } catch (_) { return false; }
  }
  document.addEventListener("DOMContentLoaded", () => {
    const banner = $("#ios-banner");
    if (!banner) return;
    if (isIOSSafari() && !isStandalone() && !iosDismissedRecently()) banner.classList.remove("hidden");
    banner.addEventListener("click", (e) => {
      if (!e.target.closest("[data-dismiss]")) return;
      banner.classList.add("hidden");
      try { localStorage.setItem(IOS_KEY, String(Date.now())); } catch (_) { /* private mode */ }
    });
    $$("[data-ios-only]").forEach((el) => el.classList.toggle("hidden", !isIOSSafari()));
  });

  /* ---------------- web push toggle */
  function urlB64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  }
  async function postJSON(url, body) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  async function currentSubscription() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return null;
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  }
  async function refreshPushLabels() {
    const btns = $$("[data-push-toggle]");
    if (!btns.length) return;
    const supported = "PushManager" in window && "Notification" in window;
    let sub = null;
    try { sub = supported ? await currentSubscription() : null; } catch (_) { sub = null; }
    btns.forEach((b) => {
      const label = b.querySelector("[data-push-label]") || b;
      if (!supported) {
        label.textContent = isIOSSafari() && !isStandalone()
          ? "Notifications: add to Home Screen first (iOS 16.4+)" : "Notifications not supported here";
        b.disabled = true;
      } else {
        label.textContent = sub ? "Disable notifications" : "Enable notifications";
        b.dataset.state = sub ? "on" : "off";
      }
    });
  }
  document.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-push-toggle]");
    if (!b) return;
    e.preventDefault();
    const key = document.body.dataset.vapid;
    try {
      const sub = await currentSubscription();
      if (sub) {
        await postJSON(b.dataset.unsubscribeUrl, { endpoint: sub.endpoint });
        await sub.unsubscribe();
        toast("Notifications turned off on this device.", "info");
      } else {
        if (!key) { toast("Push is not configured yet (VAPID keys missing).", "warning"); return; }
        const perm = await Notification.requestPermission();
        if (perm !== "granted") { toast("Notifications were blocked in browser settings.", "warning"); return; }
        const reg = await navigator.serviceWorker.ready;
        const nsub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(key) });
        await postJSON(b.dataset.subscribeUrl, nsub.toJSON());
        toast("Notifications enabled on this device.", "success");
      }
    } catch (err) {
      toast("Couldn't change notification setting.", "danger");
    }
    refreshPushLabels();
  });
  document.addEventListener("DOMContentLoaded", refreshPushLabels);

  /* ---------------- global search: keyboard shortcut "/" */
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      const s = $("#global-search");
      if (s) { e.preventDefault(); s.focus(); }
    }
  });

  /* ---------------- formset "add row" helper: <button data-add-form="prefix"> */
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-add-form]");
    if (!b) return;
    e.preventDefault();
    const prefix = b.dataset.addForm;
    const total = $(`#id_${prefix}-TOTAL_FORMS`);
    const tpl = $(`#${prefix}-empty`);
    const target = $(`#${prefix}-forms`);
    if (!total || !tpl || !target) return;
    const idx = parseInt(total.value, 10);
    const html = tpl.innerHTML.replace(/__prefix__/g, String(idx));
    target.insertAdjacentHTML("beforeend", html);
    total.value = String(idx + 1);
    if (window.htmx) htmx.process(target.lastElementChild);
  });
})();

/* Keyboard: Ctrl/Cmd+K or "/" jumps to the search box */
document.addEventListener("keydown", function (e) {
  var el = document.getElementById("global-search");
  if (!el) return;
  var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
  if (((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") || (e.key === "/" && !typing)) { e.preventDefault(); el.focus(); el.select(); }
});

/* "Refresh app" in the profile menu: drop the offline caches and service worker, then reload from the server. */
document.addEventListener("click", function (e) {
  var t = e.target.closest("[data-refresh-app]");
  if (!t) return;
  e.preventDefault();
  var done = function () { location.reload(); };
  var jobs = [];
  try { if (window.caches) jobs.push(caches.keys().then(function (ks) { return Promise.all(ks.map(function (k) { return caches.delete(k); })); })); } catch (_) {}
  try { if (navigator.serviceWorker) jobs.push(navigator.serviceWorker.getRegistrations().then(function (rs) { return Promise.all(rs.map(function (r) { return r.unregister(); })); })); } catch (_) {}
  Promise.all(jobs).then(done, done);
});

/* "No quotation yet": moving a lead to Quoted or Hot needs a quotation. Ask, and offer to create one now. */
(function () {
  function close(host) { host.remove(); }
  window.dsNeedQuote = function (d) {
    var host = document.createElement("div");
    host.className = "modal-backdrop";
    host.innerHTML = '<div class="modal" role="dialog" aria-modal="true" aria-labelledby="nq-t" style="max-width:460px">' +
      '<div class="modal-head"><h2 id="nq-t"></h2><button type="button" class="icon-btn" data-nq-close aria-label="Close">×</button></div>' +
      '<p class="nq-msg" style="margin:0 0 6px"></p>' +
      '<p class="text-sm text-muted nq-hint" style="margin:0 0 16px"></p>' +
      '<div style="display:flex;gap:10px;justify-content:flex-end"><button type="button" class="btn btn-secondary" data-nq-close>Not now</button><a class="btn btn-primary nq-go"></a></div></div>';
    host.querySelector("#nq-t").textContent = d.title || "Create the quotation first";
    host.querySelector(".nq-msg").textContent = d.message || (d.name + " has no quotation yet.");
    host.querySelector(".nq-hint").textContent = d.no_itinerary ? "The itinerary holds the days, lodges, travel and prices. The quotation is made from it in one click."
      : "A draft is made from the lead's itinerary. Check the lines and send it; sending moves the lead for you.";
    host.querySelector(".nq-go").textContent = d.button || "Create quotation";
    host.querySelector(".nq-go").href = d.url;
    host.addEventListener("click", function (e) { if (e.target === host || e.target.closest("[data-nq-close]")) close(host); });
    document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { close(host); document.removeEventListener("keydown", esc); } });
    document.body.appendChild(host);
    host.querySelector(".nq-go").focus();
  };
  // the lead page and the Enquiries list send their request with HTMX: the server answers with an event
  document.body.addEventListener("needQuote", function (e) { if (e.detail) window.dsNeedQuote(e.detail); });
})();
