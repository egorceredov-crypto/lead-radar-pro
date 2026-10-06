import sqlite3

conn = sqlite3.connect('data/lead_radar.db')
c = conn.cursor()
c.execute('INSERT OR IGNORE INTO keywords (user_id, word) VALUES (?, ?)', (6, 'нужен визажист'))
conn.commit()
print('Inserted keyword')
c.execute('SELECT id, user_id, word FROM keywords WHERE user_id=6 AND word=?', ('нужен визажист',))
print(c.fetchall())
conn.close()
