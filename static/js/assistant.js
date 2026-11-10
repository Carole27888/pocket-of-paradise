(function () {
  var root = document.getElementById("ai-assistant");
  if (!root) return;
  var btn = root.querySelector("[data-ai-open]"), panel = root.querySelector(".ai-panel"), log = root.querySelector(".ai-log"),
      form = root.querySelector("form"), input = form.querySelector("input"), history = [];
  var csrf = function () { return (document.querySelector('meta[name="csrf-token"]') || {}).content || ""; };
  function add(text, who) {
    var d = document.createElement("div"); d.className = "ai-msg ai-" + who; d.textContent = text; log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
  }
  btn.addEventListener("click", function () { var open = panel.hasAttribute("hidden"); panel.toggleAttribute("hidden", !open); btn.setAttribute("aria-expanded", String(open)); if (open) input.focus(); });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim(); if (!q) return;
    input.value = ""; add(q, "me"); var wait = add("Thinking…", "bot");
    fetch(root.dataset.url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() }, body: JSON.stringify({ question: q, history: history }) })
      .then(function (r) { return r.json(); })
      .then(function (j) { wait.textContent = j.answer || j.error || "No answer."; history.push({ role: "user", content: q }, { role: "assistant", content: wait.textContent }); })
      .catch(function () { wait.textContent = "Couldn't reach the assistant. Check your connection."; });
  });
})();
