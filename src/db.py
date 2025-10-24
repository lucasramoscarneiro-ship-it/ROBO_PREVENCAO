import sqlite3
from pathlib import Path

STORES_SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
"""    

# === [ADD] Tabela de usuários (login) ===
USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    pwd_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin','operador')),
    store_id INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE SET NULL
);
"""


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS products (
    ean TEXT NOT NULL PRIMARY KEY,
    product_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ean TEXT NOT NULL,
    lot TEXT NOT NULL,
    expiry_date DATE NOT NULL,
    UNIQUE(ean, lot),
    FOREIGN KEY (ean) REFERENCES products(ean) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ean TEXT NOT NULL,
    lot TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty >= 0),
    location TEXT,
    store_id INTEGER,
    UNIQUE(ean, lot, location, store_id),
    FOREIGN KEY (ean) REFERENCES products(ean) ON DELETE CASCADE,
    FOREIGN KEY (ean, lot) REFERENCES lots(ean, lot) ON DELETE CASCADE,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    type TEXT NOT NULL CHECK(type IN ('receipt','sale','adjustment')),
    ean TEXT NOT NULL,
    lot TEXT NOT NULL,
    qty INTEGER NOT NULL,
    note TEXT,
    store_id INTEGER,
    FOREIGN KEY (store_id) REFERENCES stores(id) ON DELETE CASCADE
);
"""

# === [ADD] helpers de usuários ===
def get_user_by_username(conn, username: str):
    cur = conn.cursor()
    return cur.execute(
        "SELECT id, username, name, email, pwd_hash, role, is_active, store_id FROM users WHERE username=?",
        (username,)
    ).fetchone()


def create_user(conn, username: str, name: str, email: str, pwd_hash: str,
                role: str = "operador", is_active: int = 1, store_id: int = None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users(username, name, email, pwd_hash, role, store_id, is_active) VALUES(?,?,?,?,?,?,?)",
        (username, name, email, pwd_hash, role, store_id, is_active)
    )
    conn.commit()
    return cur.lastrowid


def list_users(conn):
    import pandas as pd
    return pd.read_sql_query(
        "SELECT id, username, name, email, role, is_active, store_id, created_at FROM users ORDER BY id DESC",
        conn
    )


def update_user_status(conn, username: str, is_active: int):
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active=? WHERE username=?", (is_active, username))
    conn.commit()

def update_user_role(conn, username: str, role: str):
    cur = conn.cursor()
    cur.execute("UPDATE users SET role=? WHERE username=?", (role, username))
    conn.commit()

def update_user_password(conn, username: str, pwd_hash: str):
    cur = conn.cursor()
    cur.execute("UPDATE users SET pwd_hash=? WHERE username=?", (pwd_hash, username))
    conn.commit()

# === [ADD] helpers de lojas ===
def list_stores(conn):
    import pandas as pd
    return pd.read_sql_query("SELECT id, name FROM stores ORDER BY id", conn)

def create_store(conn, name: str):
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO stores(name) VALUES(?)", (name,))
    conn.commit()
    return cur.lastrowid

def get_store_id(conn, name: str):
    cur = conn.cursor()
    row = cur.execute("SELECT id FROM stores WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    return create_store(conn, name)


def get_conn(db_path: str):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(conn: sqlite3.Connection):
    conn.executescript(STORES_SCHEMA)
    conn.executescript(SCHEMA)
    conn.executescript(USERS_SCHEMA)
    conn.commit()
