"""Migration: add last_checked_message_id to sources."""
import sqlite3
import os

DB_PATH = os.path.join("data", "lead_radar.db")

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

try:
    c.execute("ALTER TABLE sources ADD COLUMN last_checked_message_id INTEGER")
    print("Migration applied: added last_checked_message_id")
except sqlite3.OperationalError as e:
    if "duplicate column name" in str(e).lower():
        print("Migration skipped: column already exists")
    else:
        raise

conn.commit()
conn.close()
