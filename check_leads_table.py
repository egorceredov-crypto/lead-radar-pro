import sqlite3

conn = sqlite3.connect('data/lead_radar.db')
c = conn.cursor()

# Get the CREATE TABLE statement for leads
c.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name="leads"')
create_sql = c.fetchone()
if create_sql:
    print('CREATE TABLE SQL:')
    print(create_sql[0])
else:
    print('No CREATE TABLE statement found')

# Also check the schema
c.execute('PRAGMA table_info(leads)')
cols = c.fetchall()
print('\nColumns:')
for col in cols:
    print(f'  {col[1]}: {col[2]}')

conn.close()
