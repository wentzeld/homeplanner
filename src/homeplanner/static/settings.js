// Settings page: external calendars and remote access (PIN).
"use strict";

const IDLE_BACK_MS = 5 * 60 * 1000; // on the wall display, go back to the calendar when left alone
const CONFIRM_MS = 5000;

const $ = (sel) => document.querySelector(sel);
let config = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const resp = await fetch(path, options);
  if (resp.status === 401) {
    location.href = "/login";
    throw new Error("Please sign in");
  }
  const body = resp.status === 204 ? null : await resp.json().catch(() => null);
  if (!resp.ok) {
    let msg = `Request failed (${resp.status})`;
    if (body && typeof body.detail === "string") msg = body.detail;
    else if (body && Array.isArray(body.detail)) msg = body.detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ");
    throw new Error(msg);
  }
  return body;
}

const postJSON = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data ?? {}) });

function showMessage(box, text, isError) {
  box.textContent = text;
  box.classList.toggle("error", Boolean(isError));
  box.hidden = false;
}

function formatTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    timeZone: config.timezone, weekday: "short", hour: "numeric", minute: "2-digit",
  });
}

// --- External calendars --------------------------------------------------------------

function renderCalendars(calendars) {
  const list = $("#calendar-list");
  list.replaceChildren();
  $("#no-calendars").hidden = calendars.length > 0;
  for (const cal of calendars) {
    const item = el("li", "calendar-item");
    const dot = el("i", "dot");
    dot.style.setProperty("--c", cal.color);

    const info = el("div", "calendar-info");
    info.append(el("div", "calendar-name", cal.name), el("div", "calendar-url", cal.url));
    const status = cal.error
      ? el("div", "calendar-status error", `⚠ ${cal.error}${cal.last_updated ? ` · showing copy from ${formatTime(cal.last_updated)}` : ""}`)
      : el("div", "calendar-status", cal.last_updated ? `Updated ${formatTime(cal.last_updated)}` : "Waiting for first update…");
    info.append(status);

    const actions = el("div", "calendar-actions");
    const copy = el("button", null, "Copy link");
    copy.type = "button";
    copy.addEventListener("click", () => copyLink(cal.url, copy));
    const remove = el("button", "danger", "Remove");
    remove.type = "button";
    remove.addEventListener("click", () => confirmRemove(cal, remove));
    actions.append(copy, remove);

    item.append(dot, info, actions);
    list.append(item);
  }
}

async function copyLink(url, button) {
  try {
    await navigator.clipboard.writeText(url);
    button.textContent = "Copied ✓";
  } catch {
    // Clipboard needs a secure context; over plain http on a phone, fall back to selecting it.
    window.prompt("Copy this link:", url);
  }
  setTimeout(() => { button.textContent = "Copy link"; }, 2000);
}

// Two-step remove (no browser pop-ups on the kiosk): first click arms, second click removes.
function confirmRemove(cal, button) {
  if (button.dataset.armed) {
    api(`/api/calendars/${cal.id}`, { method: "DELETE" })
      .then(loadCalendars)
      .catch((e) => { button.textContent = e.message; });
    return;
  }
  button.dataset.armed = "1";
  button.textContent = `Really remove “${cal.name}”?`;
  setTimeout(() => {
    delete button.dataset.armed;
    button.textContent = "Remove";
  }, CONFIRM_MS);
}

async function loadCalendars() {
  renderCalendars(await api("/api/calendars"));
}

function setupColorPicker() {
  const select = $("#color-select");
  for (const c of config.colors) select.append(new Option(c.name, c.id));
  // Default to a color no family member or calendar uses yet.
  const used = new Set([...config.members, ...config.calendars].map((x) => x.color));
  const free = config.colors.find((c) => !used.has(c.color));
  select.value = (free || config.colors[0]).id;
  const update = () => {
    const c = config.colors.find((x) => x.id === select.value);
    $("#color-swatch").style.setProperty("--c", c.color);
  };
  select.addEventListener("change", update);
  update();
}

async function addCalendar(event) {
  event.preventDefault();
  const form = event.target;
  const box = $("#add-message");
  const data = { name: form.name.value.trim(), url: form.url.value.trim(), color_id: form.color_id.value };
  if (!data.name || !data.url) {
    showMessage(box, "Please enter a name and a link.", true);
    return;
  }
  const button = $("#add-button");
  button.disabled = true;
  button.textContent = "Checking link…";
  try {
    const result = await postJSON("/api/calendars", data);
    const n = result.upcoming_events;
    showMessage(box, `Added “${result.calendar.name}”: ${n} event${n === 1 ? "" : "s"} in the next 30 days.`, false);
    form.name.value = "";
    form.url.value = "";
    config = await api("/api/config");
    await loadCalendars();
  } catch (e) {
    showMessage(box, e.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Add calendar";
  }
}

// --- Remote access -------------------------------------------------------------------

function renderRemote(state) {
  const section = $("#remote-section");
  section.hidden = !state.mode; // off entirely when the server isn't in remote mode (e.g. dev)
  $("#remote-display-only").hidden = !state.is_display;
  $("#remote-signed-in").hidden = state.is_display;
  if (!state.is_display) return;
  $("#remote-status").replaceChildren(...(state.enabled
    ? ["✅ On. Open ", el("b", null, "http://homeplanner.local:8000"), " on a phone or laptop on your home Wi-Fi and enter the PIN."]
    : ["Off. Set a PIN to use HomePlanner from phones and laptops on your home Wi-Fi."]));
  $("#pin-label").textContent = state.enabled ? "Change PIN (6–8 digits)" : "New PIN (6–8 digits)";
  $("#pin-button").textContent = state.enabled ? "Change PIN" : "Set PIN";
  $("#remote-actions").hidden = !state.enabled;
}

async function loadRemote() {
  renderRemote(await api("/api/auth/state"));
}

async function submitPin(event) {
  event.preventDefault();
  const form = event.target;
  const box = $("#pin-message");
  const pin = form.pin.value.trim();
  if (!/^\d{6,8}$/.test(pin)) return showMessage(box, "The PIN must be 6 to 8 digits.", true);
  if (pin !== form.pin2.value.trim()) return showMessage(box, "The two PINs don't match.", true);
  try {
    await postJSON("/api/auth/pin", { pin });
    form.reset();
    showMessage(box, "PIN saved. Any device signed in before must sign in again.", false);
    await loadRemote();
  } catch (e) {
    showMessage(box, e.message, true);
  }
}

function armedAction(button, label, action) {
  const original = button.textContent;
  button.addEventListener("click", async () => {
    if (!button.dataset.armed) {
      button.dataset.armed = "1";
      button.textContent = label;
      setTimeout(() => { delete button.dataset.armed; button.textContent = original; }, CONFIRM_MS);
      return;
    }
    delete button.dataset.armed;
    button.textContent = original;
    try {
      await action();
      await loadRemote();
    } catch (e) {
      showMessage($("#pin-message"), e.message, true);
    }
  });
}

// --- Start-up ------------------------------------------------------------------------

async function init() {
  config = await api("/api/config");
  setupColorPicker();
  $("#add-calendar").addEventListener("submit", addCalendar);
  $("#pin-form").addEventListener("submit", submitPin);
  armedAction($("#signout-all"), "Really sign out all devices?", () => postJSON("/api/auth/signout-all")
    .then(() => showMessage($("#pin-message"), "All other devices are signed out.", false)));
  armedAction($("#disable-remote"), "Really turn off remote access?", () => postJSON("/api/auth/disable"));
  $("#logout").addEventListener("click", async () => {
    await postJSON("/api/logout");
    location.href = "/login";
  });

  const isDisplay = config.remote.is_display;
  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName);
    if (e.key === "Escape" && (!typing || !document.activeElement.value)) location.href = "/";
  });
  if (isDisplay) {
    let lastInput = Date.now();
    for (const type of ["keydown", "mousemove", "mousedown", "wheel"]) {
      document.addEventListener(type, () => { lastInput = Date.now(); }, { passive: true });
    }
    setInterval(() => { if (Date.now() - lastInput > IDLE_BACK_MS) location.href = "/"; }, 5000);
  }

  await Promise.all([loadCalendars(), loadRemote()]);
  setInterval(loadCalendars, 60 * 1000); // pick up refresh status
}

init().catch((e) => {
  console.error(e);
  document.querySelector("main").prepend(el("p", "form-message error", `Couldn't load settings: ${e.message}`));
});
