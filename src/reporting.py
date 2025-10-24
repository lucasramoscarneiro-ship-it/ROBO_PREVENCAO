from datetime import date, timedelta
import pandas as pd
from tabulate import tabulate


def build_snapshots(conn):
    """
    Retorna o snapshot atual do estoque,
    incluindo o campo store_id para permitir filtro por loja.
    """
    q = """
    SELECT 
        s.ean,
        p.product_name,
        s.lot,
        l.expiry_date,
        s.qty,
        IFNULL(s.location, '') AS location,
        s.store_id
    FROM stock s
    JOIN lots l ON l.ean = s.ean AND l.lot = s.lot
    JOIN products p ON p.ean = s.ean
    WHERE s.qty > 0
    ORDER BY s.store_id, l.expiry_date ASC
    """
    df = pd.read_sql_query(q, conn, parse_dates=["expiry_date"])
    return df


def near_expiry(df, days=15):
    """Filtra itens que vencem nos próximos X dias."""
    today = pd.Timestamp.today().normalize()
    limit = today + pd.Timedelta(days=days)
    m = (df["expiry_date"] >= today) & (df["expiry_date"] <= limit)
    return df.loc[m].copy()


def expired(df):
    """Filtra itens já vencidos."""
    today = pd.Timestamp.today().normalize()
    return df.loc[df["expiry_date"] < today].copy()


def fefo_picklist(df):
    """Sugere ordem de saída (FEFO — First Expired, First Out)."""
    picklist = df.sort_values(["ean", "expiry_date"]).copy()
    picklist["ordem_sugerida"] = picklist.groupby("ean").cumcount() + 1
    return picklist


def to_console(df, title):
    """Gera uma string formatada para exibição em console (ex: e-mail ou logs)."""
    if df.empty:
        return f"\n=== {title} ===\nNenhum item encontrado.\n"
    tbl = tabulate(df, headers="keys", tablefmt="github", showindex=False)
    return f"\n=== {title} ===\n{tbl}\n"
