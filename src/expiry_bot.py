import json
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime
import pandas as pd
from db import get_conn, init_db
import reporting
import sqlite3
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os

# === CONFIG E INICIALIZAÇÃO ===
def carregar_config(caminho_cfg):
    with open(caminho_cfg, "r", encoding="utf-8") as f:
        return json.load(f)


def garantir_db(cfg):
    conn = get_conn(cfg["database_path"])
    init_db(conn)
    return conn


# === IMPORTAÇÃO DE PLANILHA ===
def importar_planilha(conn, caminho_arquivo, store_id=None):
    df = (
        pd.read_excel(caminho_arquivo)
        if caminho_arquivo.lower().endswith(".xlsx")
        else pd.read_csv(caminho_arquivo)
    )

    # aceitar cabeçalhos em PT-BR
    mapa = {
        "ean": "ean",
        "nome_produto": "product_name",
        "produto": "product_name",
        "product_name": "product_name",
        "lote": "lot",
        "data_validade": "expiry_date",
        "validade": "expiry_date",
        "expiry_date": "expiry_date",
        "quantidade": "qty",
        "qty": "qty",
        "local": "location",
        "location": "location",
    }
    cols_norm = {c: mapa.get(c.strip().lower(), c.strip().lower()) for c in df.columns}
    df.rename(columns=cols_norm, inplace=True)

    required = {"ean", "product_name", "lot", "expiry_date", "qty", "location"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise ValueError(f"Colunas faltando no arquivo: {missing}")

    df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    if df["expiry_date"].isna().any():
        bad = df[df["expiry_date"].isna()]
        raise ValueError(f"Datas de validade inválidas nas linhas: {bad.index.tolist()}")

    cur = conn.cursor()
    for _, r in df.iterrows():
        ean = str(r["ean"]).strip()
        pname = str(r["product_name"]).strip()
        lot = str(r["lot"]).strip()
        expiry = r["expiry_date"].date().isoformat()
        qty = int(r["qty"])
        location = None if pd.isna(r.get("location")) else str(r["location"]).strip()

        cur.execute("INSERT OR IGNORE INTO products(ean, product_name) VALUES(?,?)", (ean, pname))
        cur.execute(
            "UPDATE products SET product_name=COALESCE(NULLIF(?, ''), product_name) WHERE ean=?",
            (pname, ean),
        )
        cur.execute(
            "INSERT OR IGNORE INTO lots(ean, lot, expiry_date) VALUES(?,?,?)",
            (ean, lot, expiry),
        )
        cur.execute(
            "INSERT OR IGNORE INTO stock(ean, lot, qty, location, store_id) VALUES(?,?,?,?,?)",
            (ean, lot, max(qty, 0), location, store_id),
        )
        cur.execute(
            "UPDATE stock SET qty=? WHERE ean=? AND lot=? AND IFNULL(location, '')=IFNULL(?, '') AND store_id IS ?",
            (max(qty, 0), ean, lot, location, store_id),
        )
        cur.execute(
            "INSERT INTO movements(type, ean, lot, qty, note, store_id) VALUES('adjustment',?,?,?,?,?)",
            (ean, lot, qty, f"Importação {Path(caminho_arquivo).name}", store_id),
        )
    conn.commit()

    return {
        "total_itens": len(df),
        "sucesso": True,
        "mensagem": f"{len(df)} itens importados para a loja {store_id or 'Global'}.",
    }


# === MOVIMENTAÇÃO (ENTRADA/SAÍDA) ===
def movimentar(conn, tipo, ean, lot, qty, observacao=None, local=None, store_id=None):
    """Registra entrada ou saída de estoque, criando o registro se necessário."""
    if not ean or not lot:
        raise ValueError("EAN e Lote são obrigatórios.")
    if qty <= 0:
        raise ValueError("Quantidade deve ser maior que zero.")

    tipo = tipo.lower().strip()
    if tipo not in ("receipt", "sale"):
        raise ValueError(f"Tipo de movimento inválido: {tipo}")

    cur = conn.cursor()
    sign = 1 if tipo == "receipt" else -1

    # Usa 0 como padrão se não houver loja vinculada
    store_id = store_id or 0
    local = local or "Loja 01"

    # Cria o registro de estoque se não existir
    cur.execute("""
        INSERT OR IGNORE INTO stock (ean, lot, qty, location, store_id)
        VALUES (?, ?, 0, ?, ?)
    """, (ean, lot, local, store_id))

    # Busca registro existente
    row = cur.execute("""
        SELECT id, qty FROM stock
        WHERE ean=? AND lot=? AND IFNULL(store_id,0)=? AND IFNULL(location,'')=IFNULL(?, '')
    """, (ean, lot, store_id, local)).fetchone()

    if not row:
        raise sqlite3.IntegrityError("Falha ao localizar o registro de estoque após criação.")

    stock_id, current_qty = row
    new_qty = current_qty + (sign * qty)
    if new_qty < 0:
        raise ValueError(f"Estoque insuficiente para {ean}-{lot}: atual={current_qty}, tentativa={qty}")

    # Atualiza o estoque
    cur.execute("UPDATE stock SET qty=? WHERE id=?", (new_qty, stock_id))

    # Registra o movimento
    cur.execute("""
        INSERT INTO movements (type, ean, lot, qty, note, store_id, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (tipo, ean, lot, qty, observacao or "", store_id, datetime.now().isoformat(timespec="seconds")))

    conn.commit()



# === ENVIO DE ALERTA POR E-MAIL ===
def enviar_email_alerta(cfg, subject, body, anexos=None):
    """
    Envia e-mail de alerta com ou sem anexos.
    - cfg: dicionário de configuração (inclui alert_email)
    - subject: assunto do e-mail
    - body: corpo do e-mail (texto simples)
    - anexos: lista de caminhos de arquivos (ex: [pdf_path])
    """
    try:
        alert_cfg = cfg.get("alert_email", {})
        if not alert_cfg.get("enabled", False):
            return False, "Envio de e-mail desativado."

        smtp_server = alert_cfg.get("smtp_server")
        smtp_port = alert_cfg.get("smtp_port", 587)
        username = alert_cfg.get("username")
        password = alert_cfg.get("password")
        from_addr = alert_cfg.get("from_addr", username)
        to_addrs = alert_cfg.get("to_addrs", [])

        if not all([smtp_server, username, password, from_addr, to_addrs]):
            return False, "Configuração de e-mail incompleta."

        # Monta mensagem
        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg["Subject"] = subject

        # Corpo do e-mail
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Anexa arquivos, se existirem
        if anexos:
            for arquivo in anexos:
                if os.path.exists(arquivo):
                    with open(arquivo, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f'attachment; filename="{os.path.basename(arquivo)}"',
                    )
                    msg.attach(part)
                else:
                    print(f"⚠️ Arquivo não encontrado para anexo: {arquivo}")

        # Envia o e-mail
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            if alert_cfg.get("use_tls", True):
                server.starttls()
            server.login(username, password)
            server.send_message(msg)

        return True, f"E-mail enviado com sucesso para {', '.join(to_addrs)}"

    except Exception as e:
        return False, f"Erro ao enviar e-mail: {e}"


# === EXPORTAR RELATÓRIOS (COM FILTRO DE LOJA) ===
def exportar_relatorios(conn, cfg, store_id=None):
    """
    Gera relatórios Excel filtrados por loja (store_id).
    Admins (sem store_id) veem todas as lojas.
    """
    from reporting import to_console

    df = reporting.build_snapshots(conn)
    if store_id and "store_id" in df.columns:
        df = df[df["store_id"] == store_id]

    outdir = Path(cfg["report_dir"])
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")

    loja_tag = f"_Loja_{store_id}" if store_id else ""
    path_xlsx = outdir / f"relatorio_validade{loja_tag}_{stamp}.xlsx"

    with pd.ExcelWriter(path_xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="estoque_atual")
        reporting.near_expiry(df, cfg["near_expiry_days"]).to_excel(
            writer, index=False, sheet_name="a_vencer"
        )
        reporting.expired(df).to_excel(writer, index=False, sheet_name="vencidos")
        reporting.fefo_picklist(df).to_excel(writer, index=False, sheet_name="fefo")

    near = reporting.near_expiry(df, cfg["near_expiry_days"])
    msg = to_console(near, f"Itens a vencer em {cfg['near_expiry_days']} dias")

    return path_xlsx, msg
