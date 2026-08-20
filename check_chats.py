import sqlite3

conn = sqlite3.connect('data/lead_radar.db')
c = conn.cursor()

# Check chats table
c.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="chats"')
chats_exists = c.fetchone()
print('chats table exists:', chats_exists is not None)

if chats_exists:
    c.execute('PRAGMA table_info(chats)')
    cols = c.fetchall()
    print('chats columns:', [col[1] for col in cols])

# Check foreign keys on leads
c.execute('PRAGMA foreign_key_list(leads)')
fks = c.fetchall()
print('Foreign keys on leads:')
for fk in fks:
    print(f'  {fk}')

# Try to query leads.chat_id directly
try:
    c.execute('SELECT chat_id FROM leads LIMIT 1')
    row = c.fetchone()
    print('chat_id query works:', row)
except Exception as e:
    print('chat_id query failed:', e)

conn.close()
