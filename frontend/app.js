const STORAGE_KEYS = {
  dietary: "ms_dietary_restrictions",
  favourites: "ms_favourite_foods",
  avoid: "ms_avoid_foods",
  foodLog: "ms_food_log"
};

const CHAT_API_URL = `${window.location.protocol}//${window.location.hostname || "127.0.0.1"}:5000/api/fetch/chat`;
const ORDERS_API_URL = `${window.location.protocol}//${window.location.hostname || "127.0.0.1"}:5000/api/fetch/orders`;
const DEFAULT_CALENDAR_HOUR = "12";
const HOUR_KEYS = Array.from({ length: 24 }, (_entry, index) => String(index).padStart(2, "0"));
const TIME_ROLLER_LOOP_COPIES = 3;
const TIME_ROLLER_MIDDLE_COPY = Math.floor(TIME_ROLLER_LOOP_COPIES / 2);
const TIME_ROLLER_WHEEL_GESTURE_MS = 220;
const TIME_ROLLER_SYNC_LOCK_MS = 180;

const DIETARY_FILTERS = [
  "Vegetarian",
  "Vegan",
  "Gluten-Free",
  "Dairy-Free",
  "Halal",
  "Nut-Free"
];

function loadList(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function loadObject(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function save(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function mergeNoteText(existingText, incomingText) {
  const existingLines = String(existingText || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const nextLines = String(incomingText || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  nextLines.forEach((line) => {
    if (!existingLines.includes(line)) {
      existingLines.push(line);
    }
  });

  return existingLines.join("\n");
}

function hasItem(list, value) {
  return list.some((item) => item.toLowerCase() === value.toLowerCase());
}

let dietaryRestrictions = loadList(STORAGE_KEYS.dietary);
let favouriteFoods = loadList(STORAGE_KEYS.favourites);
let avoidFoods = loadList(STORAGE_KEYS.avoid);
let foodLog = loadObject(STORAGE_KEYS.foodLog);

const thread = document.getElementById("imessage-thread");
const dietaryChips = document.getElementById("dietary-chips");
const dietaryCheckboxes = document.getElementById("dietary-checkboxes");
const dietaryInput = document.getElementById("dietary-input");
const dietaryCount = document.getElementById("dietary-count");
const monthLabel = document.getElementById("cal-month-label");
const calendarGrid = document.getElementById("calendar-grid");
const calendarDetailDate = document.getElementById("calendar-detail-date");
const calendarDetailLabel = document.getElementById("calendar-detail-label");
const calendarTimeRoller = document.getElementById("calendar-time-roller");
const calendarDetailInput = document.getElementById("calendar-detail-input");
const calendarSavedEntryList = document.getElementById("calendar-saved-entry-list");
const calendarEntryCount = document.getElementById("calendar-entry-count");
const calendarImportBtn = document.getElementById("calendar-import-btn");
const calendarImportStatus = document.getElementById("calendar-import-status");
const aiStateNode = document.getElementById("fetch-ai-state");

const initialMessages = [
  { from: "received", text: "Hi, this is Fetch. What sounds good today?" },
  { from: "sent", text: "Keep it dairy-free and show my comfort food options." },
  { from: "received", text: "Done. I’ll favor the meals you love and filter everything else." }
];

let chatHistory = [];
let imessageBusy = false;
let ordersImportBusy = false;
let timeRollerSyncLocked = false;
let timeRollerWheelGestureActive = false;
let timeRollerWheelGestureTimer = 0;
let timeRollerSyncUnlockTimer = 0;

const today = new Date();
let viewYear = today.getFullYear();
let viewMonth = today.getMonth();
let selectedDateKey = dateKey(viewYear, viewMonth, today.getDate());
let selectedCalendarHour = DEFAULT_CALENDAR_HOUR;

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"
];

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function addBubble(from, text) {
  const div = document.createElement("div");
  div.className = `bubble ${from}`;
  div.textContent = text;
  thread.appendChild(div);
  thread.scrollTop = thread.scrollHeight;
  chatHistory.push({
    role: from === "sent" ? "user" : "assistant",
    text
  });
}

function setImessageBusy(isBusy) {
  imessageBusy = isBusy;
  document.getElementById("imessage-send").disabled = isBusy;
  document.getElementById("imessage-input").disabled = isBusy;
}

function setOrdersImportStatus(message, state = "idle") {
  calendarImportStatus.textContent = message;
  calendarImportStatus.dataset.state = state;
}

function isValidHourKey(value) {
  return /^(?:[01]\d|2[0-3])$/.test(value);
}

function cloneCalendarEntries(entries) {
  return Object.fromEntries(
    Object.entries(entries).map(([date, schedule]) => [date, { ...schedule }])
  );
}

function normalizeDaySchedule(value) {
  const normalized = {};

  if (typeof value === "string") {
    const text = value.trim();
    if (text) {
      normalized[DEFAULT_CALENDAR_HOUR] = text;
    }
    return normalized;
  }

  if (!value || typeof value !== "object") {
    return normalized;
  }

  Object.entries(value).forEach(([hour, note]) => {
    if (!isValidHourKey(hour)) {
      return;
    }

    const text = String(note ?? "").trim();
    if (text) {
      normalized[hour] = text;
    }
  });

  return normalized;
}

function getDaySchedule(date) {
  return normalizeDaySchedule(foodLog[date]);
}

function hasDayEntries(schedule) {
  return Object.keys(schedule).length > 0;
}

function formatHourLabel(hour) {
  const hourValue = Number(hour);
  const displayHour = hourValue % 12 || 12;
  return `${displayHour}:00`;
}

function formatHourPeriod(hour) {
  return Number(hour) >= 12 ? "PM" : "AM";
}

function formatHourOptionLabel(hour) {
  return `${formatHourLabel(hour)} ${formatHourPeriod(hour)}`;
}

function buildDayPreview(schedule) {
  const hours = Object.keys(schedule).sort((left, right) => left.localeCompare(right));
  if (hours.length === 0) {
    return "";
  }

  const firstHour = hours[0];
  const firstNote = schedule[firstHour];
  const suffix = hours.length > 1 ? ` +${hours.length - 1} more` : "";
  return `${formatHourLabel(firstHour)} ${firstNote}${suffix}`;
}

function focusCalendarDetailInput() {
  calendarDetailInput.focus();
}

function updateTimeRollerSelection() {
  calendarTimeRoller.querySelectorAll(".calendar-time-option").forEach((button) => {
    const isActive = button.dataset.hour === selectedCalendarHour;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });
}

function getPreferredRollerButton(hour) {
  return calendarTimeRoller.querySelector(
    `.calendar-time-option[data-hour="${hour}"][data-copy="${TIME_ROLLER_MIDDLE_COPY}"]`
  );
}

function getClosestRollerButton() {
  const rollerRect = calendarTimeRoller.getBoundingClientRect();
  const rollerCenter = rollerRect.top + (rollerRect.height / 2);
  let closestButton = null;
  let closestDistance = Number.POSITIVE_INFINITY;

  calendarTimeRoller.querySelectorAll(".calendar-time-option").forEach((button) => {
    const rect = button.getBoundingClientRect();
    const distance = Math.abs((rect.top + (rect.height / 2)) - rollerCenter);

    if (distance < closestDistance) {
      closestDistance = distance;
      closestButton = button;
    }
  });

  return closestButton;
}

function scrollTimeRollerToHour(hour, behavior = "smooth") {
  const button = getPreferredRollerButton(hour);
  if (!button) {
    return;
  }

  const targetTop = button.offsetTop - ((calendarTimeRoller.clientHeight - button.offsetHeight) / 2);
  const nextTop = Math.max(targetTop, 0);

  if (behavior === "auto") {
    calendarTimeRoller.scrollTop = nextTop;
    return;
  }

  // VS Code's embedded browser can ignore smooth scrolling on this container,
  // so set the target position directly to keep the selected hour and the
  // visible centered hour in sync.
  calendarTimeRoller.scrollTop = nextTop;
}

function recenterTimeRoller(button) {
  if (!button) {
    return;
  }

  const currentCopy = Number(button.dataset.copy);
  if (currentCopy === TIME_ROLLER_MIDDLE_COPY) {
    return;
  }

  const middleButton = getPreferredRollerButton(button.dataset.hour);
  if (!middleButton) {
    return;
  }

  calendarTimeRoller.scrollTop += middleButton.offsetTop - button.offsetTop;
}

function lockTimeRollerSync(duration = TIME_ROLLER_SYNC_LOCK_MS) {
  timeRollerSyncLocked = true;
  window.clearTimeout(timeRollerSyncUnlockTimer);
  timeRollerSyncUnlockTimer = window.setTimeout(() => {
    timeRollerSyncLocked = false;
  }, duration);
}

function isTimeRollerWheelLocked() {
  return calendarTimeRoller.dataset.wheelLocked === "true";
}

function lockTimeRollerWheel() {
  calendarTimeRoller.dataset.wheelLocked = "true";
  timeRollerWheelGestureActive = true;
  window.clearTimeout(timeRollerWheelGestureTimer);
  timeRollerWheelGestureTimer = window.setTimeout(() => {
    delete calendarTimeRoller.dataset.wheelLocked;
    timeRollerWheelGestureActive = false;
  }, TIME_ROLLER_WHEEL_GESTURE_MS);
}

function stepCalendarHour(direction) {
  const currentIndex = HOUR_KEYS.indexOf(selectedCalendarHour);
  const safeIndex = currentIndex >= 0 ? currentIndex : HOUR_KEYS.indexOf(DEFAULT_CALENDAR_HOUR);
  const nextIndex = (safeIndex + direction + HOUR_KEYS.length) % HOUR_KEYS.length;
  setSelectedCalendarHour(HOUR_KEYS[nextIndex], { scroll: true, behavior: "auto", lockSync: true });
}

function handleTimeRollerWheel(event) {
  event.preventDefault();

  if (timeRollerWheelGestureActive || isTimeRollerWheelLocked()) {
    lockTimeRollerWheel();
    setSelectedCalendarHour(selectedCalendarHour, { scroll: true, behavior: "auto", lockSync: true });
    return;
  }

  if (event.deltaY === 0) {
    return;
  }

  const direction = event.deltaY > 0 ? 1 : -1;
  lockTimeRollerWheel();
  stepCalendarHour(direction);
}

function setSelectedCalendarHour(hour, { scroll = false, focusInput = false, behavior = "smooth", lockSync = false } = {}) {
  if (!isValidHourKey(hour)) {
    return;
  }

  selectedCalendarHour = hour;
  const schedule = getDaySchedule(selectedDateKey);

  calendarDetailInput.value = schedule[hour] || "";
  updateTimeRollerSelection();
  renderSavedCalendarEntries(schedule);

  if (scroll) {
    if (lockSync) {
      lockTimeRollerSync();
    }
    scrollTimeRollerToHour(hour, behavior);
  }

  if (focusInput) {
    focusCalendarDetailInput();
  }
}

function syncCalendarHourFromRoller() {
  if (timeRollerSyncLocked) {
    return;
  }

  const closestButton = getClosestRollerButton();
  if (!closestButton) {
    return;
  }

  if (Number(closestButton.dataset.copy) !== TIME_ROLLER_MIDDLE_COPY) {
    lockTimeRollerSync(0);
    recenterTimeRoller(closestButton);
  }

  const closestHour = closestButton.dataset.hour;
  if (closestHour === selectedCalendarHour) {
    return;
  }

  setSelectedCalendarHour(closestHour);
}

function populateCalendarHourOptions() {
  calendarTimeRoller.innerHTML = "";

  for (let copy = 0; copy < TIME_ROLLER_LOOP_COPIES; copy += 1) {
    HOUR_KEYS.forEach((hour) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "calendar-time-option";
      button.dataset.hour = hour;
      button.dataset.copy = String(copy);
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", "false");

      const timeValue = document.createElement("span");
      timeValue.className = "calendar-time-option-value";
      timeValue.textContent = formatHourLabel(hour);

      const periodValue = document.createElement("span");
      periodValue.className = "calendar-time-option-period";
      periodValue.textContent = formatHourPeriod(hour);

      button.appendChild(timeValue);
      button.appendChild(periodValue);
      const handlePress = (event) => {
        event.preventDefault();
        setSelectedCalendarHour(hour, { scroll: true, focusInput: true, behavior: "auto" });
      };

      button.addEventListener("pointerdown", handlePress);
      button.addEventListener("mousedown", handlePress);
      button.addEventListener("click", handlePress);

      calendarTimeRoller.appendChild(button);
    });
  }

  updateTimeRollerSelection();
}

function renderSavedCalendarEntries(schedule) {
  const filledHours = Object.keys(schedule).sort((left, right) => left.localeCompare(right));

  calendarSavedEntryList.innerHTML = "";

  if (filledHours.length === 0) {
    calendarSavedEntryList.innerHTML = '<span class="empty-hint">No saved times for this day yet.</span>';
    return;
  }

  filledHours.forEach((hour) => {
    const entryButton = document.createElement("button");
    entryButton.type = "button";
    entryButton.className = `calendar-saved-entry${hour === selectedCalendarHour ? " active" : ""}`;

    const time = document.createElement("span");
    time.className = "calendar-saved-entry-time";
    time.textContent = formatHourOptionLabel(hour);

    const note = document.createElement("span");
    note.className = "calendar-saved-entry-note";
    note.textContent = schedule[hour];

    entryButton.appendChild(time);
    entryButton.appendChild(note);
    entryButton.addEventListener("click", () => {
      setSelectedCalendarHour(hour, { scroll: true, focusInput: true });
    });

    calendarSavedEntryList.appendChild(entryButton);
  });
}

function syncCalendarDetailForHour() {
  const schedule = getDaySchedule(selectedDateKey);
  const filledHours = Object.keys(schedule).sort((left, right) => left.localeCompare(right));

  if (!isValidHourKey(selectedCalendarHour)) {
    selectedCalendarHour = DEFAULT_CALENDAR_HOUR;
  }

  if (!schedule[selectedCalendarHour] && filledHours.length > 0) {
    selectedCalendarHour = filledHours[0];
  }

  setSelectedCalendarHour(selectedCalendarHour, { scroll: true, behavior: "auto" });
}

function saveSelectedCalendarHour() {
  const hour = selectedCalendarHour;

  if (!isValidHourKey(hour)) {
    return;
  }

  selectedCalendarHour = hour;
  upsertCalendarEntry(selectedDateKey, calendarDetailInput.value, hour);
}

function clearSelectedCalendarHour() {
  const hour = selectedCalendarHour;

  if (!isValidHourKey(hour)) {
    return;
  }

  selectedCalendarHour = hour;
  upsertCalendarEntry(selectedDateKey, "", hour);
}

function applyCalendarUpdates(updates, selectedDate) {
  let calendarChanged = false;
  let selectionChanged = false;

  if (Array.isArray(updates) && updates.length > 0) {
    updates.forEach((item) => {
      if (!item || !isValidDateKey(item.date)) {
        return;
      }

      if (item.action === "remove") {
        if (Object.prototype.hasOwnProperty.call(foodLog, item.date)) {
          delete foodLog[item.date];
          calendarChanged = true;
        }
        return;
      }

      const text = String(item.note ?? "").trim();
      const daySchedule = getDaySchedule(item.date);
      if (text && daySchedule[DEFAULT_CALENDAR_HOUR] !== text) {
        daySchedule[DEFAULT_CALENDAR_HOUR] = text;
        foodLog[item.date] = daySchedule;
        calendarChanged = true;
      }
    });
  }

  if (selectedDate && isValidDateKey(selectedDate)) {
    selectedDateKey = selectedDate;
    const [year, month] = selectedDate.split("-").map(Number);
    viewYear = year;
    viewMonth = month - 1;
    selectionChanged = true;
  }

  if (calendarChanged) {
    save(STORAGE_KEYS.foodLog, foodLog);
  }

  return { calendarChanged, selectionChanged };
}

function getPreferenceStorageKey(listName) {
  if (listName === "dietaryRestrictions") {
    return STORAGE_KEYS.dietary;
  }

  if (listName === "favouriteFoods") {
    return STORAGE_KEYS.favourites;
  }

  if (listName === "avoidFoods") {
    return STORAGE_KEYS.avoid;
  }

  return null;
}

function getPreferenceItems(listName) {
  if (listName === "dietaryRestrictions") {
    return dietaryRestrictions;
  }

  if (listName === "favouriteFoods") {
    return favouriteFoods;
  }

  if (listName === "avoidFoods") {
    return avoidFoods;
  }

  return null;
}

function normalizePreferenceComparisonValue(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/\b(filters?|restrictions?)\b/g, " ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function canonicalizePreferenceValue(listName, value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "";
  }

  if (listName !== "dietaryRestrictions") {
    return text;
  }

  const normalized = normalizePreferenceComparisonValue(text);
  const knownMatch = DIETARY_FILTERS.find((item) => normalizePreferenceComparisonValue(item) === normalized);
  if (knownMatch) {
    return knownMatch;
  }

  const existingMatch = dietaryRestrictions.find((item) => normalizePreferenceComparisonValue(item) === normalized);
  return existingMatch || text;
}

function setPreferenceItems(listName, items) {
  if (listName === "dietaryRestrictions") {
    dietaryRestrictions = items;
    return;
  }

  if (listName === "favouriteFoods") {
    favouriteFoods = items;
    return;
  }

  if (listName === "avoidFoods") {
    avoidFoods = items;
  }
}

function applyPreferenceUpdates(updates) {
  const changedLists = new Set();

  if (!Array.isArray(updates) || updates.length === 0) {
    return false;
  }

  updates.forEach((item) => {
    if (!item || typeof item !== "object") {
      return;
    }

    const listName = item.list;
    const action = item.action;
    const value = canonicalizePreferenceValue(listName, item.value);
    const items = getPreferenceItems(listName);

    if (!items || !value) {
      return;
    }

    if (action === "add") {
      if (!hasItem(items, value)) {
        setPreferenceItems(listName, [...items, value]);
        changedLists.add(listName);
      }
      return;
    }

    if (action === "remove") {
      const nextItems = items.filter((entry) => entry.toLowerCase() !== value.toLowerCase());
      if (nextItems.length !== items.length) {
        setPreferenceItems(listName, nextItems);
        changedLists.add(listName);
      }
      return;
    }

    if (action === "toggle") {
      if (hasItem(items, value)) {
        const nextItems = items.filter((entry) => entry.toLowerCase() !== value.toLowerCase());
        setPreferenceItems(listName, nextItems);
      } else {
        setPreferenceItems(listName, [...items, value]);
      }
      changedLists.add(listName);
    }
  });

  changedLists.forEach((listName) => {
    const storageKey = getPreferenceStorageKey(listName);
    const items = getPreferenceItems(listName);
    if (storageKey && items) {
      save(storageKey, items);
    }
  });

  return changedLists.size > 0;
}

function applyAssistantUpdates(payload) {
  const preferenceChanged = applyPreferenceUpdates(payload?.preference_updates);
  const { calendarChanged, selectionChanged } = applyCalendarUpdates(
    payload?.calendar_updates,
    payload?.selected_date
  );

  if (preferenceChanged || calendarChanged || selectionChanged) {
    syncAllViews();
  }
}

async function requestChatReply(message) {
  const response = await fetch(CHAT_API_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      message,
      conversation: chatHistory,
      state: buildAiPayload()
    })
  });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(payload?.error || "Fetch AI could not answer right now.");
  }

  return payload;
}

async function sendImessage() {
  const input = document.getElementById("imessage-input");
  const text = input.value.trim();
  if (!text || imessageBusy) {
    return;
  }

  addBubble("sent", text);
  input.value = "";

  setImessageBusy(true);
  try {
    const payload = await requestChatReply(text);
    applyAssistantUpdates(payload);
    addBubble("received", payload.reply || "I updated that for you.");
  } catch (error) {
    addBubble("received", error.message || "I hit a snag trying to help with that.");
  } finally {
    setImessageBusy(false);
    input.focus();
  }
}

function updateClock() {
  const now = new Date();
  const clock = document.getElementById("clock");
  let hours = now.getHours();
  const minutes = now.getMinutes().toString().padStart(2, "0");
  hours = hours % 12 || 12;
  clock.textContent = `${hours}:${minutes}`;
}

function dateKey(year, month, day) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function formatDateLabel(key) {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric"
  });
}

function isValidDateKey(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function sortDateKeys(entries) {
  return Object.keys(entries).sort((left, right) => left.localeCompare(right));
}

function buildAiPayload() {
  const sortedEntries = {};

  sortDateKeys(foodLog).forEach((key) => {
    sortedEntries[key] = getDaySchedule(key);
  });

  return {
    app: "Fetch",
    version: 1,
    exportedAt: new Date().toISOString(),
    selectedDate: selectedDateKey,
    preferences: {
      dietaryRestrictions: [...dietaryRestrictions],
      favouriteFoods: [...favouriteFoods],
      avoidFoods: [...avoidFoods]
    },
    calendar: {
      entries: sortedEntries
    }
  };
}

function syncAiBridge() {
  const payload = buildAiPayload();
  const formatted = JSON.stringify(payload, null, 2);

  if (aiStateNode) {
    aiStateNode.textContent = formatted;
  }

  window.dispatchEvent(new CustomEvent("fetch:state-updated", {
    detail: payload
  }));
}

function syncAllViews() {
  renderDietaryCheckboxes();
  renderDietaryChips();
  renderCalendar();
  updateCalendarDetail();
  syncAiBridge();
}

function normalizeStringArray(value) {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((item) => String(item).trim())
    .filter((item, index, source) => item && source.findIndex((entry) => entry.toLowerCase() === item.toLowerCase()) === index);
}

function normalizeCalendarEntries(value) {
  const normalized = {};

  if (!value || typeof value !== "object") {
    return normalized;
  }

  Object.entries(value).forEach(([key, entry]) => {
    if (!isValidDateKey(key)) {
      return;
    }

    const schedule = normalizeDaySchedule(entry);
    if (hasDayEntries(schedule)) {
      normalized[key] = schedule;
    }
  });

  return normalized;
}

function applyAiPayload(payload) {
  if (!payload || typeof payload !== "object") {
    throw new Error("Payload must be a JSON object.");
  }

  const nextDietary = normalizeStringArray(payload.preferences?.dietaryRestrictions);
  const nextFavourites = normalizeStringArray(payload.preferences?.favouriteFoods);
  const nextAvoid = normalizeStringArray(payload.preferences?.avoidFoods);
  const nextCalendar = normalizeCalendarEntries(payload.calendar?.entries);

  dietaryRestrictions = nextDietary;
  favouriteFoods = nextFavourites;
  avoidFoods = nextAvoid;
  foodLog = nextCalendar;

  save(STORAGE_KEYS.dietary, dietaryRestrictions);
  save(STORAGE_KEYS.favourites, favouriteFoods);
  save(STORAGE_KEYS.avoid, avoidFoods);
  save(STORAGE_KEYS.foodLog, foodLog);

  if (payload.selectedDate && isValidDateKey(payload.selectedDate)) {
    selectedDateKey = payload.selectedDate;
    const [year, month] = payload.selectedDate.split("-").map(Number);
    viewYear = year;
    viewMonth = month - 1;
  } else if (!isValidDateKey(selectedDateKey)) {
    selectedDateKey = dateKey(today.getFullYear(), today.getMonth(), today.getDate());
  }

  syncAllViews();
}

function upsertCalendarEntry(date, note, hour = DEFAULT_CALENDAR_HOUR) {
  if (!isValidDateKey(date)) {
    throw new Error("Date must use YYYY-MM-DD.");
  }

  if (!isValidHourKey(hour)) {
    throw new Error("Hour must use HH between 00 and 23.");
  }

  const text = String(note ?? "").trim();
  const daySchedule = getDaySchedule(date);

  if (text) {
    daySchedule[hour] = text;
    foodLog[date] = daySchedule;
  } else {
    delete daySchedule[hour];
    if (hasDayEntries(daySchedule)) {
      foodLog[date] = daySchedule;
    } else {
      delete foodLog[date];
    }
  }

  selectedDateKey = date;
  const [year, month] = date.split("-").map(Number);
  viewYear = year;
  viewMonth = month - 1;

  save(STORAGE_KEYS.foodLog, foodLog);
  syncAllViews();
}

function scheduleCalendarEntries(entries) {
  const normalized = normalizeCalendarEntries(entries);

  Object.entries(normalized).forEach(([date, schedule]) => {
    const nextSchedule = getDaySchedule(date);

    Object.entries(schedule).forEach(([hour, note]) => {
      nextSchedule[hour] = note;
    });

    foodLog[date] = nextSchedule;
  });

  save(STORAGE_KEYS.foodLog, foodLog);
  syncAllViews();
}

function mergeCalendarEntries(entries) {
  const normalized = normalizeCalendarEntries(entries);
  let changed = false;

  Object.entries(normalized).forEach(([date, schedule]) => {
    const currentSchedule = getDaySchedule(date);

    Object.entries(schedule).forEach(([hour, note]) => {
      const merged = mergeNoteText(currentSchedule[hour], note);
      if (merged && merged !== currentSchedule[hour]) {
        currentSchedule[hour] = merged;
        changed = true;
      }
    });

    if (hasDayEntries(currentSchedule)) {
      foodLog[date] = currentSchedule;
    }
  });

  if (!changed) {
    return 0;
  }

  save(STORAGE_KEYS.foodLog, foodLog);
  syncAllViews();
  return Object.keys(normalized).length;
}

async function importOrdersFromServer() {
  const response = await fetch(ORDERS_API_URL, {
    method: "GET",
    headers: {
      Accept: "application/json"
    }
  });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(payload?.error || "Fetch could not read your saved orders right now.");
  }

  const importedDays = Number(payload?.count) || Object.keys(payload?.entries || {}).length;
  const changedDays = mergeCalendarEntries(payload?.entries || {});

  return {
    importedDays,
    changedDays
  };
}

async function runOrdersImport() {
  if (ordersImportBusy) {
    return;
  }

  ordersImportBusy = true;
  calendarImportBtn.disabled = true;
  setOrdersImportStatus("Importing past orders into the calendar...", "loading");

  try {
    const result = await importOrdersFromServer();

    if (result.importedDays === 0) {
      setOrdersImportStatus("No past orders were found for this account.", "idle");
    } else if (result.changedDays === 0) {
      setOrdersImportStatus(`Past orders are already synced for ${result.importedDays} day${result.importedDays === 1 ? "" : "s"}.`, "success");
    } else {
      setOrdersImportStatus(`Imported past orders into ${result.changedDays} calendar day${result.changedDays === 1 ? "" : "s"}.`, "success");
    }

    return result;
  } catch (error) {
    setOrdersImportStatus(error.message || "Fetch could not read your saved orders right now.", "error");
    throw error;
  } finally {
    ordersImportBusy = false;
    calendarImportBtn.disabled = false;
  }
}

function updateDietaryStorage(nextItems) {
  dietaryRestrictions = nextItems;
  save(STORAGE_KEYS.dietary, dietaryRestrictions);
  renderDietaryCheckboxes();
  renderDietaryChips();
  syncAiBridge();
}

function toggleDietaryRestriction(value) {
  if (hasItem(dietaryRestrictions, value)) {
    updateDietaryStorage(dietaryRestrictions.filter((item) => item.toLowerCase() !== value.toLowerCase()));
    return;
  }
  updateDietaryStorage([...dietaryRestrictions, value]);
}

function renderDietaryCheckboxes() {
  dietaryCheckboxes.innerHTML = "";

  DIETARY_FILTERS.forEach((item) => {
    const label = document.createElement("label");
    const checked = hasItem(dietaryRestrictions, item);
    label.className = `filter-option${checked ? " active" : ""}`;

    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = checked;
    input.addEventListener("change", () => toggleDietaryRestriction(item));

    const box = document.createElement("span");
    box.className = "filter-option-box";

    const text = document.createElement("span");
    text.className = "filter-option-text";
    text.textContent = item;

    label.appendChild(input);
    label.appendChild(box);
    label.appendChild(text);
    dietaryCheckboxes.appendChild(label);
  });
}

function renderDietaryChips() {
  dietaryChips.innerHTML = "";
  dietaryCount.textContent = `${dietaryRestrictions.length} selected`;

  if (dietaryRestrictions.length === 0) {
    dietaryChips.innerHTML = '<span class="empty-hint">No dietary filters selected yet.</span>';
    return;
  }

  dietaryRestrictions.forEach((item) => {
    const chip = document.createElement("div");
    chip.className = "chip";

    const text = document.createElement("span");
    text.textContent = item;

    const button = document.createElement("button");
    button.type = "button";
    button.title = `Remove ${item}`;
    button.innerHTML = "&times;";
    button.addEventListener("click", () => {
      updateDietaryStorage(dietaryRestrictions.filter((entry) => entry.toLowerCase() !== item.toLowerCase()));
    });

    chip.appendChild(text);
    chip.appendChild(button);
    dietaryChips.appendChild(chip);
  });
}

function addDietaryRestriction() {
  const value = dietaryInput.value.trim();
  if (!value || hasItem(dietaryRestrictions, value)) {
    dietaryInput.value = "";
    return;
  }

  updateDietaryStorage([...dietaryRestrictions, value]);
  dietaryInput.value = "";
}

function setupItemList({ listElId, inputElId, addBtnId, storageKey, getArray, setArray }) {
  const listEl = document.getElementById(listElId);
  const inputEl = document.getElementById(inputElId);

  function render() {
    const items = getArray();
    listEl.innerHTML = "";

    if (items.length === 0) {
      const li = document.createElement("li");
      li.className = "empty-hint";
      li.textContent = "Nothing added yet.";
      li.style.background = "transparent";
      li.style.border = "0";
      li.style.padding = "0";
      listEl.appendChild(li);
      return;
    }

    items.forEach((item, index) => {
      const li = document.createElement("li");
      const span = document.createElement("span");
      span.textContent = item;

      const button = document.createElement("button");
      button.className = "drop-btn";
      button.type = "button";
      button.textContent = "Drop";
      button.addEventListener("click", () => {
        const next = [...getArray()];
        next.splice(index, 1);
        setArray(next);
        save(storageKey, next);
        render();
        syncAiBridge();
      });

      li.appendChild(span);
      li.appendChild(button);
      listEl.appendChild(li);
    });
  }

  function addItem() {
    const value = inputEl.value.trim();
    const items = getArray();

    if (!value || hasItem(items, value)) {
      inputEl.value = "";
      return;
    }

    const next = [...items, value];
    setArray(next);
    save(storageKey, next);
    inputEl.value = "";
    render();
    syncAiBridge();
  }

  document.getElementById(addBtnId).addEventListener("click", addItem);
  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      addItem();
    }
  });

  render();
}

function getMonthEntryCount() {
  const prefix = `${viewYear}-${String(viewMonth + 1).padStart(2, "0")}-`;
  return Object.keys(foodLog).filter((key) => key.startsWith(prefix)).length;
}

function updateCalendarDetail() {
  const schedule = getDaySchedule(selectedDateKey);
  const filledHours = Object.keys(schedule).sort((left, right) => left.localeCompare(right));

  calendarDetailDate.textContent = formatDateLabel(selectedDateKey);
  calendarDetailLabel.textContent = filledHours.length > 0
    ? `${filledHours.length} saved time${filledHours.length === 1 ? "" : "s"}. Pick a time to edit its note.`
    : "Pick a time, then add a meal, prep note, or reminder in the box below.";

  syncCalendarDetailForHour();
}

function renderCalendar() {
  monthLabel.textContent = `${MONTH_NAMES[viewMonth]} ${viewYear}`;
  calendarEntryCount.textContent = String(getMonthEntryCount());
  calendarGrid.innerHTML = "";

  const firstWeekday = new Date(viewYear, viewMonth, 1).getDay();
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();

  for (let index = 0; index < firstWeekday; index += 1) {
    const filler = document.createElement("div");
    filler.className = "cal-day empty";
    calendarGrid.appendChild(filler);
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    const key = dateKey(viewYear, viewMonth, day);
    const weekday = new Date(viewYear, viewMonth, day).getDay();
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cal-day";

    if (
      viewYear === today.getFullYear() &&
      viewMonth === today.getMonth() &&
      day === today.getDate()
    ) {
      button.classList.add("today");
    }

    if (key === selectedDateKey) {
      button.classList.add("selected");
    }

    const top = document.createElement("div");
    top.className = "cal-day-top";

    const number = document.createElement("span");
    number.className = "cal-day-number";
    number.textContent = String(day);

    const week = document.createElement("span");
    week.className = "cal-day-week";
    week.textContent = WEEKDAY_SHORT[weekday];

    top.appendChild(number);
    top.appendChild(week);
    button.appendChild(top);

    const body = document.createElement("div");
    body.className = "cal-day-body";

    const schedule = getDaySchedule(key);

    if (hasDayEntries(schedule)) {
      const preview = document.createElement("span");
      preview.className = "cal-day-preview";
      preview.textContent = buildDayPreview(schedule);

      const marker = document.createElement("span");
      marker.className = "cal-day-note";

      body.appendChild(preview);
      body.appendChild(marker);
    }

    button.appendChild(body);

    button.addEventListener("click", () => {
      selectedDateKey = key;
      renderCalendar();
      updateCalendarDetail();
      focusCalendarDetailInput();
    });

    calendarGrid.appendChild(button);
  }
}

function changeMonth(offset) {
  viewMonth += offset;

  if (viewMonth < 0) {
    viewMonth = 11;
    viewYear -= 1;
  }

  if (viewMonth > 11) {
    viewMonth = 0;
    viewYear += 1;
  }

  selectedDateKey = dateKey(viewYear, viewMonth, 1);
  renderCalendar();
  updateCalendarDetail();
}

document.getElementById("imessage-send").addEventListener("click", sendImessage);
document.getElementById("imessage-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    sendImessage();
  }
});

document.getElementById("dietary-add-btn").addEventListener("click", addDietaryRestriction);
dietaryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    addDietaryRestriction();
  }
});

calendarTimeRoller.addEventListener("scroll", syncCalendarHourFromRoller);
calendarTimeRoller.addEventListener("wheel", handleTimeRollerWheel, { passive: false });

document.getElementById("cal-prev").addEventListener("click", () => changeMonth(-1));
document.getElementById("cal-next").addEventListener("click", () => changeMonth(1));
calendarImportBtn.addEventListener("click", () => {
  runOrdersImport().catch(() => {});
});

document.getElementById("calendar-save-btn").addEventListener("click", () => {
  saveSelectedCalendarHour();
});

document.getElementById("calendar-clear-btn").addEventListener("click", () => {
  clearSelectedCalendarHour();
});

window.fetchDataBridge = {
  exportState: () => buildAiPayload(),
  importState: (payload) => applyAiPayload(payload),
  getCalendarEntries: () => cloneCalendarEntries(foodLog),
  scheduleCalendarEntry: (date, note, hour = DEFAULT_CALENDAR_HOUR) => upsertCalendarEntry(date, note, hour),
  scheduleCalendarEntries: (entries) => scheduleCalendarEntries(entries),
  importOrdersFromServer: () => importOrdersFromServer().then((result) => result.changedDays),
  upsertCalendarEntry: (date, note, hour = DEFAULT_CALENDAR_HOUR) => upsertCalendarEntry(date, note, hour),
  clearCalendarEntry: (date) => {
    delete foodLog[date];
    save(STORAGE_KEYS.foodLog, foodLog);
    syncAllViews();
  },
  clearCalendarHour: (date, hour) => upsertCalendarEntry(date, "", hour),
  selectDate: (date) => {
    if (!isValidDateKey(date)) {
      throw new Error("Date must use YYYY-MM-DD.");
    }
    selectedDateKey = date;
    const [year, month] = date.split("-").map(Number);
    viewYear = year;
    viewMonth = month - 1;
    syncAllViews();
  }
};

window.addEventListener("fetch:schedule-calendar-entry", (event) => {
  if (!event.detail) {
    return;
  }

  const { date, note } = event.detail;
  upsertCalendarEntry(date, note);
});

window.addEventListener("fetch:schedule-calendar-entries", (event) => {
  if (!event.detail) {
    return;
  }

  scheduleCalendarEntries(event.detail);
});

window.addEventListener("fetch:import-state", (event) => {
  if (!event.detail) {
    return;
  }

  applyAiPayload(event.detail);
});

setupItemList({
  listElId: "favourite-list",
  inputElId: "favourite-input",
  addBtnId: "favourite-add-btn",
  storageKey: STORAGE_KEYS.favourites,
  getArray: () => favouriteFoods,
  setArray: (items) => {
    favouriteFoods = items;
  }
});

setupItemList({
  listElId: "avoid-list",
  inputElId: "avoid-input",
  addBtnId: "avoid-add-btn",
  storageKey: STORAGE_KEYS.avoid,
  getArray: () => avoidFoods,
  setArray: (items) => {
    avoidFoods = items;
  }
});

initialMessages.forEach((message) => addBubble(message.from, message.text));
updateClock();
setInterval(updateClock, 30000);
populateCalendarHourOptions();
renderDietaryCheckboxes();
renderDietaryChips();
renderCalendar();
updateCalendarDetail();
syncAiBridge();
runOrdersImport().catch((error) => {
  console.warn(error.message || "Order import failed.");
});
