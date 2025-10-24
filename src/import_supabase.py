import pandas as pd
import psycopg2
import streamlit as st

# 📦 Lê os CSVs exportados
users = pd.read_csv("users_export.csv")
stores = pd.read_csv("stores_export.csv")

# 🔌 Conexão com Supabase
creds = st.secrets["postgres"]
conn = psycopg2.connect(
    host=creds["host"],
    port=creds["port"],
    dbname=creds["database"],
    user=creds["user"],
    password=creds["password"],
    sslmode=creds.get("sslmode", "require")
)
cur = conn.cursor()

# ✅ Insere as lojas primeiro
for _, row in stores.iterrows():
    cur.execute("""
        INSERT INTO stores (id, name)
        VALUES (%s, %s)
        ON CONFLICT (id) DO NOTHING;
    """, (int(row["id"]), row["name"]))

conn.commit()

# ✅ Agora insere os usuários
for _, row in users.iterrows():
    cur.execute("""
        INSERT INTO users (id, username, name, email, pwd_hash, role, is_active, store_id, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO NOTHING;
    """, (
        int(row["id"]),
        row["username"],
        row["name"],
        row["email"],
        row["pwd_hash"],
        row["role"],
        bool(row["is_active"]),
        None if pd.isna(row["store_id"]) else int(row["store_id"]),
        row["created_at"]
    ))

conn.commit()
cur.close()
conn.close()

print("✅ Dados importados com sucesso para o Supabase!")
