# src/db_supabase.py
import os
from typing import Optional, Any
import psycopg2
import psycopg2.extras
import pandas as pd
import socket
import bcrypt
from pathlib import Path
import json
import psycopg2
import psycopg2.extras
import streamlit as st
# === CREDENCIAIS ===
try:
    import streamlit as st
    _SECRETS = st.secrets.get("postgres", {})
except Exception:
    _SECRETS = {
        "host": os.getenv("PGHOST"),
        "port": os.getenv("PGPORT", "5432"),
        "database": os.getenv("PGDATABASE", "postgres"),
        "user": os.getenv("PGUSER", "postgres"),
        "password": os.getenv("PGPASSWORD"),
        "sslmode": os.getenv("PGSSLMODE", "require"),
    }


def get_conn():
    """Conecta ao Supabase e garante o uso do schema 'public' (inclusive em poolers)."""
    try:
        _SECRETS = st.secrets["postgres"]
    except Exception:
        raise RuntimeError("⚠️ Erro: credenciais do banco não encontradas em st.secrets['postgres'].")

    try:
        conn = psycopg2.connect(
            host=_SECRETS["host"],
            port=_SECRETS.get("port", 5432),
            dbname=_SECRETS.get("database", "postgres"),
            user=_SECRETS["user"],
            password=_SECRETS["password"],
            sslmode=_SECRETS.get("sslmode", "require"),
            connect_timeout=10,
            cursor_factory=psycopg2.extras.RealDictCursor,
            options='-c search_path=public'  # ✅ força schema public
        )
        print("✅ Conectado ao Supabase (search_path=public via options).")

        # 🔧 Tentativa adicional (opcional, caso o pooler permita)
        with conn.cursor() as cur:
            cur.execute("SHOW search_path;")
            current_schema = cur.fetchone()
            print(f"📂 Schema ativo após conexão: {current_schema}")
        return conn

    except Exception as e:
        print(f"❌ Falha ao conectar ao Supabase: {e}")
        raise


# ---------- SCHEMA ----------
STORES_SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
"""

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
    """Cria tabelas que faltarem no Supabase (seguro para múltiplas execuções)."""
    with conn.cursor() as cur:
        cur.execute(STORES_SCHEMA)
        cur.execute(USERS_SCHEMA)
        cur.execute(SCHEMA)
    conn.commit()


# ---------- HELPERS DE USUÁRIO ----------
def get_user_by_username(conn, username: str) -> Optional[tuple]:
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
    return (
        row["id"], row["username"], row["name"], row["email"],
        row["pwd_hash"], row["role"], row["is_active"], row["store_id"]
    )


def create_user(conn, username: str, name: str, email: str, password: str,
                role: str = "operador", is_active: int = 1, store_id: int | None = None) -> int:
    """
    Cria um usuário com senha criptografada (bcrypt).
    Se store_id for None, cria/associa à loja padrão 'Loja 01'.
    """
    if store_id is None:
        store_id = get_store_id(conn, "Loja 01")

    pwd_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    sql = """
        INSERT INTO users (username, name, email, pwd_hash, role, store_id, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (username) DO NOTHING
        RETURNING id
    """
    with conn.cursor() as cur:
        cur.execute(sql, (username, name, email, pwd_hash, role, store_id, is_active))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT id FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
        new_id = row["id"]
    conn.commit()
    print(f"👤 Usuário '{username}' criado com ID {new_id}, vinculado à loja {store_id}")
    return new_id


def list_users(conn) -> pd.DataFrame:
    """
    Retorna todos os usuários como DataFrame com colunas padronizadas.
    Compatível com cursores do tipo RealDictCursor (dict) e padrão (tuple).
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    id,
                    username,
                    name,
                    email,
                    role,
                    is_active,
                    store_id,
                    created_at
                FROM users
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()

        if not rows:
            return pd.DataFrame()

        # --- Caso RealDictCursor (lista de dicionários)
        if isinstance(rows[0], dict):
            df = pd.DataFrame(rows)
        else:
            # --- Caso cursor padrão (lista de tuplas)
            cols = ["id", "username", "name", "email", "role", "is_active", "store_id", "created_at"]
            df = pd.DataFrame(rows, columns=cols)

        return df

    except Exception as e:
        print(f"[list_users] Erro ao consultar usuários: {e}")
        return pd.DataFrame()



def update_user_status(conn, username: str, is_active: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET is_active=%s WHERE username=%s", (is_active, username))
    conn.commit()


def update_user_role(conn, username: str, role: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET role=%s WHERE username=%s", (role, username))
    conn.commit()


def update_user_password(conn, username: str, new_password: str) -> None:
    """Atualiza a senha do usuário com hash seguro."""
    pwd_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET pwd_hash=%s WHERE username=%s", (pwd_hash, username))
    conn.commit()


# ---------- HELPERS DE LOJA ----------
def list_stores(conn) -> pd.DataFrame:
    return pd.read_sql("SELECT id, name FROM stores ORDER BY id", conn)


def create_store(conn, name: str) -> int:
    """
    Cria uma loja se não existir e retorna seu ID.
    Também gera automaticamente um arquivo config_loja_{id}.json.
    """
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
            cur.execute("SELECT id FROM stores WHERE name=%s", (name,))
            row = cur.fetchone()
            if not row:
                raise RuntimeError(f"Falha ao criar ou recuperar loja '{name}'")
            new_id = row["id"]
    conn.commit()

    # --- 🔧 Cria arquivo de configuração individual para a loja ---
    try:
        base_dir = Path(__file__).resolve().parents[1]
        cfg_path = base_dir / f"config_loja_{new_id}.json"
        if not cfg_path.exists():
            default_cfg = {
                "store_id": new_id,
                "store_name": name,
                "alert_email": {"enabled": False, "to": "", "from": "", "smtp": "", "port": "", "password": ""},
                "last_alert_sent": None
            }
            cfg_path.write_text(json.dumps(default_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"🧩 Configuração criada: {cfg_path}")
    except Exception as e:
        print(f"⚠️ Não foi possível gerar config_loja_{new_id}.json: {e}")

    print(f"🏬 Loja '{name}' registrada com ID {new_id}")
    return new_id


def get_store_id(conn, name: str) -> int:
    sql = "SELECT id FROM stores WHERE name=%s"
    with conn.cursor() as cur:
        cur.execute(sql, (name,))
        row = cur.fetchone()
        if row:
            return row["id"]
    return create_store(conn, name)
