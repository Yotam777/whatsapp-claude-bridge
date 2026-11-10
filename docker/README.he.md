# התקנה על TrueNAS (שרת MAIN, כרטיס 3050)

הכול רץ בקונטיינר אחד: הגשר לווצאפ, הסוכן (תמלול על כרטיס המסך) ו-Claude Code.
המידע האישי נשמר ב-dataset אחד: הסשן של ווצאפ, ההיסטוריה, התמלולים, ההגדרות והקובץ `CLAUDE.md`.
את ה-image בונה GitHub אוטומטית אחרי כל עדכון, ו-TrueNAS מושך אותו משם.

**זיכרון הכרטיס:** מודל התמלול נטען רק כשמגיעה הקלטה, ומשתחרר אחרי 10 דקות בלי הקלטות
(`whisper_idle_unload_minutes`). כשהוא כן פועל, הוא משתמש ביכולת המלאה של הכרטיס: float16 ו-beam 5.
אם הכרטיס לא זמין, התמלול עובר אוטומטית למעבד.

## 1. חד-פעמי: הרשאה ל-image
ב-GitHub: **Profile ← Packages ← whatsapp-claude-bridge ← Package settings ← Change visibility ← Public**.
ב-image אין שום מידע אישי, רק קוד. אם מעדיפים שיישאר פרטי, צריך להגדיר ב-TrueNAS התחברות ל-`ghcr.io` עם token.

## 2. טוקן ל-Claude (במחשב, שכבר מחובר)
ב-PowerShell:
```
claude setup-token
```
זה מדפיס טוקן ארוך שמחובר למנוי שלך. שמור אותו לשלב 5.

## 3–4. dataset בשרת
ב-TrueNAS: **Datasets ← Yotam-Storage ← Add Dataset**, ושם `whatsapp-claude`. ההרשאות: root בלבד (`chmod 700`).
בהתקנה חדשה ה-dataset ריק. האפליקציה מעתיקה לתוכו את הקוד ומחכה ל-`app/agent/config.json`:
מעתיקים לשם את `agent/config.example.json` בשם `config.json`, ובהפעלה הראשונה סורקים QR מה-Logs.

## 5. התקנת האפליקציה
1. **Apps ← Discover Apps ← ⋮ ← Install via YAML**, ושם `whatsapp-claude`.
2. הדבק את התוכן של `docker/truenas-app.yaml`, והחלף את `PASTE-TOKEN-HERE` בטוקן משלב 2.
3. **Save**. ההורדה הראשונה לוקחת כמה דקות, כי ה-image שוקל כ-3GB.

דרייבר NVIDIA כבר מותקן אצלך, כי Plex משתמש בו. אם האפליקציה לא רואה את הכרטיס, בדוק ב-**Apps ← Configuration ← Settings**
שמסומן **Install NVIDIA Drivers**.

## 6. בדיקה
- **Apps ← whatsapp-claude ← Logs**: אמור להופיע `agent starting`, ובהקלטה הראשונה `whisper model loaded on the GPU`.
- כתוב בקבוצת Claude: "מה ביקשו ממני היום?"
- הסטטוס של האפליקציה צריך להיות **healthy**, כלומר הסוכן חי והגשר מחובר לווצאפ.
  אפשר לנטר אותו גם ב-Uptime Kuma עם מוניטור מסוג Docker container.

## עדכונים
אחרי שאני דוחף שינוי, GitHub בונה image חדש בתוך 5–10 דקות. ב-TrueNAS: **Apps ← whatsapp-claude ← Update**,
או **Stop** ואחר כך **Start** (`pull_policy: always` מושך את הגרסה החדשה). ההגדרות והמידע ב-dataset לא משתנים.

## נקודות שחזור
- **הקוד:** כל בנייה נשמרת ב-image עם תג משלה, `ghcr.io/yotam777/whatsapp-claude-bridge:sha-<commit>`. הגרסה שרצה לפני העדכון האחרון נשמרת גם בתג `:previous`.
  כדי לחזור לגרסה קודמת, מחליפים ב-YAML של האפליקציה את `:latest` בתג הרצוי.
- **המידע:** Snapshots של ה-dataset ב-TrueNAS (**Datasets ← whatsapp-claude ← Snapshots**).
