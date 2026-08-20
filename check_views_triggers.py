import sqlite3

conn = sqlite3.connect('data/lead_radar.db')
c = conn.cursor()

# Check for views
c.execute('SELECT name FROM sqlite_master WHERE type="view"')
views = c.fetchall()
print('Views:', [v[0] for v in views])

# Check for triggers
c.execute('SELECT name FROM sqlite_master WHERE type="trigger"')
triggers = c.fetchall()
print('Triggers:', [t[0] for t in triggers])

conn.close()
