# src/scheduler_alertas.py
import json
from pathlib import Path
from datetime import datetime
from db_supabase import get_conn  # ✅ usa o Supabase
import expiry_bot as bot
import reporting
from report_pdf import gerar_relatorio_pdf
import psycopg2


# === Carrega config global ===
CFG_PATH = Path(__file__).resolve().parents[1] / "config.json"
cfg = json.loads(Path(CFG_PATH).read_text(encoding="utf-8"))

# A conexão Supabase ignora o argumento (mantém compatibilidade)
conn = get_conn("")

def enviar_alertas_automaticos():
    """Envia e-mails automáticos de alerta 1x/dia para cada loja com produtos próximos da validade."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM stores")
            lojas = cur.fetchall()
    except Exception as e:
        print("⚠️ Banco de dados não inicializado corretamente:", e)
        return

    if not lojas:
        print("ℹ️ Nenhuma loja cadastrada. Nenhum alerta a enviar.")
        return

    hoje = datetime.now().strftime("%Y-%m-%d")

    for loja in lojas:
        loja_id, loja_nome = loja["id"], loja["name"]

        try:
            CFG_STORE_PATH = Path(__file__).resolve().parents[1] / f"config_loja_{loja_id}.json"
            cfg_loja = json.loads(Path(CFG_STORE_PATH).read_text(encoding="utf-8")) if CFG_STORE_PATH.exists() else cfg

            alert_cfg = cfg_loja.get("alert_email", {})
            if not alert_cfg.get("enabled", False):
                print(f"🚫 Loja {loja_nome}: envio de e-mails desativado.")
                continue

            ultimo_envio = cfg_loja.get("last_alert_sent")
            if ultimo_envio == hoje:
                print(f"⏳ {loja_nome}: alerta já enviado hoje, pulando.")
                continue

            # Snapshot do estoque da loja
            df = reporting.build_snapshots(conn)
            if df is None or df.empty or "store_id" not in df.columns:
                continue

            df = df[df["store_id"] == loja_id]
            if df.empty:
                continue

            near = reporting.near_expiry(df, cfg.get("near_expiry_days", 15))
            if near.empty:
                continue

            pdf_path = gerar_relatorio_pdf(
                cfg_loja,
                df=df,
                total_estoque=int(df["qty"].sum()),
                total_a_vencer=int(near["qty"].sum()),
                total_vencido=int(reporting.expired(df)["qty"].sum()),
                total_vendido=0,
                store_id=loja_id,
            )

            subject = f"⚠️ {loja_nome}: Relatório de produtos próximos da validade"
            body = f"Segue em anexo o relatório de validade da loja {loja_nome} ({hoje})."

            ok, info = bot.enviar_email_alerta(cfg_loja, subject, body, anexos=[pdf_path])
            if ok:
                cfg_loja["last_alert_sent"] = hoje
                CFG_STORE_PATH.write_text(json.dumps(cfg_loja, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"[{datetime.now():%H:%M}] ✅ E-mail enviado para {loja_nome}")
            else:
                print(f"[{datetime.now():%H:%M}] ❌ Erro ao enviar e-mail: {info}")

        except Exception as e:
            print(f"❌ Erro ao processar {loja_nome}: {e}")

if __name__ == "__main__":
    enviar_alertas_automaticos()
