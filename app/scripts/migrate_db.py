import sqlite3
import os

DB_PATH = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/lead_radar.db?check_same_thread=False&timeout=30")
if DB_PATH.startswith("sqlite+aiosqlite:///./"):
    DB_PATH = DB_PATH[len("sqlite+aiosqlite:///./"):].split("?")[0]
elif DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH[len("sqlite:///"):].split("?")[0]
elif DB_PATH.startswith("sqlite+aiosqlite:///"):
    DB_PATH = DB_PATH[len("sqlite+aiosqlite:///"):].split("?")[0]

if not os.path.exists(DB_PATH):
    alt = os.path.join("data", os.path.basename(DB_PATH))
    if os.path.exists(alt):
        DB_PATH = alt
    else:
        old = "lead_radar.db"
        if os.path.exists(old):
            DB_PATH = old
        else:
            print("DB not found, will be created on first run")
            DB_PATH = None

def get_columns(conn, table):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]

def add_column(conn, table, column, col_type):
    cols = get_columns(conn, table)
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        print(f"  + Added {table}.{column} ({col_type})")
    else:
        print(f"  = {table}.{column} already exists")

def main():
    if DB_PATH is None:
        print("DB not found, will be created on first run")
        return

    conn = sqlite3.connect(DB_PATH)
    print("Tables:", get_columns(conn, "sqlite_master") if False else [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])

    # users table
    print("\nusers columns:", get_columns(conn, "users"))
    add_column(conn, "users", "last_name", "VARCHAR")
    add_column(conn, "users", "subscription_start_date", "DATETIME")
    add_column(conn, "users", "subscription_end_date", "DATETIME")
    add_column(conn, "users", "tariff", "VARCHAR")

    # Create new tables if missing
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("\nExisting tables:", tables)

    # chats
    if "chats" not in tables:
        conn.execute("""CREATE TABLE chats (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            chat_telegram_id BIGINT NOT NULL,
            username VARCHAR,
            title VARCHAR,
            type VARCHAR DEFAULT 'chat',
            status VARCHAR DEFAULT 'active',
            message_count INTEGER DEFAULT 0,
            created_at DATETIME
        )""")
        print("  + Created chats")

    # keywords
    if "keywords" not in tables:
        conn.execute("""CREATE TABLE keywords (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            word VARCHAR NOT NULL,
            category VARCHAR,
            created_at DATETIME
        )""")
        print("  + Created keywords")
    else:
        add_column(conn, "keywords", "category", "VARCHAR")

    # stopwords
    if "stopwords" not in tables:
        conn.execute("""CREATE TABLE stopwords (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            word VARCHAR NOT NULL,
            created_at DATETIME
        )""")
        print("  + Created stopwords")

    # chat_messages
    if "chat_messages" not in tables:
        conn.execute("""CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            telegram_message_id BIGINT,
            sender_id BIGINT,
            sender_username VARCHAR,
            text TEXT,
            date DATETIME,
            matched_keyword VARCHAR,
            is_dup BOOLEAN DEFAULT 0,
            processed BOOLEAN DEFAULT 0,
            created_at DATETIME
        )""")
        print("  + Created chat_messages")

    # radar_state
    if "radar_state" not in tables:
        conn.execute("""CREATE TABLE radar_state (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE,
            enabled BOOLEAN DEFAULT 1,
            updated_at DATETIME
        )""")
        print("  + Created radar_state")

    # tariff_plans
    if "tariff_plans" not in tables:
        conn.execute("""CREATE TABLE tariff_plans (
            id INTEGER PRIMARY KEY,
            code VARCHAR UNIQUE NOT NULL,
            name VARCHAR NOT NULL,
            price_rub FLOAT NOT NULL,
            days INTEGER NOT NULL,
            chat_limit INTEGER NOT NULL,
            keyword_limit INTEGER NOT NULL,
            description TEXT,
            is_active BOOLEAN DEFAULT 1
        )""")
        print("  + Created tariff_plans")

    # referrals
    if "referrals" not in tables:
        conn.execute("""CREATE TABLE referrals (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            referred_id INTEGER,
            code VARCHAR UNIQUE NOT NULL,
            clicks INTEGER DEFAULT 0,
            registrations INTEGER DEFAULT 0,
            payments INTEGER DEFAULT 0,
            bonus FLOAT DEFAULT 0.0,
            created_at DATETIME
        )""")
        print("  + Created referrals")

    # broadcasts
    if "broadcasts" not in tables:
        conn.execute("""CREATE TABLE broadcasts (
            id INTEGER PRIMARY KEY,
            admin_id BIGINT,
            text TEXT,
            sent_count INTEGER DEFAULT 0,
            total_count INTEGER DEFAULT 0,
            created_at DATETIME
        )""")
        print("  + Created broadcasts")

    # admin_logs
    if "admin_logs" not in tables:
        conn.execute("""CREATE TABLE admin_logs (
            id INTEGER PRIMARY KEY,
            admin_id BIGINT,
            action VARCHAR,
            target_id BIGINT,
            details TEXT,
            created_at DATETIME
        )""")
        print("  + Created admin_logs")

    # sources
    if "sources" in tables:
        add_column(conn, "sources", "category", "VARCHAR")

    # payments unique transaction_id
    if "payments" in tables:
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_transaction_id ON payments(transaction_id)")
            print("  + Created unique index on payments.transaction_id")
        except Exception as e:
            print(f"  = Unique index on payments.transaction_id skipped: {e}")

    conn.commit()
    conn.close()
    print("\nMigration complete!")

if __name__ == "__main__":
    main()