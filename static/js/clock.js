/* Clock in / out with the browser's location. The server works out the distance to the office; nothing is tracked after the tap. */
(function () {
  var root = document.querySelector("[data-clock]");
  if (!root) return;
  var btn = root.querySelector("[data-clock-btn]"), msg = root.querySelector("[data-clock-msg]"), stat = root.querySelector("[data-clock-status]"), meta = root.querySelector("[data-clock-meta]");
  var urlStatus = root.dataset.status, urlAct = root.dataset.act, state = {};
  var csrf = function () { return (document.querySelector('meta[name="csrf-token"]') || {}).content || (document.cookie.match(/dsa_csrftoken=([^;]+)/) || [])[1] || ""; };
  function render(s) {
    state = s;
    var inn = !!s.clocked_in;
    root.classList.toggle("is-in", inn);
    btn.textContent = inn ? "Clock out" : "Clock in";
    btn.className = "btn " + (inn ? "btn-secondary" : "btn-accent");
    if (inn) stat.textContent = "Clocked in since " + s.since + " · " + s.hours.toFixed(1) + " h";
    else if (s.out_at) stat.textContent = "Clocked out at " + s.out_at + " · " + s.hours.toFixed(1) + " h today";
    else stat.textContent = "Not clocked in yet";
    var bits = [];
    if (s.since) { bits.push(s.where); if (s.late) bits.push("late"); }
    meta.innerHTML = bits.join(" · ") + (s.map ? ' · <a href="' + s.map + '" target="_blank" rel="noopener">View on map</a>' : "");
    meta.hidden = !bits.length;
  }
  function say(t, bad) { msg.textContent = t || ""; msg.className = "clock-msg " + (bad ? "bad" : "good"); msg.hidden = !t; }
  function send(action, pos) {
    var f = new FormData(); f.append("action", action);
    if (pos) { f.append("lat", pos.coords.latitude); f.append("lng", pos.coords.longitude); f.append("accuracy", pos.coords.accuracy); }
    return fetch(urlAct, { method: "POST", body: f, headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) { render(j); say(j.ok ? j.message : j.error, !j.ok); });
  }
  btn.addEventListener("click", function () {
    var action = state.clocked_in ? "out" : "in";
    btn.disabled = true; say("Finding your location…");
    function done() { btn.disabled = false; }
    if (!navigator.geolocation) { send(action, null).finally(done); return; }
    navigator.geolocation.getCurrentPosition(function (pos) { send(action, pos).finally(done); },
      function (err) { say(err.code === 1 ? "Location is blocked, so this is recorded without a location." : "Couldn't get your location, so this is recorded without one."); send(action, null).finally(done); },
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 });
  });
  fetch(urlStatus, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(render);
})();
