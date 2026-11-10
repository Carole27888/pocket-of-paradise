/* Itinerary builder drag and drop: reorder days, and drag single days in from the template library.
   Works on the itinerary editor and the template editor (both use #itin-form, #days-forms and #days-empty). */
(function () {
  var form = document.getElementById("itin-form");
  var list = document.getElementById("days-forms");
  var tpl = document.getElementById("days-empty");
  if (!form || !list || !tpl) return;
  var total = form.querySelector('[name$="-TOTAL_FORMS"]');
  var line = document.createElement("div");
  line.className = "drop-line";
  var dragging = null;

  function rows() { return Array.prototype.slice.call(list.children).filter(function (c) { return c.classList.contains("day-row"); }); }
  function renumber(notify) {
    rows().forEach(function (r, i) { var n = r.querySelector('[name$="-day_number"]'); if (n) n.value = i + 1; });
    if (notify) form.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function setVal(row, name, v) {
    var el = row.querySelector('[name$="-' + name + '"]'); if (!el) return;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v === null || v === undefined ? "" : String(v);
  }
  function fill(row, d) {
    ["title", "description", "room_type", "activities", "drive_km", "drive_hours", "lodging_pp", "activities_pp", "transport", "destination", "accommodation"].forEach(function (k) { setVal(row, k, d[k]); });
    ["breakfast", "lunch", "dinner"].forEach(function (k) { setVal(row, k, d[k]); });
    setVal(row, "copy_image_from", "t:" + d.id);
    if (d.image_url) {
      var dz = row.querySelector("[data-dz]");
      if (dz) { dz.querySelector(".dz-preview img").src = d.image_url; dz.querySelector(".dz-name").textContent = "Copied from " + d.template; dz.querySelector(".dz-preview").hidden = false; dz.querySelector(".dz-empty").hidden = true; }
    }
    var note = row.querySelector("[data-rate-note]"); if (note && d.accommodation) note.textContent = "From template “" + d.template + "”.";
  }
  function flash(row) { row.classList.add("day-new"); setTimeout(function () { row.classList.remove("day-new"); }, 1400); }
  function addRow(data, before) {
    var idx = parseInt(total.value, 10);
    var wrap = document.createElement("div");
    wrap.innerHTML = tpl.innerHTML.replace(/__prefix__/g, idx).trim();
    var row = wrap.querySelector(".day-row");
    total.value = idx + 1;
    list.insertBefore(row, before || null);
    if (data) fill(row, data);
    renumber(true);
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
    flash(row);
    return row;
  }
  function beforeRow(y) {
    var rs = rows().filter(function (r) { return r !== dragging; });
    for (var i = 0; i < rs.length; i++) { var b = rs[i].getBoundingClientRect(); if (y < b.top + b.height / 2) return rs[i]; }
    return null;
  }
  function has(e, type) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], type) > -1; }

  document.addEventListener("dragstart", function (e) {
    var grip = e.target.closest && e.target.closest(".day-grip");
    var lib = e.target.closest && e.target.closest(".lib-day");
    if (grip) {
      dragging = grip.closest(".day-row");
      e.dataTransfer.setData("text/x-day-row", "1"); e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setDragImage(dragging, 24, 24); } catch (_) {}
      setTimeout(function () { dragging.classList.add("dragging"); }, 0);
      list.classList.add("dnd-active");
    } else if (lib) {
      e.dataTransfer.setData("text/x-tpl-day", lib.dataset.day); e.dataTransfer.effectAllowed = "copy";
      list.classList.add("dnd-active");
    }
  });
  list.addEventListener("dragover", function (e) {
    if (!has(e, "text/x-day-row") && !has(e, "text/x-tpl-day")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = has(e, "text/x-day-row") ? "move" : "copy";
    var b = beforeRow(e.clientY);
    list.insertBefore(line, b);
  });
  list.addEventListener("drop", function (e) {
    var isRow = has(e, "text/x-day-row"), isTpl = has(e, "text/x-tpl-day");
    if (!isRow && !isTpl) return;
    e.preventDefault();
    var b = beforeRow(e.clientY);
    if (line.parentNode) line.parentNode.removeChild(line);
    if (isRow && dragging) { list.insertBefore(dragging, b); renumber(true); flash(dragging); }
    else if (isTpl) { try { addRow(JSON.parse(e.dataTransfer.getData("text/x-tpl-day")), b); } catch (_) {} }
  });
  document.addEventListener("dragend", function () {
    if (dragging) dragging.classList.remove("dragging");
    dragging = null; list.classList.remove("dnd-active");
    if (line.parentNode) line.parentNode.removeChild(line);
  });
  list.addEventListener("dragleave", function (e) { if (!list.contains(e.relatedTarget) && line.parentNode) line.parentNode.removeChild(line); });

  // click-to-add (touch screens have no drag and drop) and the library box
  document.addEventListener("click", function (e) {
    var add = e.target.closest("[data-lib-add]");
    if (add) { addRow(JSON.parse(add.closest(".lib-day").dataset.day), null); return; }
    var head = e.target.closest("[data-lib-toggle]");
    if (head) { var body = document.querySelector("[data-lib-body]"); body.hidden = !body.hidden; head.querySelector("[data-lib-state]").textContent = body.hidden ? "show" : "hide"; }
  });
  document.addEventListener("input", function (e) {
    if (!e.target.matches("[data-lib-search]")) return;
    var q = e.target.value.trim().toLowerCase();
    document.querySelectorAll("[data-lib-tpl]").forEach(function (t) {
      var any = false;
      t.querySelectorAll(".lib-day").forEach(function (d) { var m = !q || d.dataset.search.indexOf(q) > -1; d.style.display = m ? "" : "none"; any = any || m; });
      t.style.display = any ? "" : "none";
    });
  });
  form.addEventListener("submit", function () { renumber(false); });
  window.itinDays = { addRow: addRow, renumber: renumber };   // used by the builder's Duplicate day button
})();
