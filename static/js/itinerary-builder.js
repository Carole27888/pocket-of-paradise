/* Itinerary builder helpers: live summary bar, "ready to send" checklist, collapsible and duplicable days,
   section jump links and an unsaved-changes guard. Progressive: the form works without any of it. */
(function () {
  var form = document.getElementById("itin-form");
  var list = document.getElementById("days-forms");
  var bar = document.getElementById("ib-bar");
  if (!form || !list || !bar) return;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var num = function (el) { return parseFloat(el && el.value) || 0; };
  var floor = parseFloat(form.dataset.floor) || 0;
  var dirty = false;

  function live(row) { var d = row.querySelector('input[type="checkbox"][name$="-DELETE"]'); return !(d && d.checked); }
  function days() { return $$(".day-row", list).filter(live); }
  function val(row, n) { var el = row.querySelector('[name$="-' + n + '"]'); return el ? (el.type === "checkbox" ? el.checked : el.value) : ""; }
  function optionRows(sel) { return $$(sel, form).filter(function (r) { var d = r.querySelector('input[name$="-DELETE"]'); return !(d && d.checked); }); }

  // ---- the checklist
  function checks() {
    var ds = days();
    var untitled = ds.filter(function (r) { return !String(val(r, "title")).trim(); }).length;
    var undescribed = ds.filter(function (r) { return !String(val(r, "description")).trim(); }).length;
    var unpriced = ds.filter(function (r) { return val(r, "accommodation") && !num(r.querySelector('[name$="-lodging_pp"]')); }).length;
    var lodges = optionRows("[data-hotel]").filter(function (r) { return num(r.querySelector('[name$="-price_per_person"]')) > 0; }).length;
    var rides = optionRows("[data-travel]").filter(function (r) { return num(r.querySelector('[name$="-price"]')) > 0; }).length;
    var photos = !!(form.querySelector('[name="photo_pack"]') && form.querySelector('[name="photo_pack"]').value) || ds.some(function (r) { return $("[data-pk-thumbs] img", r) || $(".dz-preview:not([hidden]) img", r); });
    var margin = num(form.querySelector('[name="margin_percent"]'));
    return [
      { ok: ds.length > 0, text: "At least one day", fix: "#ib-days" },
      { ok: !!form.querySelector('[name="start_date"]').value, text: "Start date set", fix: "#ib-basics" },
      { ok: ds.length > 0 && !untitled, text: untitled ? untitled + " day" + (untitled > 1 ? "s" : "") + " without a title" : "Every day has a title", fix: "#ib-days" },
      { ok: ds.length > 0 && !undescribed, text: undescribed ? undescribed + " day" + (undescribed > 1 ? "s" : "") + " with nothing written (try Write for me)" : "Every day is written up", fix: "#ib-days" },
      { ok: !unpriced, text: unpriced ? unpriced + " night" + (unpriced > 1 ? "s" : "") + " with a lodge but no rate" : "Every lodge night is priced", fix: "#ib-days" },
      { ok: !!form.querySelector('[name="intro"]').value.trim(), text: "Welcome paragraph written", fix: "#ib-basics" },
      { ok: photos, text: "Photos chosen (pack or per day)", fix: "#ib-basics" },
      { ok: lodges > 0, text: lodges ? "Lodge options offered (" + lodges + ")" : "No lodge options yet", fix: "#ib-lodges", soft: true },
      { ok: rides > 0, text: rides ? "Travel options offered (" + rides + ")" : "No travel option yet", fix: "#ib-travel", soft: true },
      { ok: !!form.querySelector('[name="terms"]').value.trim(), text: "Payment & cancellation terms filled in", fix: "#ib-basics" },
      { ok: margin >= floor, text: margin >= floor ? "Margin " + margin + "% is at or above the minimum" : "Margin " + margin + "% is below the minimum (" + floor + "%): a manager must send it", fix: "#ib-basics" }
    ];
  }
  function renderChecks() {
    var cs = checks(), hard = cs.filter(function (c) { return !c.soft; }), done = cs.filter(function (c) { return c.ok; }).length;
    var btn = $("#ib-ready");
    $("#ib-ready-text").textContent = done + " of " + cs.length + " ready";
    btn.className = "ib-ready " + (hard.every(function (c) { return c.ok; }) ? "good" : (done >= cs.length - 3 ? "warn" : "bad"));
    $("#ib-pop").innerHTML = '<div class="ib-pop-title">Ready to send?</div>' + cs.map(function (c) {
      return '<a href="' + c.fix + '" class="ib-chk ' + (c.ok ? "ok" : (c.soft ? "soft" : "no")) + '"><i>' + (c.ok ? "✓" : (c.soft ? "–" : "!")) + "</i><span>" + c.text + "</span></a>";
    }).join("");
  }

  // ---- numbers mirrored from the form's own totals
  function mirror() {
    var t = document.getElementById("t-sell"), p = document.getElementById("t-pp");
    if (t) $("#ib-total").textContent = t.textContent;
    if (p) $("#ib-pp").textContent = p.textContent;
    $("#ib-ndays").textContent = days().length;
  }

  // ---- collapsible days with a one-line summary
  function enhance(row) {
    if (row.dataset.ib) return;
    row.dataset.ib = "1";
    var head = $(".flex.justify-between", row);
    if (!head) return;
    var left = head.firstElementChild;
    var tog = document.createElement("button");
    tog.type = "button"; tog.className = "ib-tog"; tog.setAttribute("aria-label", "Collapse or expand this day"); tog.textContent = "▾";
    left.insertBefore(tog, left.firstChild.nextSibling);
    var sum = document.createElement("span"); sum.className = "ib-sum";
    left.appendChild(sum);
    var dup = document.createElement("button");
    dup.type = "button"; dup.className = "btn btn-ghost btn-sm ib-dup"; dup.textContent = "Duplicate";
    var rm = head.lastElementChild;
    head.insertBefore(dup, rm);
  }
  function summarise(row) {
    var sum = $(".ib-sum", row); if (!sum) return;
    var parts = [String(val(row, "title")).trim(), (function () { var s = row.querySelector('[name$="-accommodation"]'); return s && s.value ? s.options[s.selectedIndex].text : ""; })(), (function () { var c = $("[data-day-cost]", row); return c && c.textContent !== "—" ? "cost " + c.textContent : ""; })()].filter(Boolean);
    sum.textContent = parts.join(" · ");
  }
  function summariseAll() { $$(".day-row", list).forEach(function (r) { enhance(r); summarise(r); }); }
  list.addEventListener("click", function (e) {
    var tog = e.target.closest(".ib-tog");
    if (tog) { tog.closest(".day-row").classList.toggle("collapsed"); return; }
    var dup = e.target.closest(".ib-dup");
    if (dup && window.itinDays) {
      var src = dup.closest(".day-row"), d = {};
      ["title", "description", "room_type", "activities", "drive_km", "drive_hours", "lodging_pp", "activities_pp", "transport", "destination", "accommodation"].forEach(function (k) { d[k] = val(src, k); });
      ["breakfast", "lunch", "dinner"].forEach(function (k) { d[k] = val(src, k); });
      var row = window.itinDays.addRow(d, src.nextElementSibling);
      var c = row.querySelector('[name$="-copy_image_from"]'); if (c) c.value = "";
      enhance(row);
      dirty = true; run();
    }
  });
  $$("[data-days-toggle]").forEach(function (b) { b.addEventListener("click", function () { $$(".day-row", list).forEach(function (r) { r.classList.toggle("collapsed", b.dataset.daysToggle === "collapse"); }); }); });
  new MutationObserver(function () { summariseAll(); run(); }).observe(list, { childList: true });

  // ---- the checklist pop-over, jump links
  $("#ib-ready").addEventListener("click", function (e) { e.stopPropagation(); var pop = $("#ib-pop"); pop.hidden = !pop.hidden; this.setAttribute("aria-expanded", String(!pop.hidden)); });
  document.addEventListener("click", function (e) { if (!e.target.closest(".ib-ready-wrap")) { $("#ib-pop").hidden = true; } });
  bar.addEventListener("click", function (e) {
    var a = e.target.closest('a[href^="#ib-"]');
    if (!a) return;
    e.preventDefault();
    var t = document.querySelector(a.getAttribute("href"));
    if (t) { t.scrollIntoView({ behavior: "smooth", block: "start" }); $("#ib-pop").hidden = true; }
  });
  $("#ib-pop").addEventListener("click", function (e) {
    var a = e.target.closest("a[href^='#ib-']"); if (!a) return;
    e.preventDefault(); var t = document.querySelector(a.getAttribute("href")); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); this.hidden = true;
  });

  // ---- unsaved changes
  form.addEventListener("input", function () { dirty = true; });
  form.addEventListener("change", function () { dirty = true; });
  form.addEventListener("submit", function () { dirty = false; });
  window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });

  function run() { summariseAll(); mirror(); renderChecks(); }
  form.addEventListener("input", function () { setTimeout(run, 0); });
  form.addEventListener("change", function () { setTimeout(run, 0); });
  form.addEventListener("click", function (e) { if (e.target.closest("[data-add-form]") || e.target.closest(".rm-x")) setTimeout(run, 30); });
  setTimeout(function () { run(); dirty = false; }, 50);
  if (days().length > 3) $$(".day-row", list).forEach(function (r, i) { if (i > 0) r.classList.add("collapsed"); });   // long trips open with the first day only
})();
