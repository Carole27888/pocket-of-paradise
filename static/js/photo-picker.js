/* "Choose photos" for itinerary days: one shared modal fed by the library search JSON. Works for days added later (delegated). */
(function () {
  var modal, cur = null, picked = [], timer;
  function el(h) { var d = document.createElement("div"); d.innerHTML = h.trim(); return d.firstChild; }
  function build() {
    modal = el('<div class="pk-modal" role="dialog" aria-modal="true" aria-label="Choose photos"><div class="pk-box"><div class="pk-head"><select class="input" data-pk-cat></select><input class="input" type="search" placeholder="Search tags: lion, sunset, tent…" data-pk-q><button type="button" class="btn btn-secondary" data-pk-close>Cancel</button></div><div class="pk-tags mchips" data-pk-tags style="padding:8px 16px 0"></div><div class="pk-body" data-pk-body></div><div class="pk-foot"><span class="text-muted text-sm" data-pk-count></span><button type="button" class="btn btn-primary" data-pk-done>Use these photos</button></div></div></div>');
    document.body.appendChild(modal);
    modal.addEventListener("click", function (e) {
      if (e.target === modal || e.target.closest("[data-pk-close]")) return close();
      if (e.target.closest("[data-pk-done]")) return done();
      var it = e.target.closest(".pk-item");
      if (it) { var id = it.dataset.id, i = picked.findIndex(function (p) { return p.id === id; });
        if (i >= 0) { picked.splice(i, 1); it.classList.remove("on"); } else { picked.push({ id: id, thumb: it.dataset.thumb }); it.classList.add("on"); }
        count(); }
      var t = e.target.closest("[data-tag]"); if (t) { modal.dataset.tag = t.dataset.tag === modal.dataset.tag ? "" : t.dataset.tag; load(); }
    });
    modal.querySelector("[data-pk-q]").addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(load, 220); });
    modal.querySelector("[data-pk-cat]").addEventListener("change", function () { modal.dataset.tag = ""; load(); });
  }
  function count() { modal.querySelector("[data-pk-count]").textContent = picked.length + " selected"; }
  function load() {
    var url = document.getElementById("itin-form").dataset.photoUrl;
    var p = new URLSearchParams({ q: modal.querySelector("[data-pk-q]").value, category: modal.querySelector("[data-pk-cat]").value, tag: modal.dataset.tag || "" });
    fetch(url + "?" + p).then(function (r) { return r.json(); }).then(function (j) {
      var ids = picked.map(function (x) { return x.id; });
      modal.querySelector("[data-pk-body]").innerHTML = j.results.map(function (r) { return '<div class="pk-item' + (ids.indexOf(String(r.id)) >= 0 ? " on" : "") + '" data-id="' + r.id + '" data-thumb="' + r.thumb + '"><img src="' + r.thumb + '" alt="" loading="lazy"></div>'; }).join("") || '<p class="text-muted">No photos found. Add some in the Photo library.</p>';
      modal.querySelector("[data-pk-tags]").innerHTML = j.tags.slice(0, 14).map(function (t) { return '<button type="button" class="chip' + (modal.dataset.tag === t[0] ? " active" : "") + '" data-tag="' + t[0] + '">' + t[0] + "</button>"; }).join("");
    });
  }
  function close() { modal.classList.remove("open"); cur = null; }
  function done() {
    var row = cur, box = row.querySelector("[data-pk-hidden]"), prefix = box.dataset.pkHidden;
    box.querySelectorAll("input").forEach(function (i) { i.remove(); });
    picked.forEach(function (p) { var i = document.createElement("input"); i.type = "hidden"; i.name = prefix + "-photos"; i.value = p.id; box.appendChild(i); });
    row.querySelector("[data-pk-thumbs]").innerHTML = picked.map(function (p) { return '<img src="' + p.thumb + '" alt="">'; }).join("");
    close();
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-pk-open]"); if (!b) return;
    if (!modal) build();
    cur = b.closest(".day-row");
    var box = cur.querySelector("[data-pk-hidden]");
    picked = Array.prototype.map.call(box.querySelectorAll("input"), function (i) { return { id: i.value, thumb: "" }; });
    var thumbs = cur.querySelectorAll("[data-pk-thumbs] img");
    picked.forEach(function (p, i) { p.thumb = thumbs[i] ? thumbs[i].src : ""; });
    var sel = modal.querySelector("[data-pk-cat]"), form = document.getElementById("itin-form"), pack = form.querySelector('[name="photo_pack"]');
    sel.innerHTML = '<option value="">All packs</option>' + JSON.parse(form.dataset.packs).map(function (c) { return '<option value="' + c.id + '">' + c.name + "</option>"; }).join("");
    sel.value = pack && pack.value ? pack.value : "";
    modal.dataset.tag = ""; modal.querySelector("[data-pk-q]").value = "";
    modal.classList.add("open"); count(); load();
  });
})();
