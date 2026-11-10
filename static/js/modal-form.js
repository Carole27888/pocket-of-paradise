/* Open a server-rendered form in a pop-up. Any element with data-modal-form="<url>" (and data-modal-title) opens it;
   the form is fetched as a fragment, submitted with fetch, and re-rendered in place if it has errors. */
(function () {
  var host;
  function build() {
    host = document.createElement("div");
    host.className = "modal-backdrop";
    host.hidden = true;
    host.innerHTML = '<div class="modal" role="dialog" aria-modal="true" aria-labelledby="mf-title" style="max-width:640px;width:calc(100vw - 32px)"><div class="modal-head"><h2 id="mf-title"></h2><button type="button" class="icon-btn" data-modal-close aria-label="Close">&times;</button></div><div class="mf-body" style="max-height:70vh;overflow:auto;padding:2px"></div></div>';
    document.body.appendChild(host);
    host.addEventListener("click", function (e) { if (e.target === host || e.target.closest("[data-modal-close]")) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !host.hidden) close(); });
  }
  function close() { host.hidden = true; }
  function csrf(form) { var t = form.querySelector("[name=csrfmiddlewaretoken]"); return t ? t.value : ""; }
  function show(html) {
    var body = host.querySelector(".mf-body");
    body.innerHTML = html;
    var form = body.querySelector("form");
    if (!form) return;
    var first = form.querySelector("input:not([type=hidden]), select, textarea"); if (first) first.focus();
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = form.querySelector("[type=submit]"); if (btn) btn.disabled = true;
      fetch(form.action, { method: "POST", body: new FormData(form), headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": csrf(form) }, credentials: "same-origin" })
        .then(function (r) { var ct = r.headers.get("content-type") || ""; return ct.indexOf("json") > -1 ? r.json().then(function (j) { return { json: j }; }) : r.text().then(function (t) { return { html: t }; }); })
        .then(function (res) { if (res.json && res.json.ok) { if (res.json.redirect) { location.href = res.json.redirect; } else { location.reload(); } } else if (res.html) { show(res.html); } })
        .catch(function () { if (btn) btn.disabled = false; body.insertAdjacentHTML("afterbegin", '<div class="alert alert-danger mb-3">Could not save. Check your connection and try again.</div>'); });
    });
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-modal-form]");
    if (!t) return;
    e.preventDefault();
    if (!host) build();
    host.querySelector("#mf-title").textContent = t.dataset.modalTitle || "";
    host.querySelector(".mf-body").innerHTML = '<p class="text-muted">Loading…</p>';
    host.hidden = false;
    fetch(t.dataset.modalForm, { headers: { "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" }).then(function (r) { return r.text(); }).then(show);
  });
})();
