# src/painel_expiry_bot.py
import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path
import json
from datetime import datetime, timedelta
import hashlib
from db_supabase import get_conn, init_db
import reporting
import expiry_bot as bot
from report_pdf import gerar_relatorio_pdf
from nfe_import import parse_nfe_xml
import streamlit.components.v1 as components
import psycopg2.extras

if "forcar_reload_vendas" not in st.session_state:
    st.session_state["forcar_reload_vendas"] = False
    
@st.cache_data(ttl=5, show_spinner=False)
def carregar_vendas(_conn, store_id, _salt=None):
    """Carrega vendas do banco com cache controlado por salt."""
    query = """
        SELECT 
            m.ts AS data_venda,
            m.ean,
            COALESCE(p.product_name, 'Produto não identificado') AS product_name,
            m.lot,
            COALESCE(l.expiry_date, CURRENT_DATE + INTERVAL '180 days') AS expiry_date,
            m.qty AS qtd_vendida,
            COALESCE(m.note, '') AS observacao,
            m.origin_key,
            ('Loja ' || m.store_id::text) AS location,
            m.store_id
        FROM public.movements m
        LEFT JOIN public.products p ON p.ean = m.ean
        LEFT JOIN (
            SELECT DISTINCT ean, lot, expiry_date FROM public.lots
        ) l ON l.ean = m.ean AND l.lot = m.lot
        WHERE m.type = 'sale'
        AND m.store_id = %s
        ORDER BY m.ts DESC;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, (store_id,))
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def main(conn, cfg, user):
    try:
        st.cache_data.clear()
        st.cache_resource.clear()
    except Exception:
        pass

    try:
        store_id = int(user.get("store_id")) if user.get("store_id") is not None else None
    except Exception:
        store_id = None
    # Caminho do config (usado ao salvar alterações na aba de Configurações)
    # --- Carregar configuração específica da loja ---
    # --- Carregar configuração específica da loja (SEM cache global) ---
    CFG_STORE_PATH = Path(__file__).resolve().parents[1] / f"config_loja_{store_id}.json"

    # Se não existir o arquivo da loja, cria com base na config global
    if not CFG_STORE_PATH.exists():
        Path(CFG_STORE_PATH).write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    # Carrega a configuração da loja diretamente (sem cache compartilhado)
    with open(CFG_STORE_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<


    # ADIÇÃO: indicador de loja atual na barra lateral (informativo)
    st.sidebar.info(f"🧭 Loja atual: {store_id}")

    st.markdown("""
    <style>
    /* === Uploader totalmente personalizado === */

    /* Oculta o botão e o texto "Browse files" */
    div[data-testid="stFileUploader"] button {
    display: none !important;
    }

    /* Oculta o texto interno "Drag and drop file here" que o Streamlit adiciona */
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"] div:first-child {
    display: none !important;
    }

    /* Oculta qualquer span residual que contenha "Browse files" */
    div[data-testid="stFileUploader"] span {
    display: none !important;
    }

    /* Personaliza a área de upload */
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"] {
    border: 2px dashed #b0b0b0 !important;
    border-radius: 12px !important;
    background: #f9f9f9 !important;
    position: relative;
    padding: 16px !important;
    }

    /* Texto central em português */
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]::before {
    content: "📁 Arraste e solte o arquivo aqui\\Aou clique para selecionar";
    white-space: pre-wrap;
    display: block;
    text-align: center;
    font-weight: 600;
    color: #222;
    padding: 6px 0;
    }

    /* Rodapé informativo */
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]::after {
    content: "Limite: 200 MB • Formatos aceitos: XLSX, CSV";
    display: block;
    text-align: center;
    font-size: 12px;
    color: #666;
    margin-top: 6px;
    }

    /* Efeito hover */
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]:hover {
    background: #eef6ff !important;
    border-color: #007bff !important;
    }
    </style>
    """, unsafe_allow_html=True)


    st.title("Controle LRC — Sistema de Controle de Validades")
    
    try:
        hoje = datetime.now().date()

        cur = conn.cursor()
        
        # ✅ Apenas classifica, não altera o banco
        cur = conn.cursor()
        cur.execute("""
            SELECT s.ean, s.lot, s.qty, s.location, s.store_id, p.product_name, l.expiry_date
            FROM stock s
            LEFT JOIN lots l ON l.ean = s.ean AND l.lot = s.lot
            LEFT JOIN products p ON p.ean = s.ean
            WHERE s.store_id = %s;
        """, (store_id,))
        itens = cur.fetchall()

        # Transforma em DataFrame para facilitar o tratamento
        df_all = pd.DataFrame(itens, columns=["ean", "lot", "qty", "location", "store_id", "product_name", "expiry_date"])
        df_all["expiry_date"] = pd.to_datetime(df_all["expiry_date"], errors="coerce")
        df_all["status"] = df_all["expiry_date"].apply(
            lambda d: "vencido" if pd.notna(d) and d.date() < datetime.now().date() else "ativo"
        )
        df_all = df_all[df_all["qty"].fillna(0) > 0].copy()
        # Substitui o dataframe principal
        df = df_all.copy()
        print(f"📦 Estoque carregado ({len(df)} registros) — {len(df[df['status']=='vencido'])} vencido(s).")


    except Exception as e:
        print(f"[auto_expiry_check] Erro ao processar vencidos: {e}")

    # --- Exibe todos os registros, incluindo vencidos (mesmo com qty=0) ---
    df = df.copy()
    df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    df["expiry_date"] = df["expiry_date"].dt.normalize()

    # 🔧 Mantém todos os itens, mas marca os vencidos
    df["status"] = df["expiry_date"].apply(
        lambda d: "vencido" if pd.notna(d) and d.date() <= datetime.now().date() else "ativo"
    )



    # --- Continua o fluxo normal ---
    hoje = datetime.now().date()
    near = df[(df["expiry_date"].dt.date > hoje) & (df["expiry_date"].dt.date <= hoje + timedelta(days=cfg["near_expiry_days"]))].copy()
    exp = df[df["expiry_date"].dt.date < hoje].copy()

    # 🔔 Banner de alerta dentro do painel principal (refinado)
    if near is not None and not near.empty:
        total = int(near["qty"].sum()) if "qty" in near.columns else len(near)

        # Calcula faixas de vencimento
        hoje = datetime.now().date()
        near["dias_restantes"] = pd.to_datetime(near["expiry_date"]).dt.date - hoje
        near["dias_restantes"] = near["dias_restantes"].apply(lambda x: x.days if pd.notna(x) else None)

        ate7 = near[near["dias_restantes"] <= 7]
        ate15 = near[(near["dias_restantes"] > 7) & (near["dias_restantes"] <= 15)]
        vencendo_hoje = near[near["dias_restantes"] == 0]

        resumo = []
        if not vencendo_hoje.empty:
            resumo.append(f"🟥 {len(vencendo_hoje)} vencendo **HOJE**")
        if not ate7.empty:
            resumo.append(f"🟧 {len(ate7)} vencendo em até **7 dias**")
        if not ate15.empty:
            resumo.append(f"🟨 {len(ate15)} vencendo em até **15 dias**")

        resumo_str = " | ".join(resumo) if resumo else f"⚠️ {total} item(ns) próximos da validade"

        st.warning(
            f"{resumo_str}\n\nConfira abaixo os detalhes ou acesse a aba 📋 **Controle Operacional → A Vencer**.",
            icon="⚠️"
        )

        with st.expander("🔎 Ver lista de itens próximos do vencimento"):
            st.dataframe(
                near.rename(columns={
                    "product_name": "Produto",
                    "lot": "Lote",
                    "expiry_date": "Validade",
                    "qty": "Qtde",
                    "location": "Local"
                }),
                use_container_width=True
            )
    else:
        st.info("✅ Nenhum item com validade próxima detectado nesta loja.")



    abas = st.tabs([
        "📥 Importação e Atualização de Estoque",
        "📋 Controle Operacional",
        "📈 Relatórios e Indicadores",
        "📤 Alertas e Comunicação",
        "⚙️ Configurações do Sistema",
    ])

    # ------------------ ABA 0: Importação ------------------
    with abas[0]:
        st.subheader("📥 Importar Planilha de Estoque (Excel/CSV)")
        file = st.file_uploader("Selecione o arquivo de estoque", type=["xlsx", "csv"], key="upload_estoque")
        if file:
            tmp = Path("data") / f"upload_{datetime.now().strftime('%H%M%S')}"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(file.read())
            if st.button("Importar Planilha", type="primary"):
                try:
                    bot.importar_planilha(conn, str(tmp), store_id=store_id)
                    st.success("Importação concluída com sucesso!")
                except Exception as e:
                    st.error(str(e))

        st.divider()
        st.subheader("📄 Importar Nota Fiscal Eletrônica (XML) — automático para perecíveis")
        xml_file = st.file_uploader("Selecione o arquivo .xml da NF-e", type=["xml"], key="upload_xml")
        if xml_file:
            tmp_xml = Path("data") / f"nfe_{datetime.now().strftime('%H%M%S')}.xml"
            tmp_xml.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_xml, "wb") as f:
                f.write(xml_file.read())
            df_nfe = parse_nfe_xml(str(tmp_xml))
            if df_nfe.empty:
                st.warning("Nenhum produto perecível encontrado na nota fiscal.")
            else:
                st.success(f"{len(df_nfe)} produto(s) perecível(is) encontrado(s). Itens serão registrados automaticamente.")
                st.dataframe(df_nfe, use_container_width=True)
                try:
                    # 1️⃣ Primeiro, insere todos os produtos e lotes
                    for _, row in df_nfe.iterrows():
                        ean = str(row["ean"])
                        pname = str(row["product_name"])
                        lot = str(row["lot"])
                        expiry = row["expiry_date"]
                        # Garante que expiry seja string no formato YYYY-MM-DD
                        if pd.notna(expiry):
                            if isinstance(expiry, pd.Timestamp):
                                expiry = expiry.date().isoformat()
                            elif isinstance(expiry, str):
                                expiry = expiry.strip()
                            else:
                                expiry = str(expiry)
                        else:
                            expiry = None

                        cur = conn.cursor()
                        cur.execute(
                            "INSERT INTO products(ean, product_name) VALUES(%s,%s) ON CONFLICT (ean) DO NOTHING",
                            (ean, pname)
                        )
                        cur.execute("UPDATE products SET product_name=COALESCE(NULLIF(%s, ''), product_name) WHERE ean=%s", (pname, ean))
                        if expiry:
                            cur.execute(
                                "INSERT INTO lots(ean, lot, expiry_date) VALUES(%s,%s,%s) ON CONFLICT (ean, lot) DO NOTHING",
                                (ean, lot, expiry)
                            )
                        else:
                            cur.execute(
                                "INSERT INTO lots(ean, lot, expiry_date) VALUES(%s,%s,CURRENT_DATE + INTERVAL '180 days') ON CONFLICT (ean, lot) DO NOTHING",
                                (ean, lot)
                            )



                    # 2️⃣ Faz um único commit no final da importação
                    conn.commit()

                    # 3️⃣ Agora registra os movimentos
                    for _, row in df_nfe.iterrows():
                        ean = str(row["ean"])
                        lot = str(row["lot"])
                        qty = int(row["qty"]) if not pd.isna(row["qty"]) else 0
                        bot.movimentar(
                            conn,
                            "receipt",
                            ean,
                            lot,
                            qty,
                            observacao="Importado via NF-e",
                            local=f"Loja {store_id}",
                            store_id=store_id,
                        )

                    st.success("NF-e processada e estoque atualizado com sucesso!")
                except Exception as e:
                    st.error(f"Erro ao registrar itens da NF-e: {e}")

        # 🔄 Atualiza DataFrame principal para refletir novas quantidades
        cur = conn.cursor()
        cur.execute("""
            SELECT s.ean, s.lot, s.qty, s.location, s.store_id, p.product_name, l.expiry_date
            FROM stock s
            LEFT JOIN lots l ON l.ean = s.ean AND l.lot = s.lot
            LEFT JOIN products p ON p.ean = s.ean
            WHERE s.store_id = %s;
        """, (store_id,))
        itens = cur.fetchall()

        df = pd.DataFrame(itens, columns=["ean", "lot", "qty", "location", "store_id", "product_name", "expiry_date"])
        df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")

        st.divider()
        st.subheader("➕ Registrar Entrada (cria lote automaticamente)")
        with st.form("form_entrada"):
            ean_r = st.text_input("EAN")
            pname_r = st.text_input("Nome do Produto")
            lot_r = st.text_input("Lote")
            expiry_r = st.date_input("Data de Validade")
            qty_r = st.number_input("Quantidade", min_value=1, step=1)
            location_r = st.text_input("Local", value=f"Loja {store_id}")
            submitted_r = st.form_submit_button("Registrar Entrada")

            if submitted_r:
                try:
                    if not ean_r.strip() or not lot_r.strip():
                        st.error("Preencha o EAN e o lote corretamente antes de registrar.")
                        st.stop()

                    cur = conn.cursor()

                    # 1️⃣ Garante que o produto exista
                    cur.execute("""
                        INSERT INTO products(ean, product_name)
                        VALUES (%s, %s)
                        ON CONFLICT (ean) DO NOTHING
                    """, (ean_r, pname_r))
                    cur.execute("""
                        UPDATE products
                        SET product_name = COALESCE(NULLIF(%s, ''), product_name)
                        WHERE ean = %s
                    """, (pname_r, ean_r))

                    # 2️⃣ Garante que o lote exista
                    cur.execute("""
                        INSERT INTO lots(ean, lot, expiry_date)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (ean, lot) DO NOTHING
                    """, (ean_r, lot_r, expiry_r.isoformat()))

                    # 3️⃣ Registra o movimento (sem alterar estoque diretamente)
                    entry_key = f"EntradaWeb:{ean_r}:{lot_r}:{int(qty_r)}:{store_id}:{datetime.now().strftime('%Y%m%d%H%M%S')}"
                    bot.movimentar(
                        conn,
                        "receipt",
                        ean_r,
                        lot_r,
                        int(qty_r),
                        observacao=entry_key,
                        local=location_r,
                        store_id=store_id,
                    )

                    st.success("Entrada registrada com sucesso!")
                    st.rerun()


                except Exception as e:
                    st.error(f"Erro ao registrar: {e}")



        st.divider()
        st.subheader("➖ Registrar Saída (Venda)")

        # 🔄 Carrega snapshot atualizado direto do banco
        cur = conn.cursor()
        cur.execute("""
            SELECT s.ean, s.lot, s.qty, s.location, s.store_id, p.product_name, l.expiry_date
            FROM stock s
            LEFT JOIN lots l ON l.ean = s.ean AND l.lot = s.lot
            LEFT JOIN products p ON p.ean = s.ean
            WHERE s.store_id = %s;
        """, (store_id,))
        itens = cur.fetchall()

        df_disponivel = pd.DataFrame(
            itens,
            columns=["ean", "lot", "qty", "location", "store_id", "product_name", "expiry_date"]
        )
        df_disponivel["expiry_date"] = pd.to_datetime(df_disponivel["expiry_date"], errors="coerce")

        # Filtra somente produtos com saldo positivo
        df_disponivel = df_disponivel[
            (df_disponivel["qty"].fillna(0) > 0)
            & (df_disponivel["product_name"].notna())
            & (df_disponivel["ean"].notna())
        ].copy()

        if df_disponivel.empty:
            st.info("Não há itens com saldo disponível para venda nesta loja.")
        else:
            # ===============================
            # 🔹 FORMULÁRIO DE VENDA
            # ===============================
            with st.form("form_saida_venda"):
                prods = (
                    df_disponivel[["ean", "product_name"]]
                    .drop_duplicates()
                    .sort_values(["product_name", "ean"])
                )
                prod_options = prods.to_dict("records")
                prod_sel = st.selectbox(
                    "Produto",
                    options=prod_options,
                    format_func=lambda r: f"{r['product_name']} — {r['ean']}",
                    key="saida_produto",
                )

                df_lotes = (
                    df_disponivel[df_disponivel["ean"] == prod_sel["ean"]]
                    .sort_values(["expiry_date", "lot"])
                    .copy()
                )
                lote_options = df_lotes.to_dict("records")

                lote_sel = st.selectbox(
                    "Lote / Validade / Saldo / Local",
                    options=lote_options,
                    format_func=lambda r: (
                        f"Lote {r['lot']} • Val {pd.to_datetime(r['expiry_date']).date():%d/%m/%Y} "
                        f"• Qtde {int(r['qty'])} • {r.get('location','') or ''}"
                    ),
                    key="saida_lote",
                )

                saldo_lote = int(lote_sel["qty"])
                qty_v = st.number_input(
                    "Quantidade a vender",
                    min_value=1,
                    max_value=max(1, saldo_lote),
                    value=1,
                    step=1,
                    help=f"Saldo disponível neste lote: {saldo_lote}",
                )

                location_v = st.text_input("Local", value=f"Loja {store_id}", key="saida_local")

                submitted_v = st.form_submit_button("Registrar Saída")

            # ===============================
            # 🔹 PROCESSAMENTO ÚNICO DA VENDA
            # ===============================
            if submitted_v:
                # Protege contra duplo clique
                if st.session_state.get("venda_em_progresso", False):
                    st.warning("⏳ Venda já em processamento, aguarde...")
                    st.stop()

                st.session_state["venda_em_progresso"] = True

                try:
                    if int(qty_v) > int(lote_sel["qty"]):
                        st.error("Quantidade maior que o saldo disponível.")
                        st.session_state["venda_em_progresso"] = False
                        st.stop()

                    bot.movimentar(
                        conn,
                        tipo="sale",
                        ean=lote_sel["ean"],
                        lot=lote_sel["lot"],
                        qty=int(qty_v),
                        observacao=f"Venda manual via painel — {datetime.now():%Y-%m-%d %H:%M}",
                        local=location_v,
                        store_id=store_id,
                    )

                    # Marca que precisa atualizar os dados
                    st.session_state["forcar_reload_vendas"] = True
                    st.session_state["venda_em_progresso"] = False

                    # Mostra mensagem e atualiza sem rerun
                    st.success(f"✅ Venda registrada com sucesso! ({qty_v} unid. removidas do lote {lote_sel['lot']})")

                    # Atualiza a lista de vendas manualmente sem reload global
                    df_vendidos = carregar_vendas(conn, store_id, _salt=datetime.now().timestamp())

                except Exception as e:
                    conn.rollback()
                    st.session_state["venda_em_progresso"] = False
                    st.error(f"❌ Erro ao registrar saída: {e}")





    # ------------------ Cálculos de totais ------------------
    total_estoque = 0
    if df is not None and not df.empty:
        if "qty" in df.columns:
            total_estoque = df["qty"].fillna(0).sum()
        elif "quantidade" in df.columns:
            total_estoque = df["quantidade"].fillna(0).sum()
        else:
            total_estoque = len(df)

    try:
        total_estoque = int(total_estoque)
    except (ValueError, TypeError):
        total_estoque = 0

    # ------------------ Total vencido ------------------
    total_vencido = 0
    if exp is not None and not exp.empty and "qty" in exp.columns:
        total_vencido = exp["qty"].fillna(0).sum()

    try:
        total_vencido = int(total_vencido)
    except (ValueError, TypeError):
        total_vencido = 0

    # ------------------ Total a vencer ------------------
    total_a_vencer = 0
    if near is not None and not near.empty and "qty" in near.columns:
        total_a_vencer = near["qty"].fillna(0).sum()

    try:
        total_a_vencer = int(total_a_vencer)
    except (ValueError, TypeError):
        total_a_vencer = 0

    if isinstance(store_id, str) and store_id.isdigit():
        store_id = int(store_id)


    try:
        if store_id is None:
            mov = pd.read_sql_query(
                "SELECT * FROM movements WHERE store_id IS NULL",
                conn,
                parse_dates=["ts"]
            )
        else:
            mov = pd.read_sql_query(
                "SELECT * FROM movements WHERE store_id = %s",
                conn,
                params=(store_id,),
                parse_dates=["ts"]
            )
    except Exception as e:
        print(f"[movements] Erro ao consultar movimentos: {e}")
        mov = pd.DataFrame(columns=["id", "ts", "type", "ean", "lot", "qty", "note", "store_id"])


    # ------------------ ABA 1: Operacional ------------------
    with abas[1]:
        # 🔄 Separar estoque por status de validade
        df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
        hoje = datetime.now().date()

        df_ativos = df[df["expiry_date"].dt.date >= hoje].copy()
        df_vencidos = df[df["expiry_date"].dt.date < hoje].copy()
        df_vencidos["qty"] = df_vencidos["qty"].fillna(0).astype(int)

        # ==============================
        # 📦 ESTOQUE ATUAL (válidos)
        # ==============================
        st.subheader("📦 Estoque Atual (Produtos válidos)")
        if df_ativos.empty:
            st.info("Nenhum item válido em estoque.")
        else:
            df_ativos["expiry_date_br"] = df_ativos["expiry_date"].dt.strftime("%d/%m/%Y")
            st.dataframe(
                df_ativos.rename(columns={
                    "ean": "EAN",
                    "product_name": "Produto",
                    "lot": "Lote",
                    "expiry_date_br": "Validade",
                    "qty": "Qtde",
                    "location": "Local",
                    "store_id": "Loja"
                })[["EAN", "Produto", "Lote", "Validade", "Qtde", "Local", "Loja"]],
                use_container_width=True
            )

        st.subheader("✏️ Gerenciar Itens do Estoque")

        filtro = st.text_input("Buscar por nome do produto ou EAN:")
        if filtro:
            df_filtrado = df[df["product_name"].str.contains(filtro, case=False, na=False) |
                             df["ean"].str.contains(filtro, case=False, na=False)]
        else:
            df_filtrado = df.copy()

        if df_filtrado.empty:
            st.info("Nenhum item encontrado para o filtro informado.")
        else:
            st.dataframe(
                df_filtrado.rename(columns={
                    "ean": "EAN",
                    "product_name": "Produto",
                    "lot": "Lote",
                    "expiry_date": "Validade",
                    "qty": "Quantidade",
                    "location": "Local",
                    "store_id": "Loja"
                }),
                use_container_width=True
            )
            st.markdown("#### 🔍 Selecione o item para editar ou excluir")
            ean_sel = st.selectbox("Selecione o EAN", options=df_filtrado["ean"].unique())
            lotes = df_filtrado[df_filtrado["ean"] == ean_sel]["lot"].unique()
            lot_sel = st.selectbox("Selecione o Lote", options=lotes)

            item = df_filtrado[(df_filtrado["ean"] == ean_sel) & (df_filtrado["lot"] == lot_sel)].iloc[0]

            col1, col2, col3 = st.columns(3)
            with col1:
                # --- Quantidade ---
                qtd_val = (
                    item.get("qty")
                    or item.get("quantidade")
                    or item.get("Quantidade")
                    or 0
                )
                try:
                    qtd_inicial = int(float(qtd_val)) if pd.notna(qtd_val) else 0
                except (ValueError, TypeError):
                    qtd_inicial = 0
                nova_qtd = st.number_input("Quantidade", min_value=0, value=qtd_inicial)

            with col2:
                # --- Validade ---
                try:
                    if "expiry_date" in item and pd.notna(item["expiry_date"]):
                        data_inicial = pd.to_datetime(item["expiry_date"]).date()
                    else:
                        raise ValueError
                except Exception:
                    # Se der erro ou for vazio, define como hoje + 30 dias
                    data_inicial = datetime.now().date() + timedelta(days=30)
                nova_data = st.date_input("Validade", value=data_inicial)

            with col3:
                # --- Local ---
                local_inicial = (
                    item.get("location")
                    or item.get("local")
                    or f"Loja {store_id or '01'}"
                )
                novo_local = st.text_input("Local", value=local_inicial, key="editar_local")


            colA, colB = st.columns(2)
            if colA.button("💾 Salvar Alterações"):
                try:
                    cur = conn.cursor()
                    cur.execute("UPDATE stock SET qty=%s, location=%s WHERE ean=%s AND lot=%s", (nova_qtd, novo_local, ean_sel, lot_sel))
                    cur.execute("UPDATE lots SET expiry_date=%s WHERE ean=%s AND lot=%s", (nova_data.isoformat(), ean_sel, lot_sel))
                    conn.commit()
                    st.success("Item atualizado com sucesso!")
                except Exception as e:
                    st.error(f"Erro ao atualizar: {e}")

            if colB.button("🗑️ Excluir Item do Estoque"):
                confirm = st.checkbox("Confirmar exclusão permanente do item")
                if confirm:
                    try:
                        cur = conn.cursor()
                        cur.execute("DELETE FROM stock WHERE ean=%s AND lot=%s", (ean_sel, lot_sel))
                        cur.execute("DELETE FROM lots WHERE ean=%s AND lot=%s", (ean_sel, lot_sel))
                        conn.commit()
                        st.warning("Item excluído com sucesso!")
                    except Exception as e:
                        st.error(f"Erro ao excluir: {e}")

        st.subheader(f"⚠️ A Vencer (≤ {cfg['near_expiry_days']} dias)")
        st.dataframe(near.rename(columns={
            "product_name":"Produto", "lot":"Lote", "expiry_date":"Validade", "qty":"Qtde", "location":"Local", "store_id":"Loja"
        }), use_container_width=True)
        

        st.subheader("🏷️ Sugestão FEFO (Primeiro a Vencer, Primeiro a Sair)")

        df_fefo = reporting.fefo_picklist(df).rename(columns={
            "product_name": "Produto",
            "lot": "Lote",
            "expiry_date": "Validade",
            "qty": "Qtde",
            "location": "Local",
        })

        # 🔧 Gera mensagens e tags coloridas conforme dias restantes
        def gerar_tag_e_mensagem(validade_str):
            try:
                validade = pd.to_datetime(validade_str)
                dias = (validade - datetime.now()).days
            except Exception:
                return ("⚪", "❓ Data inválida")

            if dias < 0:
                return ("🔴", "❌ Produto vencido — recolher imediatamente")
            elif dias <= 7:
                return ("🟠", "🔁 Priorizar venda imediata — vence em menos de 7 dias")
            elif dias <= 15:
                return ("🟠", "🧊 Reforçar exposição — produto próximo da validade")
            elif dias <= 30:
                return ("🟡", "📦 Monitorar — planejar reposição e promoções")
            else:
                return ("🟢", "✅ Estoque saudável — dentro do prazo ideal")

        # Aplica função de tag + mensagem somente se houver registros
        if not df_fefo.empty and "Validade" in df_fefo.columns:
            df_fefo[["Tag", "Sugestão"]] = df_fefo["Validade"].apply(
                lambda x: pd.Series(gerar_tag_e_mensagem(x))
            )
        else:
            df_fefo["Tag"] = []
            df_fefo["Sugestão"] = []

        # Exibe com tags coloridas
        st.dataframe(
            df_fefo[["Tag", "Produto", "Lote", "Validade", "Qtde", "Local", "Sugestão"]],
            use_container_width=True,
        )

        st.divider()
        st.subheader("🛒 Itens Vendidos")

        try:
            query_vendidos = """
                SELECT 
                    m.ts AS data_venda,
                    m.ean,
                    COALESCE(p.product_name, 'Produto não identificado') AS product_name,
                    m.lot,
                    COALESCE(l.expiry_date, CURRENT_DATE + INTERVAL '180 days') AS expiry_date,
                    m.qty AS qtd_vendida,
                    COALESCE(m.note, '') AS observacao,
                    m.origin_key,
                    ('Loja ' || m.store_id::text) AS location,
                    m.store_id
                FROM public.movements m
                LEFT JOIN public.products p ON p.ean = m.ean
                LEFT JOIN (
                    SELECT DISTINCT ean, lot, expiry_date
                    FROM public.lots
                ) l ON l.ean = m.ean AND l.lot = m.lot
                WHERE m.type = 'sale'
                AND m.store_id = %s
                ORDER BY m.ts DESC;
            """

            # 🔧 Correto: usar query_vendidos
            if st.session_state.get("forcar_reload_vendas", False):
                st.cache_data.clear()
                st.session_state["forcar_reload_vendas"] = False

            df_vendidos = carregar_vendas(conn, store_id)

            if df_vendidos.empty:
                st.info("Nenhum item vendido registrado ainda.")
            else:
                # Formata datas para exibição
                df_vendidos["expiry_date"] = pd.to_datetime(df_vendidos["expiry_date"], errors="coerce")
                df_vendidos["data_venda"] = pd.to_datetime(df_vendidos["data_venda"], errors="coerce")
                df_vendidos["Validade"] = df_vendidos["expiry_date"].dt.strftime("%d/%m/%Y")
                df_vendidos["Data Venda"] = df_vendidos["data_venda"].dt.strftime("%d/%m/%Y %H:%M")

                # Exibe tabela formatada
                st.dataframe(
                    df_vendidos.rename(columns={
                        "ean": "EAN",
                        "product_name": "Produto",
                        "lot": "Lote",
                        "qtd_vendida": "Qtde Vendida",
                        "location": "Local",
                        "store_id": "Loja",
                        "origin_key": "Chave Origem"
                    })[
                        ["EAN", "Produto", "Lote", "Validade",
                        "Qtde Vendida", "Local", "Data Venda",
                        "Loja", "Chave Origem"]
                    ],
                    width="stretch"
                )

        except Exception as e:
            st.error(f"Erro ao carregar itens vendidos: {e}")




        # ------------------------------------------------------------
        # 🔧 NOVO BLOCO: Mostrar também itens vencidos no estoque (qty = 0)
        # ------------------------------------------------------------
        st.divider()
        st.subheader("❌ Itens Vencidos")

        df_vencidos = df.copy()
        df_vencidos["expiry_date"] = pd.to_datetime(df_vencidos["expiry_date"], errors="coerce")
        df_vencidos = df_vencidos[df_vencidos["expiry_date"].dt.date <= datetime.now().date()]

        if df_vencidos.empty:
            st.info("Nenhum item vencido encontrado no estoque.")
        else:
            # Formata data no padrão brasileiro
            df_vencidos["expiry_date_br"] = df_vencidos["expiry_date"].dt.strftime("%d/%m/%Y")

            # Exibe a tabela
            st.dataframe(
                df_vencidos.rename(columns={
                    "ean": "EAN",
                    "product_name": "Produto",
                    "lot": "Lote",
                    "expiry_date_br": "Validade",
                    "qty": "Qtde",
                    "location": "Local",
                    "store_id": "Loja"
                })[["EAN", "Produto", "Lote", "Validade", "Qtde", "Local", "Loja"]],
                use_container_width=True
            )    

       
    # ------------------ ABA 2: Relatórios e Indicadores ------------------
    total_recebido = int(mov[mov["type"] == "receipt"]["qty"].fillna(0).sum()) if not mov.empty else 0
    try:
        df_vendas_kpi = pd.read_sql_query(
            "SELECT COALESCE(SUM(qty), 0) AS total FROM movements WHERE type='sale' AND store_id=%s;",
            conn,
            params=(store_id,)
        )
        total_vendido = int(df_vendas_kpi.iloc[0]["total"]) if not df_vendas_kpi.empty else 0
    except Exception as e:
        print(f"[KPI Vendidos] Erro: {e}")
        total_vendido = 0

    total_perdido = int(exp["qty"].fillna(0).sum()) if not exp.empty else 0

    perc_vendido = (total_vendido / total_recebido * 100) if total_recebido > 0 else 0
    perc_perdido = (total_perdido / total_recebido * 100) if total_recebido > 0 else 0

    # ------------------ ABA 2: Relatórios e Indicadores ------------------
    with abas[2]:
        st.subheader("📊 Indicadores (KPIs)")

        # ==============================================================
        # 🔹 Cálculos de totais consolidados
        # ==============================================================

        # Total recebido (entradas)
        try:
            df_recebido = pd.read_sql_query(
                "SELECT COALESCE(SUM(qty), 0) AS total_recebido FROM movements WHERE type = 'receipt' AND store_id = %s;",
                conn,
                params=(store_id,)
            )
            total_recebido = int(df_recebido.iloc[0]["total_recebido"]) if not df_recebido.empty else 0
        except Exception as e:
            print(f"[KPI Recebidos] Erro: {e}")
            total_recebido = 0

        # Total vendido (quantidade de vendas reais)
        try:
            query_vendidos_kpi = """
                SELECT 
                    COALESCE(SUM(m.qty), 0) AS total_vendido
                FROM public.movements m
                WHERE m.type = 'sale' AND m.store_id = %s;
            """
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query_vendidos_kpi, (store_id,))
                result = cur.fetchone()
                total_vendido = int(result["total_vendido"]) if result and result["total_vendido"] is not None else 0
        except Exception as e:
            print(f"[KPI Vendidos] Erro: {e}")
            total_vendido = 0

        # Total a vencer
        try:
            total_a_vencer = int(near["qty"].fillna(0).sum()) if not near.empty else 0
        except Exception:
            total_a_vencer = 0

        # Total vencido (perdas)
        try:
            total_vencido = int(exp["qty"].fillna(0).sum()) if not exp.empty else 0
        except Exception:
            total_vencido = 0

        # Total em estoque
        try:
            total_estoque = int(df["qty"].fillna(0).sum()) if not df.empty else 0
        except Exception:
            total_estoque = 0

        # ==============================================================
        # 🔹 KPIs principais
        # ==============================================================
        perc_vendido = (total_vendido / total_estoque * 100) if total_recebido > 0 else 0
        perc_perdido = (total_vencido / total_estoque * 100) if total_recebido > 0 else 0

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("📦 Em Estoque", total_estoque)
        c2.metric("🛒 Vendidos", total_vendido, f"{perc_vendido:.1f}%")
        c3.metric("⚠️ A Vencer", total_a_vencer)
        c4.metric("❌ Vencidos (Perdas)", total_vencido, f"{perc_perdido:.1f}%")

        # ==============================================================
        # 🔹 Gráfico de pizza - Distribuição geral
        # ==============================================================
        st.subheader("📊 Distribuição Geral do Estoque")

        categorias = {
            "🟦 Em Estoque": total_estoque,
            "🟩 Vendidos": total_vendido,
            "🟨 A Vencer": total_a_vencer,
            "🟥 Vencidos (Perdas)": total_vencido
        }

        dist_df = pd.DataFrame(list(categorias.items()), columns=["Categoria", "Quantidade"])
        cores = ["#007bff", "#28a745", "#ffc107", "#dc3545"]

        fig2 = px.pie(
            dist_df,
            values="Quantidade",
            names="Categoria",
            hole=0.45,
            color="Categoria",
            color_discrete_sequence=cores,
        )
        fig2.update_traces(
            textposition="inside",
            textinfo="label+percent",
            textfont_size=14,
            hovertemplate="<b>%{label}</b><br>Qtd: %{value}<br>(%{percent})<extra></extra>",
        )
        fig2.update_layout(
            title={
                "text": "Distribuição do Estoque por Categoria",
                "x": 0.5,
                "xanchor": "center",
                "font": {"size": 18, "color": "#333", "family": "Segoe UI"},
            },
            showlegend=True,
            legend_title_text="Categoria",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=-0.2,
                xanchor="center",
                x=0.5,
                font=dict(size=12)
            ),
            margin=dict(t=60, b=0, l=0, r=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )

        st.plotly_chart(fig2, width="stretch")

        # ==============================================================
        # 🔹 Exportação de relatórios
        # ==============================================================
        st.divider()
        colA, colB = st.columns(2)
        if colA.button("📊 Gerar Relatório Excel"):
            path, _ = bot.exportar_relatorios(conn, cfg, store_id=store_id)
            colA.success(f"Relatório Excel gerado em: {path}")
            with open(path, "rb") as f:
                colA.download_button("Baixar Excel", f, file_name=Path(path).name)

        if colB.button("📄 Gerar Relatório PDF"):
            pdf_path = gerar_relatorio_pdf(
                cfg,
                df=df,
                total_estoque=total_estoque,
                total_a_vencer=total_a_vencer,
                total_vencido=total_vencido,
                total_vendido=total_vendido,
                store_id=user.get("store_id")
            )
            colB.success(f"PDF gerado em: {pdf_path}")
            with open(pdf_path, "rb") as f:
                colB.download_button("Baixar PDF", f, file_name=Path(pdf_path).name)


    # ------------------ ABA 4: Configurações ------------------
    if user.get("role") == "admin":
        with abas[4]:
            st.subheader("⚙️ Parâmetros do Sistema (Administrador)")

            # --- Carrega lista de lojas ---
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT id, name FROM stores ORDER BY name;")
                    lojas = cur.fetchall()
            except Exception as e:
                st.error(f"Erro ao carregar lojas: {e}")
                lojas = []

            # ✅ Compatível com dicts e tuplas (psycopg2)
            loja_opcoes = {}
            if lojas:
                for s in lojas:
                    if isinstance(s, dict):
                        nome = s.get("name", f"Loja {s.get('id')}")
                        loja_opcoes[f"{nome} (ID {s['id']})"] = s["id"]
                    else:
                        nome = s[1] if len(s) > 1 else f"Loja {s[0]}"
                        loja_opcoes[f"{nome} (ID {s[0]})"] = s[0]

            # ✅ Verifica se há lojas cadastradas
            if not loja_opcoes:
                st.warning("⚠️ Nenhuma loja cadastrada ainda. Crie uma loja antes de configurar.")
                st.stop()

            loja_sel = st.selectbox("Selecione a loja para configurar", options=list(loja_opcoes.keys()))
            loja_id = loja_opcoes.get(loja_sel)

            if not loja_id:
                st.info("Selecione uma loja válida para continuar.")
                st.stop()

            # --- Caminho de configuração por loja ---
            CFG_STORE_PATH = Path(__file__).resolve().parents[1] / f"config_loja_{loja_id}.json"

            # Carrega ou herda config da loja
            if CFG_STORE_PATH.exists():
                cfg_loja = json.loads(CFG_STORE_PATH.read_text(encoding="utf-8"))
            else:
                cfg_loja = cfg.copy()

            # --- Formulário de parâmetros ---
            days = st.number_input(
                "Dias para considerar 'A vencer'",
                min_value=1,
                max_value=120,
                value=int(cfg_loja.get("near_expiry_days", cfg.get("near_expiry_days", 30)))
            )

            st.divider()
            st.subheader("✉️ E-mail (SMTP Gmail)")

            col1, col2 = st.columns(2)
            with col1:
                username = st.text_input("Usuário (Gmail)", value=cfg_loja.get("alert_email", {}).get("username", ""))
                from_addr = st.text_input("Remetente (From)", value=cfg_loja.get("alert_email", {}).get("from_addr", ""))
                to_addrs = st.text_input(
                    "Destinatário(s) separados por vírgula",
                    value=",".join(cfg_loja.get("alert_email", {}).get("to_addrs", []))
                )
            with col2:
                smtp_server = st.text_input("Servidor SMTP", value=cfg_loja.get("alert_email", {}).get("smtp_server", "smtp.gmail.com"))
                smtp_port = st.number_input("Porta SMTP", value=int(cfg_loja.get("alert_email", {}).get("smtp_port", 587)))
                use_tls = st.checkbox("Usar TLS", value=bool(cfg_loja.get("alert_email", {}).get("use_tls", True)))
                password = st.text_input("Senha de app", type="password", value=cfg_loja.get("alert_email", {}).get("password", ""))
                enabled = st.checkbox("Habilitar envio de e-mails", value=bool(cfg_loja.get("alert_email", {}).get("enabled", False)))

            # --- Botão de salvar ---
            if st.button("💾 Salvar configurações da loja selecionada"):
                cfg_loja["near_expiry_days"] = int(days)
                cfg_loja["alert_email"] = {
                    "enabled": bool(enabled),
                    "smtp_server": smtp_server,
                    "smtp_port": int(smtp_port),
                    "use_tls": bool(use_tls),
                    "username": username,
                    "password": password,
                    "from_addr": from_addr,
                    "to_addrs": [a.strip() for a in to_addrs.split(",") if a.strip()]
                }

                CFG_STORE_PATH.write_text(json.dumps(cfg_loja, indent=2, ensure_ascii=False), encoding="utf-8")
                st.success(f"Configurações atualizadas para {loja_sel}.")
                st.rerun()
