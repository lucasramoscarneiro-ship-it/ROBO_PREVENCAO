# -*- coding: utf-8 -*-
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from datetime import datetime
from pathlib import Path
import pandas as pd
import io
import matplotlib.pyplot as plt
import psycopg2


def gerar_relatorio_pdf(cfg, df, total_estoque, total_a_vencer, total_vencido, total_vendido, store_id=None):
    """
    Gera um relatório PDF resumido e visualmente agradável.
    Compatível com Supabase (PostgreSQL via psycopg2).
    """

    # =====================
    # 🔧 Recupera nome da loja (para título)
    # =====================
    loja_nome = "Todas as Lojas"
    if store_id:
        try:
            conn = psycopg2.connect(
                cfg["uri"],
                sslmode=cfg.get("sslmode", "require"),
            )
            cur = conn.cursor()
            cur.execute("SELECT name FROM stores WHERE id = %s", (store_id,))
            row = cur.fetchone()
            if row and row[0]:
                loja_nome = row[0]
            conn.close()
        except Exception as e:
            print(f"[report_pdf] Falha ao obter nome da loja: {e}")
            loja_nome = f"Loja {store_id}"

    # =====================
    # 🔧 Filtragem de dados
    # =====================
    if store_id and "store_id" in df.columns:
        df = df[df["store_id"] == store_id]

    outdir = Path(cfg.get("report_dir", "data/reports"))
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    filename = f"relatorio_validade_{loja_nome.replace(' ', '_')}_{stamp}.pdf"
    path_pdf = outdir / filename
    pdf_path_str = str(path_pdf.resolve())

    # =====================
    # 🧾 Documento base
    # =====================
    doc = SimpleDocTemplate(
        pdf_path_str,
        pagesize=A4,
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        alignment=1,
        fontSize=16,
        spaceAfter=10,
        textColor=colors.darkblue
    )
    style_subtitle = ParagraphStyle(
        'SubTitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.black,
        alignment=1
    )

    elements = []

    # =====================
    # 🏷️ Cabeçalho
    # =====================
    elements.append(Paragraph(f"<b>Relatório de Validades — {loja_nome}</b>", style_title))
    elements.append(Paragraph(f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}", style_subtitle))
    elements.append(Spacer(1, 0.3 * cm))

    # =====================
    # 📊 Indicadores resumidos
    # =====================
    data_kpi = [
        ["Indicador", "Quantidade"],
        ["Total em Estoque", f"{total_estoque}"],
        ["A Vencer", f"{total_a_vencer}"],
        ["Vencidos", f"{total_vencido}"],
        ["Vendidos", f"{total_vendido}"]
    ]
    kpi_table = Table(data_kpi, colWidths=[8 * cm, 6 * cm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightblue),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 0.4 * cm))

    # =====================
    # 🥧 Gráfico tipo donut
    # =====================
    try:
        categorias = {
            "🛒 Vendidos": total_vendido,
            "⚠️ A Vencer": total_a_vencer,
            "❌ Vencidos": total_vencido,
            "📦 Em Estoque": total_estoque
        }
        categorias = {k: v for k, v in categorias.items() if v > 0}

        fig, ax = plt.subplots(figsize=(4.5, 4.5), facecolor="white")
        wedges, texts, autotexts = ax.pie(
            categorias.values(),
            labels=categorias.keys(),
            autopct="%1.1f%%",
            startangle=90,
            counterclock=False,
            pctdistance=0.8,
            textprops={"fontsize": 9, "color": "black", "weight": "bold"},
            wedgeprops={"linewidth": 1.5, "edgecolor": "white"},
            colors=["#4B9FE1", "#FFC857", "#E84855", "#3CB371"]
        )
        centre_circle = plt.Circle((0, 0), 0.55, fc="white", lw=0)
        fig.gca().add_artist(centre_circle)
        ax.set_title("Distribuição do Estoque", fontsize=12, fontweight="bold", color="#333333", pad=12)
        plt.tight_layout()
        img_buf = io.BytesIO()
        plt.savefig(img_buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        img_buf.seek(0)
        elements.append(Image(img_buf, width=9 * cm, height=9 * cm))
        elements.append(Spacer(1, 0.5 * cm))
    except Exception as e:
        elements.append(Paragraph(f"Erro ao gerar gráfico: {e}", styles["Normal"]))

    # =====================
    # 🧾 Tabela de produtos
    # =====================
    if not df.empty:
        resumo = df[["product_name", "lot", "expiry_date", "qty", "location"]].copy()
        resumo.rename(columns={
            "product_name": "Produto",
            "lot": "Lote",
            "expiry_date": "Validade",
            "qty": "Qtde",
            "location": "Local"
        }, inplace=True)
        resumo["Validade"] = pd.to_datetime(resumo["Validade"]).dt.strftime("%d/%m/%Y")

        max_rows = 20
        if len(resumo) > max_rows:
            resumo = resumo.head(max_rows)
            elements.append(Paragraph("<i>Exibindo apenas as 20 primeiras linhas...</i>", style_subtitle))

        data_table = [resumo.columns.tolist()] + resumo.values.tolist()
        table = Table(data_table, colWidths=[5 * cm, 2 * cm, 2.5 * cm, 2 * cm, 3 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 0.3 * cm))
    else:
        elements.append(Paragraph("Nenhum produto encontrado para o relatório.", styles["Normal"]))

    # =====================
    # 📄 Rodapé
    # =====================
    elements.append(Paragraph("<b>Relatório resumido — Sistema Controle LRC</b>", style_subtitle))

    # =====================
    # 🖨️ Gera o PDF final
    # =====================
    doc.build(elements)
    return pdf_path_str
