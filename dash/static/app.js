// Dashboard front-end: renders /data, sends actions to /action. Hebrew (default) and English.
const $ = id => document.getElementById(id);

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

// ---- continue the conversation from here// ---- continue the conversation from here (a normal message from you in the WhatsApp group) ----
const say = $("say");
const grow = () => { say.style.height = "auto"; say.style.height = Math.min(140, say.scrollHeight) + "px"; };
say.addEventListener("input", grow);
say.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
let outbox = [];  // messages typed here and not yet seen in the WhatsApp conversation: shown at once as "sending…"
let sending = false;
$("composer").onsubmit = async e => {
  e.preventDefault();
  const text = say.value.trim(); if (!text || sending) return;
  sending = true;
  const item = { text, at: new Date().toISOString(), state: "sending" };
  outbox.push(item); say.value = ""; grow(); drawOutbox(true);
  try {
    const j = await (await fetch("/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ do: "say", text }) })).json();
    item.state = j && !j.error ? "sent" : "failed"; item.err = j && j.error;
  } catch (err) { item.state = "failed"; item.err = String(err); }
  sending = false;
  if (item.state === "failed") { toast(L("השליחה נכשלה — הטקסט חזר לתיבה", "Send failed — text is back in the box")); outbox = outbox.filter(x => x !== item); if (!say.value) { say.value = text; grow(); } }
  drawOutbox(true); setTimeout(load, 1500); setTimeout(load, 6000);
};
function drawOutbox(scroll) {
  const chat = $("chat"); chat.querySelectorAll(".bubble.outbox").forEach(x => x.remove());
  const shown = new Set((last && last.conversation || []).filter(m => !m.tool).map(m => (m.content || "").trim()));
  outbox = outbox.filter(x => !(x.state === "sent" && shown.has(x.text)) && Date.now() - Date.parse(x.at) < 5 * 60000);
  outbox.forEach(x => chat.insertAdjacentHTML("beforeend", `<div class="bubble me outbox ${x.state}"><span class="who">${L("אתה", "You")}</span>${esc(x.text)}<span class="meta">${x.state === "sending" ? L("⏳ שולח…", "⏳ sending…") : L("✓ נשלח", "✓ sent")}</span></div>`));
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
      <div class="item"><div class="item-head"><strong>${L("לפי יום", "By day")}</strong></div>${c.slice().reverse().map(r => `<div class="bar-row"><span class="muted">${r.day.slice(8, 10)}.${r.day.slice(5, 7)}</span><span class="meter"><span style="width:${100 * r.cost / max}%"></span></span><span>$${r.cost}</span></div>`).join("")}</div>
      <div class="item"><div class="item-head"><strong>${L("ריצות Claude היום", "Claude runs today")}</strong></div>${runs.length ? runs.map(r => `<div class="bar-row" style="grid-template-columns:52px 70px 1fr 70px"><span class="muted">${hhmm(toDate(r.at))}</span><span class="muted">${esc(r.model || "")}</span><span class="${r.ok ? "" : "bad"}">${r.ok ? "" : "✖ "}${Math.round(r.seconds)} ${L("שנ'", "s")}</span><span>$${(r.cost || 0).toFixed(3)}</span></div>`).join("") : `<span class="muted">—</span>`}</div>`;
  }
  return "";
}
function renderDrawer(reset) {
  if (!drawerKind || !last) return;
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
  chat.innerHTML = (d.conversation || []).map(m => `<div class="bubble ${m.tool ? "tool" : "me"}"><span class="who">${m.tool ? L("🤖 הכלי", "🤖 Tool") : L("אתה", "You")}</span>${wa(m.content || (m.media_type ? "[" + (MT[m.media_type] || m.media_type) + "]" : ""))}<span class="meta">${fmt(m.timestamp)}</span></div>`).join("")
    || `<p class="empty">${L("עדיין אין שיחה", "No conversation yet")}</p>`;
  drawOutbox(false);
  if (atBottom || !chat._init) { chat.scrollTop = chat.scrollHeight; chat._init = true; }
  const pc = $("pendingChip"); pc.hidden = !a.pending; pc.textContent = a.pending ? L(`${a.pending} ממתינות לתשובה`, `${a.pending} awaiting an answer`) : "";
  // send mode: drafts only, no autonomous sending
  $("modeBadge").textContent = (b.paused ? "⛔ " + L("מושהה", "Paused") : "📝 " + L("טיוטות בלבד", "Drafts only")) + (pend ? " · " + pend : "");
  $("modeNote").textContent = L("הכלי לא שולח לאף אחד חוץ ממך. כל שליחה = טיוטה שאתה מאשר.", "The tool sends to no one but you. Every send is a draft you approve.");
  $("pauseBox").textContent = b.paused ? L(`⛔ שליחה לאחרים מושהית עד ${fmt(p.paused_until)} — `, `⛔ Sending to others is paused until ${fmt(p.paused_until)} — `) + (p.pause_reason || "") : "";
  // system
  $("sys").innerHTML = `
    <dt>${L("שיחת Claude", "Session")}</dt><dd>${L("מאז", "since")} ${fmt(a.session_started)}</dd>
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
    act({ do: "model", model: sel.value, hours: 0 }, L("המודל הוחלף", "Model changed")); };
  const rc = mi.recommend;
  $("modelRec").textContent = rc && rc.model ? `💡 ${L("המלצה: ", "Suggestion: ")}${(guide[rc.model] || {}).he || rc.model} (${rc.why})` : "";
  // live steps of the current / last Claude run (stay after the end, the last one marked)
  const lv = d.live || null, st = (lv && lv.steps) || [];
  $("modelBox").classList.toggle("working", !!(lv && !lv.done));
  $("steps").innerHTML = st.length ? `<div class="steps-head">${lv.done ? L(`✅ הריצה האחרונה · ${Math.floor(lv.secs / 60)}:${String(lv.secs % 60).padStart(2, "0")} · ${esc(lv.model)}`, `✅ last run · ${esc(lv.model)}`) : L(`⏳ עובד עכשיו · ${esc(lv.model)}`, `⏳ working · ${esc(lv.model)}`)}</div>` +
    st.map((x, i) => `<div class="step${i === st.length - 1 ? (lv.done ? " final" : " now") : ""}" title="${esc(x.text)}"><span class="t">${hhmm(toDate(x.at))}</span><span>${esc(x.text)}</span></div>`).join("") : "";
  $("log").textContent = (d.log || []).join("\n");
  renderDrawer(false);
  drawCharts(d);
}

// ---- control group card ----
async function loadGroup() {
  try { const j = await (await fetch("/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ do: "group_info" }) })).json();
    $("grpName").textContent = j.name || j.stored_name || "—"; $("grpName").title = j.jid || ""; } catch (e) {}
}
$("grpRename").onclick = async () => {
  const n = $("grpInput").value.trim(); if (!n) { toast(L("כתוב שם", "Type a name")); return; }
  if (!confirm(L(`לשנות את שם קבוצת הבקרה ל: ${n}?`, `Rename the control group to: ${n}?`))) return;
  const j = await act({ do: "group_rename", name: n }, L("השם שונה", "Renamed")); if (j && !j.error) { $("grpInput").value = ""; loadGroup(); }
};
$("grpCreate").onclick = async () => {
  const n = $("grpInput").value.trim() || "Claude";
  if (!confirm(L(`ליצור קבוצה חדשה "${n}" שבה רק אתה ולהעביר אליה את השיחה עם Claude? הקבוצה הנוכחית תישאר אבל הסוכן יפסיק להאזין לה.`,
                 `Create a new group "${n}" with only you and move the Claude chat there?`))) return;
  const j = await act({ do: "group_create", name: n }, L("הקבוצה נוצרה — הסוכן עולה מחדש", "Group created — agent restarting"));
  if (j && !j.error) { $("grpInput").value = ""; setTimeout(loadGroup, 3000); }
};
loadGroup(); setInterval(loadGroup, 300000);

async function load() {
  try { const d = await (await fetch("/data", { cache: "no-store" })).json(); if (d.error) throw new Error(d.error); render(d); }
  catch (e) { $("connText").textContent = L("אין גישה לנתונים", "No access to data"); $("connSub").textContent = String(e); }
}
load(); setInterval(load, 10000);
