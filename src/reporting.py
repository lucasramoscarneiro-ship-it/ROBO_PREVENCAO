# -*- coding: utf-8 -*-
"""
Módulo: reporting.py
Responsável por gerar snapshots, relatórios e filtros de vencimento do estoque.

Compatível com: Supabase (PostgreSQL via psycopg2)
Autor: Lucas Ramos (Apex Platform)
Versão: 2025-10-27
"""

from datetime import datetime, timedelta
import pandas as pd
from tabulate import tabulate
import psycopg2.extras


# ============================================================
# 🔹 SNAPSHOT PRINCIPAL (CONSULTA CONSOLIDADA)
# ============================================================

def build_snapshots(conn):
    """
    Retorna o snapshot atual do estoque (visão consolidada),
    incluindo produtos sem lote e garantindo visibilidade por loja.

    ✅ Corrigido e otimizado:
      - Usa cursor RealDictCursor (compatível com Supabase Pooler)
      - Inclui prefixo 'public.' em todas as tabelas
      - Corrige tipos e evita erro do pandas com SQLAlchemy
      - Sempre retorna DataFrame válido (nunca None)
    """

    q = """
    SELECT 
        s.ean,
        COALESCE(p.product_name, 'Produto sem nome') AS product_name,
        s.lot,
        COALESCE(l.expiry_date, CURRENT_DATE + INTERVAL '180 days') AS expiry_date,
        COALESCE(s.qty, 0) AS qty,
        COALESCE(s.location, '') AS location,
        CAST(s.store_id AS INTEGER) AS store_id
    FROM public.stock AS s
    LEFT JOIN public.lots AS l 
        ON l.ean = s.ean AND l.lot = s.lot
    LEFT JOIN public.products AS p 
        ON p.ean = s.ean
    WHERE s.qty >= 0
    ORDER BY s.store_id, s.ean, s.lot;
    """

    try:
        # 🔧 Usa cursor compatível com Supabase (sem precisar de SQLAlchemy)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(q)
            rows = cur.fetchall()
            df = pd.DataFrame(rows)

        # 🔍 Pós-processamento e normalização
        if not df.empty:
            df["ean"] = df["ean"].astype(str)
            if "store_id" in df.columns:
                df["store_id"] = pd.to_numeric(df["store_id"], errors="coerce").fillna(0).astype(int)
            df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
            print(f"🔍 Snapshot carregado com {len(df)} registros — stores únicos: {df['store_id'].unique().tolist()}")
        else:
            print("⚠️ Nenhum registro retornado do snapshot (consulta executada, mas vazia).")

    except Exception as e:
        print(f"[build_snapshots] Erro ao consultar snapshot: {e}")
        df = pd.DataFrame(columns=["ean", "product_name", "lot", "expiry_date", "qty", "location", "store_id"])

    return df


# ============================================================
# 🔹 FILTROS DE VENCIMENTO
# ============================================================

def near_expiry(df, days=15):
    """
    Filtra itens que vencem nos próximos X dias.
    Retorna DataFrame com as mesmas colunas.
    """
    if df is None or df.empty or "expiry_date" not in df.columns:
        return pd.DataFrame(columns=df.columns if df is not None else [])

    today = pd.Timestamp.today().normalize()
    limit = today + pd.Timedelta(days=days)

    mask = (df["expiry_date"] >= today) & (df["expiry_date"] <= limit)
    result = df.loc[mask].copy()

    print(f"🟡 {len(result)} itens a vencer em até {days} dias.")
    return result


def expired(df):
    """Filtra itens já vencidos."""
    if df is None or df.empty or "expiry_date" not in df.columns:
        return pd.DataFrame(columns=df.columns if df is not None else [])

    today = pd.Timestamp.today().normalize()
    mask = df["expiry_date"] < today
    result = df.loc[mask].copy()

    print(f"🔴 {len(result)} itens vencidos encontrados.")
    return result


# ============================================================
# 🔹 ORDENAÇÃO FEFO (FIRST EXPIRED, FIRST OUT)
# ============================================================

def fefo_picklist(df):
    """
    Sugere ordem de saída (FEFO — First Expired, First Out).
    Retorna DataFrame ordenado por validade e com campo 'ordem_sugerida'.
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=["ean", "product_name", "lot", "expiry_date", "qty", "location", "ordem_sugerida"]
        )

    df = df.copy()
    df = df.sort_values(["ean", "expiry_date"], ascending=[True, True])
    df["ordem_sugerida"] = df.groupby("ean").cumcount() + 1

    print(f"📦 Lista FEFO gerada ({len(df)} registros ordenados).")
    return df


# ============================================================
# 🔹 CONSOLE / ALERTAS FORMATADOS
# ============================================================

def to_console(df, title):
    """
    Gera string formatada para exibição (e-mails, logs ou alertas).
    Exemplo:
        === Itens a vencer ===
        | ean | produto | validade | qty |
    """
    if df is None or df.empty:
        return f"\n=== {title} ===\nNenhum item encontrado.\n"

    display_cols = [c for c in ["ean", "product_name", "lot", "expiry_date", "qty", "location"] if c in df.columns]
    tbl = tabulate(df[display_cols], headers="keys", tablefmt="github", showindex=False)

    print(f"📋 Geração de relatório '{title}' concluída ({len(df)} linhas).")
    return f"\n=== {title} ===\n{tbl}\n"
