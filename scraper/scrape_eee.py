"""
Scraper: Encuesta Mensual de Expectativas Económicas — BCB
Descarga PDFs mensuales, extrae todas las variables y genera JSONs.
"""

import requests, re, os, json, sys
from pathlib import Path
import pdfplumber

BASE_URL = "https://www.bcb.gob.bo"
SEARCH_URL = f"{BASE_URL}/?q=resultados-encuestas"
PDF_DIR = Path(__file__).parent / "pdfs"
DATA_DIR = Path(__file__).parent.parent / "data"

MESES = {
    "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
    "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9, "sep": 9,
    "octubre": 10, "oct": 10, "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12,
}
MESES_LABEL = {
    1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
}

FALLBACK_URLS = {
    (2024, 1): f"{BASE_URL}/webdocs/24_resultados_encuestas/Reporte%20EEE%20ene%202024_SCRI.pdf",
    (2024, 2): f"{BASE_URL}/webdocs/24_resultados_encuestas/Reporte%20EEE%20feb%202024.pdf",
    (2025, 1): f"{BASE_URL}/webdocs/24_resultados_encuestas/Reporte%20EEE%20Enero%202025_v2.pdf",
    (2025, 2): f"{BASE_URL}/webdocs/24_resultados_encuestas/Reporte%20EEE%20Febrero%202025_v2.pdf",
}


def discover_pdfs(years):
    """Discover PDF URLs from the BCB search page."""
    urls = {}
    for year in years:
        r = requests.get(SEARCH_URL, params={
            "field_fecha_eee_value[value][year]": str(year),
            "q": "resultados-encuestas",
        }, timeout=30)
        found = re.findall(
            rf'href="({re.escape(BASE_URL)}/webdocs/24_resultados_encuestas/[^"]*\.pdf)"',
            r.text,
        )
        seen = set()
        for url in found:
            if url not in seen:
                seen.add(url)
                urls[url] = year
    for (y, m), url in FALLBACK_URLS.items():
        if y in years:
            urls[url] = y
    return list(urls.keys())


def download_pdfs(urls):
    """Download PDFs that don't exist locally yet."""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for url in urls:
        fname = url.split("/")[-1].replace("%20", "_")
        fp = PDF_DIR / fname
        if not fp.exists():
            r = requests.get(url, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000:
                fp.write_bytes(r.content)
                print(f"  Descargado: {fname}")
        paths.append(fp)
    return [p for p in paths if p.exists()]


def parse_number(s):
    """Parse a number from the PDF text, handling comma decimals and thousands dots."""
    s = s.strip()
    if re.match(r'^-?\d{1,3}(\.\d{3})+$', s):
        return float(s.replace(".", ""))
    if re.match(r'^-?\d{1,3}(\.\d{3})+,\d+$', s):
        return float(s.replace(".", "").replace(",", "."))
    dot_count = s.count(".")
    comma_count = s.count(",")
    if dot_count >= 2 and comma_count == 0:
        return float(s.replace(".", ""))
    if dot_count == 1 and comma_count == 0:
        clean = s.replace(".", "")
        try:
            parts = s.split(".")
            if len(parts[0]) <= 3 and len(parts[1]) == 3 and float(s) > 999:
                return float(clean)
        except:
            pass
    return float(s.replace(",", "."))


def extract_survey_date(text):
    """Extract survey month and year from the PDF header."""
    m = re.search(
        r"(Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre|Noviembre|Diciembre)\s+(\d{4})",
        text, re.IGNORECASE,
    )
    if not m:
        return None, None
    month = MESES.get(m.group(1).lower(), 0)
    year = int(m.group(2))
    return year, month


def extract_row(text, row_pattern):
    """Extract Mediana, Media, Moda, Desv, Coef, D1, D9, Resp from a data row."""
    pattern = row_pattern + r"\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)\s+([\d,.\-]+)\s+(\d+)"
    m = re.search(pattern, text)
    if not m:
        return None
    vals = [m.group(i) for i in range(1, 9)]
    return {
        "mediana": parse_number(vals[0]),
        "media": parse_number(vals[1]),
        "moda": parse_number(vals[2]),
        "desv_estandar": parse_number(vals[3]),
        "coef_variacion": parse_number(vals[4]),
        "decil1": parse_number(vals[5]),
        "decil9": parse_number(vals[6]),
        "respuestas": int(vals[7]),
    }


def extract_all_variables(text, survey_year, survey_month):
    """Extract all variables from a single PDF's page 1 text."""
    target_year = survey_year + 1
    result = {
        "survey_month": f"{survey_year}-{survey_month:02d}",
        "label": f"{MESES_LABEL[survey_month]} {survey_year}",
    }

    # --- Inflación ---
    # Var mensual del mes de la encuesta
    mes_name_pattern = r"(?:Enero|Febrero|Marzo|Abril|Mayo|Junio|Julio|Agosto|Septiembre|Octubre|Noviembre|Diciembre)"
    var_mensual = extract_row(text, rf"{mes_name_pattern}\s+{survey_year}\s+\(var\.\s*mensual\)")
    if var_mensual:
        result["inflacion_mensual"] = var_mensual

    # Dic año actual (var. 12 meses)
    dic_actual = extract_row(text, rf"Diciembre\s+{survey_year}\s+\(var\.\s*12\s*meses\)")
    if dic_actual:
        result["inflacion_dic_actual"] = dic_actual

    # Dic año siguiente (var. 12 meses) — SERIE PRINCIPAL
    dic_next = extract_row(text, rf"Diciembre\s+{target_year}\s+\(var\.\s*12\s*meses\)")
    if dic_next:
        result["inflacion_dic_siguiente"] = dic_next
        result["inflacion_dic_siguiente"]["target"] = f"Dic {target_year}"

    # --- Tipo de Cambio ---
    tc_actual = extract_row(text, rf"Diciembre\s+{survey_year}\s+\(fin\s+de\s+periodo\)")
    if tc_actual:
        result["tipo_cambio_dic_actual"] = tc_actual

    tc_next = extract_row(text, rf"Diciembre\s+{target_year}\s+\(fin\s+de\s+periodo\)")
    if tc_next:
        result["tipo_cambio_dic_siguiente"] = tc_next
        result["tipo_cambio_dic_siguiente"]["target"] = f"Dic {target_year}"

    # --- PIB ---
    pib_actual = extract_row(text, rf"(?:Año|A.o)\s+{survey_year}\s+\(var\.\s*anual\s+acumulada\)")
    if pib_actual:
        result["pib_actual"] = pib_actual

    pib_next = extract_row(text, rf"(?:Año|A.o)\s+{target_year}\s+\(var\.\s*anual\s+acumulada\)")
    if pib_next:
        result["pib_siguiente"] = pib_next
        result["pib_siguiente"]["target"] = f"Año {target_year}"

    # --- Balance Fiscal ---
    fiscal_actual = extract_row(text, rf"(?:Año|A.o)\s+{survey_year}\s+\(total\s+a.o/PIB\s+anual\)")
    if fiscal_actual:
        result["balance_fiscal_actual"] = fiscal_actual

    fiscal_next = extract_row(text, rf"(?:Año|A.o)\s+{target_year}\s+\(total\s+a.o/PIB\s+anual\)")
    if fiscal_next:
        result["balance_fiscal_siguiente"] = fiscal_next

    # --- Reservas Internacionales ---
    rin_dic_actual = None
    rin_pattern_actual = rf"Diciembre\s+{survey_year}\s+\(fin\s+de\s+periodo\)"
    rin_section = text[text.find("Reservas"):] if "Reservas" in text else ""
    if rin_section:
        rin_dic_actual = extract_row(rin_section, r"Diciembre\s+\d{4}\s+\(fin\s+de\s+periodo\)")

    rin_dic_next = None
    if rin_section:
        matches = list(re.finditer(r"Diciembre\s+(\d{4})\s+\(fin\s+de\s+periodo\)", rin_section))
        for match in matches:
            yr = int(match.group(1))
            row = extract_row(rin_section[match.start():], r"Diciembre\s+\d{4}\s+\(fin\s+de\s+periodo\)")
            if yr == survey_year and row:
                result["rin_dic_actual"] = row
            elif yr == target_year and row:
                result["rin_dic_siguiente"] = row
                result["rin_dic_siguiente"]["target"] = f"Dic {target_year}"

    # --- Tasa de Desocupación ---
    desoc_section = text[text.find("Desocupaci"):] if "Desocupaci" in text else ""
    if desoc_section:
        dic_desoc = extract_row(desoc_section, rf"Diciembre\s+{survey_year}\s+\(mensual\)")
        if dic_desoc:
            result["desocupacion_dic_actual"] = dic_desoc
        dic_desoc_next = extract_row(desoc_section, rf"Diciembre\s+{target_year}\s+\(mensual\)")
        if dic_desoc_next:
            result["desocupacion_dic_siguiente"] = dic_desoc_next

    # --- Percepción de la economía ---
    ipe_match = re.search(r"Percepci.n de la econom.a \(IPE\)\s+([\d,.\-]+)", text)
    isa_match = re.search(r"Situaci.n Actual \(ISA\)\s+([\d,.\-]+)", text)
    isf_match = re.search(r"Situaci.n Futura \(ISF\)\s+([\d,.\-]+)", text)
    if ipe_match:
        result["ipe"] = parse_number(ipe_match.group(1))
    if isa_match:
        result["isa"] = parse_number(isa_match.group(1))
    if isf_match:
        result["isf"] = parse_number(isf_match.group(1))

    return result


def process_all_pdfs(pdf_paths):
    """Process all PDFs and return list of extracted data."""
    all_data = []
    seen = set()

    for fp in pdf_paths:
        try:
            pdf = pdfplumber.open(fp)
            text = pdf.pages[0].extract_text()
            pdf.close()
        except Exception as e:
            print(f"  Error leyendo {fp.name}: {e}")
            continue

        year, month = extract_survey_date(text)
        if not year or not month:
            continue

        key = (year, month)
        if key in seen:
            continue
        seen.add(key)

        data = extract_all_variables(text, year, month)
        if data.get("inflacion_dic_siguiente"):
            all_data.append(data)
            print(f"  OK: {data['label']}")
        else:
            print(f"  SKIP: {fp.name} — sin fila Dic {year + 1}")

    all_data.sort(key=lambda x: x["survey_month"])
    return all_data


def build_series_json(all_data):
    """Build individual series JSONs from the combined data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # --- Inflación expectations ---
    inflacion = {
        "metadata": {
            "fuente": "Banco Central de Bolivia — Encuesta Mensual de Expectativas Económicas",
            "url": "https://www.bcb.gob.bo/?q=resultados-encuestas",
            "descripcion": "Tasa de inflación interanual (a 12 meses) esperada para diciembre del año siguiente",
            "nota": "Cada mes presenta la expectativa de los encuestados sobre la inflación al cierre del próximo año",
            "ultimo_dato": all_data[-1]["label"],
            "total_observaciones": len(all_data),
        },
        "series": [],
    }
    for d in all_data:
        inf = d.get("inflacion_dic_siguiente", {})
        inflacion["series"].append({
            "survey_month": d["survey_month"],
            "label": d["label"],
            "target": inf.get("target", ""),
            "mediana": inf.get("mediana"),
            "media": inf.get("media"),
            "decil1": inf.get("decil1"),
            "decil9": inf.get("decil9"),
            "respuestas": inf.get("respuestas"),
        })

    with open(DATA_DIR / "expectativas_inflacion.json", "w", encoding="utf-8") as f:
        json.dump(inflacion, f, ensure_ascii=False, indent=2)

    # --- Tipo de Cambio expectations ---
    tc = {
        "metadata": {
            "fuente": "BCB — EEE",
            "descripcion": "Tipo de cambio Bs/$us esperado para fin de periodo, diciembre del año siguiente",
            "ultimo_dato": all_data[-1]["label"],
        },
        "series": [],
    }
    for d in all_data:
        val = d.get("tipo_cambio_dic_siguiente", {})
        if val:
            tc["series"].append({
                "survey_month": d["survey_month"],
                "label": d["label"],
                "target": val.get("target", ""),
                "mediana": val.get("mediana"),
                "decil1": val.get("decil1"),
                "decil9": val.get("decil9"),
            })
    with open(DATA_DIR / "expectativas_tipo_cambio.json", "w", encoding="utf-8") as f:
        json.dump(tc, f, ensure_ascii=False, indent=2)

    # --- PIB expectations ---
    pib = {
        "metadata": {
            "fuente": "BCB — EEE",
            "descripcion": "Crecimiento del PIB esperado (var. anual acumulada) para el año siguiente",
            "ultimo_dato": all_data[-1]["label"],
        },
        "series": [],
    }
    for d in all_data:
        val = d.get("pib_siguiente", {})
        if val:
            pib["series"].append({
                "survey_month": d["survey_month"],
                "label": d["label"],
                "target": val.get("target", ""),
                "mediana": val.get("mediana"),
                "decil1": val.get("decil1"),
                "decil9": val.get("decil9"),
            })
    with open(DATA_DIR / "expectativas_pib.json", "w", encoding="utf-8") as f:
        json.dump(pib, f, ensure_ascii=False, indent=2)

    # --- RIN expectations ---
    rin = {
        "metadata": {
            "fuente": "BCB — EEE",
            "descripcion": "Reservas Internacionales Netas esperadas (MM $us) para diciembre del año siguiente",
            "ultimo_dato": all_data[-1]["label"],
        },
        "series": [],
    }
    for d in all_data:
        val = d.get("rin_dic_siguiente", {})
        if val:
            rin["series"].append({
                "survey_month": d["survey_month"],
                "label": d["label"],
                "target": val.get("target", ""),
                "mediana": val.get("mediana"),
                "decil1": val.get("decil1"),
                "decil9": val.get("decil9"),
            })
    with open(DATA_DIR / "expectativas_rin.json", "w", encoding="utf-8") as f:
        json.dump(rin, f, ensure_ascii=False, indent=2)

    # --- Percepción (IPE/ISA/ISF) ---
    percepcion = {
        "metadata": {
            "fuente": "BCB — EEE",
            "descripcion": "Índice de Percepción Económica, Situación Actual y Situación Futura",
            "ultimo_dato": all_data[-1]["label"],
        },
        "series": [],
    }
    for d in all_data:
        percepcion["series"].append({
            "survey_month": d["survey_month"],
            "label": d["label"],
            "ipe": d.get("ipe"),
            "isa": d.get("isa"),
            "isf": d.get("isf"),
        })
    with open(DATA_DIR / "expectativas_percepcion.json", "w", encoding="utf-8") as f:
        json.dump(percepcion, f, ensure_ascii=False, indent=2)

    # --- Combined ---
    combined = {
        "metadata": {
            "fuente": "BCB — Encuesta Mensual de Expectativas Económicas",
            "url": "https://www.bcb.gob.bo/?q=resultados-encuestas",
            "ultimo_dato": all_data[-1]["label"],
            "total_observaciones": len(all_data),
        },
        "data": all_data,
    }
    with open(DATA_DIR / "expectativas_all.json", "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    # --- Metadata ---
    meta = {
        "ultimo_scrape": all_data[-1]["survey_month"],
        "ultimo_dato": all_data[-1]["label"],
        "total_observaciones": len(all_data),
        "archivos": [
            "expectativas_inflacion.json",
            "expectativas_tipo_cambio.json",
            "expectativas_pib.json",
            "expectativas_rin.json",
            "expectativas_percepcion.json",
            "expectativas_all.json",
        ],
    }
    with open(DATA_DIR / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return inflacion


def main():
    start_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    end_year = 2026
    years = list(range(start_year, end_year + 1))

    print(f"Buscando PDFs para años {years}...")
    urls = discover_pdfs(years)
    print(f"  {len(urls)} PDFs encontrados")

    print("Descargando...")
    paths = download_pdfs(urls)
    print(f"  {len(paths)} PDFs listos")

    print("Extrayendo datos...")
    all_data = process_all_pdfs(paths)
    print(f"  {len(all_data)} observaciones extraídas")

    print("Generando JSONs...")
    build_series_json(all_data)
    print("Listo.")


if __name__ == "__main__":
    main()
