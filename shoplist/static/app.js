"use strict";

const POLL_MS = 5000;
const $ = (sel, root = document) => root.querySelector(sel);

const state = { user: null, list: null, view: "list", pollTimer: null };

// ---------- API ----------

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Requested-With": "shoplist" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (res.status === 401 && path !== "/api/login") {
    state.user = null;
    showAuth();
    throw new Error("Please sign in again");
  }
  if (!res.ok) {
    let msg = `Something went wrong (${res.status})`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail)) msg = data.detail.map((d) => d.msg).join(", ");
    } catch {}
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

// ---------- helpers ----------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) node.append(c);
  return node;
}

function when(iso) {
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d) / 86400000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (days < 1 && d.getDate() === new Date().getDate()) return `today ${time}`;
  if (days < 7) return d.toLocaleDateString([], { weekday: "short" }) + ` ${time}`;
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

function formData(form) {
  const data = Object.fromEntries(new FormData(form));
  for (const box of form.querySelectorAll("input[type=checkbox]")) data[box.name] = box.checked;
  return data;
}

function handleForm(form, fn, errorEl = $(".error", form)) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("button:not([type=button])", form);
    errorEl.textContent = "";
    button.disabled = true;
    try {
      await fn(formData(form));
    } catch (err) {
      errorEl.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
}

// ---------- views ----------

function showOnly(id) {
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== id;
}

function showAuth(setupRequired = false) {
  stopPolling();
  $("#tabs").hidden = true;
  $("#whoami").textContent = "";
  showOnly(setupRequired ? "view-setup" : "view-login");
}

function switchView(view) {
  state.view = view;
  showOnly(`view-${view}`);
  for (const b of document.querySelectorAll("#tabs button")) {
    b.classList.toggle("active", b.dataset.view === view);
  }
  if (view === "list") loadList();
  if (view === "history") loadHistory();
  if (view === "household") loadHousehold();
}

function signedIn(user) {
  state.user = user;
  $("#whoami").textContent = user.display_name;
  $("#tabs").hidden = false;
  switchView("list");
  startPolling();
}

// ---------- list ----------

async function loadList() {
  try {
    const list = await api("GET", "/api/list");
    $("#list-error").textContent = "";
    // Skip re-rendering when nothing changed so polling doesn't disturb a tap in progress.
    if (JSON.stringify(list) === JSON.stringify(state.list)) return;
    state.list = list;
    renderList();
  } catch (err) {
    $("#list-error").textContent = err.message;
  }
}

function renderList() {
  const { items } = state.list;
  const ul = $("#items");
  ul.replaceChildren(...items.map(renderItem));
  $("#empty").hidden = items.length > 0;
  $("#complete-bar").hidden = items.length === 0;
  const bought = items.filter((i) => i.checked).length;
  $("#progress").textContent = `${bought} of ${items.length} in the trolley`;
}

function renderItem(item) {
  const meta = item.checked && item.checked_by
    ? `ticked by ${item.checked_by}`
    : `added by ${item.added_by ?? "someone"} · ${when(item.added_at)}`;
  return el("li", { class: "item" + (item.checked ? " checked" : "") },
    el("input", {
      type: "checkbox",
      checked: item.checked,
      "aria-label": `Got ${item.name}`,
      onchange: (e) => updateItem(item.id, { checked: e.target.checked }),
    }),
    el("div", { class: "main" },
      el("div", { class: "name" }, item.name),
      el("div", { class: "meta" }, meta),
    ),
    el("div", { class: "qty" },
      el("button", {
        type: "button", "aria-label": `One less ${item.name}`, disabled: item.quantity <= 1,
        onclick: () => updateItem(item.id, { quantity: item.quantity - 1 }),
      }, "−"),
      el("span", {}, String(item.quantity)),
      el("button", {
        type: "button", "aria-label": `One more ${item.name}`, disabled: item.quantity >= 999,
        onclick: () => updateItem(item.id, { quantity: item.quantity + 1 }),
      }, "+"),
    ),
    el("button", {
      type: "button", class: "remove", "aria-label": `Remove ${item.name}`,
      onclick: () => removeItem(item.id),
    }, "×"),
  );
}

async function updateItem(id, changes) {
  try {
    await api("PATCH", `/api/items/${id}`, changes);
  } catch (err) {
    $("#list-error").textContent = err.message;
  }
  loadList();
}

async function removeItem(id) {
  try {
    await api("DELETE", `/api/items/${id}`);
  } catch (err) {
    $("#list-error").textContent = err.message;
  }
  loadList();
}

async function completeShop() {
  const dialog = $("#complete-dialog");
  dialog.returnValue = "";
  $("#mark-all").checked = false;
  dialog.showModal();
  dialog.addEventListener("close", async () => {
    if (dialog.returnValue !== "ok") return;
    try {
      await api("POST", "/api/list/complete", {
        list_id: state.list.id,
        mark_all_bought: $("#mark-all").checked,
      });
    } catch (err) {
      $("#list-error").textContent = err.message;
    }
    loadList();
  }, { once: true });
}

// ---------- history ----------

async function loadHistory() {
  const shops = await api("GET", "/api/history");
  $("#history-empty").hidden = shops.length > 0;
  $("#history").replaceChildren(...shops.map((shop) => {
    const details = el("details", {},
      el("summary", {},
        el("div", {},
          el("div", {}, when(shop.completed_at)),
          el("div", { class: "sub" }, `completed by ${shop.completed_by ?? "someone"}`),
        ),
        el("div", { class: "sub" }, `${shop.bought_count}/${shop.item_count} bought`),
      ),
    );
    details.addEventListener("toggle", async () => {
      if (!details.open || details.dataset.loaded) return;
      details.dataset.loaded = "1";
      const detail = await api("GET", `/api/history/${shop.id}`);
      details.append(el("ul", {}, detail.items.map((i) =>
        el("li", {},
          el("span", { class: i.checked ? "" : "not-bought" }, `${i.quantity} × ${i.name}`),
          el("span", { class: "sub" }, i.added_by ?? ""),
        ),
      )));
    });
    return el("li", {}, details);
  }));
}

// ---------- household ----------

async function loadHousehold() {
  const card = $("#members-card");
  card.hidden = !state.user.is_admin;
  if (!state.user.is_admin) return;
  const users = await api("GET", "/api/users");
  $("#members").replaceChildren(...users.map((u) => {
    const isMe = u.id === state.user.id;
    const act = (label, changes, cls = "") =>
      el("button", {
        type: "button", class: cls,
        onclick: async () => {
          try {
            await api("PATCH", `/api/users/${u.id}`, changes());
            loadHousehold();
          } catch (err) {
            alert(err.message);
          }
        },
      }, label);
    return el("li", { class: u.active ? "" : "inactive" },
      el("div", { class: "who" },
        u.display_name,
        el("span", { class: "muted small" }, ` @${u.username}`),
        u.is_admin ? el("span", { class: "badge" }, "admin") : null,
        u.active ? null : el("span", { class: "badge" }, "disabled"),
      ),
      el("div", { class: "actions" },
        act("Reset password", () => {
          const password = prompt(`New password for ${u.display_name} (at least 8 characters):`);
          if (!password) throw new Error("Password not changed");
          return { password };
        }),
        isMe ? null : act(u.is_admin ? "Remove admin" : "Make admin", () => ({ is_admin: !u.is_admin })),
        isMe ? null : act(u.active ? "Disable" : "Enable", () => ({ active: !u.active }), u.active ? "danger" : ""),
      ),
    );
  }));
}

// ---------- polling ----------

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    if (document.visibilityState === "visible" && state.view === "list") loadList();
  }, POLL_MS);
}

function stopPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = null;
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.user && state.view === "list") loadList();
});

// ---------- wiring ----------

handleForm($("#setup-form"), async (data) => {
  signedIn(await api("POST", "/api/setup", data));
});

handleForm($("#login-form"), async (data) => {
  const user = await api("POST", "/api/login", data);
  $("#login-form").reset();
  signedIn(user);
});

handleForm($("#add-form"), async (data) => {
  await api("POST", "/api/items", { name: data.name, quantity: Number(data.quantity) || 1 });
  const form = $("#add-form");
  form.reset();
  form.elements.name.focus();
  await loadList();
}, $("#list-error"));

handleForm($("#password-form"), async (data) => {
  await api("POST", "/api/me/password", data);
  $("#password-form").reset();
  alert("Password changed. Other devices have been signed out.");
});

handleForm($("#member-form"), async (data) => {
  await api("POST", "/api/users", data);
  $("#member-form").reset();
  loadHousehold();
});

$("#complete-btn").addEventListener("click", completeShop);

$("#logout-btn").addEventListener("click", async () => {
  await api("POST", "/api/logout");
  state.user = null;
  showAuth();
});

for (const b of document.querySelectorAll("#tabs button")) {
  b.addEventListener("click", () => switchView(b.dataset.view));
}

(async function start() {
  const { setup_required, user } = await api("GET", "/api/session");
  if (user) signedIn(user);
  else showAuth(setup_required);
})();
