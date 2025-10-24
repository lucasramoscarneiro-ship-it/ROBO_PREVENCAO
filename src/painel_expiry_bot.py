# src/painel_expiry_bot.py
import streamlit as st
import pandas as pd
import plotly.express as px
from pathlib import Path
import json
from datetime import datetime, timedelta

from db import get_conn, init_db
import reporting
import expiry_bot as bot
from report_pdf import gerar_relatorio_pdf
from nfe_import import parse_nfe_xml
import streamlit.components.v1 as components


def main(conn, cfg, user):

    store_id = user.get("store_id")
    # Caminho do config (usado ao salvar alterações na aba de Configurações)
    # --- Carregar configuração específica da loja ---
    # --- Carregar configuração específica da loja (SEM cache global) ---
    CFG_PATH = Path(__file__).resolve().parents[1] / "config.json"
    CFG_STORE_PATH = Path(__file__).resolve().parents[1] / f"config_loja_{store_id}.json"

    # Se não existir o arquivo da loja, cria com base na config global
    if not CFG_STORE_PATH.exists():
        Path(CFG_STORE_PATH).write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    # Carrega a configuração da loja diretamente (sem cache compartilhado)
    with open(CFG_STORE_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

    # --------- UI GLOBAL ---------
    st.set_page_config(page_title="Controle LRC - Painel Web da Loja", layout="wide")

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

    # ------------------ PRÉ-CÁLCULOS COMPARTILHADOS ------------------
    df = reporting.build_snapshots(conn)
    if "store_id" in df.columns:
        df = df[df["store_id"] == store_id]
    near = reporting.near_expiry(df, cfg["near_expiry_days"])
    exp = reporting.expired(df)
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
                        cur.execute("INSERT OR IGNORE INTO products(ean, product_name) VALUES(?,?)", (ean, pname))
                        cur.execute("UPDATE products SET product_name=COALESCE(NULLIF(?, ''), product_name) WHERE ean=?", (pname, ean))
                        if expiry:
                            cur.execute("INSERT OR IGNORE INTO lots(ean, lot, expiry_date) VALUES(?,?,?)", (ean, lot, expiry))
                        else:
                            cur.execute("INSERT OR IGNORE INTO lots(ean, lot, expiry_date) VALUES(?,?,DATE('now', '+180 days'))", (ean, lot))


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
                cur = conn.cursor()
                # Garante que o produto exista
                cur.execute("INSERT OR IGNORE INTO products(ean, product_name) VALUES(?, ?)", (ean_r, pname_r))
                cur.execute("UPDATE products SET product_name = COALESCE(NULLIF(?, ''), product_name) WHERE ean = ?", (pname_r, ean_r))

                # Garante que o lote exista
                cur.execute("INSERT OR IGNORE INTO lots(ean, lot, expiry_date) VALUES(?,?,?)",
                            (ean_r, lot_r, expiry_r.isoformat()))

                # Verifica se já existe item igual no estoque da mesma loja
                cur.execute("""
                    SELECT qty FROM stock WHERE ean=? AND lot=? AND store_id=?
                """, (ean_r, lot_r, store_id))
                row = cur.fetchone()

                if row:
                    # Atualiza a quantidade existente
                    nova_qtd = row[0] + qty_r
                    cur.execute("""
                        UPDATE stock
                        SET qty=?, location=?, updated_at=CURRENT_TIMESTAMP
                        WHERE ean=? AND lot=? AND store_id=?
                    """, (nova_qtd, location_r, ean_r, lot_r, store_id))
                    st.info(f"Item já existia — quantidade atualizada para {nova_qtd}.")
                else:
                    # Cria novo registro de estoque
                    cur.execute("""
                        INSERT INTO stock (ean, lot, qty, location, store_id)
                        VALUES (?, ?, ?, ?, ?)
                    """, (ean_r, lot_r, qty_r, location_r, store_id))
                    st.success("Novo item de estoque criado com sucesso!")

                conn.commit()

                # Registra o movimento
                bot.movimentar(
                    conn,
                    "receipt",
                    ean_r,
                    lot_r,
                    qty_r,
                    observacao="Entrada via Web",
                    local=location_r,
                    store_id=store_id,
                )

            except Exception as e:
                st.error(f"Erro ao registrar: {e}")

        st.divider()
        st.subheader("➖ Registrar Saída (Venda)")

        # usamos o snapshot 'df' já filtrado por store_id, criado acima
        # vamos considerar somente itens com saldo > 0
        # Garante que df foi carregado antes de tentar acessar
        if "df" not in locals() or df is None:
            df_disponivel = pd.DataFrame()
        else:
            try:
                df_disponivel = df[df["qty"] > 0].copy()
            except Exception:
                df_disponivel = pd.DataFrame()


        with st.form("form_saida"):
            if df_disponivel.empty:
                st.info("Não há itens com saldo disponível para venda nesta loja.")
                submitted_v = st.form_submit_button("Registrar Saída", disabled=True)
            else:
                # Lista de produtos únicos disponíveis (por EAN)
                prods = (
                    df_disponivel[["ean", "product_name"]]
                    .drop_duplicates()
                    .sort_values(["product_name", "ean"])
                )

                # selectbox de produtos (objeto = dict -> fácil recuperar EAN)
                prod_options = prods.to_dict("records")
                prod_sel = st.selectbox(
                    "Produto",
                    options=prod_options,
                    format_func=lambda r: f"{r['product_name']} — {r['ean']}",
                    key="saida_produto",
                )

                # filtra lotes do produto selecionado
                df_lotes = (
                    df_disponivel[df_disponivel["ean"] == prod_sel["ean"]]
                    .copy()
                    .sort_values(["expiry_date", "lot"])
                )

                # monta opções de lotes com rótulo rico
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

                # quantidade limitada ao saldo disponível do lote
                saldo_lote = int(lote_sel["qty"])
                qty_v = st.number_input(
                    "Quantidade a vender",
                    min_value=1,
                    max_value=max(1, saldo_lote),
                    value=1,
                    step=1,
                    key="saida_qtd",
                    help=f"Saldo disponível neste lote: {saldo_lote}",
                )

                # local opcional (preenche com a loja)
                location_v = st.text_input("Local", value=f"Loja {store_id}", key="saida_loc")

                submitted_v = st.form_submit_button("Registrar Saída")

        if submitted_v:
            try:
                # segurança extra: impede vender acima do saldo (se mexeram no DOM)
                if qty_v > int(lote_sel["qty"]):
                    st.error("Quantidade solicitada é maior que o saldo disponível do lote.")
                else:
                    bot.movimentar(
                        conn,
                        "sale",
                        ean=lote_sel["ean"],
                        lot=lote_sel["lot"],
                        qty=qty_v,
                        observacao="Saída via Web",
                        local=location_v,
                        store_id=store_id,
                    )
                    st.success("Saída registrada com sucesso!")
                    st.rerun()
            except Exception as e:
                st.error(str(e))


    

    total_estoque = int(df["qty"].sum()) if not df.empty else 0
    total_vencido = int(exp["qty"].sum()) if not exp.empty else 0
    total_a_vencer = int(near["qty"].sum()) if not near.empty else 0

    mov = pd.read_sql_query("SELECT * FROM movements WHERE store_id IS ?", conn, params=(store_id,), parse_dates=["ts"])
    if not mov.empty:
        mov["data"] = mov["ts"].dt.date
    total_recebido = int(mov[mov["type"]=="receipt"]["qty"].sum()) if not mov.empty else 0
    total_vendido = int(mov[mov["type"]=="sale"]["qty"].sum()) if not mov.empty else 0
    perc_vendido = (total_vendido/total_recebido*100) if total_recebido>0 else 0
    perc_vencido = (total_vencido/total_recebido*100) if total_recebido>0 else 0

    # ------------------ ABA 1: Operacional ------------------
    with abas[1]:
        st.subheader("📋 Estoque Atual")
        st.dataframe(df.rename(columns={
            "product_name":"Produto", "lot":"Lote", "expiry_date":"Validade", "qty":"Qtde", "location":"Local", "store_id":"Loja"
        }), use_container_width=True)

        st.divider()
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
                nova_qtd = st.number_input("Quantidade", min_value=0, value=int(item["qty"]))
            with col2:
                nova_data = st.date_input("Validade", value=pd.to_datetime(item["expiry_date"]).date())
            with col3:
                novo_local = st.text_input("Local", value=item.get("location", "Loja 01"))

            colA, colB = st.columns(2)
            if colA.button("💾 Salvar Alterações"):
                try:
                    cur = conn.cursor()
                    cur.execute("UPDATE stock SET qty=?, location=? WHERE ean=? AND lot=?", (nova_qtd, novo_local, ean_sel, lot_sel))
                    cur.execute("UPDATE lots SET expiry_date=? WHERE ean=? AND lot=?", (nova_data.isoformat(), ean_sel, lot_sel))
                    conn.commit()
                    st.success("Item atualizado com sucesso!")
                except Exception as e:
                    st.error(f"Erro ao atualizar: {e}")

            if colB.button("🗑️ Excluir Item do Estoque"):
                confirm = st.checkbox("Confirmar exclusão permanente do item")
                if confirm:
                    try:
                        cur = conn.cursor()
                        cur.execute("DELETE FROM stock WHERE ean=? AND lot=?", (ean_sel, lot_sel))
                        cur.execute("DELETE FROM lots WHERE ean=? AND lot=?", (ean_sel, lot_sel))
                        conn.commit()
                        st.warning("Item excluído com sucesso!")
                    except Exception as e:
                        st.error(f"Erro ao excluir: {e}")

        st.subheader(f"⚠️ A Vencer (≤ {cfg['near_expiry_days']} dias)")
        st.dataframe(near.rename(columns={
            "product_name":"Produto", "lot":"Lote", "expiry_date":"Validade", "qty":"Qtde", "location":"Local", "store_id":"Loja"
        }), use_container_width=True)
        st.subheader("❌ Vencidos")
        st.dataframe(exp.rename(columns={
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
                return ("🔴", "🔁 Priorizar venda imediata — vence em menos de 7 dias")
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

    # ------------------ ABA 2: Relatórios e Indicadores ------------------
    with abas[2]:
        st.subheader("📊 Indicadores (KPIs)")
        c1,c2,c3,c4,c5 = st.columns(5)
        c1.metric("📦 Em Estoque", total_estoque)
        c2.metric("🛒 Vendidos", total_vendido, f"{perc_vendido:.1f}%")
        c3.metric("⚠️ A Vencer", total_a_vencer)
        c4.metric("❌ Vencidos (Perdas)", total_vencido, f"{perc_vencido:.1f}%")
        c5.metric("📥 Recebidos", total_recebido)

        st.divider()
        st.subheader("Distribuição do Estoque Atual")
        categorias = {"Vendidos": total_vendido, "A vencer": total_a_vencer, "Vencidos": total_vencido, "Em estoque": total_estoque}
        dist_df = pd.DataFrame(list(categorias.items()), columns=["Categoria","Quantidade"])
        fig2 = px.pie(dist_df, values="Quantidade", names="Categoria", title="Distribuição do Estoque")
        st.plotly_chart(fig2, use_container_width=True)

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

    # ------------------ ABA 3: Alertas ------------------
    if user.get("role") == "admin":
        with abas[3]:
            st.subheader("✉️ Enviar alerta de itens a vencer (e-mail)")
            st.caption("Configure o Gmail (senha de app) em ⚙️ Configurações antes de enviar.")

            # Seletor de loja para envio de alerta
            lojas = conn.execute("SELECT id, name FROM stores").fetchall()
            loja_opcoes = {f"{s[1]} (ID {s[0]})": s[0] for s in lojas}
            loja_sel_alerta = st.selectbox("Selecione a loja para enviar o alerta", options=list(loja_opcoes.keys()))

            if st.button("📤 Enviar alerta agora"):
                store_id_alerta = loja_opcoes[loja_sel_alerta]
                df_alerta = reporting.build_snapshots(conn)
                df_alerta = df_alerta[df_alerta["store_id"] == store_id_alerta]
                near_alerta = reporting.near_expiry(df_alerta, cfg["near_expiry_days"])

                if near_alerta.empty:
                    st.info(f"Nenhum produto próximo da validade para a loja {loja_sel_alerta}.")
                else:
                    near_body = reporting.to_console(near_alerta, f"Itens a vencer em {cfg['near_expiry_days']} dias")
                    ok, info = bot.enviar_email_alerta(cfg, f"⚠️ Alerta: produtos a vencer — {loja_sel_alerta}", near_body)
                    if ok:
                        st.success(info)
                    else:
                        st.error(f"❌ {info}")
    else:
        with abas[3]:
            st.info("🔒 Acesso restrito: apenas administradores podem visualizar e enviar alertas.")

    # ------------------ ABA 4: Configurações ------------------
    if user.get("role") == "admin":
        with abas[4]:
            st.subheader("⚙️ Parâmetros do Sistema (Administrador)")

            lojas = conn.execute("SELECT id, name FROM stores").fetchall()
            loja_opcoes = {f"{s[1]} (ID {s[0]})": s[0] for s in lojas}
            loja_sel = st.selectbox("Selecione a loja para configurar", options=list(loja_opcoes.keys()))
            loja_id = loja_opcoes[loja_sel]

            CFG_STORE_PATH = Path(__file__).resolve().parents[1] / f"config_loja_{loja_id}.json"

            # Carrega ou herda config da loja
            if CFG_STORE_PATH.exists():
                cfg_loja = json.loads(CFG_STORE_PATH.read_text(encoding="utf-8"))
            else:
                cfg_loja = cfg.copy()

            # Formulário de configuração
            days = st.number_input(
                "Dias para considerar 'A vencer'",
                min_value=1, max_value=120, value=int(cfg_loja.get("near_expiry_days", cfg["near_expiry_days"]))
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
    else:
        with abas[4]:
            st.info("🔒 Acesso restrito: apenas administradores podem acessar as configurações do sistema.")
