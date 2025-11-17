import xml.etree.ElementTree as ET
import pandas as pd


def parse_nfe_xml(xml_path: str):
    """
    Lê o XML da NF-e e retorna um DataFrame com produtos perecíveis.
    Critérios:
      - Possui tag <dVal> (data de validade) OU
      - NCM começa com prefixos de alimentos.
    Campos retornados:
      ean, product_name, lot, expiry_date, qty, ncm
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception as e:
        raise ValueError(f"Erro ao ler o XML: {e}")

    # Detecta namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0].strip("{")
    nsmap = {"ns": ns} if ns else {}

    pereciveis_prefix = [
        "02", "03", "04", "07", "08", "09",
        "10", "11", "15", "16", "17", "18", "19", "20"
    ]

    items = []

    for det in root.findall(".//ns:det", nsmap):
        prod = det.find("ns:prod", nsmap)
        if prod is None:
            continue

        ean = (prod.findtext("ns:cProd", namespaces=nsmap) or "").strip()
        name = (prod.findtext("ns:xProd", namespaces=nsmap) or "").strip()
        qty = float(prod.findtext("ns:qCom", namespaces=nsmap) or 0)
        lot = (
            prod.findtext("ns:nLote", namespaces=nsmap)
            or f"LOTE-{ean[-4:]}" if ean else "SEMLOTE"
        )
        expiry = (
            prod.findtext("ns:dVal", namespaces=nsmap)
            or prod.findtext("ns:dVenc", namespaces=nsmap)
            or ""
        )
        ncm = (prod.findtext("ns:NCM", namespaces=nsmap) or "").strip()

        # Filtro de perecíveis
        if not expiry and not any(ncm.startswith(p) for p in pereciveis_prefix):
            continue

        if expiry:
            expiry = expiry.split("T")[0]

        items.append({
            "ean": ean,
            "product_name": name,
            "lot": lot,
            "expiry_date": expiry,
            "qty": qty,
            "ncm": ncm,
        })

    df = pd.DataFrame(items)
    if df.empty:
        return pd.DataFrame(columns=["ean", "product_name", "lot", "expiry_date", "qty", "ncm"])

    # Normaliza e ordena colunas
    df = df[["ean", "product_name", "lot", "expiry_date", "qty", "ncm"]]
    df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")

    return df
