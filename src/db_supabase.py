# src/db_supabase.py
import os
from typing import Optional, Any
import psycopg2
import psycopg2.extras
import pandas as pd

# Em apps Streamlit, as credenciais vêm do .streamlit/secrets.toml
try:
    import streamlit as st
    _SECRETS = st.secrets.get("postgres", {})
except Exception:
    # fallback p/ execução fora do Streamlit (ex.: scripts)
    _SECRETS = {
        "host": os.getenv("PGHOST"),
        "port": os.getenv("PGPORT", "5432"),
        "database": os.getenv("PGDATABASE", "postgres"),
        "user": os.getenv("PGUSER", "postgres"),
        "password": os.getenv("PGPASSWORD"),
        "sslmode": os.getenv("PGSSLMODE", "require"),
    }


def get_conn(_ignored_path: str = ""):
    """
    Devolve uma conexão psycopg2 ao Postgres do Supabase.
    O argumento é ignorado (mantido por compatibilidade com o SQLite).
    """
    required = ["host", "port", "database", "user", "password"]
    missing = [k for k in required if not _SECRETS.get(k)]
    if missing:
        raise RuntimeError(f"Faltam chaves no secrets.toml (postgres): {missing}")

    conn = psycopg2.connect(
        host=_SECRETS["host"],
        port=int(_SECRETS["port"]),
        dbname=_SECRETS["database"],
        user=_SECRETS["user"],
        password=_SECRETS["password"],
        sslmode=_SECRETS.get("sslmode", "require"),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = False  # manter controle de transação
    return conn


# ---------- SCHEMA ----------

# stores (já existe no seu projeto)
STORES_SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
"""

# users (já existe no seu projeto)
USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    pwd_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','operador')),
    store_id INTEGER REFERENCES stores(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);
"""

# demais tabelas usadas no app
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    ean TEXT PRIMARY KEY,
    product_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lots (
    id SERIAL PRIMARY KEY,
    ean TEXT NOT NULL REFERENCES products(ean) ON DELETE CASCADE,
    lot TEXT NOT NULL,
    expiry_date DATE NOT NULL,
    UNIQUE(ean, lot)
);

CREATE TABLE IF NOT EXISTS stock (
    id SERIAL PRIMARY KEY,
    ean TEXT NOT NULL REFERENCES products(ean) ON DELETE CASCADE,
    lot TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty >= 0),
    location TEXT,
    store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE,
    UNIQUE(ean, lot, location, store_id)
);

CREATE TABLE IF NOT EXISTS movements (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    type TEXT NOT NULL CHECK (type IN ('receipt','sale','adjustment')),
    ean TEXT NOT NULL,
    lot TEXT NOT NULL,
    qty INTEGER NOT NULL,
    note TEXT,
    store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE
);
"""


def init_db(conn) -> None:
    """Cria tabelas que faltarem no Supabase (safe para rodar várias vezes)."""
    with conn.cursor() as cur:
        cur.execute(STORES_SCHEMA)
        cur.execute(USERS_SCHEMA)
        cur.execute(SCHEMA)
    conn.commit()


# ---------- HELPERS DE USUÁRIO ----------

def get_user_by_username(conn, username: str) -> Optional[tuple]:
    """
    Retorna a linha do usuário com colunas alinhadas ao seu auth.py:
    (id, username, name, email, pwd_hash, role, is_active, store_id)
    """
    sql = """
        SELECT id, username, name, email, pwd_hash, role, is_active, store_id
        FROM users
        WHERE username = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql, (username,))
        row = cur.fetchone()
    if not row:
        return None
    # psycopg2 RealDictRow -> tuple na ordem esperada
    return (
        row["id"], row["username"], row["name"], row["email"],
        row["pwd_hash"], row["role"], row["is_active"], row["store_id"]
    )


def create_user(conn, username: str, name: str, email: str, pwd_hash: str,
                role: str = "operador", is_active: int = 1, store_id: int | None = None) -> int:
    sql = """
        INSERT INTO users (username, name, email, pwd_hash, role, store_id, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (username, name, email, pwd_hash, role, store_id, is_active))
        new_id = cur.fetchone()["id"]
    conn.commit()
    return new_id


def list_users(conn) -> pd.DataFrame:
    sql = """
        SELECT
            id, username, name, email, role, is_active, store_id, created_at
        FROM users
        ORDER BY id DESC
    """
    return pd.read_sql(sql, conn)


def update_user_status(conn, username: str, is_active: int) -> None:
    sql = "UPDATE users SET is_active=%s WHERE username=%s"
    with conn.cursor() as cur:
        cur.execute(sql, (is_active, username))
    conn.commit()


def update_user_role(conn, username: str, role: str) -> None:
    sql = "UPDATE users SET role=%s WHERE username=%s"
    with conn.cursor() as cur:
        cur.execute(sql, (role, username))
    conn.commit()


def update_user_password(conn, username: str, pwd_hash: str) -> None:
    sql = "UPDATE users SET pwd_hash=%s WHERE username=%s"
    with conn.cursor() as cur:
        cur.execute(sql, (pwd_hash, username))
    conn.commit()


# ---------- HELPERS DE LOJA ----------

def list_stores(conn) -> pd.DataFrame:
    return pd.read_sql("SELECT id, name FROM stores ORDER BY id", conn)


def create_store(conn, name: str) -> int:
    sql = """
        INSERT INTO stores (name)
        VALUES (%s)
        ON CONFLICT (name) DO NOTHING
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (name,))
        row = cur.fetchone()
        if row and "id" in row:
            new_id = row["id"]
        else:
            # já existia; pega id
            cur.execute("SELECT id FROM stores WHERE name=%s", (name,))
            new_id = cur.fetchone()["id"]
    conn.commit()
    return new_id


def get_store_id(conn, name: str) -> int:
    sql = "SELECT id FROM stores WHERE name=%s"
    with conn.cursor() as cur:
        cur.execute(sql, (name,))
        row = cur.fetchone()
        if row:
            return row["id"]
    return create_store(conn, name)
