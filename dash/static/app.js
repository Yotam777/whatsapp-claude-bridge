// Dashboard front-end: renders /data, sends actions to /action. Hebrew (default) and English.
const $ = id => document.getElementById(id);

// ---- language ----
let lang = "he";
try { lang = localStorage.getItem("wa-dash-lang") || "he"; } catch (e) {}
const L = (he, en) => lang === "en" ? en : he;
const LOCALE = () => lang === "en" ? "en-GB" : "he-IL";
const EN = {
  "דלג לתוכן": "Skip to content", "ווצאפ ↔ Claude": "WhatsApp ↔ Claude", "חיבור לווצאפ": "WhatsApp connection", "טוען…": "Loading…",
  "לפני 24 שעות": "24 h ago", "לפני 12 שעות": "12 h ago", "עכשיו": "now", "נשלחו היום": "Sent today", "התקבלו היום": "Received today",
  "הודעות נכנסות": "incoming messages", "טיוטות ממתינות": "Pending drafts", "לבדיקה ושליחה שלך": "for you to review and send",
  "תומללו היום": "Transcribed today", "הודעות קוליות": "voice notes", "עלות Claude היום": "Claude cost today",
  "השיחה שלך עם הכלי": "Your conversation with the tool", "שליחה לאנשים אחרים": "Sending to other people",
  "📝 טיוטות בלבד": "📝 Drafts only", "אתה בודק ושולח": "you review and send", "⚡ שליחה עצמאית": "⚡ Auto send", "לשעה אחת": "for one hour",
  "⏸ השהה 6 שעות": "⏸ Pause 6 hours", "▶ חידוש שליחה": "▶ Resume sending", "מצב המערכת": "System status",
  "↻ הפעל מחדש גשר": "↻ Restart bridge", "↻ הפעל מחדש סוכן": "↻ Restart agent", "💾 גיבוי עכשיו": "💾 Back up now",
  "עדכונים": "Updates", "גרסת הכלי:": "Tool version:", "בדוק עדכון לכל הכלי": "Check for a tool update", "בדוק עדכון ללוח הבקרה": "Check for a dashboard update", "פעילות ב-24 השעות האחרונות": "Activity in the last 24 hours",
  "עלות Claude לפי יום": "Claude cost per day", "טיוטות": "Drafts", "שליחות": "Sent", "תמלולים": "Transcripts", "יומן": "Log",
  "לוח בקרה · גרסה": "Dashboard · version",
  "הנתונים נקראים מהמחשב שלך בלבד · עובד גם בלי אינטרנט": "Data is read from this computer only · works offline",
};
const DYNAMIC = new Set(["chat", "sys", "connText", "connSub", "modeNote", "pauseBox", "updNote", "toolUpdNote", "toolVer", "updated", "toast", "log", "ver"]);
function translateStatic() {
  document.documentElement.lang = lang; document.documentElement.dir = lang === "en" ? "ltr" : "rtl";
  document.title = L("ווצאפ ↔ Claude", "WhatsApp ↔ Claude");
  const walk = n => { for (const c of n.childNodes) {
    if (c.nodeType === 3) { const t = c.textContent.trim(); if (!t) continue;
      if (c._he === undefined) c._he = t; const target = lang === "en" ? (EN[c._he] || c._he) : c._he;
      c.textContent = c.textContent.replace(c.textContent.trim(), target); }
    else if (c.nodeType === 1 && !["SCRIPT", "STYLE", "CANVAS"].includes(c.tagName) && !DYNAMIC.has(c.id) && !c.id?.startsWith("tab-")) walk(c); } };
  walk(document.body);
  $("langBtn").textContent = lang === "en" ? "עב" : "EN";
}

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
// WhatsApp formatting: *bold*, _italic_, ~strike~ (applied after escaping)
const wa = s => esc(s).replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<strong>$2</strong>").replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>").replace(/(^|[\s(])~([^~\n]+)~/g, "$1<s>$2</s>");
const fmt = s => s ? new Date(s.replace(" ", "T")).toLocaleString(LOCALE(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
const mins = s => s ? Math.max(0, Math.floor((Date.now() - new Date(s.replace(" ", "T"))) / 60000)) : null;
function dur(s) {
  const m = mins(s); if (m === null) return "—";
  const d = Math.floor(m / 1440), h = Math.floor(m / 60) % 24, mm = m % 60;
  if (d) return L(`${d} ימים ו-${h} שעות`, `${d} d ${h} h`); if (h) return L(`${h} שעות ו-${mm} דקות`, `${h} h ${mm} min`); return L(`${mm} דקות`, `${mm} min`);
}
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

// ---- tabs ----
document.querySelectorAll('[role="tab"]').forEach(b => b.onclick = () => {
  document.querySelectorAll('[role="tab"]').forEach(x => x.setAttribute("aria-selected", x === b));
  document.querySelectorAll(".tab-panel").forEach(p => p.hidden = p.id !== "tab-" + b.dataset.tab);
});

// ---- controls ----
$("mDraft").onclick = () => act({ do: "mode", mode: "draft" }, L("מצב טיוטות בלבד הופעל", "Drafts-only mode on"));
$("mAuto").onclick = () => { if (confirm(L("לאפשר לכלי לשלוח בעצמו לאנשים אחרים במשך שעה?", "Let the tool send to other people by itself for one hour?"))) act({ do: "mode", mode: "auto", hours: 1 }, L("שליחה עצמאית לשעה הופעלה", "Auto send on for one hour")); };
$("pauseBtn").onclick = () => act({ do: "pause", hours: 6 }, L("השליחה לאחרים הושהתה ל-6 שעות", "Sending to others paused for 6 hours"));
$("resumeBtn").onclick = () => act({ do: "resume" }, L("השליחה חודשה", "Sending resumed"));
$("backupBtn").onclick = () => act({ do: "backup" }, L("הגיבוי התחיל ברקע", "Backup started"));
document.querySelectorAll("[data-restart]").forEach(b => b.onclick = () => {
  const what = b.dataset.restart === "bridge" ? L("הגשר", "the bridge") : L("הסוכן", "the agent");
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

function timeline(d) {
  // 96 slots of 15 minutes, coloured from the connection log
  const el = $("timeline"), now = Date.now(), slot = 15 * 60000, n = 96, start = now - n * slot;
  const ev = (d.conn_log || []).map(e => ({ t: new Date(e.at.replace(" ", "T")).getTime(), on: e.event === "connected" }));
  let state = null; const before = ev.filter(e => e.t < start); if (before.length) state = before[before.length - 1].on;
  let html = "", j = 0, onSlots = 0, known = 0;
  for (let i = 0; i < n; i++) {
    const t0 = start + i * slot, t1 = t0 + slot; let off = false;
    while (j < ev.length && ev[j].t < t1) { if (!ev[j].on) off = true; state = ev[j].on; j++; }
    let cls = state === null ? "na" : (off || !state ? "off" : "on");
    if (i === n - 1 && d.bridge && d.bridge.connected) cls = "on";
    if (cls !== "na") { known++; if (cls === "on") onSlots++; }
    html += `<span class="${cls}" style="width:${100 / n}%" title="${new Date(t0).toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit" })}"></span>`;
  }
  el.innerHTML = html;
  el.setAttribute("aria-label", known ? L(`זמינות ${Math.round(100 * onSlots / known)}% ב-24 השעות האחרונות`, `${Math.round(100 * onSlots / known)}% availability in the last 24 hours`) : L("אין עדיין נתוני חיבור", "No connection data yet"));
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
  $("ver").textContent = d.version; $("toolVer").textContent = d.tool_version || "—";
  $("updated").textContent = L("עודכן ", "Updated ") + new Date().toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const b = d.bridge || {}, p = d.policy || {}, a = d.agent || {};
  // hero
  const up = !b.error && b.connected;
  $("connDot").className = "pulse " + (up ? "ok" : "bad");
  $("connText").textContent = b.error ? L("הגשר לא פועל", "Bridge is not running") : up ? L(`מחובר ${dur(b.connected_since)}`, `Connected for ${dur(b.connected_since)}`) : L("מנותק", "Disconnected");
  $("connSub").textContent = b.error ? L("הנתונים מוצגים מהמחשב. נסה \"הפעל מחדש גשר\".", "Showing local data. Try \"Restart bridge\".") :
    L("מאז ", "Since ") + fmt(b.connected_since) + " · " + (b.disconnects ? L(`${b.disconnects} ניתוקים מאז הפעלת הגשר, אחרון ${fmt(b.last_disconnect)}`, `${b.disconnects} disconnects since the bridge started, last ${fmt(b.last_disconnect)}`) : L("ללא ניתוקים מאז הפעלת הגשר", "no disconnects since the bridge started"));
  timeline(d);
  // tiles
  $("kSent").textContent = b.sent_today ?? "—"; $("kSentFoot").textContent = b.daily_cap ? L(`מתוך תקרה של ${b.daily_cap}`, `of a ${b.daily_cap} daily cap`) : "";
  $("kRecv").textContent = d.received_today ?? 0;
  $("kDrafts").textContent = (d.drafts || []).filter(x => x.status === "pending").length;
  $("kTr").textContent = d.transcribed_today ?? 0;
  $("kCost").textContent = "$" + (a.spent_today ?? 0).toFixed(2);
  const pct = Math.min(100, 100 * (a.spent_today || 0) / (a.budget || 5));
  $("kCostBar").style.width = pct + "%"; $("kCostMeter").className = "meter" + (pct >= 100 ? " bad" : pct >= 80 ? " warn" : "");
  $("kCostMeter").setAttribute("aria-valuenow", Math.round(pct)); $("kCostMeter").title = L(`תקרה יומית $${a.budget}`, `daily cap $${a.budget}`);
  // chat
  const chat = $("chat"); const atBottom = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 30;
  const MT = { audio: L("הודעה קולית", "voice note"), image: L("תמונה", "image"), document: L("קובץ", "file"), video: L("וידאו", "video") };
  chat.innerHTML = (d.conversation || []).map(m => `<div class="bubble ${m.tool ? "tool" : "me"}"><span class="who">${m.tool ? L("🤖 הכלי", "🤖 Tool") : L("אתה", "You")}</span>${wa(m.content || (m.media_type ? "[" + (MT[m.media_type] || m.media_type) + "]" : ""))}<span class="meta">${fmt(m.timestamp)}</span></div>`).join("")
    || `<p class="empty">${L("עדיין אין שיחה", "No conversation yet")}</p>`;
  if (atBottom) chat.scrollTop = chat.scrollHeight;
  const pc = $("pendingChip"); pc.hidden = !a.pending; pc.textContent = a.pending ? L(`${a.pending} ממתינות לתשובה`, `${a.pending} awaiting an answer`) : "";
  // send mode
  const auto = p.mode === "auto";
  $("mDraft").setAttribute("aria-checked", !auto); $("mAuto").setAttribute("aria-checked", auto);
  $("modeNote").textContent = auto ? L(`הכלי שולח בעצמו עד ${fmt(p.auto_until)}, ואז חוזר לטיוטות.`, `The tool sends by itself until ${fmt(p.auto_until)}, then returns to drafts.`)
    : L("הכלי לא שולח לאף אחד חוץ ממך — רק מכין טיוטות.", "The tool sends to no one but you — it only prepares drafts.");
  $("pauseBox").textContent = b.paused ? L(`⛔ שליחה לאחרים מושהית עד ${fmt(p.paused_until)} — `, `⛔ Sending to others is paused until ${fmt(p.paused_until)} — `) + (p.pause_reason || "") : "";
  // system
  const hb = mins(a.heartbeat);
  const st = (ok, t) => `<span class="${ok ? "ok" : "bad"}"><span class="dot"></span>${t}</span>`;
  $("sys").innerHTML = `
    <dt>${L("גשר ווצאפ", "WhatsApp bridge")}</dt><dd>${b.error ? st(false, L("לא פועל", "not running")) : st(true, L("פועל", "running"))} <span class="muted">${L("מאז", "since")} ${fmt(b.started)}</span></dd>
    <dt>${L("סוכן ברקע", "Background agent")}</dt><dd>${hb !== null && hb < 3 ? st(true, L("פעיל", "active")) : st(false, L("לא מגיב", "not responding"))} <span class="muted">${ago(a.heartbeat)}</span></dd>
    <dt>${L("עדכון מיידי", "Live updates")}</dt><dd>${mins(a.events_connected) !== null ? st(true, L("מחובר", "connected")) : st(false, L("לא מחובר", "not connected"))}</dd>
    <dt>${L("שיחת Claude", "Claude session")}</dt><dd>${L("מאז", "since")} ${fmt(a.session_started)} <span class="muted">(${L("מתחדשת כל 24 שעות", "renewed every 24 h")})</span></dd>
    <dt>${L("גיבוי אחרון", "Last backup")}</dt><dd>${fmt(a.backup_done)}</dd>
    <dt>${L("תוספים", "Plugins")}</dt><dd class="muted">${esc((a.plugins || []).join(", ") || L("אין", "none"))}</dd>`;
  // tables
  const drafts = d.drafts || [];
  const pend = drafts.filter(x => x.status === "pending").length; $("cDrafts").textContent = pend || "";
  const STATUS = { pending: L("ממתינה", "pending"), sent: L("נשלחה", "sent"), discarded: L("בוטלה", "discarded") };
  $("tab-drafts").innerHTML = drafts.length ? `<table><thead><tr><th>${L("אל", "To")}</th><th>${L("הודעה", "Message")}</th><th>${L("נוצרה", "Created")}</th><th>${L("סטטוס", "Status")}</th><th></th></tr></thead><tbody>${drafts.map(r => `<tr><td>${esc(r.name)}</td><td class="msg">${esc(r.message)}</td><td class="muted">${fmt(r.created)}</td><td><span class="pill ${r.status}">${STATUS[r.status] || esc(r.status)}</span></td><td>${r.status === "pending" ? `<button class="btn small" onclick="act({do:'draft',id:${r.id},status:'sent'},L('סומן כנשלח','Marked as sent'))">✔ ${L("שלחתי", "Sent it")}</button> <button class="btn small" onclick="act({do:'draft',id:${r.id},status:'discarded'},L('הטיוטה בוטלה','Draft discarded'))">${L("בטל", "Discard")}</button>` : ""}</td></tr>`).join("")}</tbody></table>` : `<p class="empty">${L("אין טיוטות", "No drafts")}</p>`;
  const sent = d.sent || [];
  $("tab-sent").innerHTML = sent.length ? `<table><thead><tr><th>${L("אל", "To")}</th><th>${L("תוצאה", "Result")}</th><th>${L("מתי", "When")}</th></tr></thead><tbody>${sent.map(r => `<tr><td>${esc(r.recipient)}</td><td>${r.ok ? `<span class="ok">✔ ${L("נשלחה", "sent")}</span>` : `<span class="bad">✖ ${esc(String(r.detail).slice(0, 80))}</span>`}</td><td class="muted">${fmt(r.timestamp)}</td></tr>`).join("")}</tbody></table>` : `<p class="empty">${L("אין שליחות", "Nothing sent")}</p>`;
  const tr = d.transcripts || [];
  $("tab-tr").innerHTML = tr.length ? `<table><thead><tr><th>${L("מתי", "When")}</th><th>${L("מאת", "From")}</th><th>${L("תמלול", "Transcript")}</th></tr></thead><tbody>${tr.map(r => `<tr><td class="muted">${fmt(r.ts)}</td><td>${esc(r.sender)}</td><td class="msg">${esc(r.text)}</td></tr>`).join("")}</tbody></table>` : `<p class="empty">${L("אין תמלולים", "No transcripts")}</p>`;
  $("log").textContent = (d.log || []).join("\n");
  drawCharts(d);
}

async function load() {
  try { const d = await (await fetch("/data", { cache: "no-store" })).json(); if (d.error) throw new Error(d.error); render(d); }
  catch (e) { $("connText").textContent = L("אין גישה לנתונים", "No access to data"); $("connSub").textContent = String(e); }
}
load(); setInterval(load, 10000);
