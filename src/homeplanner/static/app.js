// Kiosk page logic: 5-day view, navigation, add-event form.
"use strict";

const DAYS_SHOWN = 7; // Sunday..Saturday
const EVENTS_REFRESH_MS = 60 * 1000;
const EVENTS_RETRY_MS = 10 * 1000; // while the footer shows an error or "waiting"
const WEATHER_REFRESH_MS = 10 * 60 * 1000;
const WEATHER_RETRY_MS = 15 * 1000; // while there's no weather yet (e.g. network not up at boot)
const IDLE_RESET_MS = 5 * 60 * 1000; // return to today after this long without input
const CURSOR_HIDE_MS = 5 * 1000;
const FILTER_RESET_MS = 60 * 1000; // legend filter clears after this long without input
const WEATHER_ICONS = {
  clear: "☀️", "partly-cloudy": "⛅", cloudy: "☁️", fog: "🌫️",
  drizzle: "🌦️", rain: "🌧️", snow: "❄️", thunder: "⛈️",
};

const state = {
  config: null,
  demo: false,
  start: null, // ISO date of the Sunday of the shown week; null = follow the current week
  weather: null,
  lastInput: Date.now(),
  eventsError: false,
  lastEvents: null, // last /api/events response, so days can redraw when weather arrives
  eventsByKey: new Map(), // rendered card key -> event, for opening details
  selected: null, // event shown in the details dialog
  editing: null, // {id, scope} while the form is editing an existing event
  refreshing: false,
  filter: null, // {kind: "member" | "family" | "calendar", name} chosen in the legend
  eventsRequest: 0,
};

const $ = (sel) => document.querySelector(sel);

// --- Dates (ISO "YYYY-MM-DD" strings in the configured timezone) -------------

function todayISO() {
  return localDateOf(new Date());
}

function addDays(iso, n) {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function weekdayIndex(iso) { // 0 = Sunday
  return new Date(iso + "T00:00:00Z").getUTCDay();
}

function formatDay(iso, opts) {
  return new Date(iso + "T12:00:00Z").toLocaleDateString(undefined, { timeZone: "UTC", ...opts });
}

function formatTime(isoDateTime) {
  return new Date(isoDateTime)
    .toLocaleTimeString(undefined, { timeZone: state.config.timezone, hour: "numeric", minute: "2-digit" })
    .replace(":00", "");
}

function localDateOf(dateTime) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: state.config.timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date(dateTime));
}

function weekStart(iso) { // the Sunday on or before iso
  return addDays(iso, -weekdayIndex(iso));
}

function currentStart() {
  return state.start ?? weekStart(todayISO());
}

// --- DOM helpers ---------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

async function api(path, options) {
  const resp = await fetch(path, options);
  if (resp.status === 401) { // a phone/laptop whose sign-in expired
    location.href = "/login";
    throw new Error("Please sign in");
  }
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const err = new Error(errorMessage(body) || `Request failed (${resp.status})`);
    err.status = resp.status;
    throw err;
  }
  return body;
}

function errorMessage(body) {
  if (!body || !body.detail) return null;
  if (typeof body.detail === "string") return body.detail;
  // FastAPI validation errors: [{loc, msg}, ...]
  return body.detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ");
}

function showToast(text) {
  const toast = $("#toast");
  toast.textContent = text;
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 4000);
}

// --- Rendering -----------------------------------------------------------------

function renderClock() {
  if (!state.config) return;
  const now = new Date();
  const tz = state.config.timezone;
  $("#time").textContent = now.toLocaleTimeString(undefined, { timeZone: tz, hour: "numeric", minute: "2-digit" });
  $("#date").textContent = now.toLocaleDateString(undefined, {
    timeZone: tz, weekday: "long", month: "long", day: "numeric",
  });
}

function renderLegend() {
  const legend = $("#legend");
  // Redrawing replaces the buttons; keep keyboard focus on the same entry.
  const focused = legend.contains(document.activeElement) ? document.activeElement.dataset : null;
  const refocus = focused && { kind: focused.kind, name: focused.name };
  legend.replaceChildren();
  const entries = [
    ...state.config.members.map((m) => ({ ...m, kind: "member" })),
    { name: "Family", color: state.config.family_color, kind: "family" },
    ...state.config.calendars.map((c) => ({ ...c, kind: "calendar" })),
  ];
  for (const entry of entries) {
    const item = el("button", "legend-item");
    item.type = "button";
    item.dataset.kind = entry.kind;
    item.dataset.name = entry.name;
    item.style.setProperty("--c", entry.color);
    const selected = state.filter && state.filter.kind === entry.kind && state.filter.name === entry.name;
    item.classList.toggle("selected", Boolean(selected));
    item.classList.toggle("faded", Boolean(state.filter && !selected));
    item.setAttribute("aria-pressed", selected ? "true" : "false");
    item.title = selected ? "Show everyone" : `Show only ${entry.name}`;
    item.append(el("i"), entry.name);
    legend.append(item);
    if (refocus && refocus.kind === entry.kind && refocus.name === entry.name) item.focus();
  }
}

// Legend filter: one person/calendar at a time; clicking the selected one again clears it.
function matchesFilter(ev) {
  const f = state.filter;
  if (!f) return true;
  if (f.kind === "member") return ev.member === f.name;
  if (f.kind === "calendar") return ev.calendar === f.name;
  return !ev.member && !ev.calendar; // "Family": untagged whole-family events
}

function setFilter(kind, name) {
  const same = state.filter && state.filter.kind === kind && state.filter.name === name;
  state.filter = same || !kind ? null : { kind, name };
  renderLegend();
  if (state.lastEvents) renderDays(state.lastEvents);
}

function renderRangeLabel(start) {
  const today = todayISO();
  const end = addDays(start, DAYS_SHOWN - 1);
  const fmt = (iso) => formatDay(iso, { weekday: "short", month: "short", day: "numeric" });
  const thisWeek = weekStart(today);
  let prefix = "";
  if (start === thisWeek) prefix = "This week · ";
  else if (start === addDays(thisWeek, 7)) prefix = "Next week · ";
  else if (start === addDays(thisWeek, -7)) prefix = "Last week · ";
  const label = $("#range-label");
  label.textContent = `${prefix}${fmt(start)} – ${fmt(end)}`;
  if (state.filter) label.append(el("span", "filter-note", ` · Showing ${state.filter.name} (click again to show all)`));
}

function dayLabel(iso) {
  const today = todayISO();
  if (iso === today) return "Today";
  if (iso === addDays(today, 1)) return "Tomorrow";
  return formatDay(iso, { weekday: "long" });
}

function renderDayWeather(iso) {
  const box = el("div", "day-weather");
  const day = state.weather?.report?.daily.find((d) => d.date === iso);
  if (!day) return box;
  const unit = state.weather.report.unit;
  box.append(el("span", "icon", WEATHER_ICONS[day.icon] || ""));
  const temps = el("span");
  temps.append(`${Math.round(day.high)}° `, el("span", "lo", `${Math.round(day.low)}°${unit}`));
  box.append(temps);
  // Always add the rain line (maybe empty) so all day headers have the same height.
  const rainy = day.precipitation_chance != null && day.precipitation_chance >= 30;
  box.append(el("div", "rain", rainy ? `💧 ${day.precipitation_chance}%` : "\u00a0"));
  box.title = day.description;
  return box;
}

function eventTimeLabel(ev, iso) {
  if (ev.start === ev.end) return formatTime(ev.start); // no end time given
  const startsToday = localDateOf(ev.start) === iso;
  // An event ending exactly at midnight ends "today", so look at the instant just before its end.
  const endsToday = localDateOf(new Date(Date.parse(ev.end) - 1)) === iso;
  if (startsToday && endsToday) return `${formatTime(ev.start)} – ${formatTime(ev.end)}`;
  if (startsToday) return `${formatTime(ev.start)} →`;
  if (endsToday) return `→ ${formatTime(ev.end)}`;
  return "All day (continues)";
}

function makeOpenable(node, ev) {
  const key = String(state.eventsByKey.size);
  state.eventsByKey.set(key, ev);
  node.dataset.key = key;
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.setAttribute("aria-label", `${ev.title}, open details`);
  return node;
}

function renderEvent(ev, iso) {
  const meta = [ev.member || ev.calendar, ev.location].filter(Boolean).join(" · ");
  if (ev.all_day) {
    const pill = makeOpenable(el("div", "pill", ev.title), ev);
    if (!matchesFilter(ev)) pill.classList.add("filtered-out");
    pill.style.setProperty("--c", ev.color);
    pill.title = meta;
    return pill;
  }
  const card = makeOpenable(el("div", "event"), ev);
  if (!matchesFilter(ev)) card.classList.add("filtered-out");
  card.style.setProperty("--c", ev.color);
  if (new Date(ev.end) < new Date()) card.classList.add("past");
  card.append(el("div", "ev-time", eventTimeLabel(ev, iso)), el("div", "ev-title", ev.title));
  const who = ev.member || ev.calendar;
  if (who) card.append(el("div", "ev-meta", who));
  // Location is only shown on hover/focus (see styles.css) to save space; details show it too.
  if (ev.location) card.append(el("div", "ev-location", `📍 ${ev.location}`));
  return card;
}

function renderDays(data) {
  state.eventsByKey = new Map();
  const today = todayISO();
  const container = $("#days");
  container.replaceChildren();
  for (const day of data.days) {
    const col = el("section", "day");
    if (day.date === today) col.classList.add("today");
    else if (day.date < today) col.classList.add("past-day");
    const wd = weekdayIndex(day.date);
    if (wd === 0 || wd === 6) col.classList.add("weekend");

    const head = el("div", "day-head");
    const title = el("div");
    title.append(el("div", "day-name", dayLabel(day.date)),
                 el("div", "day-date", formatDay(day.date, { weekday: "short", month: "short", day: "numeric" })));
    head.append(title, renderDayWeather(day.date));

    const allDay = el("div", "all-day");
    const timed = el("div", "timed");
    for (const ev of day.events) (ev.all_day ? allDay : timed).append(renderEvent(ev, day.date));
    if (!day.events.length) timed.append(el("div", "empty", "Nothing planned"));

    col.append(head, allDay, timed);
    container.append(col);
  }
  renderRangeLabel(data.start);
  renderSync(data);
}

function renderSync(data) {
  const sync = $("#sync");
  sync.replaceChildren();
  if (state.refreshing) {
    sync.classList.remove("error", "waiting");
    sync.append("Syncing…");
    return;
  }
  const synced = data.last_synced
    ? new Date(data.last_synced).toLocaleTimeString(undefined, { timeZone: state.config.timezone, hour: "numeric", minute: "2-digit" })
    : null;
  // Network not ready yet (typically just after boot) is expected, not an error.
  const waiting = data.error_kind === "network" && !synced;
  sync.classList.toggle("error", Boolean(data.error) && !waiting);
  sync.classList.toggle("waiting", waiting);
  if (state.demo) sync.append(el("span", "demo", "DEMO"));
  if (waiting) {
    sync.append("Waiting for network…");
  } else if (data.error) {
    sync.append(synced
      ? `⚠ Can't reach Google Calendar · showing events from ${synced}`
      : `⚠ Calendar not connected: ${data.error}`);
  } else if (synced) {
    sync.append(`Synced ${synced}`);
  }
}

function renderCurrentWeather() {
  const box = $("#current-weather");
  box.replaceChildren();
  const report = state.weather?.report;
  if (!report) return;
  const text = el("div");
  text.append(el("div", null, `${Math.round(report.current.temperature)}°${report.unit}`),
              el("div", "desc", report.current.description));
  box.append(el("span", "icon", WEATHER_ICONS[report.current.icon] || ""), text);
}

// --- Data loading --------------------------------------------------------------

async function loadEvents() {
  const request = ++state.eventsRequest;
  const params = new URLSearchParams({ start: currentStart(), days: DAYS_SHOWN });
  try {
    const data = await api(`/api/events?${params}`);
    if (request !== state.eventsRequest) return; // ignore stale responses
    state.eventsError = Boolean(data.error);
    state.lastEvents = data;
    renderDays(data);
  } catch (e) {
    console.warn("loading events failed", e);
    if (request !== state.eventsRequest) return;
    state.eventsError = true;
    renderSync({ error: e.message });
  }
}

// Refresh every minute normally, but every 10s while there's an error, so the footer
// clears soon after the backend recovers (e.g. Wi-Fi coming up after boot).
function scheduleEventsRefresh() {
  setTimeout(async () => {
    await loadEvents();
    scheduleEventsRefresh();
  }, state.eventsError ? EVENTS_RETRY_MS : EVENTS_REFRESH_MS);
}

async function loadWeather() {
  try {
    state.weather = await api("/api/weather");
    renderCurrentWeather();
    if (state.lastEvents) renderDays(state.lastEvents); // per-day forecasts
  } catch (e) {
    console.warn("loading weather failed", e);
  }
}

// Every 10 min normally, but every 15s until the Pi has weather, so it appears soon
// after the backend recovers instead of up to 10 minutes later.
function scheduleWeatherRefresh() {
  setTimeout(async () => {
    await loadWeather();
    scheduleWeatherRefresh();
  }, state.weather?.report ? WEATHER_REFRESH_MS : WEATHER_RETRY_MS);
}

// --- Navigation ----------------------------------------------------------------

function setStart(sunday) {
  state.start = sunday === weekStart(todayISO()) ? null : sunday;
  loadEvents();
}

const nav = {
  prev: () => setStart(addDays(currentStart(), -7)),
  next: () => setStart(addDays(currentStart(), 7)),
  today: () => setStart(weekStart(todayISO())),
  nextWeek: () => setStart(addDays(weekStart(todayISO()), 7)),
};

// --- Add-event dialog ------------------------------------------------------------

const dialog = $("#add-dialog");
const form = $("#add-form");

function pad(n) { return String(n).padStart(2, "0"); }

function setFormMode(editing) {
  state.editing = editing;
  $("#form-title").textContent = editing ? "Edit event" : "Add family event";
  $("#custom-repeat-note").hidden = true;
  $("#span-note").hidden = true;
  $("#guest-note").hidden = true;
  setGuestLock(false);
  form.recurrence.disabled = false;
  form.querySelector(".repeat-field").hidden = false;
}

// Events the Family calendar was only invited to: Google only lets us change who it's for.
function setGuestLock(locked) {
  for (const control of form.querySelectorAll("input, select, textarea")) {
    if (control.name !== "member") control.disabled = locked;
  }
}

function openAddDialog() {
  form.reset();
  setFormMode(null);
  $("#form-error").hidden = true;
  const today = todayISO();
  // Viewing another week: default to its Sunday (or today if today is in it).
  const start = currentStart();
  const inView = today >= start && today <= addDays(start, 6);
  form.date.value = inView ? today : start;
  const now = new Date();
  const startHour = Math.min(now.getHours() + 1, 22);
  form.start_time.value = `${pad(startHour)}:00`;
  form.end_time.value = `${pad(startHour + 1)}:00`;
  updateFormVisibility();
  dialog.showModal();
  form.title.focus();
}

function updateFormVisibility() {
  const allDay = form.all_day.checked;
  for (const field of form.querySelectorAll(".time-field")) field.hidden = allDay;
  const repeatHidden = form.querySelector(".repeat-field").hidden;
  form.querySelector(".repeat-until").hidden = repeatHidden || form.recurrence.value === "none";
}

function keepEndAfterStart() {
  const [h, m] = form.start_time.value.split(":").map(Number);
  if (Number.isNaN(h)) return;
  if (!form.end_time.value || form.end_time.value <= form.start_time.value) {
    form.end_time.value = `${pad(Math.min(h + 1, 23))}:${pad(h >= 23 ? 59 : m)}`;
  }
}

function formPayload() {
  const allDay = form.all_day.checked;
  const payload = {
    title: form.title.value,
    date: form.date.value,
    all_day: allDay,
    member: form.member.value || null,
    location: form.location.value || null,
    description: form.description.value || null,
    recurrence: form.recurrence.value,
  };
  if (!allDay) {
    payload.start_time = form.start_time.value || null;
    payload.end_time = form.end_time.value || null;
  }
  if (payload.recurrence !== "none" && form.recurrence_until.value) {
    payload.recurrence_until = form.recurrence_until.value;
  }
  return payload;
}

function validate(p) {
  if (!p.title.trim()) return "Please enter a title.";
  if (!p.date) return "Please pick a date.";
  if (!p.all_day) {
    if (!p.start_time || !p.end_time) return "Please enter start and end times, or tick All day.";
    if (p.end_time <= p.start_time) return "End time must be after the start time.";
  }
  if (p.recurrence_until && p.recurrence_until < p.date) return "Repeat-until must be on or after the date.";
  return null;
}

async function submitForm(event) {
  event.preventDefault();
  const payload = formPayload();
  const errorBox = $("#form-error");
  const problem = validate(payload);
  if (problem) {
    errorBox.textContent = problem;
    errorBox.hidden = false;
    return;
  }
  const save = $("#save");
  save.disabled = true;
  save.textContent = "Saving…";
  try {
    const editing = state.editing;
    const saved = await api(
      editing ? `/api/events/${encodeURIComponent(editing.id)}?scope=${editing.scope}` : "/api/events",
      {
        method: editing ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    dialog.close();
    showToast(editing ? `Saved “${saved.title}”` : `Added “${saved.title}”`);
    loadEvents();
  } catch (e) {
    errorBox.textContent = e.message;
    errorBox.hidden = false;
    if (e.status === 404) loadEvents(); // deleted elsewhere
  } finally {
    save.disabled = false;
    save.textContent = "Save (Enter)";
  }
}

// --- Event details, edit & delete ------------------------------------------------

const eventDialog = $("#event-dialog");

function whenText(ev) {
  const tz = state.config.timezone;
  const day = (d) => new Date(d).toLocaleDateString(undefined, { timeZone: tz, weekday: "long", month: "long", day: "numeric" });
  if (ev.all_day) {
    const last = new Date(Date.parse(ev.end) - 1); // end is exclusive
    const first = day(ev.start);
    return localDateOf(last) === localDateOf(ev.start) ? `${first} · all day` : `${first} – ${day(last)}`;
  }
  const times = ev.start === ev.end ? formatTime(ev.start) : `${formatTime(ev.start)} – ${formatTime(ev.end)}`;
  return `${day(ev.start)} · ${times}`;
}

function showDetailError(message) {
  const box = $("#detail-error");
  box.textContent = message;
  box.hidden = !message;
}

function resetDetailButtons() {
  const del = $("#detail-delete");
  del.textContent = "Delete";
  delete del.dataset.armed;
  delete del.dataset.scope;
  $("#scope-choice").hidden = true;
  state.scopeAction = null;
}

function openDetails(ev) {
  state.selected = ev;
  $("#detail-color").style.setProperty("--c", ev.color);
  $("#detail-title").textContent = ev.title;
  $("#detail-when").textContent = whenText(ev);
  $("#detail-repeat").hidden = !ev.recurring;
  $("#detail-who").textContent = ev.member ? `For ${ev.member}` : ev.calendar ? `From ${ev.calendar}` : "Whole family";
  const loc = $("#detail-location");
  loc.hidden = !ev.location;
  loc.textContent = ev.location ? `📍 ${ev.location}` : "";
  const notes = $("#detail-notes");
  notes.hidden = !ev.description;
  notes.textContent = ev.description || "";
  const note = $("#detail-note");
  const guest = ev.editable && ev.owned === false;
  note.hidden = ev.editable && !guest;
  note.textContent = !ev.editable
    ? `From ${ev.calendar || "another calendar"} (read-only)`
    : guest ? "Created by someone else: you can change who it's for." : "";
  $("#detail-edit").hidden = !ev.editable;
  $("#detail-delete").hidden = !ev.editable || guest;
  resetDetailButtons();
  showDetailError("");
  eventDialog.showModal();
  $(ev.editable ? "#detail-edit" : "#detail-close").focus();
}

// Repeating events: ask "This event" / "All events" first, then run the action with that scope.
function withScope(action, question) {
  const ev = state.selected;
  if (!ev.recurring) return action("this");
  state.scopeAction = action;
  $("#scope-question").textContent = question;
  $("#scope-choice").hidden = false;
  $("#scope-choice button").focus();
}

async function startEdit(scope) {
  const ev = state.selected;
  showDetailError("");
  let details;
  try {
    details = await api(`/api/events/${encodeURIComponent(ev.id)}`);
  } catch (e) {
    showDetailError(e.message);
    if (e.status === 404) loadEvents();
    return;
  }
  if (!details.form_editable) {
    showDetailError("Events that run past midnight can only be edited in Google Calendar. You can delete them here.");
    return;
  }
  eventDialog.close();
  form.reset();
  setFormMode({ id: ev.id, scope });
  $("#form-error").hidden = true;
  form.title.value = details.title;
  form.date.value = details.date;
  form.all_day.checked = details.all_day;
  form.start_time.value = details.start_time ? details.start_time.slice(0, 5) : "09:00";
  form.end_time.value = details.end_time ? details.end_time.slice(0, 5) : "10:00";
  form.member.value = details.member || "";
  form.location.value = details.location || "";
  form.description.value = details.description || "";
  const occurrenceOnly = details.recurring && scope === "this";
  form.querySelector(".repeat-field").hidden = occurrenceOnly || details.custom_repeat;
  $("#custom-repeat-note").hidden = occurrenceOnly || !details.custom_repeat;
  form.recurrence.value = occurrenceOnly || details.custom_repeat ? "none" : details.recurrence;
  form.recurrence_until.value = details.recurrence_until || "";
  if (details.all_day && details.span_days > 1) {
    $("#span-note").textContent = `Lasts ${details.span_days} days (kept when you change the date)`;
    $("#span-note").hidden = false;
  }
  if (details.owned === false) {
    setGuestLock(true);
    $("#guest-note").hidden = false;
  }
  updateFormVisibility();
  dialog.showModal();
  (details.owned === false ? form.member : form.title).focus();
}

async function deleteSelected(scope) {
  const ev = state.selected;
  try {
    await api(`/api/events/${encodeURIComponent(ev.id)}?scope=${scope}`, { method: "DELETE" });
    eventDialog.close();
    showToast(scope === "all" ? `Deleted all “${ev.title}” events` : `Deleted “${ev.title}”`);
    loadEvents();
  } catch (e) {
    showDetailError(e.message);
    resetDetailButtons();
    if (e.status === 404) loadEvents();
  }
}

// Two-step delete (no browser pop-ups on the kiosk): pick scope if repeating, then confirm.
function onDeleteClick() {
  const del = $("#detail-delete");
  if (del.dataset.armed) return deleteSelected(del.dataset.scope);
  withScope((scope) => {
    $("#scope-choice").hidden = true;
    del.dataset.armed = "1";
    del.dataset.scope = scope;
    del.textContent = scope === "all" ? "Really delete all events?" : "Really delete?";
    del.focus();
  }, "Delete which events?");
}

async function refreshNow() {
  if (state.refreshing) return;
  state.refreshing = true;
  renderSync({});
  try {
    await api("/api/refresh", { method: "POST" });
  } catch (e) {
    console.warn("refresh failed", e);
  } finally {
    state.refreshing = false;
  }
  await Promise.all([loadEvents(), loadWeather()]);
}

// --- Input handling ------------------------------------------------------------

function onKeyDown(e) {
  if (dialog.open || eventDialog.open || e.ctrlKey || e.metaKey || e.altKey) return;
  const key = e.key.toLowerCase();
  const actions = {
    arrowleft: nav.prev, arrowright: nav.next, t: nav.today, w: nav.nextWeek, n: openAddDialog,
    s: () => { location.href = "/settings"; },
    r: refreshNow,
  };
  if (actions[key]) {
    e.preventDefault();
    actions[key]();
  }
}

function noteInput() {
  state.lastInput = Date.now();
  document.body.classList.remove("hide-cursor");
}

function idleCheck() {
  const idle = Date.now() - state.lastInput;
  if (idle > CURSOR_HIDE_MS) document.body.classList.add("hide-cursor");
  if (idle > IDLE_RESET_MS && state.start !== null && !dialog.open) nav.today();
  if (idle > FILTER_RESET_MS && state.filter) setFilter(null);
}

// --- Start-up ------------------------------------------------------------------

async function init() {
  state.config = await api("/api/config");
  state.demo = (await api("/api/status")).demo;
  const select = $("#member-select");
  for (const m of state.config.members) select.append(new Option(m.name, m.name));
  renderLegend();
  renderClock();

  $("#prev").addEventListener("click", nav.prev);
  $("#next").addEventListener("click", nav.next);
  $("#today").addEventListener("click", nav.today);
  $("#next-week").addEventListener("click", nav.nextWeek);
  $("#add").addEventListener("click", openAddDialog);
  $("#cancel").addEventListener("click", () => dialog.close());
  form.addEventListener("submit", submitForm);
  const days = $("#days");
  days.addEventListener("click", (e) => {
    const node = e.target.closest("[data-key]");
    if (node) openDetails(state.eventsByKey.get(node.dataset.key));
  });
  days.addEventListener("keydown", (e) => {
    const node = e.target.closest("[data-key]");
    if (node && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      openDetails(state.eventsByKey.get(node.dataset.key));
    }
  });
  $("#detail-close").addEventListener("click", () => eventDialog.close());
  $("#detail-edit").addEventListener("click", () => withScope(startEdit, "Edit which events?"));
  $("#detail-delete").addEventListener("click", onDeleteClick);
  for (const button of document.querySelectorAll("#scope-choice button")) {
    button.addEventListener("click", () => state.scopeAction && state.scopeAction(button.dataset.scope));
  }
  $("#sync").addEventListener("click", refreshNow);
  $("#legend").addEventListener("click", (e) => {
    const item = e.target.closest(".legend-item");
    if (item) setFilter(item.dataset.kind, item.dataset.name);
  });
  form.all_day.addEventListener("change", updateFormVisibility);
  form.recurrence.addEventListener("change", updateFormVisibility);
  form.start_time.addEventListener("change", keepEndAfterStart);
  document.addEventListener("keydown", onKeyDown);
  for (const type of ["keydown", "mousemove", "mousedown", "wheel"]) {
    document.addEventListener(type, noteInput, { passive: true });
  }

  await loadWeather(); // before events so day columns get their forecast
  await loadEvents();
  setInterval(renderClock, 1000);
  scheduleEventsRefresh();
  scheduleWeatherRefresh();
  setInterval(idleCheck, 1000);
}

init().catch((e) => {
  console.error(e);
  // The backend may still be starting (e.g. right after boot): retry.
  document.body.replaceChildren(el("p", "empty", "Starting up…"));
  setTimeout(() => location.reload(), 5000);
});
