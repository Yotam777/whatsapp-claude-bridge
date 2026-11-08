# התקנה במצב "עוזר קריאה בלבד" (מחשב Windows עם כרטיס NVIDIA)

המצב הזה נועד למי שרוצה שני דברים:
1. **תמלול** של כל ההקלטות בווצאפ, על כרטיס המסך.
2. **צ'אט עם Claude** מתוך קבוצה פרטית בווצאפ, כולל שאלות על השיחות. למשל: "כמה ביצים אשתי ביקשה?"

Claude **לא יכול** לשלוח הודעות, להריץ פקודות או לשנות קבצים במחשב. כך גם הודעה זדונית ממישהו אחר לא יכולה לגרום לו
לעשות משהו, כי פשוט אין לו כלי לבצע אותו.

## מה צריך
- Windows 10/11 שדולק תמיד. (9700K + RTX 3080 זה הרבה יותר ממספיק.)
- דרייבר NVIDIA עדכני. את CUDA ו-cuDNN ההתקנה מורידה לבד (בערך 1GB).
- [uv](https://docs.astral.sh/uv/) ו-[Claude Code](https://docs.claude.com/claude-code), מחובר למנוי שלך:
  להריץ `claude` פעם אחת ולהתחבר.
- git.

## התקנה
```
git clone https://github.com/Yotam777/whatsapp-claude-bridge
cd whatsapp-claude-bridge
copy agent\config.readonly.example.json agent\config.json
```
פתח את `agent\config.json` ושנה את `owner_name` לשם שלך. אחר כך:
```
setup.cmd
```
האשף מקשר את הווצאפ (סריקת QR מהנייד) ויוצר את קבוצת "Claude". מכיוון שקיים כבר `config.json`, ההגדרות שלמטה נשמרות.

## אחרי ההתקנה
1. ערוך את `agent\home\CLAUDE.md` ומלא את החלק **"אנשים"**, למשל "אשתי = מיכל". כך Claude יידע למי אתה מתכוון.
2. שלח הקלטה כלשהי לעצמך. ב-`agent\agent.log` צריכה להופיע השורה `whisper model loaded on the GPU`.
   אם כתוב `on the CPU`, שלח לי את השורה שלפניה.
3. כתוב בקבוצת Claude: "מה ביקשו ממני היום?"

## מה שונה מברירת המחדל (`config.readonly.example.json`)
| הגדרה | ערך | למה |
|---|---|---|
| `assistant_mode` | `readonly` | Claude מקבל רק כלי חיפוש וקריאה. אין לו Bash, עריכת קבצים או שליחה |
| `whisper_device` | `cuda` | תמלול על כרטיס המסך, עם חזרה אוטומטית למעבד אם ה-GPU לא זמין |
| `transcribe_all_chats` | `true` | מתמלל הקלטות בכל השיחות, לא רק בקבוצת Claude |
| `auto_update` | `false` | בלי הורדת קוד חדש מ-GitHub והרצה שלו כל לילה |
| `daily_budget_usd` | `1000` | במנוי, ה"עלות" היא רק הערכה. המגבלה האמיתית היא מכסת המנוי, והסוכן כבר ממתין לבד לאיפוס שלה |
| `deep_model` | `sonnet` | לא לשרוף את מכסת המנוי על Opus אוטומטית. `!!` בתחילת הודעה עדיין עובד אם תשנה |
| `voice_replies` | `false` | תשובה קולית שולחת את הטקסט לשירות של מיקרוסופט (edge-tts). אפשר להדליק |
| `morning_summary` | `false` | סיכום בוקר צורך מהמכסה כל יום. אפשר להדליק |

כדי לחזור למצב המלא (Claude עם גישה מלאה למחשב, טיוטות וכו'): `"assistant_mode": "full"`.

## עדכונים
זה עותק פרטי של [WallaBoohi/whatsapp-claude-bridge](https://github.com/WallaBoohi/whatsapp-claude-bridge).
`update.cmd` מחפש גרסאות בריפו המקורי ולכן לא מתאים לעותק הזה. כדי לקבל גרסה חדשה מהמקור:
```
git remote add upstream https://github.com/WallaBoohi/whatsapp-claude-bridge
git pull upstream main
```
אחר כך מריצים מחדש את `setup.cmd`, שמתקין רכיבים ושומר את ההגדרות.
