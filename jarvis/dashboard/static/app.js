"use strict";

// Le jeton d'accès arrive dans l'ancre de l'URL (#token=...) et n'est jamais envoyé ailleurs.
const TOKEN = (() => {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("token");
  if (fromHash) {
    try { sessionStorage.setItem("jarvis-token", fromHash); } catch (_) {}
    history.replaceState(null, "", location.pathname);
    return fromHash;
  }
  try { return sessionStorage.getItem("jarvis-token") || ""; } catch (_) { return ""; }
})();

const $ = (id) => document.getElementById(id);
const STATE_LABELS = { idle: "En veille", listening: "J'écoute", thinking: "Réflexion", speaking: "Je parle" };
const TOOL_LABELS = {
  remember: "Mémorisation", recall: "Recherche en mémoire", update_memory: "Mise à jour de la mémoire",
  forget: "Oubli", search_conversations: "Recherche dans les conversations", add_task: "Nouvelle tâche",
  list_tasks: "Lecture des tâches", complete_task: "Tâche terminée", open_application: "Ouverture d'application",
  open_url: "Ouverture de page", play_media: "Lecture média", media_control: "Contrôle média",
  system_status: "État du système", list_processes: "Processus", look_at_screen: "Analyse de l'écran",
  read_clipboard: "Lecture du presse-papiers", write_clipboard: "Copie", set_timer: "Minuteur",
  list_timers: "Minuteurs", cancel_timer: "Annulation du minuteur", run_command: "Commande système",
  list_directory: "Lecture du dossier", search_files: "Recherche de fichiers", read_text_file: "Lecture de fichier",
  write_text_file: "Écriture de fichier", open_path: "Ouverture", get_weather: "Météo",
  gmail_list: "Lecture des e-mails", gmail_read: "Lecture d'un e-mail", gmail_send: "Envoi d'e-mail",
  calendar_list: "Lecture de l'agenda", calendar_create: "Création d'événement", calendar_delete: "Suppression d'événement",
};

let info = {};
let liveBubble = null;
let liveTurn = null;
const streamedTurns = new Set();  // tours dont le texte est déjà affiché au fil de l'eau
let currentConfirm = null;
const toolChips = new Map();

// ------------------------------------------------------------------ API

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "X-Jarvis-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { $("lock").hidden = false; throw new Error("unauthorized"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) {
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }
  return node;
}

function toast(text) {
  const t = el("div", { class: "toast" }, text);
  $("toasts").append(t);
  setTimeout(() => t.remove(), 7000);
}

// ------------------------------------------------------------------ horloge

function tickClock() {
  const now = new Date();
  $("clock-time").textContent = now.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  $("clock-date").textContent = now.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
}

// ------------------------------------------------------------------ état

function setState(state) {
  $("reactor").dataset.state = state;
  $("state-label").textContent = STATE_LABELS[state] || state;
  $("btn-mic").classList.toggle("live", state === "listening");
}

function renderInfo() {
  $("k-model").textContent = info.model || "–";
  $("k-effort").textContent = info.effort || "–";
  $("k-voice").textContent = info.voice_enabled ? (info.voice || "–").replace(/Neural$/, "") : "désactivée";
  const s = info.stats || {};
  $("k-memory").textContent = `${s.facts ?? 0} faits · ${s.episodes ?? 0} conversations`;
  $("btn-mic").disabled = !info.voice_enabled;
  $("hint").textContent = info.voice_enabled
    ? (info.wake_word ? "Dis « Hey Jarvis » ou clique sur le micro" : "Clique sur le micro pour parler")
    : "Mode texte";
  $("subtitle").textContent = `Au service de ${info.user || ""}`;
  const pct = info.max_context_tokens ? Math.min(100, (100 * (info.context_tokens || 0)) / info.max_context_tokens) : 0;
  $("m-ctx").style.width = `${pct}%`;
  $("v-ctx").textContent = `Conversation : ${info.session_turns || 0} échanges · contexte ${Math.round(pct)}%`;
}

// ------------------------------------------------------------------ conversation

function scrollChat() {
  const chat = $("chat");
  chat.scrollTop = chat.scrollHeight;
}

function addMessage(kind, text, meta) {
  $("chat-empty").hidden = true;
  const node = el("div", { class: `msg ${kind}` }, meta ? el("span", { class: "meta" }, meta) : null, text);
  $("chat").append(node);
  scrollChat();
  return node;
}

function addActivity(text, detail) {
  const li = el("li", {}, el("b", {}, new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })), " ", text,
    detail ? el("div", {}, detail) : null);
  $("activity").prepend(li);
  while ($("activity").children.length > 150) $("activity").lastChild.remove();
}

function handleEvent(ev, replay = false) {
  const d = ev.data || {};
  switch (ev.type) {
    case "state": setState(d.state); break;
    case "user_message":
      addMessage("user", d.text, d.source === "voice" ? "🎙 voix" : null);
      break;
    case "assistant_delta":
      if (!liveBubble || liveTurn !== d.turn) {
        liveBubble = addMessage("jarvis live", "");
        liveTurn = d.turn;
        streamedTurns.add(d.turn);
      }
      liveBubble.textContent += d.text;
      scrollChat();
      break;
    case "assistant_message":
      if (liveBubble) {
        liveBubble.classList.remove("live");
        liveBubble = null;
        liveTurn = null;
      }
      if (d.text && !streamedTurns.has(d.turn)) addMessage("jarvis", d.text);
      if (!replay) refreshState();
      break;
    case "tool_start": {
      const chip = el("div", { class: "chip" }, `⚙ ${TOOL_LABELS[d.name] || d.name}…`);
      toolChips.set(d.id, chip);
      // La bulle en cours est close : le texte qui suit l'outil ira dans une nouvelle bulle.
      if (liveBubble) { liveBubble.classList.remove("live"); liveBubble = null; }
      $("chat-empty").hidden = true;
      $("chat").append(chip);
      scrollChat();
      if (!replay) addActivity(TOOL_LABELS[d.name] || d.name, JSON.stringify(d.args));
      break;
    }
    case "tool_end": {
      const chip = toolChips.get(d.id);
      if (chip) {
        chip.classList.add(d.ok ? "ok" : "fail");
        chip.textContent = `${d.ok ? "✓" : "✗"} ${TOOL_LABELS[d.name] || d.name}`;
        chip.title = d.summary || "";
      }
      if (!replay) addActivity(`${d.ok ? "✓" : "✗"} ${d.name}`, (d.summary || "").slice(0, 300));
      break;
    }
    case "notification":
      addMessage("note", d.text, "rappel");
      if (!replay) toast(d.text);
      break;
    case "error":
      addMessage("err", d.message);
      break;
    case "consolidating":
      $("hint").textContent = "Je range cette conversation dans ma mémoire…";
      break;
    case "consolidated":
      addMessage("note", `Conversation mémorisée : ${d.summary}`, "mémoire");
      if (!replay) { refreshState(); loadMemory(); }
      break;
    case "confirm_request":
      if (!replay) openConfirm(d);
      break;
    case "confirm_resolved":
      if (currentConfirm && currentConfirm.id === d.id) closeConfirm();
      if (!replay) addActivity(d.approved ? "Action autorisée" : "Action refusée");
      break;
    case "memory_changed":
      if (!replay) { loadMemory(); refreshState(); }
      break;
    case "wake":
      $("hint").textContent = "Je t'écoute…";
      break;
    case "stopped":
      addActivity("Parole interrompue");
      break;
  }
}

function connectEvents() {
  const source = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  source.onopen = () => { $("link-dot").classList.add("on"); $("reactor").classList.remove("offline"); };
  source.onmessage = (msg) => handleEvent(JSON.parse(msg.data));
  source.onerror = () => { $("link-dot").classList.remove("on"); $("reactor").classList.add("offline"); };
}

// ------------------------------------------------------------------ confirmations

function openConfirm(d) {
  currentConfirm = d;
  $("confirm-action").textContent = `Jarvis veut ${d.action}.`;
  $("confirm").hidden = false;
  $("confirm-yes").focus();
}
function closeConfirm() { currentConfirm = null; $("confirm").hidden = true; }
async function answerConfirm(approved) {
  if (!currentConfirm) return;
  const id = currentConfirm.id;
  closeConfirm();
  await api("POST", `/api/confirm/${id}`, { approved }).catch((e) => toast(e.message));
}

// ------------------------------------------------------------------ système

function meter(id, value) {
  const bar = $(`m-${id}`);
  bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
  bar.classList.toggle("high", value >= 85);
  $(`v-${id}`).textContent = `${Math.round(value)}%`;
}

async function refreshSystem() {
  try {
    const s = await api("GET", "/api/system");
    meter("cpu", s.cpu); meter("ram", s.ram); meter("disk", s.disk);
    $("battery-row").hidden = s.battery === null;
    if (s.battery !== null) meter("bat", s.battery);
    $("sys-info").textContent = `${s.os} · ${s.ram_used_gb}/${s.ram_total_gb} Go · allumé depuis ${s.uptime_h} h`;
    const timers = $("timers");
    timers.replaceChildren(...(s.timers.length
      ? s.timers.map((t) => el("li", {}, el("span", {}, t.label), el("b", { class: "muted" }, t.due.slice(11, 16))))
      : [el("li", { class: "muted small" }, "Aucun minuteur.")]));
    if (info.voice_enabled && $("reactor").dataset.state === "listening") {
      $("reactor").style.setProperty("--s", String(1 + Math.min(0.18, (s.mic_level || 0) / 20000)));
    } else {
      $("reactor").style.setProperty("--s", "1");
    }
  } catch (_) {}
}

// ------------------------------------------------------------------ aujourd'hui

function fmtTime(iso, allDay) {
  if (allDay) return "journée";
  const d = new Date(iso);
  const today = new Date();
  const time = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === today.toDateString() ? time : `dem. ${time}`;
}

async function refreshOverview(force = false) {
  try {
    if (force) await api("POST", "/api/overview/refresh");
    const o = await api("GET", "/api/overview");
    if (o.weather) {
      const w = o.weather;
      $("weather").replaceChildren(
        el("div", { class: "wx-now" }, el("span", { class: "wx-temp" }, `${w.temp}°`),
          el("div", {}, el("div", {}, w.text), el("div", { class: "muted small" }, `${w.place} · ressenti ${w.feels}° · vent ${w.wind} km/h`))),
        el("div", { class: "wx-days" }, w.days.map((d) => el("div", { class: "wx-day" },
          el("div", { class: "muted" }, new Date(d.date).toLocaleDateString("fr-FR", { weekday: "short" })),
          el("div", {}, `${d.min}° / ${d.max}°`), el("div", { class: "muted" }, `☂ ${d.rain}%`)))),
      );
      $("weather").classList.remove("muted", "small");
    }
    if (o.events) {
      const now = Date.now();
      $("events").replaceChildren(...(o.events.length ? o.events.map((ev) => {
        const soon = !ev.all_day && new Date(ev.start) - now < 3600e3 && new Date(ev.start) > now;
        return el("li", { class: `item${soon ? " soon" : ""}` },
          el("span", { class: "when" }, fmtTime(ev.start, ev.all_day)), el("span", { class: "title" }, ev.title),
          ev.location ? el("span", { class: "sub" }, ev.location) : null);
      }) : [el("li", { class: "muted small" }, "Rien de prévu aujourd'hui ni demain.")]));
    }
    if (o.emails) {
      $("emails").replaceChildren(...(o.emails.length ? o.emails.map((m) =>
        el("li", { class: "item" }, el("span", { class: "when" }, "✉"),
          el("span", { class: "title" }, m.subject || "(sans objet)"),
          el("span", { class: "sub" }, `${m.from.replace(/<.*>/, "").trim()} — ${m.snippet.slice(0, 110)}`)))
        : [el("li", { class: "muted small" }, "Boîte de réception à jour.")]));
    }
    for (const e of o.errors || []) addActivity("Erreur", e);
  } catch (_) {}
}

// ------------------------------------------------------------------ mémoire

let searchTimer = null;

async function loadMemory() {
  try {
    const q = $("memory-search").value.trim();
    const m = await api("GET", `/api/memory?q=${encodeURIComponent(q)}`);
    $("facts-count").textContent = `${m.stats.facts}`;
    renderFacts(m.facts, Boolean(q));
    renderEpisodes(m.episodes);
    renderTasks(m.tasks);
  } catch (_) {}
}

function renderFacts(facts, searching) {
  if (!facts.length) {
    $("facts").replaceChildren(el("p", { class: "muted small" },
      searching ? "Aucun souvenir ne correspond." : "Jarvis n'a encore rien mémorisé. Parle-lui de toi !"));
    return;
  }
  const groups = new Map();
  for (const f of facts) {
    if (!groups.has(f.category)) groups.set(f.category, []);
    groups.get(f.category).push(f);
  }
  const ordered = searching ? [...groups] : [...groups].sort((a, b) => a[0].localeCompare(b[0]));
  $("facts").replaceChildren(...ordered.map(([category, items]) =>
    el("div", { class: "fact-group" }, el("h3", {}, category), items.map(renderFact))));
}

function renderFact(f) {
  const text = el("span", { class: "text", title: `Mémorisé le ${f.created_at.slice(0, 10)}` }, f.content);
  const save = async () => {
    text.contentEditable = "false";
    const value = text.textContent.trim();
    if (value && value !== f.content) await api("PUT", `/api/facts/${f.id}`, { fact: value }).catch((e) => toast(e.message));
  };
  text.addEventListener("blur", save);
  text.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); text.blur(); } });
  const cycle = async () => {
    const next = (f.importance % 3) + 1;
    await api("PUT", `/api/facts/${f.id}`, { importance: next }).catch((e) => toast(e.message));
  };
  return el("div", { class: "fact" },
    el("span", { class: `imp i${f.importance}`, title: "Importance (cliquer pour changer)", onclick: cycle, style: "cursor:pointer" }),
    text,
    el("span", { class: "acts" },
      el("button", { class: "mini", title: "Modifier", onclick: () => { text.contentEditable = "true"; text.focus(); } }, "✎"),
      el("button", { class: "mini", title: "Oublier", onclick: () => {
        if (confirm(`Oublier : « ${f.content} » ?`)) api("DELETE", `/api/facts/${f.id}`).catch((e) => toast(e.message));
      } }, "✕")));
}

function renderEpisodes(episodes) {
  $("episodes").replaceChildren(...(episodes.length ? episodes.map((e) =>
    el("li", { class: "item" },
      el("span", { class: "when" }, new Date(e.started_at).toLocaleDateString("fr-FR", { day: "numeric", month: "short" })),
      el("span", { class: "title" }, e.summary),
      el("span", { class: "sub" }, el("button", { class: "mini", title: "Supprimer ce souvenir", onclick: () => {
        if (confirm("Supprimer le résumé de cette conversation ?")) api("DELETE", `/api/episodes/${e.id}`).then(loadMemory);
      } }, "supprimer"))))
    : [el("li", { class: "muted small" }, "Les conversations terminées apparaîtront ici, résumées.")]));
}

function renderTasks(tasks) {
  const today = new Date().toISOString().slice(0, 10);
  $("tasks").replaceChildren(...(tasks.length ? tasks.map((t) => {
    const box = el("input", { type: "checkbox", checked: t.done, onchange: () =>
      api("POST", `/api/tasks/${t.id}/toggle`, { done: box.checked }).catch((e) => toast(e.message)) });
    return el("li", { class: `task${t.done ? " done" : ""}` }, box,
      el("span", { class: "title" }, t.title),
      t.due ? el("span", { class: `due${!t.done && t.due.slice(0, 10) < today ? " late" : ""}` }, t.due) : null,
      el("button", { class: "mini", title: "Supprimer", onclick: () => api("DELETE", `/api/tasks/${t.id}`) }, "✕"));
  }) : [el("li", { class: "muted small" }, "Aucune tâche. Dis « ajoute à ma liste… » à Jarvis.")]));
}

// ------------------------------------------------------------------ actions

async function refreshState() {
  try {
    info = await api("GET", "/api/state");
    renderInfo();
    return info;
  } catch (_) { return null; }
}

function bindUi() {
  $("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("input").value.trim();
    if (!text) return;
    $("input").value = "";
    await api("POST", "/api/ask", { text }).catch((err) => toast(err.message));
  });
  $("btn-mic").addEventListener("click", () => api("POST", "/api/listen").catch((e) => toast(e.message)));
  $("btn-stop").addEventListener("click", () => api("POST", "/api/stop"));
  $("btn-briefing").addEventListener("click", () => api("POST", "/api/briefing"));
  $("btn-reset").addEventListener("click", () => api("POST", "/api/reset"));
  $("btn-refresh").addEventListener("click", () => refreshOverview(true));
  $("confirm-yes").addEventListener("click", () => answerConfirm(true));
  $("confirm-no").addEventListener("click", () => answerConfirm(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (currentConfirm) answerConfirm(false); else api("POST", "/api/stop"); }
  });
  $("memory-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadMemory, 250); });
  $("fact-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fact = $("fact-input").value.trim();
    if (!fact) return;
    await api("POST", "/api/facts", { fact, importance: Number($("fact-importance").value) }).catch((err) => toast(err.message));
    $("fact-input").value = "";
  });
  $("task-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = $("task-input").value.trim();
    if (!title) return;
    await api("POST", "/api/tasks", { title, due: $("task-due").value }).catch((err) => toast(err.message));
    $("task-input").value = ""; $("task-due").value = "";
  });
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t === tab);
      for (const p of document.querySelectorAll(".tab-panel")) p.hidden = p.id !== `tab-${tab.dataset.tab}`;
    });
  }
}

async function main() {
  bindUi();
  tickClock();
  setInterval(tickClock, 1000);
  if (!TOKEN) { $("lock").hidden = false; return; }
  const state = await refreshState();
  if (!state) return;
  setState(state.state);
  for (const ev of state.history) handleEvent(ev, true);
  for (const c of state.pending_confirms) openConfirm(c);
  connectEvents();
  refreshSystem();
  setInterval(refreshSystem, 2000);
  refreshOverview();
  setInterval(refreshOverview, 120000);
  loadMemory();
}

main();
