// PIN sign-in for phones and laptops on the home network.
"use strict";

const form = document.querySelector("#login-form");
const errorBox = document.querySelector("#login-error");
const button = document.querySelector("#login-button");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  button.disabled = true;
  try {
    const resp = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: form.pin.value.trim() }),
    });
    if (resp.ok) {
      location.href = "/";
      return;
    }
    const body = await resp.json().catch(() => ({}));
    errorBox.textContent = typeof body.detail === "string" ? body.detail : "Sign-in failed";
    errorBox.hidden = false;
    form.pin.select();
  } catch {
    errorBox.textContent = "Can't reach HomePlanner. Are you on the home Wi-Fi?";
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
  }
});
