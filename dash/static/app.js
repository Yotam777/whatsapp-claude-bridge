// Dashboard front-end: renders /data, sends actions to /action. Hebrew (default) and English.
const $ = id => document.getElementById(id);
const post = a => fetch("/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(a) }).then(r => r.json());
let curChat = ""; try { curChat = localStorage.getItem("wa-dash-chat") || ""; } catch (e) {}
let syncMap = {}; try { syncMap = JSON.parse(localStorage.getItem("wa-dash-sync") || "{}"); } catch (e) {}
const PURPOSE = { dev: "פיתוח הגשר והכלי", general: "כללית", "4m": "עבודת 4M", local: "מקומית (בלי ווצאפ)" };
const MODEL_HE = m => ({ auto: "אוטו" })[m] || m;

// ---- language ----
let lang = "he";
try { lang = localStorage.getItem("wa-dash-lang") || "he"; } catch (e) {}
const L = (he, en) => lang === "en" ? en : he;
const LOCALE = () => lang === "en" ? "en-GB" : "he-IL";
// static texts carry data-en="..."; their Hebrew original is remembered on first run
function translateStatic() {
  document.documentElement.lang = lang; document.documentElement.dir = lang === "en" ? "ltr" : "rtl";
  document.title = L("ווצאפ ↔ Claude", "WhatsApp ↔ Claude");
  document.querySelectorAll("[data-en]").forEach(el => { if (el._he === undefined) el._he = el.textContent; el.textContent = lang === "en" ? el.dataset.en : el._he; });
  $("langBtn").textContent = lang === "en" ? "עב" : "EN";
  $("say").placeholder = L("המשך את השיחה עם הכלי… (Enter לשליחה, Shift+Enter לשורה חדשה)", "Continue the conversation… (Enter to send, Shift+Enter for a new line)");
}

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
// WhatsApp formatting: *bold*, _italic_, ~strike~ (applied after escaping)
const wa = s => esc(s).replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<strong>$2</strong>").replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>").replace(/(^|[\s(])~([^~\n]+)~/g, "$1<s>$2</s>");
const toDate = s => new Date(String(s).replace(" ", "T"));
const fmt = s => s ? toDate(s).toLocaleString(LOCALE(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const hhmm = t => new Date(t).toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit" });
const mins = s => s ? Math.max(0, Math.floor((Date.now() - toDate(s)) / 60000)) : null;
function durMin(m) {
  const d = Math.floor(m / 1440), h = Math.floor(m / 60) % 24, mm = Math.round(m % 60);
  if (d) return L(`${d} ימים ו-${h} שעות`, `${d} d ${h} h`); if (h) return L(`${h} שעות ו-${mm} דקות`, `${h} h ${mm} min`); return L(`${mm} דקות`, `${mm} min`);
}
const dur = s => { const m = mins(s); return m === null ? "—" : durMin(m); };
function ago(s) {
  const m = mins(s); if (m === null) return ""; if (m < 1) return L("עכשיו", "now");
  if (m < 60) return L(`לפני ${m} דק'`, `${m} min ago`); if (m < 1440) return L(`לפני ${Math.floor(m / 60)} שע'`, `${Math.floor(m / 60)} h ago`);
  return L(`לפני ${Math.floor(m / 1440)} ימים`, `${Math.floor(m / 1440)} days ago`);
}
function toast(t) { const el = $("toast"); el.textContent = t; el.classList.add("show"); clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove("show"), 2600); }
async function act(a, msg) {
  try {
    const j = await (await fetch("/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(a) })).json();
    if (j.error) toast(L("שגיאה: ", "Error: ") + j.error); else if (msg) toast(msg);
    load(); return j;
  } catch (e) { toast(L("הפעולה נכשלה", "Action failed")); }
}

// ---- theme ----
const themeKey = "wa-dash-theme";
let charts = [], last = null;
function applyTheme(t) { document.documentElement.dataset.theme = t; try { localStorage.setItem(themeKey, t); } catch (e) {} if (last) drawCharts(last); }
try { const t = localStorage.getItem(themeKey); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
$("themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme === "dark" || (document.documentElement.dataset.theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  applyTheme(dark ? "light" : "dark");
};
$("langBtn").onclick = () => { lang = lang === "en" ? "he" : "en"; try { localStorage.setItem("wa-dash-lang", lang); } catch (e) {} translateStatic(); if (last) render(last); };
translateStatic();

// ---- header popover (sending to others) ----
$("modeBtn").onclick = e => { e.stopPropagation(); const o = $("modePop").hidden; $("modePop").hidden = !o; $("modeBtn").setAttribute("aria-expanded", o); };
document.addEventListener("click", e => { if (!e.target.closest(".pop-wrap")) { $("modePop").hidden = true; $("modeBtn").setAttribute("aria-expanded", false); } });

// ---- controls ----
$("pauseBtn").onclick = () => act({ do: "pause", hours: 6 }, L("השליחה לאחרים הושהתה ל-6 שעות", "Sending to others paused for 6 hours"));
$("resumeBtn").onclick = () => act({ do: "resume" }, L("השליחה חודשה", "Sending resumed"));
$("backupBtn").onclick = () => act({ do: "backup" }, L("הגיבוי התחיל ברקע", "Backup started"));
document.querySelectorAll("[data-restart]").forEach(b => b.onclick = () => {
  const what = { bridge: L("הגשר", "the bridge"), agent: L("הסוכן", "the agent"), all: L("את הגשר ואת הסוכן", "the bridge and the agent") }[b.dataset.restart];
  if (confirm(L(`להפעיל מחדש את ${what}? זה לוקח כ-20 שניות.`, `Restart ${what}? It takes about 20 seconds.`))) act({ do: "restart", what: b.dataset.restart }, L(`${what} מופעל מחדש…`, `Restarting ${what}…`));
});
$("updBtn").onclick = async () => {
  $("updNote").textContent = L("בודק…", "Checking…");
  const j = await act({ do: "update_check" });
  if (!j) return;
  if (j.available) {
    $("updNote").innerHTML = L(`גרסה ${esc(j.version)} זמינה. `, `Version ${esc(j.version)} is available. `);
    const b = document.createElement("button"); b.className = "btn primary small"; b.textContent = L("עדכן עכשיו", "Update now");
    b.onclick = () => act({ do: "update_apply" }, L("מתעדכן — החלון ייסגר ויפתח מחדש", "Updating — the window will close and reopen")); $("updNote").appendChild(b);
  } else $("updNote").textContent = j.message || L("הגרסה עדכנית", "Up to date");
};
$("toolUpdBtn").onclick = async () => {
  $("toolUpdNote").textContent = L("בודק…", "Checking…");
  const j = await act({ do: "tool_update_check", force: true });
  if (!j) return;
  if (j.error && !j.latest) { $("toolUpdNote").textContent = L("לא ניתן לבדוק כרגע (אין אינטרנט?)", "Can't check right now (offline?)"); return; }
  if (!j.available) { $("toolUpdNote").textContent = L(`הכלי עדכני (${j.current})`, `Up to date (${j.current})`); return; }
  $("toolUpdNote").innerHTML = L(`גרסה ${esc(j.latest)} זמינה. `, `Version ${esc(j.latest)} is available. `);
  const b = document.createElement("button"); b.className = "btn primary small"; b.textContent = L("עדכן עכשיו", "Update now");
  b.onclick = () => { if (confirm(L("העדכון עוצר את הגשר והסוכן לדקה-שתיים, בודק את עצמו, וחוזר לגרסה הקודמת אם משהו נכשל. להמשיך?",
    "The update stops the bridge and the agent for a minute or two, tests itself, and rolls back if anything fails. Continue?")))
    act({ do: "tool_update_apply" }, L("העדכון התחיל בחלון נפרד", "Update started in a separate window")); };
  $("toolUpdNote").appendChild(b);
};

// ---- maintainer: save to git / release (shown only when config.json has "maintainer": true) ----
async function gitRefresh() {
  const j = await act({ do: "git_status" }); if (!j || j.error) return;
  $("gitInfo").textContent = L(`ענף ${j.branch} · גרסה ${j.version} · ${j.changed} קבצים לא שמורים`, `Branch ${j.branch} · version ${j.version} · ${j.changed} unsaved files`);
  if (!$("pubVer").value) $("pubVer").value = j.next;
}
$("gitCommitBtn").onclick = async () => { await act({ do: "git_commit", notes: $("pubNotes").value }, L("נשמר בגיט", "Saved to git")); gitRefresh(); };
$("releaseBtn").onclick = () => {
  const v = $("pubVer").value;
  if (confirm(L(`לשחרר את גרסה ${v} לכל המשתמשים? הבדיקות ירוצו בחלון נפרד, ואם יעברו — הגרסה תתפרסם.`, `Release version ${v} to all users? Checks run in a separate window and the release publishes if they pass.`)))
    act({ do: "release", version: v, notes: $("pubNotes").value }, L("השחרור התחיל בחלון נפרד", "Release started in a separate window"));
};
let pubShown = false;

// ---- display settings (saved on this computer) ----
const VIEW_KEY = "wa-dash-view", STEPS = [0.85, 0.92, 1, 1.1, 1.2, 1.32, 1.45];
let view = { chat: 2, ui: 2, font: "", bg: "" };
try { view = Object.assign(view, JSON.parse(localStorage.getItem(VIEW_KEY) || "{}")); } catch (e) {}
function applyView() {
  const r = document.documentElement.style;
  r.setProperty("--chat-scale", STEPS[view.chat]); r.setProperty("--ui-scale", STEPS[view.ui]);
  if (view.font) r.setProperty("--ui-font", view.font); else r.removeProperty("--ui-font");
  $("chat").dataset.bg = view.bg || "";
  $("chatFsOut").textContent = Math.round(100 * STEPS[view.chat]) + "%"; $("uiFsOut").textContent = Math.round(100 * STEPS[view.ui]) + "%";
  $("fontSel").value = view.font || ""; $("bgSel").value = view.bg || "";
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(view)); } catch (e) {}
}
document.querySelectorAll("[data-step]").forEach(b => b.onclick = () => {
  const k = b.dataset.step; view[k] = Math.max(0, Math.min(STEPS.length - 1, view[k] + Number(b.dataset.d))); applyView();
});
$("fontSel").onchange = e => { view.font = e.target.value; applyView(); };
$("bgSel").onchange = e => { view.bg = e.target.value; applyView(); };
$("viewReset").onclick = () => { view = { chat: 2, ui: 2, font: "", bg: "" }; applyView(); };
applyView();

// ---- continue the conversation from here (PC chat, optionally also posted to the WhatsApp group) ----
const say = $("say");
const grow = () => { say.style.height = "auto"; say.style.height = Math.min(140, say.scrollHeight) + "px"; };
say.addEventListener("input", grow);
say.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
let outbox = [];  // messages typed here and not yet seen in the conversation: shown at once as "sending…"
let sending = false, attached = [];
const baseName = pth => String(pth).split(/[\\/]/).pop();
function drawAttach() {
  const row = $("attachRow"); row.hidden = !attached.length;
  row.innerHTML = attached.map((f, i) => `<span class="attach"><span title="${esc(f.name)}">📎 ${esc(f.name)}</span><button type="button" data-i="${i}" aria-label="${L("הסר קובץ", "Remove file")}">✕</button></span>`).join("");
  row.querySelectorAll("button").forEach(btn => btn.onclick = () => { attached.splice(+btn.dataset.i, 1); drawAttach(); });
}
async function upload(file) {
  try {
    const j = await (await fetch("/upload?name=" + encodeURIComponent(file.name || "image.png"), { method: "POST", body: file })).json();
    if (j.ok) attached.push(j); else toast(L("העלאה נכשלה: ", "Upload failed: ") + (j.error || ""));
  } catch (e) { toast(L("העלאה נכשלה", "Upload failed")); }
  drawAttach();
}
say.addEventListener("paste", e => { const fs = [...((e.clipboardData && e.clipboardData.files) || [])]; if (fs.length) { e.preventDefault(); fs.forEach(upload); } });
$("attachBtn").onclick = () => $("fileInp").click();
$("fileInp").onchange = e => { [...e.target.files].forEach(upload); e.target.value = ""; };
const dropZone = document.querySelector(".chat-card");
["dragenter", "dragover"].forEach(ev => dropZone.addEventListener(ev, e => { if ([...(e.dataTransfer.types || [])].includes("Files")) { e.preventDefault(); dropZone.classList.add("composer-drop"); } }));
["dragleave", "drop"].forEach(ev => dropZone.addEventListener(ev, () => dropZone.classList.remove("composer-drop")));
dropZone.addEventListener("drop", e => { if (e.dataTransfer.files.length) { e.preventDefault(); [...e.dataTransfer.files].forEach(upload); } });
$("syncChk").onchange = e => { syncMap[curChat] = e.target.checked; try { localStorage.setItem("wa-dash-sync", JSON.stringify(syncMap)); } catch (er) {} sendHint(); };
$("stopBtn").onclick = () => { $("stopBtn").disabled = true; act({ do: "stop", chat: curChat }, L("עוצר את העבודה…", "Stopping…")).then(() => setTimeout(() => { $("stopBtn").disabled = false; load(); }, 2500)); };
function sendHint() {
  const local = curChat === "local", sync = $("syncChk").checked;
  $("syncLbl").hidden = local;
  $("sendHint").textContent = local ? L("שיחה מקומית — התשובות מוצגות כאן בלבד, בלי ווצאפ.", "Local chat — answers show here only, no WhatsApp.")
    : sync ? L("ההודעה והתשובה נשלחות גם לקבוצה בווצאפ.", "Message and answer are also posted to the WhatsApp group.")
    : L("נשאר כאן בלבד. אפשר להמשיך מהנייד מאותה נקודה.", "Stays here only. You can continue from the phone from this point.");
}
$("composer").onsubmit = async e => {
  e.preventDefault();
  const text = say.value.trim(); if ((!text && !attached.length) || sending) return;
  const isLocal = curChat === "local", sync = !isLocal && $("syncChk").checked;
  if (sync && attached.length > 1) { toast(L("בשליחה לווצאפ אפשר לצרף קובץ אחד", "Only one file when posting to WhatsApp")); return; }
  sending = true;
  const files = attached, shown = (text + files.map(f => "\n📎 " + baseName(f.path)).join("")).trim();
  const item = { text: shown, at: new Date().toISOString(), state: "sending", chat: curChat };
  outbox.push(item); say.value = ""; attached = []; drawAttach(); grow(); drawOutbox(true);
  let ok = false;
  try {
    const body = (isLocal || !sync) ? { do: "say_pc", chat: curChat, text, sync: 0, attach: files.map(f => f.path) }
                                    : { do: "say", chat: curChat, text, media_path: files[0] && files[0].path };
    const j = await post(body);
    ok = !!j && !j.error; item.state = ok ? "sent" : "failed"; item.err = j && j.error;
    if (ok && j.agent_alive === false) toast(L("הסוכן לא פעיל כרגע — ההודעה תטופל כשיחזור", "The agent is not running — it will be handled when it is back"));
  } catch (err) { item.state = "failed"; item.err = String(err); }
  sending = false;
  if (!ok) { toast(L("השליחה נכשלה — הטקסט חזר לתיבה", "Send failed — text is back in the box")); outbox = outbox.filter(x => x !== item); if (!say.value) { say.value = text; grow(); } attached = files; drawAttach(); }
  else if (sync && files.length) outbox = outbox.filter(x => x !== item);
  drawOutbox(true); setTimeout(load, 1200); setTimeout(load, 5000);
};
function drawOutbox(scroll) {
  const chat = $("chat"); chat.querySelectorAll(".bubble.outbox").forEach(x => x.remove());
  const shown = new Set((last && last.conversation || []).filter(m => !m.tool).map(m => (m.content || "").trim()));
  outbox = outbox.filter(x => !(x.state === "sent" && shown.has(x.text)) && Date.now() - Date.parse(x.at) < 5 * 60000);
  outbox.filter(x => x.chat === curChat).forEach(x => chat.insertAdjacentHTML("beforeend", `<div class="bubble me outbox ${x.state}"><span class="who">${L("אתה", "You")}</span>${esc(x.text)}<span class="meta">${x.state === "sending" ? L("⏳ שולח…", "⏳ sending…") : L("✓ נשלח", "✓ sent")}</span></div>`));
  if (scroll) chat.scrollTop = chat.scrollHeight;
}

// ---- today panel (collapsed by default; opens at the expense of the chat height) ----
let todayOpen = false;
try { todayOpen = localStorage.getItem("wa-dash-today") === "1"; } catch (e) {}
function setToday(v) {
  todayOpen = v; $("todayPanel").hidden = !v; $("todayBtn").setAttribute("aria-expanded", v);
  try { localStorage.setItem("wa-dash-today", v ? "1" : "0"); } catch (e) {}
  const c = $("chat"); c.scrollTop = c.scrollHeight;
}
$("todayBtn").onclick = () => setToday(!todayOpen);
setToday(todayOpen);

// ---- detail drawer ----
let recvFocus = false;
let drawerKind = null, sentAll = false, recvQ = "", recvChat = "";
const DRAWERS = () => ({
  drafts: L("📝 טיוטות", "📝 Drafts"), sent: L("📤 נשלחו", "📤 Sent"), received: L("📥 התקבלו", "📥 Received"),
  tr: L("🎙️ תמלולים", "🎙️ Transcripts"), cost: L("💲 עלות", "💲 Cost"),
});
function openDrawer(kind) {
  drawerKind = kind; $("scrim").hidden = false; $("drawer").hidden = false; renderDrawer(true); $("drawerClose").focus();
}
function closeDrawer() { drawerKind = null; $("scrim").hidden = true; $("drawer").hidden = true; }
document.querySelectorAll("[data-detail]").forEach(b => b.onclick = () => openDrawer(b.dataset.detail));
$("scrim").onclick = closeDrawer; $("drawerClose").onclick = closeDrawer;
document.addEventListener("keydown", e => { if (e.key === "Escape" && drawerKind) closeDrawer(); });

const waPhone = r => { const x = String(r || ""); return /^\d{8,15}$/.test(x) ? x : /^(\d{8,15})@s\.whatsapp\.net$/.test(x) ? x.split("@")[0] : null; };
const STATUS = () => ({ pending: L("ממתינה", "pending"), sending: L("נשלחת…", "sending…"), sent: L("נשלחה", "sent"), discarded: L("נמחקה", "deleted"), cancelled: L("בוטלה", "cancelled"), failed: L("נכשלה", "failed") });
const empty = t => `<p class="empty">${t}</p>`;

function draftCard(r) {
  const st = STATUS(), ph = waPhone(r.recipient), pend = r.status === "pending";
  return `<article class="item ${pend ? "pending" : r.status === "sent" ? "ok-i" : r.status === "failed" ? "bad-i" : ""}">
    <div class="item-head"><span class="avatar">${esc(String(r.name || r.recipient || "?").trim().charAt(0))}</span><div class="who-col"><strong>${esc(r.name || r.recipient)}</strong><span class="muted" style="font-size:.78rem">${fmt(r.created)} · ${ago(r.created)}${ph ? " · +" + esc(ph) : ""}</span></div><span class="pill ${esc(r.status)}">${st[r.status] || esc(r.status)}</span></div>
    <div class="item-body">${esc(r.message)}</div>
    ${pend ? `<div class="item-actions">
      <button class="btn small primary" data-approve="${r.id}">✅ ${L("אשר ושלח", "Approve & send")}</button>
      <button class="btn small danger" data-del="${r.id}">🗑 ${L("מחק", "Delete")}</button>
      ${ph ? `<a class="btn small" target="_blank" rel="noopener" href="https://wa.me/${ph}?text=${encodeURIComponent(r.message)}">↗ ${L("פתח בווצאפ", "Open in WhatsApp")}</a>` : ""}
    </div>` : ""}
  </article>`;
}

function drawerContent(kind, d) {
  if (kind === "drafts") {
    const all = d.drafts || [], pend = all.filter(x => x.status === "pending"), rest = all.filter(x => x.status !== "pending");
    return (pend.length ? `<div class="sum-row"><span class="stat warn">${L(`${pend.length} ממתינות לאישור שלך`, `${pend.length} awaiting your approval`)}</span></div>` + pend.map(draftCard).join("") : empty(L("אין טיוטות ממתינות 🎉", "No pending drafts 🎉")))
      + (rest.length ? `<h3 class="muted" style="font-size:.82rem;margin:8px 0 0">${L("היסטוריה", "History")}</h3>` + rest.map(draftCard).join("") : "");
  }
  if (kind === "sent") {
    const all = d.sent || [], others = all.filter(r => !r.own), s = sentAll ? all : others;
    const chips = `<div class="sum-row"><button class="btn small${sentAll ? "" : " primary"}" data-sent="0">${L("לאנשים אחרים", "To others")} · ${others.length}</button>
      <button class="btn small${sentAll ? " primary" : ""}" data-sent="1">${L("הכול (כולל תשובות בקבוצה)", "All (incl. group replies)")} · ${all.length}</button></div>`;
    return chips + (s.length ? s.map(r => `<article class="item ${r.ok ? "ok-i" : "bad-i"}"><div class="item-head"><span class="avatar">${esc(String(r.recipient || "?").trim().charAt(0))}</span><div class="who-col"><strong>${esc(r.recipient)}</strong><span class="muted" style="font-size:.78rem">${fmt(r.timestamp)} · ${ago(r.timestamp)}</span></div>
      <span class="pill ${r.ok ? "sent" : "failed"}">${r.ok ? L("נשלחה", "sent") : L("נכשלה", "failed")}</span></div>
      ${r.text ? `<div class="item-body">${esc(r.text)}</div>` : ""}${r.ok ? "" : `<span class="bad" style="font-size:.8rem">${esc(String(r.detail).slice(0, 160))}</span>`}</article>`).join("")
      : empty(sentAll ? L("אין שליחות", "Nothing sent") : L("לא נשלחה אף הודעה לאנשים אחרים — הכול טיוטות 👍", "Nothing sent to others — drafts only 👍")));
  }
  if (kind === "received") {
    const MT = { audio: L("🎙️ הודעה קולית", "🎙️ voice note"), image: L("🖼️ תמונה", "🖼️ image"), document: L("📎 קובץ", "📎 file"), video: L("🎬 וידאו", "🎬 video") };
    const all = d.received || [], q = recvQ.trim().toLowerCase();
    const txt = r => r.content || (r.media_type ? MT[r.media_type] || r.media_type : "");
    const hit = r => !q || [r.sender, r.chat, txt(r)].some(x => String(x || "").toLowerCase().includes(q));
    const chats = {}; all.forEach(r => { const k = r.chat || "?"; (chats[k] = chats[k] || { n: 0, group: r.group, last: r.timestamp, items: [] }).n++; if (hit(r) && (!recvChat || recvChat === k)) chats[k].items.push(r); });
    const order = Object.entries(chats).sort((x, y) => String(y[1].last).localeCompare(String(x[1].last)));
    const chips = `<div class="sum-row"><button class="btn small${recvChat ? "" : " primary"}" data-chat="">${L("כל השיחות", "All chats")} · ${all.length}</button>` +
      order.slice(0, 12).map(([k, v]) => `<button class="btn small${recvChat === k ? " primary" : ""}" data-chat="${esc(k)}">${v.group ? "👥 " : ""}${esc(k)} · ${v.n}</button>`).join("") + `</div>`;
    const search = `<input id="recvSearch" class="search" type="search" value="${esc(recvQ)}" placeholder="${L("חיפוש לפי שם, שיחה או מילה…", "Search name, chat or word…")}" aria-label="${L("חיפוש בהודעות שהתקבלו", "Search received")}">`;
    const groups = order.filter(([, v]) => v.items.length).map(([k, v]) => `<details class="item" open><summary class="item-head"><span class="avatar">${v.group ? "👥" : esc(String(k).charAt(0))}</span><div class="who-col"><strong>${esc(k)}</strong><span class="muted" style="font-size:.78rem">${v.items.length} ${L("הודעות", "messages")} · ${L("אחרונה", "last")} ${hhmm(toDate(v.last))}</span></div></summary>
      ${v.items.map(r => `<div class="msg-row${v.group ? "" : " solo"}"><span class="muted">${hhmm(toDate(r.timestamp))}</span>${v.group ? `<strong>${esc(r.sender)}</strong>` : ""}<span>${esc(txt(r))}</span></div>`).join("")}</details>`).join("");
    return search + chips + (groups || empty(all.length ? L("אין תוצאות לחיפוש", "No matches") : L("לא התקבלו הודעות היום", "No messages received today")));
  }
  if (kind === "tr") {
    const t = d.transcripts || [];
    return t.length ? t.map(r => `<article class="item"><div class="item-head"><strong>${esc(r.sender)}</strong><span class="muted" style="font-size:.78rem">${fmt(r.ts)}</span></div><div class="item-body">${esc(r.text)}</div></article>`).join("") : empty(L("אין תמלולים", "No transcripts"));
  }
  if (kind === "cost") {
    const a = d.agent || {}, c = d.costs || [], runs = d.runs_today || [], max = Math.max(0.01, ...c.map(x => x.cost));
    const byModel = {}; runs.forEach(r => { const m = r.model || "?"; byModel[m] = byModel[m] || { n: 0, cost: 0 }; byModel[m].n++; byModel[m].cost += r.cost || 0; });
    const mmax = Math.max(0.01, ...Object.values(byModel).map(x => x.cost)), pct = Math.min(100, 100 * (a.spent_today || 0) / (a.budget || 1));
    return `<div class="sum-row"><span class="stat">${L("היום", "Today")}: $${(a.spent_today ?? 0).toFixed(2)}</span><span class="stat">${L("תקרה", "Cap")}: $${a.budget}</span><span class="stat">${L("ריצות היום", "Runs today")}: ${runs.length}</span></div>
      <div class="item"><div class="item-head"><strong>${L("ניצול התקרה היומית", "Daily cap used")}</strong><span class="muted">${Math.round(pct)}%</span></div><span class="meter${pct > 80 ? " hot" : ""}"><span style="width:${pct}%"></span></span></div>
      ${Object.keys(byModel).length ? `<div class="item"><div class="item-head"><strong>${L("היום לפי מודל", "Today by model")}</strong></div>${Object.entries(byModel).sort((x, y) => y[1].cost - x[1].cost).map(([m, v]) => `<div class="bar-row" style="grid-template-columns:90px 1fr 120px"><span>${m === "?" ? L("לא נרשם", "not recorded") : esc(m)}</span><span class="meter"><span style="width:${100 * v.cost / mmax}%"></span></span><span>$${v.cost.toFixed(2)} · ${v.n} ${L("ריצות", "runs")}</span></div>`).join("")}</div>` : ""}
      <div class="item"><div class="item-head"><strong>${L("לפי שיחה — היום", "By conversation — today")}</strong></div>${(d.groups || []).map(g => `<div class="conv-row"><span>${g.jid === "local" ? "💻" : "👥"} ${esc(g.name)} · <span class="muted">${esc(MODEL_HE(g.model))}</span></span><span>$${g.spent} / $${g.budget}</span><span class="meter${g.spent >= g.budget ? " hot" : ""}"><span style="width:${Math.min(100, 100 * g.spent / (g.budget || 1))}%"></span></span></div>`).join("")}<p class="note tight">${L("תקרה לכל שיחה בנפרד — לשינוי: בכרטיס \"השיחה הנוכחית\" ← 💲 תקרה", "Each conversation has its own cap — change it in the 'This conversation' card")}</p></div>
      <div class="item"><div class="item-head"><strong>${L("לפי יום", "By day")}</strong></div>${c.slice().reverse().map(r => `<div class="bar-row"><span class="muted">${r.day.slice(8, 10)}.${r.day.slice(5, 7)}</span><span class="meter"><span style="width:${100 * r.cost / max}%"></span></span><span>$${r.cost}</span></div>`).join("")}</div>
      <div class="item"><div class="item-head"><strong>${L("ריצות Claude היום", "Claude runs today")}</strong></div>${runs.length ? runs.map(r => `<div class="bar-row" style="grid-template-columns:52px 70px 1fr 70px"><span class="muted">${hhmm(toDate(r.at))}</span><span class="muted">${esc(r.model || "")}</span><span class="${r.ok ? "" : "bad"}">${r.ok ? "" : "✖ "}${Math.round(r.seconds)} ${L("שנ'", "s")}</span><span>$${(r.cost || 0).toFixed(3)}</span></div>`).join("") : `<span class="muted">—</span>`}</div>`;
  }
  return "";
}
function renderDrawer(reset) {
  if (!drawerKind || !last || drawerKind === "ctx") return;
  const names = DRAWERS(), body = $("drawerBody"), top = body.scrollTop;
  $("drawerTitle").textContent = names[drawerKind];
  $("drawerTabs").innerHTML = Object.entries(names).map(([k, n]) => `<button role="tab" aria-selected="${k === drawerKind}" data-k="${k}">${n}</button>`).join("");
  $("drawerTabs").querySelectorAll("button").forEach(b => b.onclick = () => { drawerKind = b.dataset.k; renderDrawer(true); });
  body.innerHTML = drawerContent(drawerKind, last);
  body.querySelectorAll("[data-chat]").forEach(b => b.onclick = () => { recvChat = b.dataset.chat; renderDrawer(true); });
  const rs = $("recvSearch"); if (rs) { if (recvFocus) { rs.focus(); rs.setSelectionRange(rs.value.length, rs.value.length); }
    rs.oninput = () => { recvQ = rs.value; recvFocus = true; renderDrawer(false); }; rs.onblur = () => { recvFocus = false; }; }
  body.querySelectorAll("[data-sent]").forEach(b => b.onclick = () => { sentAll = b.dataset.sent === "1"; renderDrawer(true); });
  body.scrollTop = reset ? 0 : top;
  body.querySelectorAll("[data-approve]").forEach(b => b.onclick = () => {
    const r = (last.drafts || []).find(x => x.id == b.dataset.approve); if (!r) return;
    if (!confirm(L(`לשלוח עכשיו הודעה אחת אל ${r.name || r.recipient}?\n\n${r.message}`, `Send exactly one message to ${r.name || r.recipient} now?\n\n${r.message}`))) return;
    b.disabled = true; act({ do: "draft_decide", id: r.id, approve: true }, L("נשלח (אישור אחד = הודעה אחת)", "Sent (one approval = one message)")).then(() => setTimeout(load, 1500));
  });
  body.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { b.disabled = true; act({ do: "draft_decide", id: +b.dataset.del, approve: false }, L("הטיוטה נמחקה", "Draft deleted")); });
}

// ---- connection strip: exact segments, hover tooltips, summary ----
function timeline(d) {
  const el = $("timeline"), now = Date.now(), span = 24 * 3600000, start = now - span;
  const ev = (d.conn_log || []).map(e => ({ t: toDate(e.at).getTime(), on: e.event === "connected" })).sort((a, b) => a.t - b.t);
  let state = null, cur = start; const segs = [];
  const push = (to, st) => { if (to > cur) { const l = segs[segs.length - 1]; if (l && l.st === st) l.e = to; else segs.push({ s: cur, e: to, st }); cur = to; } };
  for (const e of ev) { if (e.t <= start) { state = e.on; continue; } push(e.t, state); state = e.on; }
  if (d.bridge && d.bridge.connected && state === false) state = true; // the log lags behind the live status
  push(now, state);
  const label = st => st === true ? L("מחובר", "Connected") : st === false ? L("מנותק", "Disconnected") : L("אין נתונים", "No data");
  el.innerHTML = segs.map((g, i) => `<span class="seg ${g.st === true ? "on" : g.st === false ? "off" : "na"}" data-i="${i}" style="width:${100 * (g.e - g.s) / span}%"></span>`).join("");
  const tot = {}, cnt = {}; for (const g of segs) { tot[g.st] = (tot[g.st] || 0) + g.e - g.s; cnt[g.st] = (cnt[g.st] || 0) + 1; }
  const tip = $("tlTip");
  el.onmousemove = e => {
    const s = e.target.closest(".seg"); if (!s) { tip.hidden = true; return; }
    const g = segs[+s.dataset.i], m = (g.e - g.s) / 60000;
    tip.innerHTML = `<b>${g.st === true ? "🟢 " : g.st === false ? "🔴 " : "⚪ "}${label(g.st)}</b>${hhmm(g.s)} – ${g.e >= now - 1000 ? L("עכשיו", "now") : hhmm(g.e)}<br>${L("משך", "Duration")}: ${durMin(m)}<hr>${L("סה״כ ב-24 שעות", "24 h total")}: ${durMin(tot[g.st] / 60000)} · ${cnt[g.st]} ${L("מקטעים", "segments")}`;
    tip.hidden = false;
    const w = tip.offsetWidth; let x = e.clientX - w / 2; x = Math.max(8, Math.min(innerWidth - w - 8, x));
    tip.style.left = x + "px"; tip.style.top = (el.getBoundingClientRect().bottom + 8) + "px";
  };
  el.onmouseleave = () => tip.hidden = true;
  // axis: a tick every 4 hours on the clock
  let ax = ""; const first = new Date(start); first.setMinutes(0, 0, 0); first.setHours(first.getHours() + 1);
  for (let t = first.getTime(); t < now; t += 3600000) if (new Date(t).getHours() % 2 === 0) ax += `<span style="left:${100 * (t - start) / span}%">${String(new Date(t).getHours()).padStart(2, "0")}:00</span>`;
  $("tlAxis").innerHTML = ax;
  // summary
  let on = 0, known = 0, off = 0, longest = 0;
  for (const g of segs) if (g.st !== null) { known += g.e - g.s; if (g.st) on += g.e - g.s; else { off++; longest = Math.max(longest, g.e - g.s); } }
  $("connStats").innerHTML = known ? `<span class="stat ok" title="${L("זמינות ב-24 השעות האחרונות", "Availability, last 24 h")}">${Math.round(100 * on / known)}% ${L("זמינות", "uptime")}</span>` +
    `<span class="stat ${off ? "bad" : "ok"}">${off ? L(`${off} ניתוקים`, `${off} outages`) : L("ללא ניתוקים", "no outages")}${off ? ` · ${L("הארוך", "longest")} ${durMin(longest / 60000)}` : ""}</span>` : "";
  el.setAttribute("aria-label", known ? L(`זמינות ${Math.round(100 * on / known)}% ב-24 השעות האחרונות`, `${Math.round(100 * on / known)}% availability in the last 24 hours`) : L("אין עדיין נתוני חיבור", "No connection data yet"));
}

function drawCharts(d) {
  const css = getComputedStyle(document.documentElement), muted = css.getPropertyValue("--muted").trim(), line = css.getPropertyValue("--line").trim(), brand = css.getPropertyValue("--brand").trim();
  Chart.defaults.font.family = "Heebo, Segoe UI, sans-serif"; Chart.defaults.color = muted;
  const hours = [...Array(24).keys()].map(i => (new Date().getHours() - 23 + i + 24) % 24);
  const byH = Object.fromEntries((d.hourly || []).map(r => [parseInt(r.h, 10), r.n]));
  const base = { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
    scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } }, y: { beginAtZero: true, grid: { color: line }, ticks: { precision: 0 } } } };
  charts.forEach(c => c && c.destroy());
  charts = [
    new Chart($("cHourly"), { type: "bar", data: { labels: hours.map(h => String(h).padStart(2, "0") + ":00"), datasets: [{ data: hours.map(h => byH[h] || 0), backgroundColor: brand, borderRadius: 4, label: L("הודעות", "messages") }] }, options: base }),
    new Chart($("cCost"), { type: "bar", data: { labels: (d.costs || []).map(r => r.day.slice(8, 10) + "." + r.day.slice(5, 7)), datasets: [{ data: (d.costs || []).map(r => r.cost), backgroundColor: "#5b8def", borderRadius: 4, label: "$" }] },
      options: { ...base, scales: { ...base.scales, y: { ...base.scales.y, ticks: { callback: v => "$" + v } } } } }),
  ];
}

function render(d) {
  last = d;
  if (d.maintainer && !pubShown) { pubShown = true; $("pubBox").hidden = false; gitRefresh(); }
  $("verChip").textContent = "v" + d.version; $("verChip").title = L(`לוח בקרה ${d.version} · כלי ${d.tool_version || "?"}`, `Dashboard ${d.version} · tool ${d.tool_version || "?"}`);
  $("toolVer").textContent = d.tool_version || "—";
  $("updated").textContent = L("עודכן ", "Updated ") + new Date().toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const b = d.bridge || {}, p = d.policy || {}, a = d.agent || {};
  // connection strip
  const up = !b.error && b.connected;
  $("connDot").className = "pulse " + (up ? "ok" : "bad");
  $("hero").dataset.state = up ? "ok" : "bad";
  $("connText").textContent = b.error ? L("הגשר לא פועל", "Bridge is not running") : up ? L(`מחובר ${dur(b.connected_since)}`, `Connected for ${dur(b.connected_since)}`) : L("מנותק", "Disconnected");
  $("connSub").textContent = b.error ? L("הנתונים מוצגים מהמחשב. נסה \"הפעל מחדש גשר\".", "Showing local data. Try \"Restart bridge\".") :
    L("מאז ", "Since ") + fmt(b.connected_since) + (b.disconnects ? " · " + L(`אחרון נותק ${fmt(b.last_disconnect)}`, `last disconnect ${fmt(b.last_disconnect)}`) : "");
  timeline(d);
  // header status chips
  const hb = mins(a.heartbeat), chip = (ok, t, tip) => `<span class="schip ${ok ? "ok" : "bad"}" title="${esc(tip || "")}"><span class="dot"></span>${t}</span>`;
  const allOk = !b.error && up && hb !== null && hb < 3;
  $("sysChips").innerHTML = `<span class="schip big ${allOk ? "ok" : "bad"}"><span class="dot"></span>${allOk ? L("המערכת תקינה", "System healthy") : L("דורש תשומת לב", "Needs attention")}</span>` + chip(!b.error, L("גשר", "Bridge"), b.error ? "" : L("פועל מאז ", "running since ") + fmt(b.started)) +
    chip(hb !== null && hb < 3, L("סוכן", "Agent"), ago(a.heartbeat)) +
    chip(mins(a.events_connected) !== null, L("עדכון חי", "Live"), "") +
    `<span class="schip" title="${L("גיבוי אחרון", "Last backup")}">💾 ${a.backup_done ? ago(a.backup_done) : "—"}</span>`;
  // today tiles
  const pend = (d.drafts || []).filter(x => x.status === "pending").length;
  $("kSent").textContent = b.sent_today ?? "—"; $("kSentFoot").textContent = b.daily_cap ? L(`מתוך תקרה של ${b.daily_cap}`, `of a ${b.daily_cap} cap`) : "";
  $("kRecv").textContent = d.received_today ?? 0; $("kDrafts").textContent = pend; $("kTr").textContent = d.transcribed_today ?? 0;
  $("kCost").textContent = "$" + (a.spent_today ?? 0).toFixed(2);
  $("todaySum").textContent = `📝 ${pend} · 📤 ${b.sent_today ?? 0} · 📥 ${d.received_today ?? 0}`;
  const pct = Math.min(100, 100 * (a.spent_today || 0) / (a.budget || 5));
  $("kCostBar").style.width = pct + "%"; $("kCostMeter").className = "meter" + (pct >= 100 ? " bad" : pct >= 80 ? " warn" : "");
  $("kCostMeter").setAttribute("aria-valuenow", Math.round(pct)); $("kCostMeter").title = L(`תקרה יומית $${a.budget}`, `daily cap $${a.budget}`);
  // chat
  const chat = $("chat"); const atBottom = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 40;
  const MT = { audio: L("הודעה קולית", "voice note"), image: L("תמונה", "image"), document: L("קובץ", "file"), video: L("וידאו", "video") };
  renderTabs(d);
  const isStatus = m => m.tool && /^_?(⏳|✅ סיימתי)/.test(m.content || "");
  const conv = d.conversation || [], lastStatus = conv.map(isStatus).lastIndexOf(true);
  const lv = d.live || null, steps = (lv && lv.steps) || [];
  const statusBody = () => {
    const head = lv.done ? L(`✅ סיימתי · ${Math.floor(lv.secs / 60)}:${String(lv.secs % 60).padStart(2, "0")} · ${esc(lv.model)} · ${steps.length} שלבים`, `✅ done · ${esc(lv.model)}`)
                         : L(`⏳ עובד · ${esc(lv.model)}`, `⏳ working · ${esc(lv.model)}`);
    return `<span class="st-head">${head}</span><ol>${steps.map((x, j) => `<li class="${j === steps.length - 1 && !lv.done ? "now" : ""}" title="${esc(x.text)}">${hhmm(toDate(x.at))} · ${esc(x.text)}</li>`).join("")}</ol>`;
  };
  const statusHtml = (m, i) => (i !== lastStatus || !steps.length)
    ? `<div class="bubble tool status old"><span class="st-head">${wa(m.content)}</span><span class="meta">${fmt(m.timestamp)}</span></div>`
    : `<div class="bubble tool status">${statusBody()}<span class="meta">${fmt(m.timestamp)}</span></div>`;
  const bubbleHtml = m => {
    const txt = m.content || (m.media_type ? "[" + (MT[m.media_type] || m.media_type) + "]" : "");
    const long = (txt.length > 700 || txt.split("\n").length > 14) && !expanded.has(String(m.id));
    const files = (m.files || []).map(f => `<a href="/lf/${encodeURIComponent(f)}" target="_blank" rel="noopener">📎 ${esc(f.replace(/^\d{8}_\d{6}_/, ""))}</a>`).join("");
    const canFold = txt.length > 700 || txt.split("\n").length > 14;
    return `<div class="bubble ${m.tool ? "tool" : "me"}" data-k="${esc(m.id)}"><span class="who">${m.tool ? L("🤖 הכלי", "🤖 Tool") : L("אתה", "You")}${m.src === "pc" ? `<span class="pc-tag" title="${L("נכתב בלוח הבקרה במחשב", "typed in the dashboard on the PC")}">💻</span>` : ""}</span>` +
      `<div class="bt${long ? " fold" : ""}">${wa(txt)}</div>${canFold ? `<button class="more" type="button">${long ? L("הצג עוד ▾", "Show more ▾") : L("הצג פחות ▴", "Show less ▴")}</button>` : ""}` +
      `${files ? `<div class="files">${files}</div>` : ""}<span class="meta">${fmt(m.timestamp)}</span></div>`;
  };
  let html = conv.map((m, i) => isStatus(m) ? statusHtml(m, i) : bubbleHtml(m)).join("");
  // a PC-only run has no WhatsApp status message: show its steps as a bubble at the end of the conversation
  const lastMe = conv.filter(m => !m.tool).map(m => String(m.timestamp).slice(0, 19)).sort().pop() || "";
  if (lv && steps.length && lastStatus < 0 && (!lv.done || String(lv.started).replace("T", " ") >= lastMe))
    html += `<div class="bubble tool status">${statusBody()}</div>`;
  chat.innerHTML = html || `<p class="empty">${L("עדיין אין שיחה", "No conversation yet")}</p>`;
  drawOutbox(false);
  if (atBottom || !chat._init) { chat.scrollTop = chat.scrollHeight; chat._init = true; }
  const pc = $("pendingChip"); pc.hidden = !a.pending; pc.textContent = a.pending ? L(`${a.pending} ממתינות לתשובה`, `${a.pending} awaiting an answer`) : "";
  // send mode: drafts only, no autonomous sending
  $("modeBadge").textContent = (b.paused ? "⛔ " + L("מושהה", "Paused") : "📝 " + L("טיוטות בלבד", "Drafts only")) + (pend ? " · " + pend : "");
  $("modeNote").textContent = L("הכלי לא שולח לאף אחד חוץ ממך. כל שליחה = טיוטה שאתה מאשר.", "The tool sends to no one but you. Every send is a draft you approve.");
  $("pauseBox").textContent = b.paused ? L(`⛔ שליחה לאחרים מושהית עד ${fmt(p.paused_until)} — `, `⛔ Sending to others is paused until ${fmt(p.paused_until)} — `) + (p.pause_reason || "") : "";
  // system
  $("sys").innerHTML = `
    <dt>${L("הסשן הנוכחי", "Current session")}</dt><dd>${(d.model && d.model.session_runs) || 0} ${L("הודעות", "messages")}</dd>
    <dt>${L("גיבוי", "Backup")}</dt><dd>${fmt(a.backup_done)}</dd>
    <dt>${L("תוספים", "Plugins")}</dt><dd class="muted">${esc((a.plugins || []).join(", ") || L("אין", "none"))}</dd>`;
  // model picker
  const mi = d.model || {}, guide = mi.guide || {};
  $("modelNow").innerHTML = `${L("פעיל:", "Active:")} <strong>${esc((guide[mi.current] || {}).he || mi.current || "אוטו")}</strong>` +
    (mi.until ? ` ${L("עד", "until")} ${fmt(mi.until)}` : "") + (mi.last ? ` · ${L("אחרון:", "last:")} ${esc(mi.last)}` : "");
  const tipOf = k => { const g = guide[k] || {}; return `${g.tip || ""}\n${L("עלות: ", "Cost: ")}${g.cost || ""}\n${L("בווצאפ: ", "WhatsApp: ")}${g.cmd || ""}`; };
  const sel = $("modelSel");
  if (!sel.dataset.busy) {
    sel.innerHTML = Object.entries(guide).map(([k, g]) => `<option value="${esc(k)}" title="${esc(tipOf(k))}"${(mi.current || "auto") === k ? " selected" : ""}>${esc(g.he)}</option>`).join("");
    sel.title = tipOf(sel.value); $("modelTip").textContent = (guide[sel.value] || {}).tip || "";
  }
  sel.onfocus = () => sel.dataset.busy = "1"; sel.onblur = () => delete sel.dataset.busy;
  sel.onchange = () => { $("modelTip").textContent = (guide[sel.value] || {}).tip || ""; sel.title = tipOf(sel.value); delete sel.dataset.busy;
    act({ do: "model", chat: d.chat, model: sel.value, hours: 0 }, L("המודל הוחלף", "Model changed")); };
  const cg = (d.groups || []).find(g => g.jid === d.chat) || {};
  $("modelNow").innerHTML = `<strong>${esc(cg.name || "")}</strong><br>` + $("modelNow").innerHTML;
  const rc = mi.recommend;
  $("modelRec").textContent = rc && rc.model ? `💡 ${L("המלצה: ", "Suggestion: ")}${(guide[rc.model] || {}).he || rc.model} (${rc.why})` : "";
  $("modelBox").classList.toggle("working", !!(d.live && !d.live.done));
  $("log").textContent = (d.log || []).join("\n");
  renderDrawer(false);
  drawCharts(d);
}

// ---- current conversation card + tabs ----
const groupOf = jid => ((last && last.groups) || []).find(g => g.jid === jid) || {};
function renderTabs(d) {
  const gs = d.groups || [];
  if (!d.chat || d.chat !== curChat) { curChat = d.chat; try { localStorage.setItem("wa-dash-chat", curChat); } catch (e) {} }
  $("tabs").innerHTML = gs.map(g => {
    const run = g.live && !g.live.done;
    return `<button class="tab" role="tab" aria-selected="${g.jid === d.chat}" data-chat="${esc(g.jid)}" title="${esc(PURPOSE[g.purpose] || "")} · ${L("תקרה", "cap")} $${g.budget}">` +
      `<span aria-hidden="true">${g.jid === "local" ? "💻" : "👥"}</span><span class="t-name">${esc(g.name)}</span>` +
      `${run ? `<span class="t-dot" title="${L("עובד עכשיו", "working now")}"></span>` : ""}${g.pending ? `<span class="t-badge">${g.pending}</span>` : ""}` +
      `<span class="t-meta">${esc(MODEL_HE(g.model))} · $${g.spent}</span></button>`;
  }).join("");
  $("tabAdd").textContent = "➕ " + L("חבר קבוצה", "Connect a group");
  const pr = d.presence || {}, pcL = pr.pc_last ? toDate(pr.pc_last).getTime() : 0, phL = pr.phone_last ? toDate(pr.phone_last).getTime() : 0, recent = Math.max(pcL, phL);
  $("presence").innerHTML = !recent || Date.now() - recent > 30 * 60000 ? "🕑 " + L("לא פעיל לאחרונה", "idle")
    : pcL >= phL ? `💻 <b>${L("אתה במחשב", "You are at the PC")}</b>` : `📱 <b>${L("אתה בנייד", "You are on the phone")}</b>`;
  const g = groupOf(d.chat), local = d.chat === "local";
  $("grpName").textContent = g.name || "—";
  $("grpMeta").textContent = `${PURPOSE[g.purpose] || ""} · ${L("תקרה", "cap")} $${g.budget ?? "—"} · ${L("היום", "today")} $${g.spent ?? 0}`;
  $("grpEdit").hidden = local; $("grpOff").hidden = !!g.primary;
  $("h-chat").textContent = local ? L("שיחה מקומית עם Claude", "Local chat with Claude") : L("השיחה בקבוצה", "Conversation in group") + " · " + (g.name || "");
  $("syncChk").checked = !!syncMap[d.chat]; sendHint();
  const run = !!(d.live && !d.live.done); $("stopBtn").hidden = !run;
  fitChat();
}
$("tabAdd").onclick = e => { e.stopPropagation(); toggleAdd(); };
$("tabs").addEventListener("click", e => {
  const t = e.target.closest(".tab"); if (!t) return;
  curChat = t.dataset.chat; try { localStorage.setItem("wa-dash-chat", curChat); } catch (er) {}
  const c = $("chat"); c._init = false; c.innerHTML = ""; load();
});
$("grpRename").onclick = async () => {
  const n = $("grpInput").value.trim(); if (!n) { toast(L("כתוב שם", "Type a name")); return; }
  if (!confirm(L(`לשנות את שם הקבוצה ל: ${n}?`, `Rename the group to: ${n}?`))) return;
  const j = await act({ do: "group_rename", jid: curChat, name: n }, L("השם שונה", "Renamed")); if (j && !j.error) $("grpInput").value = "";
};
$("grpBudget").onclick = async () => {
  const g = groupOf(curChat), v = prompt(L(`תקרת עלות יומית (דולר) ל"${g.name}":`, `Daily cost cap (USD) for "${g.name}":`), g.budget);
  if (v === null) return; const j = await act({ do: "group_budget", jid: curChat, budget: v }, L("התקרה עודכנה", "Cap updated")); if (j && j.error) toast(j.error);
};
$("grpOff").onclick = async () => {
  const g = groupOf(curChat);
  if (!confirm(L(`לנתק את ההאזנה לקבוצה "${g.name}"? הסוכן יפסיק לענות בה (הקבוצה והיסטוריה נשארות).`, `Stop listening to "${g.name}"?`))) return;
  const j = await act({ do: "group_disconnect", jid: curChat }, L("ההאזנה נותקה", "Disconnected")); if (j && !j.error) { curChat = ""; load(); }
};

// ---- connect a WhatsApp group ----
function toggleAdd(force) {
  const pop = $("addPop"), show = force === undefined ? pop.hidden : force; pop.hidden = !show;
  if (show) loadAvail();
}
async function loadAvail() {
  const ul = $("availList"); ul.innerHTML = `<li class="empty">${L("טוען קבוצות…", "Loading groups…")}</li>`;
  const j = await post({ do: "groups_available" });
  const gs = (j.groups || []).filter(g => g.name).sort((x, y) => x.name.localeCompare(y.name, "he"));
  ul.innerHTML = gs.length ? gs.map((g, i) => `<li><span>${esc(g.name)}</span><button class="btn small" data-i="${i}">${L("חבר", "Connect")}</button></li>`).join("") : `<li class="empty">${esc(j.error || L("אין קבוצות פנויות", "No groups available"))}</li>`;
  ul.querySelectorAll("button").forEach(b => b.onclick = async () => {
    const g = gs[+b.dataset.i]; b.disabled = true;
    let r = await post({ do: "group_connect", jid: g.jid, name: g.name, purpose: $("addPurpose").value });
    if (r.needs_confirm) {
      if (!confirm(L(`בקבוצה "${g.name}" יש ${r.members} חברים. התשובות שלי יהיו גלויות לכולם (אענה רק להודעות שלך). לחבר בכל זאת?`, `The group has ${r.members} members; my answers will be visible to all. Connect anyway?`))) { b.disabled = false; return; }
      r = await post({ do: "group_connect", jid: g.jid, name: g.name, purpose: $("addPurpose").value, confirm_members: true });
    }
    if (r.error) { toast(r.error); b.disabled = false; return; }
    toast(L("מחובר — מעכשיו אענה בקבוצה", "Connected")); curChat = r.jid; toggleAdd(false); load();
  });
}
$("newGrpBtn").onclick = async () => {
  const n = $("newGrpName").value.trim(); if (!n) { toast(L("כתוב שם לקבוצה", "Type a group name")); return; }
  $("newGrpBtn").disabled = true;
  const r = await post({ do: "group_create", name: n, purpose: $("addPurpose").value });
  $("newGrpBtn").disabled = false;
  if (r.error) { toast(r.error); return; }
  $("newGrpName").value = ""; toast(L("הקבוצה נוצרה ומחוברת", "Group created and connected")); curChat = r.jid; toggleAdd(false); load();
};
$("addClose").onclick = () => toggleAdd(false);
document.addEventListener("keydown", e => { if (e.key === "Escape") toggleAdd(false); });

// ---- long bubbles: show more / less (remembered across refreshes) ----
const expanded = new Set();
$("chat").addEventListener("click", e => {
  const b = e.target.closest(".more"); if (!b) return;
  const k = b.closest(".bubble").dataset.k; if (expanded.has(k)) expanded.delete(k); else expanded.add(k);
  if (last) render(last);
});

// ---- search the whole history ----
const sBtn = $("searchBtn"), sBox = $("searchBox"), sInp = $("searchInp"), sList = $("searchList");
let sTimer = null, sItems = [], sSel = -1, sSeq = 0;
function openSearch(v) { sBox.hidden = !v; sBtn.setAttribute("aria-expanded", v); if (v) { sInp.focus(); sInp.select(); } }
sBtn.onclick = e => { e.stopPropagation(); openSearch(sBox.hidden); };
document.addEventListener("click", e => { if (!e.target.closest(".search-wrap")) openSearch(false); });
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(true); }
  if (e.key === "Escape" && !sBox.hidden) { openSearch(false); sBtn.focus(); }
});
const hl = (t, q) => { const x = esc(t); if (!q) return x; try { return x.replace(new RegExp("(" + esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi"), "<mark>$1</mark>"); } catch (e) { return x; } };
function drawSearch(q) {
  sInp.setAttribute("aria-expanded", !!q);
  sList.innerHTML = !q ? "" : sItems.length ? sItems.map((r, i) => `<li role="option" id="so${i}" aria-selected="${i === sSel}" data-i="${i}"><span class="s-head"><b>${esc(r.chat_name)}${r.kind === "voice" ? " 🎙️" : ""}</b><span>${esc(r.sender)} · ${fmt(r.ts)}</span></span><span class="s-snip">${hl(r.snippet, q)}</span></li>`).join("")
    : `<li class="s-empty">${L("לא נמצא — נסה חלק אחר מהמילה", "Nothing found — try part of the word")}</li>`;
  sInp.setAttribute("aria-activedescendant", sSel >= 0 ? "so" + sSel : "");
}
async function runSearch() {
  const q = sInp.value.trim(); if (q.length < 2) { sItems = []; drawSearch(""); return; }
  const my = ++sSeq; const j = await post({ do: "search", q }); if (my !== sSeq) return;
  sItems = j.results || []; sSel = sItems.length ? 0 : -1; drawSearch(q);
}
sInp.addEventListener("input", () => { clearTimeout(sTimer); sTimer = setTimeout(runSearch, 180); });
sInp.addEventListener("keydown", e => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (sItems.length) { sSel = (sSel + (e.key === "ArrowDown" ? 1 : -1) + sItems.length) % sItems.length; drawSearch(sInp.value.trim()); const el = $("so" + sSel); if (el) el.scrollIntoView({ block: "nearest" }); } }
  if (e.key === "Enter" && sSel >= 0) { e.preventDefault(); pickResult(sItems[sSel]); }
});
sList.addEventListener("click", e => { const li = e.target.closest("li[data-i]"); if (li) pickResult(sItems[+li.dataset.i]); });
async function pickResult(r) {
  openSearch(false);
  const j = await post({ do: "context", chat: r.chat, ts: r.ts });
  const q = sInp.value.trim();
  drawerKind = "ctx"; $("scrim").hidden = false; $("drawer").hidden = false; $("drawerTitle").textContent = L("הקשר: ", "Context: ") + (j.chat_name || r.chat_name);
  $("drawerTabs").innerHTML = "";
  const conn = ((last && last.groups) || []).find(g => g.jid === r.chat);
  $("drawerBody").innerHTML = ((j.messages || []).map(m => `<div class="ctx-msg${m.hit ? " hit" : ""}"><span class="who">${esc(m.who)} · ${fmt(m.timestamp)}</span>${hl(m.content || (m.media_type ? "[" + m.media_type + "]" : ""), m.hit ? q : "")}</div>`).join("") || `<p class="empty">${L("אין הקשר זמין", "No context")}</p>`) +
    (conn ? `<div class="row tight"><button class="btn small" id="ctxOpen">${L("פתח את השיחה בלשונית", "Open this conversation tab")}</button></div>` : "");
  const ob = $("ctxOpen"); if (ob) ob.onclick = () => { closeDrawer(); curChat = r.chat; load(); };
  $("drawerClose").focus();
}

async function load() {
  try { const d = await (await fetch("/data?chat=" + encodeURIComponent(curChat || ""), { cache: "no-store" })).json(); if (d.error) throw new Error(d.error); render(d); }
  catch (e) { $("connText").textContent = L("אין גישה לנתונים", "No access to data"); $("connSub").textContent = String(e); }
}
load(); setInterval(load, 10000);

// ---- right-click menu: copy / cut / paste / select all, copy a whole message, move it to the input box ----
let ctxTarget = null, ctxBubble = null;
const ctx = $("ctx");
function hideCtx() { ctx.hidden = true; }
document.addEventListener("contextmenu", e => {
  const inBox = e.target.closest("#say"), bubble = e.target.closest(".bubble");
  if (!inBox && !bubble) return;
  e.preventDefault();
  ctxTarget = inBox ? say : null; ctxBubble = bubble;
  const sel = String(window.getSelection() || "");
  ctx.querySelector('[data-cmd="cut"]').hidden = !inBox;
  ctx.querySelector('[data-cmd="paste"]').hidden = !inBox;
  ctx.querySelector('[data-cmd="copymsg"]').hidden = ctx.querySelector('[data-cmd="quote"]').hidden = !bubble;
  ctx.querySelector('[data-cmd="copy"]').disabled = !sel && !(inBox && say.selectionStart !== say.selectionEnd);
  ctx.hidden = false;
  const w = ctx.offsetWidth, h = ctx.offsetHeight;
  ctx.style.left = Math.min(e.clientX, innerWidth - w - 6) + "px"; ctx.style.top = Math.min(e.clientY, innerHeight - h - 6) + "px";
});
document.addEventListener("click", e => { if (!e.target.closest("#ctx")) hideCtx(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") hideCtx(); });
window.addEventListener("blur", hideCtx);
const msgText = b => { const c = b.cloneNode(true); c.querySelectorAll(".who,.meta").forEach(x => x.remove()); return c.innerText.trim(); };
async function copyText(t) { try { await navigator.clipboard.writeText(t); } catch (e) { const ta = document.createElement("textarea"); ta.value = t; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); } }
ctx.addEventListener("click", async e => {
  const cmd = e.target.closest("button") && e.target.closest("button").dataset.cmd; if (!cmd) return;
  hideCtx();
  const boxSel = () => say.value.slice(say.selectionStart, say.selectionEnd);
  if (cmd === "copy") { await copyText(ctxTarget ? boxSel() : String(window.getSelection())); toast(L("הועתק", "Copied")); }
  if (cmd === "cut") { const t = boxSel(); await copyText(t); say.setRangeText("", say.selectionStart, say.selectionEnd, "start"); grow(); }
  if (cmd === "paste") {
    say.focus();
    let t = null;
    try { t = await navigator.clipboard.readText(); } catch (err) {}
    if (t != null) { say.setRangeText(t, say.selectionStart, say.selectionEnd, "end"); grow(); }
    else if (!document.execCommand("paste")) toast(L("להדבקה: Ctrl+V", "To paste: Ctrl+V"));
  }
  if (cmd === "selectall") {
    if (ctxTarget) { say.focus(); say.select(); }
    else if (ctxBubble) { const r = document.createRange(); r.selectNodeContents(ctxBubble); const s2 = getSelection(); s2.removeAllRanges(); s2.addRange(r); }
  }
  if (cmd === "copymsg" && ctxBubble) { await copyText(msgText(ctxBubble)); toast(L("ההודעה הועתקה", "Message copied")); }
  if (cmd === "quote" && ctxBubble) { const t = msgText(ctxBubble); say.value = (say.value ? say.value + "\n" : "") + t; grow(); say.focus(); }
});

// ---- model box: minimize (remembered); chat card height = window below its top, so the input box is always visible ----
try { if (localStorage.getItem("wa-dash-mb-min") === "1") $("modelBox").classList.add("min"); } catch (e) {}
const mbLabel = () => { const m = $("modelBox").classList.contains("min"); $("mbToggle").textContent = m ? "▸ הרחב" : "▾ מזער"; $("mbToggle").setAttribute("aria-expanded", !m); };
$("mbToggle").onclick = () => { $("modelBox").classList.toggle("min"); try { localStorage.setItem("wa-dash-mb-min", $("modelBox").classList.contains("min") ? "1" : "0"); } catch (e) {} mbLabel(); };
mbLabel();
function fitChat() {
  const card = document.querySelector(".chat-card"); if (!card) return;
  const top = card.getBoundingClientRect().top + window.scrollY;
  document.documentElement.style.setProperty("--chat-top", Math.round(top) + "px");
}
window.addEventListener("resize", fitChat); setTimeout(fitChat, 300); setTimeout(fitChat, 1500);
