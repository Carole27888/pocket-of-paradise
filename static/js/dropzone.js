/* Drag-and-drop picture boxes (.dz). Works for forms added later (e.g. "Add day") because every handler is delegated. */
(function () {
  var MAX = 5 * 1024 * 1024, OK = /^image\/(jpeg|png|webp)$/;
  function q(el, s) { return el.querySelector(s); }
  function error(dz, msg) { var e = q(dz, ".dz-error"); e.textContent = msg || ""; e.hidden = !msg; }
  function show(dz, file) {
    var input = q(dz, ".dz-input"), prev = q(dz, ".dz-preview"), empty = q(dz, ".dz-empty"), clear = q(dz, ".dz-clear");
    if (file) {
      if (!OK.test(file.type)) { error(dz, "Use a JPG, PNG or WebP picture."); input.value = ""; return; }
      if (file.size > MAX) { error(dz, "That picture is over 5 MB."); input.value = ""; return; }
      error(dz, "");
      q(prev, "img").src = URL.createObjectURL(file);
      q(prev, ".dz-name").textContent = file.name;
      if (clear) clear.checked = false;
      prev.hidden = false; empty.hidden = true;
    } else {
      error(dz, ""); input.value = "";
      q(prev, "img").removeAttribute("src"); q(prev, ".dz-name").textContent = "";
      if (clear) clear.checked = true;
      prev.hidden = true; empty.hidden = false;
    }
  }
  function zone(e) { return e.target.closest && e.target.closest("[data-dz]"); }
  document.addEventListener("click", function (e) {
    var dz = zone(e); if (!dz) return;
    if (e.target.closest(".dz-remove")) { show(dz, null); return; }
    if (e.target.closest(".dz-replace") || e.target.closest(".dz-empty")) { q(dz, ".dz-input").click(); }
  });
  document.addEventListener("change", function (e) { var dz = zone(e); if (dz && e.target.classList.contains("dz-input")) show(dz, e.target.files[0] || null); });
  ["dragenter", "dragover"].forEach(function (t) { document.addEventListener(t, function (e) { var dz = zone(e); if (dz) { e.preventDefault(); dz.classList.add("dz-over"); } }); });
  ["dragleave", "drop"].forEach(function (t) { document.addEventListener(t, function (e) { var dz = zone(e); if (dz) dz.classList.remove("dz-over"); }); });
  document.addEventListener("drop", function (e) {
    var dz = zone(e); if (!dz) return;
    e.preventDefault();
    var f = e.dataTransfer && e.dataTransfer.files[0]; if (!f) return;
    var dt = new DataTransfer(); dt.items.add(f);
    q(dz, ".dz-input").files = dt.files; show(dz, f);
  });
  window.addEventListener("dragover", function (e) { e.preventDefault(); });   // a miss must not navigate away to the file
  window.addEventListener("drop", function (e) { if (!zone(e)) e.preventDefault(); });
})();
