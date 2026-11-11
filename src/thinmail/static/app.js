// thinmail front end: folder tabs, the message list (polled), the reader.
// All mail content goes into the page through textContent / DOM nodes, never
// innerHTML; mail HTML is only shown in a sandboxed iframe served by /api.
"use strict";

const POLL_MS = 6000;
const HERO_MINUTES = 15;          // how long a code stays in the "latest code" card

const $ = id => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const ICON_LINK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>';
const ICON_COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';

const state = {
  folder: localStorage.getItem("thinmail.folder") || "*",   // "*": Inbox + Junk
  folders: [],
  messages: [],
  known: null,            // keys ("folder/uid") seen so far (null: first load)
  fresh: new Set(),       // keys of mail that arrived while the page was open
  sel: null,              // key of the row highlighted in the list
  open: null,             // the message shown in the reader
  html: false,
  images: false,
  updated: 0,
  error: "",
  loading: false,
  serverSkew: 0,
};
const title = document.querySelector(".app").dataset.title;

// ---- helpers --------------------------------------------------------------

function ago(t) {
  const now = Date.now() / 1000 - state.serverSkew;
  const s = Math.max(0, now - t);
  if (s < 45) return "now";
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  const d = new Date(t * 1000);
  if (s < 6 * 86400) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function copyText(text) {
  const done = () => toast(`copied ${text}`);
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text) && done());
  }
  if (fallbackCopy(text)) done(); else toast("copy failed: select the code by hand");
}
function fallbackCopy(text) {
  // Plain http on the LAN is no "secure context", so no Clipboard API there.
  const t = el("textarea");
  t.value = text;
  t.setAttribute("readonly", "");
  t.style.position = "fixed"; t.style.opacity = "0";
  document.body.appendChild(t);
  t.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  t.remove();
  return ok;
}
// "731 882" -> "731882"; other codes as they are.
const codeForCopy = c => /^\d{3}[ -]\d{3}$/.test(c) ? c.replace(/[ -]/, "") : c;
function copyCode(m) { if (m && m.code) copyText(codeForCopy(m.code)); }

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1600);
}

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: "same-origin", ...opts });
  if (r.status === 401) { location.href = "/login"; throw new Error("login required"); }
  let data = null;
  try { data = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}
const q = (folder, extra = "") => `?folder=${encodeURIComponent(folder)}${extra}`;
const keyOf = m => `${m.folder}/${m.uid}`;

function chips(m, big = false) {
  const box = el("span", "chips");
  if (m.code) {
    const c = el("button", "chip", m.code);
    c.title = "Copy code";
    c.insertAdjacentHTML("beforeend", ICON_COPY);
    c.addEventListener("click", ev => { ev.stopPropagation(); copyCode(m); });
    box.appendChild(c);
  }
  if (m.link) {
    const a = el("a", "chip link", big ? "Open link" : "Link");
    a.href = m.link;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.title = m.link;
    a.insertAdjacentHTML("beforeend", ICON_LINK);
    a.addEventListener("click", ev => ev.stopPropagation());
    box.appendChild(a);
  }
  return box;
}

// ---- folders --------------------------------------------------------------

async function loadFolders() {
  try {
    const data = await api("/api/folders");
    state.folders = data.folders;
    if (state.folder !== "*" && !state.folders.some(f => f.name === state.folder)) state.folder = "*";
  } catch (e) {
    state.folders = [{ name: "INBOX", role: "inbox" }];
  }
  renderFolders();
}

function renderFolders() {
  const nav = $("folders");
  nav.replaceChildren();
  // Inbox and Junk (2FA mail lands there often enough) up front, the rest after.
  const label = f => f.label || (f.role === "inbox" ? "Inbox" : f.name);
  const useful = state.folders.filter(f => !["sent", "drafts"].includes(f.role));
  const hasJunk = useful.some(f => f.role === "junk");
  const tabs = hasJunk ? [{ name: "*", label: "Inbox + Junk" }, ...useful] : useful;
  if (!hasJunk && state.folder === "*") state.folder = "INBOX";
  for (const f of tabs) {
    const b = el("button", "btn" + (f.name === state.folder ? " on" : ""), label(f));
    b.addEventListener("click", () => switchFolder(f.name));
    nav.appendChild(b);
  }
}

function switchFolder(name) {
  if (name === state.folder) return;
  state.folder = name;
  localStorage.setItem("thinmail.folder", name);
  state.messages = []; state.known = null; state.fresh.clear(); state.sel = null;
  closeReader();
  renderFolders();
  $("list").replaceChildren(el("div", "empty", "loading…"));
  refresh();
}

// ---- list -----------------------------------------------------------------

async function refresh() {
  if (state.loading) return;
  state.loading = true;
  renderStatus();
  const folder = state.folder;
  try {
    const data = await api("/api/messages" + q(folder));
    if (folder !== state.folder) return;
    state.serverSkew = Date.now() / 1000 - data.now;
    for (const m of data.messages) m.key = keyOf(m);
    const keys = new Set(data.messages.map(m => m.key));
    if (state.known) for (const k of keys) if (!state.known.has(k)) state.fresh.add(k);
    state.known = keys;
    state.messages = data.messages;
    state.error = "";
    state.updated = Date.now();
    if (state.sel !== null && !keys.has(state.sel)) state.sel = null;
    renderList();
  } catch (e) {
    state.error = e.message;
  } finally {
    state.loading = false;
    renderStatus();
  }
}

function renderList() {
  const list = $("list");
  if (!state.messages.length) {
    list.replaceChildren(el("div", "empty", "no mail here"));
  } else {
    const rows = state.messages.map(m => {
      const r = el("div", "row" + (m.seen ? "" : " unread") + (m.key === state.sel ? " sel" : "")
                         + (state.fresh.has(m.key) ? " new" : ""));
      r.dataset.key = m.key;
      r.appendChild(el("span", "dot"));
      const top = el("div", "top");
      top.appendChild(el("span", "from", m.from || m.address || "(unknown)"));
      top.appendChild(el("span", "when", ago(m.date)));
      r.appendChild(top);
      r.appendChild(chips(m));
      r.appendChild(el("div", "subject", m.subject || "(no subject)"));
      r.addEventListener("click", () => openMessage(m.key));
      return r;
    });
    list.replaceChildren(...rows);
    state.fresh.clear();      // flash once
  }
  renderHero();
  const unread = state.messages.filter(m => !m.seen).length;
  document.title = unread ? `(${unread}) ${title}` : title;
}

function renderHero() {
  const now = Date.now() / 1000 - state.serverSkew;
  const m = state.messages.find(m => (m.code || m.link) && now - m.date < HERO_MINUTES * 60);
  const hero = $("hero");
  hero.classList.toggle("hidden", !m);
  if (!m) { state.hero = null; return; }
  state.hero = m;
  $("hero-age").textContent = ago(m.date) === "now" ? "just now" : ago(m.date) + " ago";
  $("hero-from").textContent = `${m.from || m.address} — ${m.subject || ""}`;
  const code = $("hero-code");
  code.textContent = m.code || "";
  code.classList.toggle("hidden", !m.code);
  const link = $("hero-link");
  link.classList.toggle("hidden", !m.link);
  if (m.link) { link.href = m.link; link.title = m.link; }
}

function renderStatus() {
  const s = $("status");
  s.classList.toggle("err", !!state.error);
  if (state.error) s.textContent = state.error;
  else if (state.loading && !state.updated) s.textContent = "loading…";
  else if (state.updated) {
    const secs = Math.round((Date.now() - state.updated) / 1000);
    s.textContent = secs < 5 ? "up to date" : `updated ${secs}s ago`;
  }
}

function select(key, scroll = true) {
  state.sel = key;
  for (const r of $("list").querySelectorAll(".row")) {
    const on = r.dataset.key === key;
    r.classList.toggle("sel", on);
    if (on && scroll) r.scrollIntoView({ block: "nearest" });
  }
}

// ---- reader ---------------------------------------------------------------

async function openMessage(key) {
  const listed = state.messages.find(x => x.key === key);
  if (!listed) return;
  select(key, false);
  const view = state.folder;
  let m;
  try {
    m = await api(`/api/messages/${listed.uid}` + q(listed.folder));
  } catch (e) { toast(e.message); return; }
  if (view !== state.folder) return;
  m.key = keyOf(m);
  state.open = m;
  state.html = !m.text && m.hasHtml;
  state.images = false;
  if (!listed.seen) { listed.seen = true; renderList(); }
  renderReader();
}

function renderReader() {
  const m = state.open;
  $("reader").classList.toggle("open", !!m);
  $("placeholder").classList.toggle("hidden", !!m);
  $("head").classList.toggle("hidden", !m);
  $("r-body").classList.toggle("hidden", !m);
  if (!m) return;
  $("r-subject").textContent = m.subject || "(no subject)";
  $("r-from").textContent = m.from;
  $("r-addr").textContent = m.address && m.address !== m.from ? `<${m.address}>` : "";
  $("r-date").textContent = new Date(m.date * 1000).toLocaleString();
  $("r-chips").replaceChildren(chips(m, true));
  const view = $("r-view");
  view.classList.toggle("hidden", !m.hasHtml || !m.text);
  view.textContent = state.html ? "Text" : "HTML";
  $("r-images").classList.toggle("hidden", !state.html);
  $("r-images").classList.toggle("on", state.images);

  const body = $("r-body");
  if (state.html) {
    const f = el("iframe");
    // No scripts, no same-origin access; links may open new tabs.
    f.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
    f.setAttribute("referrerpolicy", "no-referrer");
    f.src = `/api/messages/${m.uid}/html` + q(m.folder, state.images ? "&images=1" : "");
    body.replaceChildren(f);
  } else {
    body.replaceChildren(textView(m.text || "(empty)", m.code));
  }
}

// Text with links made clickable and the code highlighted, built from nodes.
function textView(text, code) {
  const pre = el("pre");
  const re = /https?:\/\/[^\s<>"']+/g;
  let last = 0, mm;
  const addText = s => {
    if (!code || !s.includes(code)) { pre.appendChild(document.createTextNode(s)); return; }
    const parts = s.split(code);
    parts.forEach((p, i) => {
      if (p) pre.appendChild(document.createTextNode(p));
      if (i < parts.length - 1) pre.appendChild(el("mark", "", code));
    });
  };
  while ((mm = re.exec(text))) {
    const url = mm[0].replace(/[.,;:!?)\]]+$/, "");
    addText(text.slice(last, mm.index));
    const a = el("a", "", url);
    a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
    pre.appendChild(a);
    last = mm.index + url.length;
    re.lastIndex = last;
  }
  addText(text.slice(last));
  return pre;
}

function closeReader() {
  state.open = null;
  renderReader();
}

async function deleteOpen() {
  const m = state.open;
  if (!m) return;
  try {
    await api(`/api/messages/${m.uid}/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: m.folder }),
    });
  } catch (e) { toast(e.message); return; }
  const i = state.messages.findIndex(x => x.key === m.key);
  state.messages = state.messages.filter(x => x.key !== m.key);
  const next = state.messages[Math.min(i, state.messages.length - 1)];
  closeReader();
  state.sel = next ? next.key : null;
  renderList();
  toast("deleted");
}

// ---- events ---------------------------------------------------------------

$("refresh").addEventListener("click", refresh);
$("back").addEventListener("click", closeReader);
$("r-delete").addEventListener("click", deleteOpen);
$("r-view").addEventListener("click", () => { state.html = !state.html; state.images = false; renderReader(); });
$("r-images").addEventListener("click", () => { state.images = !state.images; renderReader(); });
$("hero-code").addEventListener("click", () => copyCode(state.hero));

document.addEventListener("keydown", ev => {
  if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.target.matches("input, textarea")) return;
  const ms = state.messages;
  const i = ms.findIndex(m => m.key === state.sel);
  const k = ev.key;
  if (k === "j" || k === "ArrowDown") { if (ms.length) select(ms[Math.min(ms.length - 1, i + 1)].key); }
  else if (k === "k" || k === "ArrowUp") { if (ms.length) select(ms[Math.max(0, i - 1)].key); }
  else if (k === "Enter" || k === "o") { if (state.sel !== null) openMessage(state.sel); }
  else if (k === "c") {
    const m = state.open || ms.find(m => m.key === state.sel) || state.hero;
    if (m && m.code) copyCode(m); else toast("no code");
  }
  else if (k === "r") refresh();
  else if (k === "#" || k === "Delete") deleteOpen();
  else if (k === "Escape") closeReader();
  else return;
  ev.preventDefault();
});

// Poll while the tab is visible; refresh at once when it comes back.
setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
setInterval(() => { renderStatus(); if (state.messages.length) renderHero(); }, 1000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

loadFolders().then(refresh);
