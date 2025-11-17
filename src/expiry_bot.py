# src/expiry_bot.py
# -*- coding: utf-8 -*-
import json
from pathlib import Path
from datetime import datetime
import os
import pandas as pd
from psycopg2 import sql
from db_supabase import get_conn, init_db
import reporting
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import hashlib

# ==============================
# Utilidades
# ==============================
def _row_get(row, key_or_idx):
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key_or_idx)
    if isinstance(key_or_idx, int):
        return row[key_or_idx]
    mapping = {"id": 0, "ts": 1, "type": 2, "ean": 3, "lot": 4, "qty": 5, "note": 6, "store_id": 7}
    idx = mapping.get(str(key_or_idx).lower())
    return row[idx] if idx is not None else None


def garantir_db(cfg):
    conn = get_conn(cfg.get("database_path", ""))
    init_db(conn)
    return conn


# ==============================
# Importação de planilha
# ==============================
def importar_planilha(conn, caminho_arquivo, store_id):
    """
    Importa planilha e registra tudo via movimentar('receipt'),
    garantindo integridade por loja.
    """
    if not store_id:
        raise ValueError("⚠️ Loja (store_id) não informada na importação.")

    # Ler planilha
    if caminho_arquivo.lower().endswith(".xlsx"):
        df = pd.read_excel(caminho_arquivo)
    else:
        df = pd.read_csv(caminho_arquivo)

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
        "qtd": "qty",
        "qty": "qty",
        "local": "location",
        "location": "location",
    }
    df.rename(columns={c: mapa.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns}, inplace=True)

    required = {"ean", "product_name", "lot", "expiry_date", "qty"}
    missing = required - set([c.lower() for c in df.columns])
    if missing:
        raise ValueError(f"Colunas faltando no arquivo: {missing}")

    df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    if df["expiry_date"].isna().any():
        linhas_ruins = df[df["expiry_date"].isna()].index.tolist()
        raise ValueError(f"Datas de validade inválidas nas linhas: {linhas_ruins}")

    with conn.cursor() as cur:
        for _, r in df.iterrows():
            ean = str(r["ean"]).strip()
            pname = str(r["product_name"]).strip()
            lot = str(r["lot"]).strip()
            expiry = r["expiry_date"].date().isoformat()
            qty = int(r["qty"])
            location = None
            if "location" in df.columns and pd.notna(r.get("location")):
                location = str(r["location"]).strip()

            # Produto e lote
            cur.execute("""
                INSERT INTO products (ean, product_name)
                VALUES (%s, %s)
                ON CONFLICT (ean) DO NOTHING
            """, (ean, pname))
            cur.execute("""
                UPDATE products
                SET product_name = COALESCE(NULLIF(%s, ''), product_name)
                WHERE ean = %s
            """, (pname, ean))
            cur.execute("""
                INSERT INTO lots (ean, lot, expiry_date)
                VALUES (%s, %s, %s)
                ON CONFLICT (ean, lot) DO NOTHING
            """, (ean, lot, expiry))

            movimentar(
                conn=conn,
                tipo="receipt",
                ean=ean,
                lot=lot,
                qty=qty,
                observacao=f"Importação {Path(caminho_arquivo).name}",
                local=location or f"Loja {store_id}",
                store_id=store_id,
                _already_open_cursor=cur,
            )

    conn.commit()
    return {
        "total_itens": len(df),
        "sucesso": True,
        "mensagem": f"{len(df)} itens importados com sucesso para a loja {store_id}.",
    }


# ==============================
# Movimentações
# ==============================

def movimentar(conn, tipo, ean, lot, qty, observacao=None, local=None, store_id=None, _already_open_cursor=None):
    """
    Movimenta o estoque (entrada ou venda) e registra o movimento real no banco.
    Corrige problema de dados literais ('ean', 'lot', etc.) gravados incorretamente.
    """
    tipo = str(tipo).lower().strip()
    if tipo not in ("receipt", "sale"):
        raise ValueError(f"Tipo inválido: {tipo}")
    if not store_id:
        raise ValueError("store_id é obrigatório.")

    try:
        qty = int(qty)
    except Exception:
        raise ValueError("Quantidade inválida.")
    if qty <= 0:
        raise ValueError("Quantidade deve ser maior que 0.")

    local = local or f"Loja {store_id}"
    note = observacao or ""

    cur_cm = None
    cur = _already_open_cursor
    if cur is None:
        cur_cm = conn.cursor()
        cur = cur_cm

    try:
        # Garante produto e lote
        cur.execute("""
            INSERT INTO products (ean, product_name)
            VALUES (%s, %s)
            ON CONFLICT (ean) DO NOTHING;
        """, (str(ean), "Produto sem nome"))

        cur.execute("""
            INSERT INTO lots (ean, lot, expiry_date)
            VALUES (%s, %s, CURRENT_DATE + INTERVAL '180 days')
            ON CONFLICT (ean, lot) DO NOTHING;
        """, (str(ean), str(lot)))

        # Busca estoque atual
        cur.execute("""
            SELECT id, qty FROM stock
            WHERE ean = %s AND lot = %s AND store_id = %s
            FOR UPDATE;
        """, (str(ean), str(lot), store_id))
        row = cur.fetchone()

        if not row:
            cur.execute("""
                INSERT INTO stock (ean, lot, qty, location, store_id)
                VALUES (%s, %s, 0, %s, %s)
                RETURNING id, qty;
            """, (str(ean), str(lot), local, store_id))
            row = cur.fetchone()

        stock_id = row["id"] if isinstance(row, dict) else row[0]
        current_qty = row["qty"] if isinstance(row, dict) else row[1]

        # Atualiza quantidade
        if tipo == "receipt":
            new_qty = current_qty + qty
        else:
            new_qty = current_qty - qty
            if new_qty < 0:
                raise ValueError(f"Saldo insuficiente para {ean}/{lot} (saldo atual: {current_qty})")

        cur.execute("""
            UPDATE stock
            SET qty = %s, location = COALESCE(%s, location)
            WHERE id = %s;
        """, (new_qty, local, stock_id))

        # 🔹 Registra o movimento REAL (sem aspas literais)
        cur.execute("""
            INSERT INTO movements (ts, type, ean, lot, qty, note, store_id, origin_key)
            VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s);
        """, (
            tipo,
            str(ean),
            str(lot),
            int(qty),
            str(note),
            int(store_id),
            f"{tipo}_{ean}_{lot}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        ))

        conn.commit()
        print(f"✅ Movimento registrado: tipo={tipo}, ean={ean}, lot={lot}, qtd={qty}, loja={store_id}")

    except Exception as e:
        conn.rollback()
        print(f"[movimentar] Erro: {e}")
        raise
    finally:
        if cur_cm is not None:
            cur_cm.close()



# ==============================
# Envio de e-mails
# ==============================
def enviar_email_alerta(cfg, subject=None, body=None, anexos=None, conn=None, store_id=None, df=None):
    """
    Envia e-mail profissional com o PDF de relatório de validades anexado,
    personalizado para cada loja, com layout institucional.
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

        # 🔎 Nome da loja dinâmico
        loja_nome = f"Loja {store_id}"
        if conn is not None and store_id is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT name FROM stores WHERE id = %s;", (store_id,))
                    row = cur.fetchone()
                    if row:
                        loja_nome = row["name"] if isinstance(row, dict) else row[0]
            except Exception:
                pass

        # 🧩 Gera PDF automaticamente
        pdf_path = None
        if conn is not None and store_id is not None:
            try:
                from report_pdf import gerar_relatorio_pdf
                import reporting

                df = df or reporting.build_snapshots(conn)
                if "store_id" in df.columns:
                    df = df[df["store_id"] == store_id]

                total_estoque = int(df["qty"].fillna(0).sum()) if "qty" in df.columns else 0
                total_a_vencer = int(reporting.near_expiry(df, cfg.get("near_expiry_days", 7))["qty"].fillna(0).sum())
                total_vencido = int(reporting.expired(df)["qty"].fillna(0).sum())
                total_vendido = 0

                pdf_path = gerar_relatorio_pdf(
                    cfg,
                    df=df,
                    total_estoque=total_estoque,
                    total_a_vencer=total_a_vencer,
                    total_vencido=total_vencido,
                    total_vendido=total_vendido,
                    store_id=store_id,
                )
                if anexos is None:
                    anexos = []
                anexos.append(pdf_path)
                print(f"📎 PDF anexado automaticamente: {pdf_path}")
            except Exception as e:
                print(f"⚠️ Falha ao gerar PDF: {e}")

        # 📬 Corpo e assunto profissionais
        subject = subject or f"📋 Relatório de Validades — {loja_nome}"

        body = (
            f"Prezada equipe da {loja_nome},\n\n"
            f"Segue em anexo o relatório atualizado de produtos para acompanhamento de estoque e controle de validade.\n\n"
            f"Atenciosamente,\n"
            f"Sistema Controle LRC"
        )

        # 💄 HTML institucional (sem 'Central do Varejo' e sem 'a vencer em até X dias')
        html_body = f"""
        <html>
        <body style="font-family:Segoe UI,Arial,sans-serif;background-color:#f4f6f9;padding:20px;color:#333;">
            <div style="background:white;border-radius:10px;padding:30px;max-width:650px;margin:auto;box-shadow:0 3px 8px rgba(0,0,0,0.1);">
                <div style="background-color:#003366;padding:15px;border-radius:8px 8px 0 0;">
                    <h2 style="color:white;margin:0;font-size:20px;">📋 Relatório de Validades — {loja_nome}</h2>
                </div>
                <div style="padding:25px 20px;">
                    <p style="font-size:15px;line-height:1.6;">
                        Prezada equipe da <strong>{loja_nome}</strong>,
                    </p>
                    <p style="font-size:15px;line-height:1.6;">
                        Segue em anexo o relatório atualizado de produtos para acompanhamento de estoque e controle de validade.
                    </p>
                    <p style="margin-top:30px;font-size:15px;line-height:1.6;">
                        Atenciosamente,<br>
                        <strong>Sistema Controle LRC</strong>
                    </p>
                </div>
                <hr style="border:none;border-top:1px solid #ddd;margin-top:20px;">
                <p style="font-size:12px;color:#666;text-align:center;">
                    Mensagem automática — não responda a este e-mail.
                </p>
            </div>
        </body>
        </html>
        """

        # ✉️ Montagem do e-mail
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs)
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        # 📎 Anexos
        if anexos:
            for arquivo in anexos:
                if arquivo and os.path.exists(arquivo):
                    with open(arquivo, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(arquivo)}"')
                    msg.attach(part)

        # 🚀 Envio
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            if alert_cfg.get("use_tls", True):
                server.starttls()
            server.login(username, password)
            server.send_message(msg)

        return True, f"E-mail enviado com sucesso para {', '.join(to_addrs)} com anexo PDF."

    except Exception as e:
        return False, f"Erro ao enviar e-mail: {e}"



# ==============================
# Exportação de relatórios
# ==============================
def exportar_relatorios(conn, cfg, store_id=None):
    df = reporting.build_snapshots(conn)
    if store_id is not None and "store_id" in df.columns:
        df = df[df["store_id"] == store_id]

    outdir = Path(cfg["report_dir"])
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    loja_tag = f"_Loja_{store_id}" if store_id is not None else ""
    path_xlsx = outdir / f"relatorio_validade{loja_tag}_{stamp}.xlsx"

    with pd.ExcelWriter(path_xlsx, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="estoque_atual")
        reporting.near_expiry(df, cfg["near_expiry_days"]).to_excel(writer, index=False, sheet_name="a_vencer")
        reporting.expired(df).to_excel(writer, index=False, sheet_name="vencidos")
        reporting.fefo_picklist(df).to_excel(writer, index=False, sheet_name="fefo")

    return path_xlsx, reporting.to_console(
        reporting.near_expiry(df, cfg["near_expiry_days"]),
        f"Itens a vencer em {cfg['near_expiry_days']} dias"
    )
