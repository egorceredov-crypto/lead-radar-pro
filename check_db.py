import sqlite3

conn = sqlite3.connect('data/lead_radar.db')
c = conn.cursor()

# Check all tables
c.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = c.fetchall()
print('Tables:', [t[0] for t in tables])

# Check leads table schema
c.execute('PRAGMA table_info(leads)')
cols = c.fetchall()
print('Leads columns:', [col[1] for col in cols])

# Check if chats table exists and has data
c.execute('SELECT count(*) FROM chats')
chat_count = c.fetchone()[0]
print('Chats count:', chat_count)

conn.close()
