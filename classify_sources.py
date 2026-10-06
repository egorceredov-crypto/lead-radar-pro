import sqlite3
import json

conn = sqlite3.connect('data/lead_radar.db')
c = conn.cursor()

# Get ALL active sources with their types
c.execute("SELECT id, type, username, chat_id, title FROM sources WHERE status='active'")
rows = c.fetchall()

channels = []
groups = []
unknown = []

for r in rows:
    sid, stype, username, chat_id, title = r
    if stype == 'channel':
        channels.append({"id": sid, "type": stype, "username": username, "chat_id": chat_id, "title": title})
    elif stype == 'group':
        groups.append({"id": sid, "type": stype, "username": username, "chat_id": chat_id, "title": title})
    else:
        unknown.append({"id": sid, "type": stype, "username": username, "chat_id": chat_id, "title": title})

print(f"Total active sources: {len(rows)}")
print(f"Channels: {len(channels)}")
print(f"Groups: {len(groups)}")
print(f"Unknown type: {len(unknown)}")

# Get failed source IDs from last log
failed_ids = [1,4,5,9,11,12,13,15,17,31,32,33,34,44,45,46,48,50,51,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,77,78,79,80,81,82,83,84,86,87,88,89,90,91,92,93,94,97,99,100,101,103,105,106,107,109,110,112,115,116,117,118,119,120,121,122,123,124,125,128,129,130,131,133,134,135,136,137,138,139,140,142,143,144,146,148,151,155,156,158,159]
failed_set = set(failed_ids)

failed_channels = [s for s in channels if s["id"] in failed_set]
failed_groups = [s for s in groups if s["id"] in failed_set]
failed_unknown = [s for s in unknown if s["id"] in failed_set]

print(f"\nFailed sources breakdown:")
print(f"  Failed channels: {len(failed_channels)}")
print(f"  Failed groups: {len(failed_groups)}")
print(f"  Failed unknown: {len(failed_unknown)}")

# Show some sample chat_ids
print(f"\nSample failed source chat_ids:")
for s in failed_channels[:5]:
    print(f"  CHANNEL id={s['id']} chat_id={s['chat_id']} username={s['username']}")
for s in failed_groups[:5]:
    print(f"  GROUP id={s['id']} chat_id={s['chat_id']} username={s['username']}")

conn.close()
