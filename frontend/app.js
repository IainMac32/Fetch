const STORAGE_KEYS = {
  dietary: "ms_dietary_restrictions",
  favourites: "ms_favourite_foods",
  avoid: "ms_avoid_foods",
  foodLog: "ms_food_log"
};

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
const calendarDetailInput = document.getElementById("calendar-detail-input");
const calendarEntryCount = document.getElementById("calendar-entry-count");

const initialMessages = [
  { from: "received", text: "Hi, this is Fetch. What sounds good today?" },
  { from: "sent", text: "Keep it dairy-free and show my comfort food options." },
  { from: "received", text: "Done. I’ll favor the meals you love and filter everything else." }
];

const today = new Date();
let viewYear = today.getFullYear();
let viewMonth = today.getMonth();
let selectedDateKey = dateKey(viewYear, viewMonth, today.getDate());

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
}

function sendImessage() {
  const input = document.getElementById("imessage-input");
  const text = input.value.trim();
  if (!text) {
    return;
  }
  addBubble("sent", text);
  input.value = "";
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

function updateDietaryStorage(nextItems) {
  dietaryRestrictions = nextItems;
  save(STORAGE_KEYS.dietary, dietaryRestrictions);
  renderDietaryCheckboxes();
  renderDietaryChips();
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
  calendarDetailDate.textContent = formatDateLabel(selectedDateKey);
  calendarDetailLabel.textContent = foodLog[selectedDateKey]
    ? "Saved note loaded. Update it or clear it below."
    : "Add a note for meals, cravings, prep, or leftovers.";
  calendarDetailInput.value = foodLog[selectedDateKey] || "";
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

    if (foodLog[key]) {
      const preview = document.createElement("span");
      preview.className = "cal-day-preview";
      preview.textContent = foodLog[key];

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
      calendarDetailInput.focus();
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

document.getElementById("cal-prev").addEventListener("click", () => changeMonth(-1));
document.getElementById("cal-next").addEventListener("click", () => changeMonth(1));

document.getElementById("calendar-save-btn").addEventListener("click", () => {
  const text = calendarDetailInput.value.trim();

  if (text) {
    foodLog[selectedDateKey] = text;
  } else {
    delete foodLog[selectedDateKey];
  }

  save(STORAGE_KEYS.foodLog, foodLog);
  renderCalendar();
  updateCalendarDetail();
});

document.getElementById("calendar-clear-btn").addEventListener("click", () => {
  delete foodLog[selectedDateKey];
  save(STORAGE_KEYS.foodLog, foodLog);
  renderCalendar();
  updateCalendarDetail();
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
renderDietaryCheckboxes();
renderDietaryChips();
renderCalendar();
updateCalendarDetail();
