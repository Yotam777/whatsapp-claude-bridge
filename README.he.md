# WhatsApp ↔ Claude: תמלול הקלטות ועוזר על השיחות

עותק פרטי של [WallaBoohi/whatsapp-claude-bridge](https://github.com/WallaBoohi/whatsapp-claude-bridge), שמותאם לשרת TrueNAS עם כרטיס NVIDIA.

**מה הוא עושה**
- מתמלל כל הקלטה קולית בעברית, מקומית על כרטיס המסך (מודל ivrit-ai/whisper-large-v3). המודל משתחרר מהזיכרון כשאין בו שימוש.
- שולח את התמלולים לקבוצת "Claude" בווצאפ.
- `/תמלול הוסף <שם>`: ההקלטות **שלך** לאיש הקשר הזה מקבלות מתחתיהן את התמלול, בתוך השיחה. מתאים למשל למישהו שלא שומע.
- צ'אט עם Claude מקבוצת "Claude", כולל שאלות על השיחות ("כמה ביצים שון ביקשה?"). Claude במצב **קריאה בלבד**: הוא לא שולח, לא מריץ פקודות ולא משנה קבצים.

**מבנה**
- `whatsapp-bridge/`: הגשר לווצאפ (Go, whatsmeow). מחזיק את החיבור ושומר הודעות ומדיה.
- `agent/`: הסוכן (Python). תמלול, הפעלת Claude Code, פקודות, ניטור.
- `whatsapp-mcp-server/`: הכלים ש-Claude מקבל לקריאה ולחיפוש בווצאפ.
- `docker/`: ה-image, סקריפט ההפעלה, בדיקת התקינות והתקנה ל-TrueNAS ([מדריך](docker/README.he.md)).

כל דחיפה ל-`main` מריצה את הבדיקות, ורק אם הן עוברות בונה את `ghcr.io/yotam777/whatsapp-claude-bridge`.
