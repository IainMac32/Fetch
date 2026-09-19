(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  let token = location.hash.slice(1), active = false, stopped = false, timer;
  try {
    if (token) sessionStorage.setItem("doordash-demo-connection", token);
    else token = sessionStorage.getItem("doordash-demo-connection") || "";
  } catch (_) { /* Keep using the fragment token if storage is unavailable. */ }
  if (token) history.replaceState(null, "", location.pathname);
  const headers = {Authorization: `Bearer ${token}`, "Content-Type": "application/json"};

  function finish(message, success = false) {
    active = false;
    stopped = true;
    clearTimeout(timer);
    $("credentials-form").hidden = true;
    $("code-form").hidden = true;
    $("empty-state").hidden = false;
    $("empty-text").textContent = message;
    $("status").textContent = success ? "DoorDash connected" : "Connection ended";
    $("status-dot").className = `status-dot ${success ? "success" : "error"}`;
    $("countdown").textContent = "";
    try { sessionStorage.removeItem("doordash-demo-connection"); } catch (_) {}
  }

  async function poll() {
    if (stopped) return;
    if (!token) return finish("Open the connection link from your MessageShopper conversation.");
    try {
      const response = await fetch("/api/handoff", {headers, cache: "no-store"});
      if (response.status === 410) return finish("This link has expired. Text CONNECT for a new one.");
      if (!response.ok) throw new Error();
      const state = await response.json();
      if (["connected", "expired", "cancelled", "failed"].includes(state.state)) {
        return finish(state.state === "connected" ? "Your DoorDash session is ready for next time." : state.message,
                      state.state === "connected");
      }
      active = state.can_control;
      $("status").textContent = state.message;
      $("countdown").textContent = active ? `${Math.floor(state.seconds_remaining / 60)}:${String(state.seconds_remaining % 60).padStart(2, "0")}` : "";
      $("empty-state").hidden = active;
      $("credentials-form").hidden = !active || state.state !== "awaiting_credentials";
      $("code-form").hidden = !active || state.state !== "awaiting_code";
      if (!active) $("empty-text").textContent = state.message;
      const busy = ["submitting", "verifying_code"].includes(state.state);
      $("sign-in").disabled = busy;
      $("verify").disabled = busy;
    } catch (_) {
      active = false;
      $("status").textContent = "Connection interrupted. Retrying…";
      $("credentials-form").hidden = true;
      $("code-form").hidden = true;
    }
    timer = setTimeout(poll, 1000);
  }

  async function submit(form, endpoint, payload, errorId, buttonId) {
    if (!active) return;
    const button = $(buttonId);
    button.disabled = true;
    $(errorId).textContent = "";
    try {
      const response = await fetch(endpoint, {
        method: "POST", headers, cache: "no-store", body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.error || "Could not submit. Please try again.");
      }
      form.reset();
      if (buttonId === "sign-in") { $("login").value = ""; $("password").value = ""; }
      else $("code").value = "";
      $("empty-state").hidden = false;
      $("empty-text").textContent = buttonId === "sign-in" ? "Signing in to DoorDash…" : "Checking the DoorDash code…";
    } catch (error) {
      $(errorId).textContent = error.message;
      button.disabled = false;
      return;
    }
    setTimeout(poll, 100);
  }

  $("credentials-form").addEventListener("submit", event => {
    event.preventDefault();
    submit($("credentials-form"), "/api/handoff/credentials",
           {login: $("login").value.trim(), password: $("password").value}, "login-error", "sign-in");
  });
  $("code-form").addEventListener("submit", event => {
    event.preventDefault();
    submit($("code-form"), "/api/handoff/code", {code: $("code").value}, "code-error", "verify");
  });
  poll();
})();
