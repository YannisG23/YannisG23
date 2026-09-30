"use strict";

// Le jeton d'accès arrive dans l'ancre de l'URL (#token=...) et n'est jamais envoyé ailleurs.
const TOKEN = (() => {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("token");
  if (fromHash) {
    try { sessionStorage.setItem("assistant-token", fromHash); } catch (_) {}
    history.replaceState(null, "", location.pathname);
    return fromHash;
  }
  try { return sessionStorage.getItem("assistant-token") || ""; } catch (_) { return ""; }
})();

const $ = (id) => document.getElementById(id);
const STATE_LABELS = { idle: "En veille", listening: "J'écoute", thinking: "Je réfléchis", speaking: "Je parle" };
const TOOL_LABELS = {
  remember: "Je retiens", recall: "Je cherche dans ma mémoire", update_memory: "Je corrige ma mémoire",
  forget: "J'oublie", search_conversations: "Je relis nos conversations", add_task: "Tâche ajoutée",
  list_tasks: "Tes tâches", complete_task: "Tâche terminée", open_application: "J'ouvre l'application",
  open_url: "J'ouvre la page", play_media: "Je lance la lecture", media_control: "Contrôle de la lecture",
  system_status: "État de l'ordinateur", list_processes: "Programmes actifs", look_at_screen: "Je regarde ton écran",
  read_clipboard: "Presse-papiers", write_clipboard: "Copié", set_timer: "Minuteur réglé",
  list_timers: "Minuteurs", cancel_timer: "Minuteur annulé", run_command: "Commande système",
  list_directory: "Je parcours le dossier", search_files: "Je cherche tes fichiers", read_text_file: "Je lis le fichier",
  write_text_file: "J'écris le fichier", open_path: "J'ouvre", get_weather: "Météo",
  gmail_list: "Je regarde tes e-mails", gmail_read: "Je lis l'e-mail", gmail_send: "J'envoie l'e-mail",
  calendar_list: "Je regarde ton agenda", calendar_create: "J'ajoute à l'agenda", calendar_delete: "Je retire de l'agenda",
  rename_assistant: "Je change de nom",
  ask_codex: "Je demande à Codex", codex_task: "Codex travaille",
  list_routines: "Tes routines", run_routine: "Je lance la routine", create_routine: "Routine apprise",
  delete_routine: "Routine supprimée",
};
const SUGGESTIONS = [
  "Fais-moi le point sur ma journée",
  "Mets-moi de la musique pour me concentrer",
  "Qu'est-ce qui ralentit mon PC ?",
  "Rappelle-moi de faire une pause dans 45 minutes",
  "Qu'est-ce que tu sais de moi ?",
  "Ajoute « réviser » à ma liste pour demain",
];

let info = {};
let currentConfirm = null;
const timerTotals = new Map();

// ------------------------------------------------------------------ utilitaires

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "X-Jarvis-Token": TOKEN, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { $("lock").hidden = false; throw new Error("Accès refusé"); }
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

function toast(text, kind = "") {
  const t = el("div", { class: `toast ${kind}` }, el("div", {}, text));
  $("toasts").append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, 6500);
}

const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const time = (d) => d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ------------------------------------------------------------------ état général

function setState(state) {
  Orb.state = state;
  const pill = $("state-pill");
  pill.dataset.state = state;
  $("app").dataset.state = state;
  $("state-label").textContent = STATE_LABELS[state] || state;
  $("btn-talk").classList.toggle("live", state === "listening");
  $("btn-stop").hidden = !(state === "thinking" || state === "speaking");
  if (state === "idle") setTimeout(() => { if (Orb && $("state-pill").dataset.state === "idle") fadeCaption(); }, 2500);
}

function renderInfo() {
  const name = info.name || "Assistant";
  $("name").textContent = name;
  document.title = `${name} · Centre de commande`;
  if (window.pywebview && window.pywebview.api && window.pywebview.api.set_title) window.pywebview.api.set_title(name);
  $("hint").textContent = info.hint || "";
  $("btn-talk").disabled = !info.voice_enabled;
  $("input").placeholder = `Écris une demande à ${name}…`;
  $("k-model").textContent = info.model || "–";
  $("k-brain").textContent = info.brain || "–";
  $("k-effort").textContent = { low: "rapide", medium: "équilibrée", high: "approfondie", xhigh: "très approfondie", max: "maximale" }[info.effort] || info.effort || "–";
  $("k-voice").textContent = !info.voice_enabled ? "désactivée" : info.tts === "ElevenLabs" ? "ElevenLabs" : (info.voice || "–").replace(/Neural$/, "");
  const pct = info.max_context_tokens ? Math.min(100, (100 * (info.context_tokens || 0)) / info.max_context_tokens) : 0;
  $("m-ctx").style.width = `${pct}%`;
  $("v-ctx").textContent = `${info.session_turns || 0} échanges`;
}

async function refreshState() {
  try { info = await api("GET", "/api/state"); renderInfo(); return info; } catch (_) { return null; }
}

// ------------------------------------------------------------------ sous-titres

let captionTimer = null;
function showCaption(text) {
  clearTimeout(captionTimer);
  const cap = $("caption");
  cap.classList.remove("fading");
  cap.textContent = text;
}
function fadeCaption() {
  const cap = $("caption");
  cap.classList.add("fading");
  captionTimer = setTimeout(() => { cap.textContent = ""; cap.classList.remove("fading"); }, 320);
}

// ------------------------------------------------------------------ conversation

const Thread = (() => {
  let turn = null;          // bloc du tour en cours
  let live = null;          // texte de l'assistant en train de s'écrire
  const streamed = new Set();
  const actions = new Map();

  function scroll() { const th = $("thread"); th.scrollTop = th.scrollHeight; }
  function begin() {
    $("empty").hidden = true;
    document.querySelector(".stage").classList.add("compact");
    turn = el("div", { class: "turn" });
    $("thread").append(turn);
    live = null;
    return turn;
  }
  function closeLive() { if (live) { live.classList.remove("live"); live = null; } }

  return {
    user(text, source) {
      begin();
      turn.append(el("div", { class: "msg-user" }, source === "voice" ? el("span", { class: "via" }, "à la voix") : null, text));
      scroll();
    },
    delta(id, text) {
      if (!turn) begin();
      if (!live) { live = el("div", { class: "msg-ai live" }); turn.append(live); }
      streamed.add(id);
      live.textContent += text;
      scroll();
    },
    final(id, text) {
      closeLive();
      if (text && !streamed.has(id)) {
        if (!turn && !$("empty").hidden && id === undefined) {
          // Message d'accueil : il devient le titre de l'écran d'accueil, avec les suggestions.
          $("empty-title").textContent = text;
          return;
        }
        if (!turn) begin();
        turn.append(el("div", { class: "msg-ai" }, text));
      }
      turn = null;
      scroll();
    },
    note(text, kind = "") {
      begin();
      turn.append(el("div", { class: `msg-note ${kind}` }, text));
      turn = null;
      scroll();
    },
    toolStart(d) {
      if (!turn) begin();
      closeLive();
      let row = turn.lastElementChild;
      if (!row || !row.classList.contains("actions")) { row = el("div", { class: "actions" }); turn.append(row); }
      const pill = el("span", { class: "action", title: "" }, el("i", { class: "ic" }), TOOL_LABELS[d.name] || d.name);
      actions.set(d.id, pill);
      row.append(pill);
      scroll();
    },
    toolEnd(d) {
      const pill = actions.get(d.id);
      if (!pill) return;
      pill.classList.add(d.ok ? "ok" : "fail");
      pill.title = d.summary || "";
    },
  };
})();

function renderSuggestions() {
  $("suggestions").replaceChildren(...SUGGESTIONS.map((s) => el("button", { class: "chip", type: "button", onclick: () => ask(s) }, s)));
}

async function ask(text) {
  await api("POST", "/api/ask", { text }).catch((e) => toast(e.message, "error"));
}

// ------------------------------------------------------------------ événements en direct

function addActivity(text, detail) {
  const li = el("li", {}, el("span", { class: "t" }, time(new Date())), el("span", {}, text), detail ? el("span", { class: "d" }, detail) : null);
  $("activity").prepend(li);
  while ($("activity").children.length > 150) $("activity").lastChild.remove();
}

function handleEvent(ev, replay = false) {
  const d = ev.data || {};
  switch (ev.type) {
    case "state": setState(d.state); break;
    case "levels":
      Orb.levels(d.mic || 0, d.out || 0);
      $("talk-ring").style.setProperty("--lvl", String(1 + (d.mic || 0) * 0.5));
      break;
    case "caption": showCaption(d.text); break;
    case "user_message": Thread.user(d.text, d.source); break;
    case "assistant_delta": Thread.delta(d.turn, d.text); break;
    case "assistant_message":
      Thread.final(d.turn, d.text);
      if (!replay) refreshState();
      break;
    case "tool_start":
      Thread.toolStart(d);
      if (!replay) addActivity(TOOL_LABELS[d.name] || d.name, JSON.stringify(d.args));
      break;
    case "tool_end":
      Thread.toolEnd(d);
      if (!replay) addActivity(`${d.ok ? "Réussi" : "Échec"} : ${TOOL_LABELS[d.name] || d.name}`, (d.summary || "").slice(0, 240));
      break;
    case "notification":
      Thread.note(d.text, "alert");
      if (!replay) toast(d.text, "alert");
      break;
    case "error":
      Thread.note(d.message, "error");
      if (!replay) toast(d.message, "error");
      break;
    case "consolidating":
      $("hint").textContent = "Je range cette conversation dans ma mémoire…";
      break;
    case "consolidated":
      Thread.note(`Conversation mémorisée : ${d.summary}`);
      if (!replay) { toast("Conversation rangée dans ma mémoire."); refreshState().then(() => renderInfo()); loadMemory(); }
      break;
    case "confirm_request": if (!replay) openConfirm(d); break;
    case "confirm_resolved":
      if (currentConfirm && currentConfirm.id === d.id) closeOverlay("confirm");
      if (!replay) addActivity(d.approved ? "Action autorisée" : "Action refusée");
      break;
    case "memory_changed": if (!replay) { loadMemory(); refreshState(); } break;
    case "wake": $("hint").textContent = "Je t'écoute…"; break;
    case "barge_in": addActivity("Interrompu à la voix"); break;
    case "renamed":
      if (!replay) { toast(`Je m'appelle maintenant ${d.name}. Appelle-moi par ce nom.`); refreshState(); }
      break;
    case "stopped": fadeCaption(); addActivity("Réponse interrompue"); break;
  }
}

function connectEvents() {
  const source = new EventSource(`/api/events?token=${encodeURIComponent(TOKEN)}`);
  let wasDown = false;
  source.onopen = () => {
    $("offline").hidden = true;
    if (wasDown) { refreshState(); loadMemory(); }
    wasDown = false;
  };
  source.onmessage = (msg) => handleEvent(JSON.parse(msg.data));
  source.onerror = () => { $("offline").hidden = false; wasDown = true; };
}

// ------------------------------------------------------------------ fenêtres

function openOverlay(id) { $(id).hidden = false; }
function closeOverlay(id) {
  $(id).hidden = true;
  if (id === "confirm") { currentConfirm = null; document.querySelector(".countdown").classList.remove("run"); }
}
const anyOverlay = () => ["palette", "confirm", "help"].find((id) => !$(id).hidden);

function openConfirm(d) {
  currentConfirm = d;
  $("confirm-action").textContent = `${info.name || "L'assistant"} veut ${d.action}.`;
  openOverlay("confirm");
  const ring = document.querySelector(".countdown");
  ring.classList.remove("run");
  void ring.getBoundingClientRect();
  ring.classList.add("run");  // 60 s, comme le délai d'attente de l'assistant
  $("confirm-yes").focus();
}
async function answerConfirm(approved) {
  if (!currentConfirm) return;
  const id = currentConfirm.id;
  closeOverlay("confirm");
  await api("POST", `/api/confirm/${id}`, { approved }).catch((e) => toast(e.message, "error"));
}

// ------------------------------------------------------------------ palette de commandes

const COMMANDS = [
  { label: "Faire le point", hint: "Météo, agenda, e-mails, tâches", run: () => api("POST", "/api/briefing") },
  { label: "Parler", hint: "Espace", run: () => talk() },
  { label: "Interrompre", hint: "Échap", run: () => api("POST", "/api/stop") },
  { label: "Nouvelle conversation", hint: "Range celle-ci dans la mémoire", run: () => api("POST", "/api/reset") },
  { label: "Mode concentration", hint: "F", run: () => toggleFocus() },
  { label: "Changer son nom", hint: "Il te demandera confirmation", run: () => {
    $("input").value = "À partir de maintenant, tu t'appelles ";
    $("input").focus();
  } },
  { label: "Ouvrir la mémoire", hint: "Panneau", run: () => showTab("memory") },
  { label: "Ouvrir les tâches", hint: "Panneau", run: () => showTab("tasks") },
  { label: "Voir l'activité", hint: "Panneau", run: () => showTab("activity") },
  { label: "État de l'ordinateur", hint: "Panneau", run: () => showTab("system") },
  { label: "Actualiser l'agenda et les e-mails", hint: "", run: () => refreshOverview(true) },
  { label: "Raccourcis clavier", hint: "?", run: () => openOverlay("help") },
];
let paletteIndex = 0;
let paletteItems = [];

function renderPalette() {
  const q = fold($("palette-input").value.trim());
  paletteItems = COMMANDS.filter((c) => !q || fold(c.label).includes(q));
  const text = $("palette-input").value.trim();
  if (text) paletteItems.push({ label: `Demander : « ${text} »`, hint: "Entrée", run: () => ask(text) });
  paletteIndex = Math.min(paletteIndex, paletteItems.length - 1);
  $("palette-list").replaceChildren(...paletteItems.map((c, i) =>
    el("li", { class: i === paletteIndex ? "active" : "", role: "option", onclick: () => runPalette(i) }, c.label, el("span", {}, c.hint))));
}
function openPalette() {
  $("palette-input").value = "";
  paletteIndex = 0;
  renderPalette();
  openOverlay("palette");
  $("palette-input").focus();
}
function runPalette(i) {
  const item = paletteItems[i];
  closeOverlay("palette");
  if (item) Promise.resolve(item.run()).catch((e) => toast(e.message, "error"));
}

// ------------------------------------------------------------------ actions

function talk() {
  if (!info.voice_enabled) { toast("Le micro n'est pas actif : écris ta demande.", "alert"); return; }
  api("POST", "/api/listen").catch((e) => toast(e.message, "error"));
}

function toggleFocus() {
  const on = !$("app").classList.contains("focus");
  $("app").classList.toggle("focus", on);
  try { localStorage.setItem("assistant-focus", on ? "1" : "0"); } catch (_) {}
}

// Mode Simple (par défaut) ou Détaillé ; en Simple, les panneaux s'ouvrent en tiroir depuis la barre d'icônes.
function setView(simple) {
  const app = $("app");
  app.classList.toggle("simple", simple);
  app.classList.remove("drawer-open");
  if (simple) app.classList.remove("focus");
  $("btn-view-toggle").textContent = simple ? "Détaillé" : "Simple";
  $("btn-view-toggle").setAttribute("aria-pressed", String(!simple));
  $("btn-view").title = simple ? "Passer en vue détaillée" : "Passer en vue simple";
  for (const b of document.querySelectorAll(".rail-btn[data-open]")) b.classList.remove("active");
  try { localStorage.setItem("assistant-view", simple ? "simple" : "detail"); } catch (_) {}
}

function toggleDrawer(name) {
  const app = $("app");
  const same = app.classList.contains("drawer-open") && document.querySelector(`.rail-btn[data-open="${name}"]`).classList.contains("active");
  if (same) return closeDrawer();
  showTab(name);
  if (name === "system") refreshSystem();
  app.classList.add("drawer-open");
  for (const b of document.querySelectorAll(".rail-btn[data-open]")) b.classList.toggle("active", b.dataset.open === name);
}

function closeDrawer() {
  $("app").classList.remove("drawer-open");
  for (const b of document.querySelectorAll(".rail-btn[data-open]")) b.classList.remove("active");
}

function showTab(name) {
  for (const t of document.querySelectorAll(".tab")) {
    const active = t.dataset.tab === name;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", String(active));
  }
  for (const p of document.querySelectorAll(".panel")) p.hidden = p.id !== `tab-${name}`;
  if ($("app").classList.contains("focus")) toggleFocus();
}

// ------------------------------------------------------------------ aujourd'hui

function tickClock() {
  const now = new Date();
  $("clock").textContent = $("clock-simple").textContent = time(now);
  $("date").textContent = now.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  renderTimersCountdown();
}

function untilLabel(start) {
  const minutes = Math.round((start - Date.now()) / 60000);
  if (minutes < 0 || minutes > 120) return null;
  return minutes <= 1 ? "maintenant" : minutes < 60 ? `dans ${minutes} min` : `dans ${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
}

async function refreshOverview(force = false) {
  try {
    if (force) await api("POST", "/api/overview/refresh");
    const o = await api("GET", "/api/overview");
    if (o.weather) {
      const w = o.weather;
      $("weather").classList.remove("muted");
      $("weather").replaceChildren(el("div", { class: "wx" },
        el("span", { class: "wx-temp" }, `${w.temp}°`),
        el("span", { class: "wx-text" }, w.text[0].toUpperCase() + w.text.slice(1)),
        el("span", { class: "wx-sub" }, `ressenti ${w.feels}° · vent ${w.wind} km/h`),
        el("div", { class: "wx-days" }, w.days.slice(1, 4).map((d) => el("div", { class: "wx-day" },
          el("b", {}, new Date(d.date).toLocaleDateString("fr-FR", { weekday: "short" })),
          el("span", {}, `${d.min}° · ${d.max}°`), el("span", { class: "muted" }, `pluie ${d.rain} %`))))));
    }
    if (o.events) renderEvents(o.events);
    if (o.emails) {
      $("mail-count").textContent = o.emails.length ? String(o.emails.length) : "";
      $("emails").replaceChildren(...(o.emails.length ? o.emails.slice(0, 5).map((m) => el("li", {},
        el("span", { class: "from" }, m.from.replace(/<.*>/, "").replace(/"/g, "").trim() || m.from),
        el("span", { class: "subj" }, m.subject || "(sans objet)")))
        : [el("li", { class: "muted" }, "Rien de nouveau.")]));
    }
    for (const e of o.errors || []) addActivity("Erreur", e);
  } catch (_) {}
}

function renderEvents(events) {
  const today = new Date().toDateString();
  const items = [];
  let lastDay = null;
  for (const ev of events) {
    const start = new Date(ev.start);
    const day = start.toDateString() === today ? "Aujourd'hui" : "Demain";
    if (day !== lastDay) { items.push(el("li", { class: "day-sep" }, day)); lastDay = day; }
    const soon = ev.all_day ? null : untilLabel(start);
    items.push(el("li", {},
      el("span", { class: "t" }, ev.all_day ? "jour" : time(start)),
      el("span", { class: "what" }, ev.title),
      soon ? el("span", { class: "soon" }, soon) : ev.location ? el("span", { class: "where" }, ev.location) : null));
  }
  $("events").replaceChildren(...(items.length ? items : [el("li", { class: "muted" }, "Rien de prévu aujourd'hui ni demain.")]));
}

let timers = [];
function renderTimersCountdown() {
  $("g-timers").hidden = timers.length === 0;
  $("timers").replaceChildren(...timers.map((t) => {
    const left = Math.max(0, Math.round((new Date(t.due) - Date.now()) / 1000));
    if (!timerTotals.has(t.id)) timerTotals.set(t.id, Math.max(left, 1));
    const pct = Math.min(100, (100 * left) / timerTotals.get(t.id));
    const mm = String(Math.floor(left / 60)).padStart(2, "0"), ss = String(left % 60).padStart(2, "0");
    return el("li", {}, el("div", { class: "row" }, el("span", {}, t.label), el("span", { class: "left" }, `${mm}:${ss}`)),
      el("div", { class: "bar" }, el("i", { style: `width:${pct}%` })));
  }));
}

// ------------------------------------------------------------------ système

function gauge(id, value) {
  const canvas = $(id);
  const ctx = canvas.getContext("2d");
  const w = canvas.width, c = w / 2, r = w * 0.4;
  const css = getComputedStyle(document.documentElement);
  const color = value >= 90 ? css.getPropertyValue("--danger") : value >= 75 ? css.getPropertyValue("--warn") : css.getPropertyValue("--accent");
  ctx.clearRect(0, 0, w, w);
  ctx.lineWidth = w * 0.08;
  ctx.lineCap = "round";
  ctx.strokeStyle = css.getPropertyValue("--line");
  ctx.beginPath(); ctx.arc(c, c, r, Math.PI * 0.75, Math.PI * 2.25); ctx.stroke();
  ctx.strokeStyle = color.trim();
  ctx.beginPath(); ctx.arc(c, c, r, Math.PI * 0.75, Math.PI * (0.75 + 1.5 * Math.min(1, value / 100))); ctx.stroke();
  ctx.fillStyle = css.getPropertyValue("--text");
  ctx.font = `600 ${w * 0.2}px ${css.getPropertyValue("--font-mono")}`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(`${Math.round(value)}`, c, c + w * 0.02);
}

async function refreshSystem() {
  try {
    const s = await api("GET", "/api/system");
    timers = s.timers || [];
    renderTimersCountdown();
    if ($("tab-system").hidden) return;
    gauge("gauge-cpu", s.cpu); gauge("gauge-ram", s.ram); gauge("gauge-disk", s.disk);
    $("gauge-bat-wrap").hidden = s.battery === null;
    if (s.battery !== null) gauge("gauge-bat", s.battery);
    $("sys-info").textContent = `${s.os} · ${s.ram_used_gb} / ${s.ram_total_gb} Go · allumé depuis ${s.uptime_h} h`;
  } catch (_) {}
}

// ------------------------------------------------------------------ mémoire et tâches

let searchTimer = null;
async function loadMemory() {
  try {
    const q = $("memory-search").value.trim();
    const m = await api("GET", `/api/memory?q=${encodeURIComponent(q)}`);
    $("facts-count").textContent = String(m.stats.facts);
    renderFacts(m.facts, Boolean(q));
    renderEpisodes(m.episodes);
    renderTasks(m.tasks);
  } catch (_) {}
}

function renderFacts(facts, searching) {
  if (!facts.length) {
    $("facts").replaceChildren(el("p", { class: "muted small" },
      searching ? "Aucun souvenir ne correspond." : "Rien pour l'instant. Parle-moi de toi, je retiendrai ce qui compte."));
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
  const text = el("span", { class: "text", title: `Retenu le ${new Date(f.created_at).toLocaleDateString("fr-FR")}` }, f.content);
  let confirming = false;
  text.addEventListener("blur", async () => {
    text.contentEditable = "false";
    const value = text.textContent.trim();
    if (value && value !== f.content) await api("PUT", `/api/facts/${f.id}`, { fact: value }).catch((e) => toast(e.message, "error"));
  });
  text.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); text.blur(); } });
  const del = el("button", { class: "mini", title: "Oublier", "aria-label": "Oublier ce souvenir" }, "✕");
  del.addEventListener("click", () => {
    if (!confirming) { confirming = true; del.textContent = "Oublier ?"; setTimeout(() => { confirming = false; del.textContent = "✕"; }, 3000); return; }
    api("DELETE", `/api/facts/${f.id}`).catch((e) => toast(e.message, "error"));
  });
  const levels = ["", "détail", "utile", "essentiel"];
  return el("div", { class: "fact" },
    el("button", { class: `imp i${f.importance}`, title: `Importance : ${levels[f.importance]} (cliquer pour changer)`, "aria-label": "Changer l'importance",
      onclick: () => api("PUT", `/api/facts/${f.id}`, { importance: (f.importance % 3) + 1 }).catch((e) => toast(e.message, "error")) }),
    text,
    el("span", { class: "acts" },
      el("button", { class: "mini", title: "Modifier", "aria-label": "Modifier", onclick: () => { text.contentEditable = "true"; text.focus(); } }, "✎"),
      del));
}

function renderEpisodes(episodes) {
  $("episodes").replaceChildren(...(episodes.length ? episodes.map((e) => el("li", {},
    el("span", { class: "when" }, new Date(e.started_at).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" }),
      el("button", { class: "mini", title: "Supprimer ce résumé", onclick: () => api("DELETE", `/api/episodes/${e.id}`).then(loadMemory) }, "✕")),
    el("span", {}, e.summary)))
    : [el("li", { class: "muted small" }, "Chaque conversation terminée est résumée ici.")]));
}

function taskItem(t) {
  const today = new Date().toISOString().slice(0, 10);
  const box = el("input", { type: "checkbox", checked: t.done, "aria-label": `Terminer ${t.title}`,
    onchange: () => api("POST", `/api/tasks/${t.id}/toggle`, { done: box.checked }).catch((e) => toast(e.message, "error")) });
  return el("li", { class: `task${t.done ? " done" : ""}` }, box,
    el("span", { class: "title" }, t.title),
    t.due ? el("span", { class: `due${!t.done && t.due.slice(0, 10) < today ? " late" : ""}` },
      new Date(t.due.slice(0, 10)).toLocaleDateString("fr-FR", { day: "numeric", month: "short" })) : null,
    el("button", { class: "mini", title: "Supprimer", "aria-label": "Supprimer", onclick: () => api("DELETE", `/api/tasks/${t.id}`) }, "✕"));
}

function renderTasks(tasks) {
  const open = tasks.filter((t) => !t.done);
  $("tasks-count").textContent = open.length ? `${open.length} en cours` : "";
  $("tasks").replaceChildren(...(tasks.length ? tasks.map(taskItem)
    : [el("li", { class: "muted small" }, "Aucune tâche. Dis-moi « ajoute … à ma liste ».")]));
  const today = new Date().toISOString().slice(0, 10);
  const soon = open.filter((t) => !t.due || t.due.slice(0, 10) <= today).slice(0, 6);
  $("tasks-today").replaceChildren(...(soon.length ? soon.map(taskItem) : [el("li", { class: "muted" }, "Rien d'urgent.")]));
}

// ------------------------------------------------------------------ liaison de l'interface

function typing(e) {
  const t = e.target;
  return t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || t.isContentEditable;
}

function bindUi() {
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("input").value.trim();
    if (!text) return;
    $("input").value = "";
    ask(text);
  });
  $("btn-talk").addEventListener("click", talk);
  $("btn-stop").addEventListener("click", () => api("POST", "/api/stop"));
  $("btn-briefing").addEventListener("click", () => api("POST", "/api/briefing"));
  $("btn-reset").addEventListener("click", () => api("POST", "/api/reset"));
  $("btn-palette").addEventListener("click", openPalette);
  $("btn-refresh").addEventListener("click", () => refreshOverview(true));
  $("confirm-yes").addEventListener("click", () => answerConfirm(true));
  $("confirm-no").addEventListener("click", () => answerConfirm(false));
  $("palette-input").addEventListener("input", () => { paletteIndex = 0; renderPalette(); });
  for (const id of ["palette", "help"]) $(id).addEventListener("click", (e) => { if (e.target === $(id)) closeOverlay(id); });
  $("memory-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadMemory, 220); });
  $("fact-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fact = $("fact-input").value.trim();
    if (!fact) return;
    await api("POST", "/api/facts", { fact, importance: Number($("fact-importance").value) }).then(() => toast("C'est retenu.")).catch((err) => toast(err.message, "error"));
    $("fact-input").value = "";
  });
  $("task-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = $("task-input").value.trim();
    if (!title) return;
    await api("POST", "/api/tasks", { title, due: $("task-due").value }).catch((err) => toast(err.message, "error"));
    $("task-input").value = ""; $("task-due").value = "";
  });
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => { showTab(tab.dataset.tab); if (tab.dataset.tab === "system") refreshSystem(); });

  for (const b of document.querySelectorAll(".rail-btn[data-open]")) b.addEventListener("click", () => toggleDrawer(b.dataset.open));
  $("btn-view").addEventListener("click", () => setView(false));
  $("btn-view-toggle").addEventListener("click", () => setView(!$("app").classList.contains("simple")));

  document.addEventListener("keydown", (e) => {
    const overlay = anyOverlay();
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); overlay === "palette" ? closeOverlay("palette") : openPalette(); return; }
    if (overlay === "palette") {
      if (e.key === "ArrowDown") { e.preventDefault(); paletteIndex = Math.min(paletteIndex + 1, paletteItems.length - 1); renderPalette(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); paletteIndex = Math.max(paletteIndex - 1, 0); renderPalette(); }
      else if (e.key === "Enter") { e.preventDefault(); runPalette(paletteIndex); }
      else if (e.key === "Escape") closeOverlay("palette");
      return;
    }
    if (overlay === "confirm") {
      if (e.key === "Escape") answerConfirm(false);
      return;
    }
    if (e.key === "F11" && window.pywebview && window.pywebview.api) { e.preventDefault(); window.pywebview.api.toggle_fullscreen(); return; }
    if (e.key === "Escape") {
      if (overlay) closeOverlay(overlay);
      else if ($("app").classList.contains("drawer-open")) closeDrawer();
      else api("POST", "/api/stop");
      return;
    }
    if (typing(e)) return;
    if (e.key === " ") { e.preventDefault(); talk(); }
    else if (e.key === "/") { e.preventDefault(); $("input").focus(); }
    else if (e.key.toLowerCase() === "f") toggleFocus();
    else if (e.key === "?") openOverlay("help");
  });
}

async function main() {
  bindUi();
  renderSuggestions();
  tickClock();
  setInterval(tickClock, 1000);
  try { if (localStorage.getItem("assistant-focus") === "1") $("app").classList.add("focus"); } catch (_) {}
  let simple = true;
  try { simple = localStorage.getItem("assistant-view") !== "detail"; } catch (_) {}
  setView(simple);
  if (!TOKEN) { $("lock").hidden = false; return; }
  const state = await refreshState();
  if (!state) return;
  setState(state.state);
  for (const ev of state.history) handleEvent(ev, true);
  for (const c of state.pending_confirms) openConfirm(c);
  connectEvents();
  refreshSystem();
  setInterval(refreshSystem, 3000);
  refreshOverview();
  setInterval(refreshOverview, 120000);
  loadMemory();
}

main();
