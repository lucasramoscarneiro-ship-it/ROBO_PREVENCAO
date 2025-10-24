import streamlit as st
from pathlib import Path
import json
import bcrypt
import reporting
from datetime import datetime
import traceback
import expiry_bot as bot
from report_pdf import gerar_relatorio_pdf
import pandas as pd
from db import (
    get_conn, init_db, list_users, create_user,
    update_user_role, update_user_status, update_user_password
)
from auth import login_box
import painel_expiry_bot as painel

st.set_page_config(
    page_title="Controle LRC - Painel Web da Loja",
    layout="wide",
    initial_sidebar_state="collapsed"
)
st.markdown("""
<style>
/* 🌐 Torna layout adaptável */
@media (max-width: 768px) {
    /* Remove margens grandes em telas pequenas */
    .block-container {
        padding-left: 0.8rem;
        padding-right: 0.8rem;
        padding-top: 0.5rem;
        padding-bottom: 1rem;
    }

    /* Ajusta tamanho de fontes */
    h1, h2, h3, h4, h5, h6 {
        font-size: 1rem !important;
    }

    /* Mantém botões grandes e clicáveis */
    button, .stButton > button {
        width: 100% !important;
        font-size: 0.9rem !important;
        padding: 0.6rem !important;
    }

    /* Tabelas mais scrolláveis */
    [data-testid="stDataFrame"] {
        overflow-x: auto !important;
    }

    /* Campos de formulário adaptáveis */
    input, textarea, select {
        width: 100% !important;
    }

    /* Tabs horizontais transformadas em vertical stack */
    [role="tablist"] {
        display: flex;
        flex-direction: column;
    }
}

/* Esconde a marca d'água do Streamlit em produção */
#MainMenu, footer, header {visibility: hidden;}
</style>
""", unsafe_allow_html=True)
# ===============================
# CONFIGURAÇÃO INICIAL
# ===============================
CFG_PATH = Path(__file__).resolve().parents[1] / "config.json"
if not CFG_PATH.exists():
    st.error(f"❌ Arquivo de configuração não encontrado: {CFG_PATH}")
    st.stop()

cfg = json.loads(CFG_PATH.read_text(encoding="utf-8"))

db_path = Path(cfg["database_path"])
db_path.parent.mkdir(parents=True, exist_ok=True)

if not db_path.exists():
    st.warning(f"⚠️ Banco de dados não encontrado em {db_path}. Será criado automaticamente.")

conn = get_conn(cfg["database_path"])
init_db(conn)

def enviar_alertas_automaticos():
    """
    Envia automaticamente e-mails de alerta para todas as lojas
    quando houver produtos com vencimento em 30, 15 ou 7 dias.
    Cada loja só recebe 1 e-mail por dia.
    """
    try:
        lojas = conn.execute("SELECT id, name FROM stores").fetchall()
        if not lojas:
            return

        hoje = datetime.now().strftime("%Y-%m-%d")

        for loja_id, loja_nome in lojas:
            CFG_STORE_PATH = Path(__file__).resolve().parents[1] / f"config_loja_{loja_id}.json"

            # Carrega configuração da loja (ou usa cfg global)
            if CFG_STORE_PATH.exists():
                cfg_loja = json.loads(CFG_STORE_PATH.read_text(encoding="utf-8"))
            else:
                cfg_loja = cfg.copy()
                CFG_STORE_PATH.write_text(json.dumps(cfg_loja, indent=2, ensure_ascii=False), encoding="utf-8")

            alert_cfg = cfg_loja.get("alert_email", {})
            if not alert_cfg.get("enabled", False):
                continue  # pula lojas com envio desativado

            # Checa se já enviou hoje
            ultimo_envio = cfg_loja.get("last_alert_sent")
            if ultimo_envio == hoje:
                print(f"⏳ Alerta já enviado hoje para {loja_nome}, pulando...")
                continue

            # Cria snapshot do estoque
            df_loja = reporting.build_snapshots(conn)
            if df_loja is None or df_loja.empty or "store_id" not in df_loja.columns:
                continue

            df_loja = df_loja[df_loja["store_id"] == loja_id]
            if df_loja.empty:
                continue

            houve_envio = False

            # Combina todos os prazos de validade em um único dataframe
            near_30 = reporting.near_expiry(df_loja, 30)
            near_15 = reporting.near_expiry(df_loja, 15)
            near_7 = reporting.near_expiry(df_loja, 7)

            # Junta todos os itens únicos
            near_total = pd.concat([near_30, near_15, near_7]).drop_duplicates(subset=["ean", "lot"], keep="first")

            if near_total is not None and not near_total.empty:
                try:
                    # 1️⃣ Gera PDF consolidado
                    pdf_path = gerar_relatorio_pdf(
                        cfg_loja,
                        df=df_loja,
                        total_estoque=int(df_loja["qty"].sum()),
                        total_a_vencer=int(near_total["qty"].sum()),
                        total_vencido=int(reporting.expired(df_loja)["qty"].sum()),
                        total_vendido=0,
                        store_id=loja_id,
                    )

                    # 2️⃣ Monta mensagem única
                    resumo = []
                    if not near_7.empty:
                        resumo.append("⚠️ Produtos com menos de 7 dias de validade.")
                    if not near_15.empty:
                        resumo.append("🟠 Produtos com validade entre 8 e 15 dias.")
                    if not near_30.empty:
                        resumo.append("🟡 Produtos com validade entre 16 e 30 dias.")
                    resumo_txt = "\n".join(resumo)

                    subject = f"⚠️ {loja_nome}: Relatório de produtos próximos da validade"
                    body = (
                        f"Prezada equipe da {loja_nome},\n\n"
                        f"Foram encontrados produtos próximos da validade:\n\n"
                        f"{resumo_txt}\n\n"
                        f"Segue em anexo o relatório completo em PDF.\n\n"
                        f"Atenciosamente,\nSistema Controle LRC"
                    )

                    # 3️⃣ Envia e-mail consolidado com o anexo
                    ok, info = bot.enviar_email_alerta(cfg_loja, subject, body, anexos=[pdf_path])

                    if ok:
                        houve_envio = True
                        cfg_loja["last_alert_sent"] = hoje
                        CFG_STORE_PATH.write_text(json.dumps(cfg_loja, indent=2, ensure_ascii=False), encoding="utf-8")
                        print(f"[{datetime.now():%Y-%m-%d %H:%M}] ✅ E-mail consolidado enviado para {loja_nome}")
                    else:
                        print(f"[{datetime.now():%Y-%m-%d %H:%M}] ❌ Falha ao enviar consolidado para {loja_nome}: {info}")

                except Exception as e:
                    print(f"Erro ao gerar ou enviar PDF consolidado para {loja_nome}: {e}")

    except Exception as e:
        print("Erro no envio automático de alertas:")
        traceback.print_exc()


st.set_page_config(page_title="Controle LRC - Acesso", layout="wide")

# ===============================
# BOOTSTRAP DO ADMIN (primeiro acesso)
# ===============================
users_df = list_users(conn)
try:
    users_df = list_users(conn)
except Exception as e:
    st.error(f"Erro ao carregar usuários: {e}")
    st.stop()

if users_df is None:
    st.error("❌ Falha ao carregar tabela de usuários. O banco pode estar corrompido.")
    st.stop()

if users_df.empty:
    st.warning("Nenhum usuário cadastrado. Crie o primeiro ADMIN.")
    with st.form("bootstrap_admin"):
        name = st.text_input("Nome completo")
        email = st.text_input("E-mail")
        username = st.text_input("Usuário (login)", value="admin")
        pwd = st.text_input("Senha", type="password")
        pwd2 = st.text_input("Confirmar senha", type="password")
        ok = st.form_submit_button("Criar ADMIN")

    if ok:
        if not (name and email and username and pwd and pwd2):
            st.error("Preencha todos os campos.")
        elif pwd != pwd2:
            st.error("As senhas não conferem.")
        else:
            try:
                pwd_hash = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                create_user(
                    conn,
                    username=username,
                    name=name,
                    email=email,
                    pwd_hash=pwd_hash,
                    role="admin",
                    is_active=1,
                )
                st.success("✅ ADMIN criado! Recarregue a página para fazer login.")
            except Exception as e:
                st.error(f"Erro ao criar ADMIN: {e}")
    st.stop()

# ===============================
# SESSÃO DE LOGIN
# ===============================
if "user" not in st.session_state:
    user = login_box(conn)
    if not user:
        st.stop()
    st.session_state["user"] = user
    st.rerun()

user = st.session_state["user"]

# ===============================
# CABEÇALHO SUPERIOR
# ===============================
col1, col2 = st.columns([4, 1])
with col1:
    store = conn.execute("SELECT name FROM stores WHERE id=?", (user.get("store_id"),)).fetchone()
    store_name = store[0] if store else "Sem loja vinculada"
    st.success(f"Bem-vindo, {user['name']} ({user['role']}) — 🏬 {store_name}")
with col2:
    if st.button("🚪 Sair"):
        del st.session_state["user"]
        st.rerun()

# 🔔 ALERTA IMEDIATO APÓS LOGIN: itens a vencer na loja do usuário
try:
    df_login = reporting.build_snapshots(conn)

    if df_login is not None and not df_login.empty:
        if "store_id" in df_login.columns:
            df_login["store_id"] = df_login["store_id"].astype(str)
            df_login = df_login[df_login["store_id"] == str(user.get("store_id"))]

        near_login = reporting.near_expiry(df_login, cfg.get("near_expiry_days", 15))

        # Evita repetir o alerta enquanto o usuário navega
        alert_key = f"near_alert_shown_{user.get('store_id')}"
        if not near_login.empty and not st.session_state.get(alert_key, False):
            total = int(near_login["qty"].sum()) if "qty" in near_login.columns else len(near_login)

            # monta lista de até 3 produtos para exibir no alerta
            produtos_preview = (
                near_login["product_name"].dropna().unique().tolist()[:3]
                if "product_name" in near_login.columns else []
            )
            resumo = ", ".join(produtos_preview) + ("..." if len(produtos_preview) >= 3 else "")

            st.toast(
                f"⚠️ {total} item(ns) com validade a vencer em até {cfg.get('near_expiry_days', 15)} dia(s). "
                f"Ex: {resumo or 'ver detalhes em 📋 Controle Operacional → A Vencer.'}",
                icon="⚠️"
            )
            st.session_state[alert_key] = True
    else:
        st.sidebar.info("📦 Nenhum item de estoque encontrado ainda.")

except Exception as e:
    # Não quebra a página se algo falhar no alerta
    st.sidebar.warning(f"Alerta de validade indisponível: {e}")

# ===============================
# ABAS PRINCIPAIS
# ===============================
abas = st.tabs([
    "📊 Painel Principal",
    "📤 Envio Manual de Alertas (ADMIN)",
    "👥 Gestão de Usuários (ADMIN)"
])

# ===============================
# PAINEL PRINCIPAL
# ===============================
with abas[0]:
    painel.main(conn, cfg, user)

# ===============================
# ENVIO MANUAL DE ALERTAS (ADMIN)
# ===============================
# ===============================
# GESTÃO DE USUÁRIOS (ADMIN)
# ===============================
with abas[1]:
    if user.get("role", "").lower() != "admin":
        st.warning("🔒 Apenas administradores podem enviar alertas manualmente.")
    else:
        st.subheader("📤 Envio Manual de Alertas (ADMIN)")

        lojas = conn.execute("SELECT id, name FROM stores").fetchall()
        loja_opcoes = {f"{s[1]} (ID {s[0]})": s[0] for s in lojas}

        loja_sel = st.selectbox(
            "Selecione a loja para enviar o alerta",
            options=list(loja_opcoes.keys()),
            key="alerta_loja_select"
        )

        if st.button("📤 Enviar alerta agora", key="btn_enviar_alerta_manual"):
            store_id_alerta = loja_opcoes[loja_sel]
            df_alerta = reporting.build_snapshots(conn)
            df_alerta = df_alerta[df_alerta["store_id"] == store_id_alerta]
            near_alerta = reporting.near_expiry(df_alerta, cfg["near_expiry_days"])

            if near_alerta.empty:
                st.info(f"Nenhum produto próximo da validade para a loja {loja_sel}.")
            else:
                near_body = reporting.to_console(
                    near_alerta, f"Itens a vencer em {cfg['near_expiry_days']} dias"
                )
                ok, info = bot.enviar_email_alerta(
                    cfg, f"⚠️ Alerta: produtos a vencer — {loja_sel}", near_body
                )
                if ok:
                    st.success(info)
                else:
                    st.error(f"❌ {info}")

    st.subheader("👥 Lista de Usuários")
    try:
        df = list_users(conn)
        if df is None or df.empty:
            st.warning("Nenhum usuário cadastrado ainda.")
        else:
            stores_df = conn.execute("SELECT id, name FROM stores").fetchall()
            store_map = {s[0]: s[1] for s in stores_df}
            if "store_id" in df.columns:
                df["Loja"] = df["store_id"].map(store_map).fillna("Sem loja vinculada")
            else:
                df["Loja"] = "Não vinculada"

            st.dataframe(
                df[["id", "username", "name", "email", "role", "is_active", "Loja", "created_at"]],
                width="stretch"
            )
    except Exception as e:
        st.error(f"Erro ao carregar usuários: {e}")


# ===============================
# GESTÃO DE USUÁRIOS
# ===============================
# ===============================
# GESTÃO DE USUÁRIOS (ADMIN)
# ===============================
with abas[2]:
    st.write("🧩 Debug: Entrou na aba Gestão de Usuários", user)
    try:
        df = list_users(conn)
        st.write("✅ DEBUG: usuários carregados:", len(df))
    except Exception as e:
        st.error(f"💥 Erro no list_users: {e}")
        st.exception(e)
        st.stop()

    role = str(user.get("role", "")).strip().lower()
    if role != "admin":
        st.warning("🔒 Acesso restrito ao administrador.")
    else:
        try:
            st.subheader("👥 Lista de Usuários")

            df = list_users(conn)
            if df is None or df.empty:
                st.warning("Nenhum usuário cadastrado ainda.")
            else:
                stores_df = conn.execute("SELECT id, name FROM stores").fetchall()
                store_map = {s[0]: s[1] for s in stores_df}

                if "store_id" in df.columns:

                    df["Loja"] = df["store_id"].map(store_map).fillna("Sem loja vinculada")
                else:
                    df["Loja"] = "Não vinculada"

                st.dataframe(
                    df[["id", "username", "name", "email", "role", "is_active", "Loja", "created_at"]],
                    width="stretch"
                )

            st.divider()
            st.subheader("➕ Criar novo usuário")
            with st.form("form_new_user"):
                name = st.text_input("Nome completo")
                email = st.text_input("E-mail")
                username = st.text_input("Usuário (login)")
                role = st.selectbox("Perfil", ["operador", "admin"])

                stores = conn.execute("SELECT id, name FROM stores").fetchall()
                store_names = [s[1] for s in stores]

                col1, col2 = st.columns(2)
                with col1:
                    opcoes_lojas = store_names + ["➕ Criar nova loja"] if store_names else ["➕ Criar nova loja"]
                    store_option = st.selectbox("Loja", opcoes_lojas, key="select_loja")

                    if store_option == "➕ Criar nova loja":
                        nova_loja = st.text_input("Digite o nome da nova loja:", key="nova_loja_input")
                        store_sel = nova_loja.strip() if nova_loja.strip() else None
                    else:
                        store_sel = store_option if store_option else None

                pwd = st.text_input("Senha", type="password")
                pwd2 = st.text_input("Confirmar senha", type="password")
                ok_new = st.form_submit_button("Criar usuário")

            if ok_new:
                if not (name and email and username and pwd and pwd2 and store_sel):
                    st.error("Preencha todos os campos obrigatórios, incluindo a loja.")
                elif pwd != pwd2:
                    st.error("As senhas não conferem.")
                else:
                    try:
                        pwd_hash = bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                        loja_existente = conn.execute("SELECT id FROM stores WHERE name=?", (store_sel,)).fetchone()
                        if not loja_existente:
                            conn.execute("INSERT INTO stores (name) VALUES (?)", (store_sel,))
                            conn.commit()

                        store_id = conn.execute("SELECT id FROM stores WHERE name=?", (store_sel,)).fetchone()[0]
                        create_user(
                            conn,
                            username=username,
                            name=name,
                            email=email,
                            pwd_hash=pwd_hash,
                            role=role,
                            is_active=1,
                            store_id=store_id
                        )
                        st.success(f"Usuário '{username}' criado e vinculado à loja '{store_sel}'.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao criar usuário: {e}")

            st.divider()
            st.subheader("✏️ Alterar perfil / Ativar ou Desativar usuário")
            col1, col2, col3 = st.columns(3)
            with col1:
                u_sel = st.text_input("Usuário (login) para alterar")
            with col2:
                novo_role = st.selectbox("Novo perfil", ["operador", "admin"])
            with col3:
                ativo = st.checkbox("Ativo", value=True)

            colA, colB = st.columns(2)
            if colA.button("Salvar alterações"):
                try:
                    update_user_role(conn, u_sel, novo_role)
                    update_user_status(conn, u_sel, 1 if ativo else 0)
                    st.success("Alterações salvas com sucesso.")
                except Exception as e:
                    st.error(f"Erro: {e}")

            st.divider()
            st.subheader("🔒 Redefinir senha de usuário")
            with st.form("form_reset_pwd"):
                u_pwd = st.text_input("Usuário (login)")
                npwd = st.text_input("Nova senha", type="password")
                npwd2 = st.text_input("Confirmar nova senha", type="password")
                ok_pwd = st.form_submit_button("Atualizar senha")

            if ok_pwd:
                if not (u_pwd and npwd and npwd2):
                    st.error("Preencha todos os campos.")
                elif npwd != npwd2:
                    st.error("As senhas não conferem.")
                else:
                    try:
                        h = bcrypt.hashpw(npwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                        update_user_password(conn, u_pwd, h)
                        st.success("Senha atualizada com sucesso.")
                    except Exception as e:
                        st.error(f"Erro: {e}")
        except Exception as e:
            st.error(f"Erro ao renderizar Gestão de Usuários: {e}")
            st.exception(e)
