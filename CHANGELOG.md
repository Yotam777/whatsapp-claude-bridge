# Changelog

## 1.0.5 — 2026-11-07
- לוח בקרה: כתב השיחה ותיבת ההקלדה גדולים יותר
- כרטיס "תצוגה": גודל כתב השיחה, גודל הממשק, גופן ורקע שיחה (נשמר במחשב)
- כפתור "הפעל הכול מחדש" (גשר + סוכן)
- אנטר מעביר את ההודעה מיד לחלון השיחה, בלי שליחה כפולה; המתנה עד 60 שניות לגשר
- בחירת המודל עברה לתוך חלון השיחה, עם מסגרת שפועמת בזמן עבודה
- חלונות שלבים: 8 הפעולות האחרונות של Claude (קורא / עורך / מריץ / גיט…)
- בווצאפ: הודעת המצב לא נמחקת — נערכת לפי הפעולה ונשארת בסוף כ"✅ סיימתי"

## 1.0.4 — 2026-11-07
- תיקון חישוב העלות בסשן רציף: נשמרת עלות כל ריצה בלבד (קודם נספרה עלות מצטברת והתקרה היומית חסמה מוקדם מדי)
- כרטיס טיוטה ברור: מספר טיוטה, ושורה ראשונה "לחיצה ארוכה ← ✅ או 👍"
- /הפעל מחדש בקבוצה: הפעלה מחדש מסודרת של הסוכן ודיווח "עליתי מחדש" עם ההגדרות
- הסוכן מחכה לגשר בעלייה ושולח שוב הודעה שנכשלה כי הגשר לא עלה
- הודעת "החיבור חזר" כוללת סיבה משוערת
- העברת הודעות גם לתמונות, וידאו, הקלטות וקבצים
- בדיקות חדשות (אישור חד-פעמי, השהיה, כרטיס, עלות) · הערות גרסה ב-GitHub רק לגרסה הנוכחית

## 1.0.3 — 2026-11-07
- לוח בקרה: מגירת "נשלחו" עם טקסט ההודעה וסינון "לאנשים אחרים"
- מגירת "עלות": ניצול התקרה היומית ופילוח לפי מודל (נרשם מודל לכל ריצה)
- מגירת "התקבלו": קיבוץ לפי שיחה, חיפוש וסינון לפי שיחה, שמות אנשי קשר

## 1.0.2 — 2026-11-07
- אישור טיוטה בתגובת ✅ / ביטול ב-❌ על כרטיס הטיוטה
- יכולות גשר חדשות: שם/תיאור/תמונה/הגדרות קבוצה, משתתפים, קישור הזמנה, עריכה ומחיקת הודעות, חסימה, אודות, סטטוס, הצמדה, כוכב, סימון כנקרא, סקרים, איש קשר, העברה
- הגנה מעברית פגומה ("????") בשמות/תיאורים + tools/wa_admin.py שקורא מקובץ UTF-8
- תזכורות (tools/wa_remind.py), OCR אופליין (Tesseract עברית+אנגלית, או OCR של ווינדוס)
- בחירת מודל: /מודל בווצאפ + רשימה נפתחת בלוח עם הסברים; המלצה מתי להחליף; !! = המודל החזק להודעה אחת
- הודעת סטטוס אחת שנערכת בזמן שהסוכן עובד; סשן רציף עם סיכום בהחלפה
- לוח בקרה מעוצב מחדש: כותרת עם מצב וגרסה, סרגל חיבור לרוחב, שיחה עם תיבת המשך, פעילות היום, מגירת טיוטות עם אישור/מחיקה, כרטיס קבוצת הבקרה (שינוי שם / יצירת קבוצה ייעודית), שמירה בגיט ושחרור (מתחזק)
- הוסר: מצב שליחה עצמאית לזמן מוקצב, קוד טיוטה בטלפון (ADB)

## 1.0.1 — 2026-11-06
תשובות בכתב נטוי עם ציטוט ההודעה שלך · !!! מבטל את תקרת העלות לשעה · Claude זול יותר (Haiku לשאלות קצרות, בלי טעינת כלים מיותרים) · תקרה יומית ברירת מחדל 20$ / Italic replies quoting your message · !!! lifts the cost cap for an hour · cheaper Claude runs · 20 USD default cap

## 1.0.0 — 2026-11-06
First release of the enhanced fork.

**Bridge**
- REST API protected by a bearer token; push events `/api/events`; `/api/status`, `/api/policy`, `/api/react`, `/api/typing`, `/api/create_group`, `/api/leave_group`.
- Setup creates the private "Claude" control group automatically (no manual group creation).
- Outgoing messages stored (`send_log`); media downloaded on arrival with unique file names; fixed upstream 403 on media download (URL tokens kept).
- Polls and decrypted votes; edits and deletions; LID-addressed chats stored under the phone number; WAL databases; connection log.
- Send policy: drafts-only by default, time-limited auto mode, health guard (pause on throttling / temporary ban), human-like pacing, quoted replies.

**Agent**
- Chat with Claude from a WhatsApp control group: 👀/✅ reactions, progress note, files in and out, durable command queue, daily session rotation with hand-off, daily budget cap, light model for short questions.
- Local voice transcription (optional extra), Windows notifications on disconnect, nightly backup with retention, log rotation, plugins, Hebrew/English.

**Dashboard** — `WhatsAppDash.exe` (offline, tray icon, light/dark, Hebrew/English, self-update).

**MCP server** — `get_status`, `search_all`, `list_transcripts`, `list_polls`, `list_drafts`, `react_to_message`, quoted replies in `send_message`.

**Tooling** — `setup.cmd` wizard, `tools/selftest.py` (21 checks), Go and Python unit tests, GitHub Actions build and release.
