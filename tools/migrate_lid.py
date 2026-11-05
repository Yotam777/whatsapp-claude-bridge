"""One-time migration: merge private chats stored under a LID (<lid>@lid) into the phone-number chat.

Run with the bridge stopped. The bridge stores new messages under the phone number from now on.
Usage: python migrate_lid.py <store dir> <agent.db>
"""
import os
import shutil
import sqlite3
import sys

store, agent_db = sys.argv[1], sys.argv[2]
wa = sqlite3.connect(os.path.join(store, "whatsapp.db"))
lid2pn = {l: p for l, p in wa.execute("SELECT lid, pn FROM whatsmeow_lid_map")}
m = sqlite3.connect(os.path.join(store, "messages.db"))
moved = merged_chats = senders = files = 0
for (cj,) in m.execute("SELECT DISTINCT chat_jid FROM messages WHERE chat_jid LIKE '%@lid'").fetchall():
    lid = cj.split("@")[0]
    if lid not in lid2pn:
        continue
    pn = lid2pn[lid] + "@s.whatsapp.net"
    moved += m.execute("UPDATE OR IGNORE messages SET chat_jid = ? WHERE chat_jid = ?", (pn, cj)).rowcount
    m.execute("DELETE FROM messages WHERE chat_jid = ?", (cj,))  # leftovers are duplicates already stored under pn
    old = m.execute("SELECT name, last_message_time FROM chats WHERE jid = ?", (cj,)).fetchone()
    if old:
        m.execute("INSERT OR IGNORE INTO chats (jid, name, last_message_time) VALUES (?, ?, ?)", (pn, old[0], old[1]))
        m.execute("UPDATE chats SET last_message_time = max(last_message_time, ?) WHERE jid = ?", (old[1], pn))
        m.execute("DELETE FROM chats WHERE jid = ?", (cj,))
        merged_chats += 1
    src = os.path.join(store, cj)
    if os.path.isdir(src):
        dst = os.path.join(store, pn)
        os.makedirs(dst, exist_ok=True)
        for f in os.listdir(src):
            if not os.path.exists(os.path.join(dst, f)):
                shutil.move(os.path.join(src, f), os.path.join(dst, f))
                files += 1
for lid, pn in lid2pn.items():
    senders += m.execute("UPDATE messages SET sender = ? WHERE sender = ?", (pn, lid)).rowcount
m.commit()
a = sqlite3.connect(agent_db)
for lid, pn in lid2pn.items():
    a.execute("UPDATE OR IGNORE transcripts SET chat_jid = ?, sender = ? WHERE chat_jid = ?", (pn + "@s.whatsapp.net", pn, lid + "@lid"))
    a.execute("UPDATE transcripts SET sender = ? WHERE sender = ?", (pn, lid))
a.commit()
print(f"messages moved {moved}, chats merged {merged_chats}, senders renamed {senders}, media files moved {files}")
