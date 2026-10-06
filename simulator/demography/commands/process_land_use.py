"""
simulator/demography/commands/process_land_use.py
─────────────────────────────────────────────────
Comando para procesar el relevamiento de usos del suelo de BA Data,
geocodificar las parcelas utilizando la traza de calles (desde PostGIS o fallback GeoJSON),
asignarlas a sus radios censales y derivar perfiles de demanda para el simulador.

Orden de ingesta:
  1. PostGIS (tabla 'calles') si la base de datos está disponible.
  2. Fallback offline a 'datos/digital_twin/calles.geojson'.

Lee los umbrales y multiplicadores base desde:
  simulator/config/zone_profiles.yaml

Uso como CLI:
    uv run python -m simulator.demography.commands.process_land_use
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml
from shapely import wkt
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

# Raiz del repo (simulator/demography/commands/ -> 3 niveles arriba de simulator/).
ROOT = Path(__file__).resolve().parents[3]
# El mismo YAML de perfiles que usa el ZoneClassifier.
ZONE_PROFILES_YAML = ROOT / "simulator" / "config" / "zone_profiles.yaml"

TITULOS_REGEX = re.compile(
    r"\b(AV|AVENIDA|CALLE|PASAJE|PJE|AUT|AUTOPISTA|BV|BOULEVARD|PQUE|PARQUE|DR|DRA|DOCTOR|DOCTORA|"
    r"GRAL|GENERAL|CNEL|CORONEL|TTE|TENIENTE|CAP|CAPITAN|ALMTE|ALMIRANTE|BRIG|BRIGADIER|"
    r"CBO|CABO|SGTO|SARGENTO|MY|MAYOR|ING|INGENIERO|ARQ|ARQUITECTO|PROF|PROFESOR|"
    r"PBTRO|PRESBITERO|MONS|MONSENOR|STA|SANTA|STO|SANTO|SAN|PRES|PRESIDENTE|"
    r"GOB|GOBERNADOR|INT|INTENDENTE|DON|DONA|HNA|HERMANA|HNO|HERMANO)\b",
    re.IGNORECASE,
)

KNOWN_ALIASES = {
    "AGURO": "AGUERO",
    "GUMES": "GUEMES",
    "CURAPALIGU": "CURAPALIGUE",
    "AV CURAPALIGU": "CURAPALIGUE",
    "ECHAGU": "ECHAGUE",
    "PEDRO ECHAGU": "PEDRO ECHAGUE",
    "RICCHIERI": "RICHIERI",
    "TENIENTE GENERAL PABLO RICCHIERI": "RICHIERI",
    "JOSE PABLO TORCUATO BATLLE Y ORDONEZ": "BATLLE Y ORDONEZ",
    "AV JOSE PABLO TORCUATO BATLLE Y ORDONEZ": "BATLLE Y ORDONEZ",
}


def _resolve_file(filename: str) -> Path | None:
    candidates = [
        ROOT / "datos" / "digital_twin" / filename,
        ROOT / "datos" / "simulator" / "demography" / filename,
        ROOT / "db" / "datos" / filename,
        ROOT / "datos" / filename,
        Path("/app/datos/digital_twin") / filename,
        Path("/app/datos/simulator/demography") / filename,
        Path("/app/db/datos") / filename,
        Path("/app/datos") / filename,
        Path("/datos") / filename,
    ]
    return next((p for p in candidates if p.exists()), None)


def _load_yaml_config(
    config_path: Path | None,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    thresholds = {
        "industrial_min_pct": 25.0,
        "office_min_pct": 15.0,
        "commercial_min_pct": 30.0,
        "residential_dominant_min_pct": 35.0,
        "residential_combined_min_pct": 40.0,
    }
    category_weights = {
        "residential_multifamily": {"demand_multiplier": 1.10, "weekend_factor": 0.60},
        "residential_singlefamily": {"demand_multiplier": 0.95, "weekend_factor": 0.85},
        "commercial": {"demand_multiplier": 1.40, "weekend_factor": 1.20},
        "office": {"demand_multiplier": 0.85, "weekend_factor": 0.10},
        "industrial": {"demand_multiplier": 0.65, "weekend_factor": 0.20},
        "other": {"demand_multiplier": 0.50, "weekend_factor": 0.75},
    }

    if config_path and config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if "classification_thresholds" in data:
                thresholds.update(data["classification_thresholds"])
            if "category_demand_weights" in data:
                for cat, val in data["category_demand_weights"].items():
                    category_weights[cat] = {
                        "demand_multiplier": float(val.get("demand_multiplier", 1.0)),
                        "weekend_factor": float(val.get("weekend_factor", 0.75)),
                    }
        except Exception as err:
            logger.warning(
                f"Error al leer {config_path}: {err}. Usando valores por defecto."
            )

    return thresholds, category_weights


def _barrios_by_radio(
    radio_codes: list[str], geometries: list, barrios_path: Path | None
) -> dict[str, str]:
    """
    Barrio de cada radio censal: el del polígono de barrio (barrios.geojson) que
    cubre la mayor parte del radio, en mayúsculas como en el relevamiento.
    Devuelve {} si no está el archivo.
    """
    if not barrios_path or not barrios_path.exists():
        print(
            "[WARNING] No se encontró barrios.geojson: el barrio de cada radio "
            "sale de sus parcelas."
        )
        return {}

    with open(barrios_path, encoding="utf-8") as fh:
        data = json.load(fh)
    polygons, names = [], []
    for feature in data.get("features", []):
        name = str((feature.get("properties") or {}).get("nombre") or "").strip()
        if not name or not feature.get("geometry"):
            continue
        polygon = shape(feature["geometry"])
        polygons.append(polygon if polygon.is_valid else polygon.buffer(0))
        names.append(name.upper())

    tree = STRtree(polygons)
    barrios: dict[str, str] = {}
    for code, radio in zip(radio_codes, geometries):
        best_name, best_area = None, 0.0
        for i in tree.query(radio):
            area = polygons[i].intersection(radio).area
            if area > best_area:
                best_name, best_area = names[i], area
        if best_name:
            barrios[code] = best_name
    return barrios


def _norm_text(text: str) -> str:
    if not text:
        return ""
    text = (
        unicodedata.normalize("NFKD", text)
        .encode("ASCII", "ignore")
        .decode("utf-8")
        .upper()
    )
    text = re.sub(r"[^A-Z0-9\s]", " ", text)
    cleaned = " ".join(text.split())
    return KNOWN_ALIASES.get(cleaned, cleaned)


def _extract_significant_tokens(text: str) -> tuple[str, ...]:
    n = _norm_text(text)
    n = TITULOS_REGEX.sub(" ", n)
    return tuple(sorted([t for t in n.split() if len(t) > 1 or t.isdigit()]))


def _categorize_parcel(tipo1: str, tipo2: str, pisos: str) -> str:
    t1 = tipo1.strip().upper()
    t2 = tipo2.strip().upper()

    if t1 in ("RESIDENCIAL", "BARRIO POPULAR"):
        if t2 == "MULTIFAMILIAR":
            return "residential_multifamily"
        elif t2 == "UNIFAMILIAR":
            return "residential_singlefamily"
        else:
            p = pisos.strip()
            if p.isdigit() and int(p) > 2:
                return "residential_multifamily"
            if p.startswith(">") or "+" in p:
                return "residential_multifamily"
            return "residential_singlefamily"

    if t1 in (
        "UNICOMERCIAL",
        "MULTICOMERCIAL",
        "GARAGE COMERCIAL",
        "ESTACION DE SERVICIO",
    ):
        return "commercial"

    if t1 in ("OFICINAS", "EQUIPAMIENTO"):
        return "office"

    if t1 in ("INDUSTRIAL",):
        return "industrial"

    return "other"


def _derive_zone_type(
    res_multi_pct: float,
    res_single_pct: float,
    commercial_pct: float,
    office_pct: float,
    industrial_pct: float,
    thresholds: dict[str, float],
) -> str:
    if industrial_pct > thresholds["industrial_min_pct"]:
        return "industrial"
    if office_pct > thresholds["office_min_pct"]:
        return "office"
    if commercial_pct > thresholds["commercial_min_pct"]:
        return "commercial"

    res_dominant = thresholds["residential_dominant_min_pct"]
    if res_multi_pct > res_dominant and res_multi_pct >= res_single_pct:
        return "residential_multifamily"
    if res_single_pct > res_dominant and res_single_pct > res_multi_pct:
        return "residential_singlefamily"

    res_combined = thresholds["residential_combined_min_pct"]
    if (res_multi_pct + res_single_pct) > res_combined:
        return (
            "residential_multifamily"
            if res_multi_pct >= res_single_pct
            else "residential_singlefamily"
        )

    return "mixed"


def _compute_demand_multiplier(
    res_multi_pct: float,
    res_single_pct: float,
    commercial_pct: float,
    office_pct: float,
    industrial_pct: float,
    category_weights: dict[str, dict[str, float]],
) -> float:
    other_pct = max(
        0.0,
        100.0
        - res_multi_pct
        - res_single_pct
        - commercial_pct
        - office_pct
        - industrial_pct,
    )
    w_multi = (res_multi_pct / 100.0) * category_weights["residential_multifamily"][
        "demand_multiplier"
    ]
    w_single = (res_single_pct / 100.0) * category_weights["residential_singlefamily"][
        "demand_multiplier"
    ]
    w_comm = (commercial_pct / 100.0) * category_weights["commercial"][
        "demand_multiplier"
    ]
    w_off = (office_pct / 100.0) * category_weights["office"]["demand_multiplier"]
    w_ind = (industrial_pct / 100.0) * category_weights["industrial"][
        "demand_multiplier"
    ]
    w_other = (other_pct / 100.0) * category_weights["other"]["demand_multiplier"]
    return round(w_multi + w_single + w_comm + w_off + w_ind + w_other, 4)


def _compute_weekend_factor(
    res_multi_pct: float,
    res_single_pct: float,
    commercial_pct: float,
    office_pct: float,
    industrial_pct: float,
    category_weights: dict[str, dict[str, float]],
) -> float:
    total = (
        res_multi_pct + res_single_pct + commercial_pct + office_pct + industrial_pct
    )
    if total <= 0:
        return category_weights["other"]["weekend_factor"]
    w_multi = (res_multi_pct / total) * category_weights["residential_multifamily"][
        "weekend_factor"
    ]
    w_single = (res_single_pct / total) * category_weights["residential_singlefamily"][
        "weekend_factor"
    ]
    w_comm = (commercial_pct / total) * category_weights["commercial"]["weekend_factor"]
    w_off = (office_pct / total) * category_weights["office"]["weekend_factor"]
    w_ind = (industrial_pct / total) * category_weights["industrial"]["weekend_factor"]
    return round(w_multi + w_single + w_comm + w_off + w_ind, 4)


async def _load_streets_from_db_or_geojson(calles_path: Path | None, session=None):
    """
    Carga el trazado de calles priorizando la tabla 'calles' de PostGIS.
    Si la base de datos no está disponible, hace fallback a 'calles.geojson'.
    """
    calles_dict = defaultdict(list)
    calles_token_dict = defaultdict(list)
    loaded_from = "none"

    # 1. Intentar consultar PostGIS
    if session is not None:
        try:
            from sqlalchemy import text

            stmt = text(
                "SELECT nomoficial, nom_mapa, alt_izqini, alt_izqfin, alt_derini, alt_derfin, ST_AsText(geom) AS geom_wkt FROM public.calles"
            )
            result = await session.execute(stmt)
            rows = result.fetchall()
            if rows:
                for row in rows:
                    nomoficial = row[0] or ""
                    nom_mapa = row[1] or ""
                    izq_i = int(float(row[2] or 0))
                    izq_f = int(float(row[3] or 0))
                    der_i = int(float(row[4] or 0))
                    der_f = int(float(row[5] or 0))
                    geom_wkt = row[6]
                    if not geom_wkt:
                        continue
                    try:
                        geom = wkt.loads(geom_wkt)
                        vals = [x for x in (izq_i, izq_f, der_i, der_f) if x > 0]
                        a_min = min(vals) if vals else 0
                        a_max = max(izq_i, izq_f, der_i, der_f)

                        names = set([_norm_text(nomoficial), _norm_text(nom_mapa)])
                        if "," in nomoficial:
                            parts = nomoficial.split(",")
                            if len(parts) == 2:
                                names.add(_norm_text(f"{parts[1]} {parts[0]}"))
                                names.add(_norm_text(f"{parts[0]} {parts[1]}"))

                        for n in names:
                            if n:
                                calles_dict[n].append((a_min, a_max, geom))

                        for raw_name in (nomoficial, nom_mapa):
                            tokens = _extract_significant_tokens(raw_name)
                            if tokens:
                                calles_token_dict[tokens].append((a_min, a_max, geom))
                                if len(tokens) >= 2:
                                    for t in tokens:
                                        if len(t) >= 4:
                                            calles_token_dict[(t,)].append(
                                                (a_min, a_max, geom)
                                            )
                    except Exception:
                        continue
                loaded_from = "postgis_db"
                print(
                    f"[INFO] Traza de calles cargada desde PostGIS (tabla 'calles'): {len(rows):,} tramos."
                )
        except Exception:
            pass

    # 2. Fallback a GeoJSON si no se cargó de la DB
    if loaded_from != "postgis_db" and calles_path and calles_path.exists():
        with open(calles_path, mode="r", encoding="utf-8") as fh:
            calles_geo = json.load(fh)

        for feat in calles_geo.get("features", []):
            props = feat.get("properties", {})
            geom_obj = feat.get("geometry")
            if not geom_obj:
                continue
            try:
                geom = shape(geom_obj)
                nomoficial = props.get("nomoficial") or ""
                nom_mapa = props.get("nom_mapa") or ""
                izq_i = int(float(props.get("alt_izqini") or 0))
                izq_f = int(float(props.get("alt_izqfin") or 0))
                der_i = int(float(props.get("alt_derini") or 0))
                der_f = int(float(props.get("alt_derfin") or 0))

                vals = [x for x in (izq_i, izq_f, der_i, der_f) if x > 0]
                a_min = min(vals) if vals else 0
                a_max = max(izq_i, izq_f, der_i, der_f)

                names = set([_norm_text(nomoficial), _norm_text(nom_mapa)])
                if "," in nomoficial:
                    parts = nomoficial.split(",")
                    if len(parts) == 2:
                        names.add(_norm_text(f"{parts[1]} {parts[0]}"))
                        names.add(_norm_text(f"{parts[0]} {parts[1]}"))

                for n in names:
                    if n:
                        calles_dict[n].append((a_min, a_max, geom))

                for raw_name in (nomoficial, nom_mapa):
                    tokens = _extract_significant_tokens(raw_name)
                    if tokens:
                        calles_token_dict[tokens].append((a_min, a_max, geom))
                        if len(tokens) >= 2:
                            for t in tokens:
                                if len(t) >= 4:
                                    calles_token_dict[(t,)].append((a_min, a_max, geom))
            except Exception:
                continue
        loaded_from = "geojson_fallback"
        print(
            f"[INFO] Traza de calles cargada desde GeoJSON ({calles_path.name}): {len(calles_geo.get('features', [])):,} tramos."
        )

    return calles_dict, calles_token_dict, loaded_from


async def process_land_use_async(
    session=None,
    land_use_csv: str | Path | None = None,
    calles_geojson: str | Path | None = None,
    radios_csv: str | Path | None = None,
    config_yaml: str | Path | None = None,
    output_radio_csv: str | Path | None = None,
    output_barrio_csv: str | Path | None = None,
    force: bool = False,
) -> dict[str, object]:
    """
    Función asíncrona de procesamiento de usos del suelo con soporte para sesión PostGIS.
    """
    t0 = time.time()

    land_use_path = (
        Path(land_use_csv)
        if land_use_csv
        else _resolve_file("relevamiento-usos-del-suelo-2022-2024.csv")
    )
    calles_path = (
        Path(calles_geojson) if calles_geojson else _resolve_file("calles.geojson")
    )
    radios_path = (
        Path(radios_csv) if radios_csv else _resolve_file("radios_caba_filtrado.csv")
    )

    config_path = Path(config_yaml) if config_yaml else ZONE_PROFILES_YAML

    out_radio = (
        Path(output_radio_csv)
        if output_radio_csv
        else (ROOT / "datos" / "simulator" / "land_use" / "land_use_by_radio.csv")
    )
    out_barrio = (
        Path(output_barrio_csv)
        if output_barrio_csv
        else (ROOT / "datos" / "simulator" / "land_use" / "land_use_by_barrio.csv")
    )

    if out_radio.exists() and not force:
        print(
            f"[INFO] '{out_radio.name}' ya existe. Omitiendo reprocesamiento (usar --force para regenerar)."
        )
        return {"status": "already_exists", "output_radio": str(out_radio)}

    if not land_use_path or not land_use_path.exists():
        print(
            f"[WARNING] No se encontró el archivo de usos del suelo: {land_use_path}. Omitiendo."
        )
        return {"status": "skipped", "reason": "land_use_csv_missing"}

    if not radios_path or not radios_path.exists():
        print(
            f"[WARNING] No se encontró el archivo de radios: {radios_path}. Omitiendo."
        )
        return {"status": "skipped", "reason": "radios_csv_missing"}

    thresholds, category_weights = _load_yaml_config(config_path)

    # 1. Cargar radios censales
    radio_codes, radio_depts, geometries = [], [], []
    with open(radios_path, mode="r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            wkt_str = row.get("Geometría en WKT")
            if not wkt_str:
                continue
            try:
                geom = wkt.loads(wkt_str)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                code = str(row.get("Código de radio") or "").strip()
                dept = str(row.get("Nombre de departamento") or "").strip()
                radio_codes.append(code)
                radio_depts.append(dept)
                geometries.append(geom)
            except Exception:
                continue

    tree = STRtree(geometries)
    radio_dept_map = dict(zip(radio_codes, radio_depts))
    # El barrio de cada radio sale del mapa de barrios. Las parcelas del
    # relevamiento se ubican por dirección y algunas caen en un radio vecino, y
    # los radios sin parcelas quedaban con el nombre de su comuna.
    radio_barrio_map = _barrios_by_radio(
        radio_codes, geometries, _resolve_file("barrios.geojson")
    )

    # 2. Cargar calles (PostGIS primero, fallback GeoJSON)
    calles_dict, calles_token_dict, source = await _load_streets_from_db_or_geojson(
        calles_path, session=session
    )

    if not calles_dict:
        print(
            "[ERROR] No se pudo cargar el trazado de calles ni de PostGIS ni de GeoJSON. Abortando."
        )
        return {"status": "error", "reason": "calles_not_loaded"}

    def geocode(calle_str: str, p_num: int) -> Point | None:
        c_norm = _norm_text(calle_str)
        tramos = calles_dict.get(c_norm)
        if not tramos:
            tokens = _extract_significant_tokens(calle_str)
            tramos = calles_token_dict.get(tokens)
            if not tramos and tokens:
                longest = max(tokens, key=len)
                if len(longest) >= 4:
                    tramos = calles_token_dict.get((longest,))
        if not tramos:
            return None
        for a_min, a_max, g in tramos:
            if a_min <= p_num <= a_max:
                frac = (p_num - a_min) / float(a_max - a_min) if a_max > a_min else 0.5
                return g.interpolate(frac, normalized=True)
        closest = min(tramos, key=lambda t: min(abs(t[0] - p_num), abs(t[1] - p_num)))
        return closest[2].interpolate(0.5, normalized=True)

    # 3. Procesar parcelas
    radio_counts = defaultdict(lambda: defaultdict(int))
    barrio_counts = defaultdict(lambda: defaultdict(int))
    radio_barrios_seen = defaultdict(lambda: defaultdict(int))
    total_active, matched_parcels = 0, 0

    with open(land_use_path, mode="r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("ESTADO", "").strip().upper() != "ACTIVO":
                continue
            total_active += 1
            barrio_raw = row.get("BARRIO", "").strip().upper()
            puerta_raw = row.get("PUERTA", "").strip()
            cat = _categorize_parcel(
                row.get("TIPO1", ""),
                row.get("TIPO2", ""),
                row.get("PISOS", ""),
            )
            if barrio_raw:
                barrio_counts[barrio_raw][cat] += 1
            if not puerta_raw.isdigit():
                continue
            pt = geocode(row.get("CALLE", ""), int(puerta_raw))
            if pt is None:
                continue
            idxs = tree.query(pt, predicate="intersects")
            r_code = (
                radio_codes[idxs[0]]
                if len(idxs) > 0
                else (
                    radio_codes[tree.nearest(pt)]
                    if tree.nearest(pt) is not None
                    else None
                )
            )
            if r_code is None:
                continue
            radio_counts[r_code][cat] += 1
            if barrio_raw:
                radio_barrios_seen[r_code][barrio_raw] += 1
            matched_parcels += 1

    # 4. Generar radio rows
    radio_rows = []
    for r_code in radio_codes:
        dept_name = radio_dept_map.get(r_code, "UNKNOWN")
        seen_b = radio_barrios_seen.get(r_code, {})
        barrio = radio_barrio_map.get(r_code) or (
            max(seen_b.items(), key=lambda x: x[1])[0] if seen_b else dept_name
        )
        counts = radio_counts.get(r_code, {})
        total_p = sum(counts.values())

        if total_p > 0:

            def p_pct(c: str) -> float:
                return round(counts.get(c, 0) / total_p * 100.0, 2)

            rm = p_pct("residential_multifamily")
            rs = p_pct("residential_singlefamily")
            cm = p_pct("commercial")
            of = p_pct("office")
            ind = p_pct("industrial")
        else:
            rm, rs, cm, of, ind = 40.0, 40.0, 10.0, 5.0, 5.0

        zt = _derive_zone_type(rm, rs, cm, of, ind, thresholds)
        dm = _compute_demand_multiplier(rm, rs, cm, of, ind, category_weights)
        wf = _compute_weekend_factor(rm, rs, cm, of, ind, category_weights)

        radio_rows.append(
            {
                "radio_code": r_code,
                "barrio": barrio,
                "department_name": dept_name,
                "zone_type": zt,
                "res_multifamily_pct": rm,
                "res_singlefamily_pct": rs,
                "commercial_pct": cm,
                "office_pct": of,
                "industrial_pct": ind,
                "total_parcelas": total_p,
                "demand_multiplier": dm,
                "weekend_factor": wf,
            }
        )

    for target_path in set(
        [out_radio, ROOT / "datos" / "simulator" / "land_use" / "land_use_by_radio.csv"]
    ):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "radio_code",
            "barrio",
            "department_name",
            "zone_type",
            "res_multifamily_pct",
            "res_singlefamily_pct",
            "commercial_pct",
            "office_pct",
            "industrial_pct",
            "total_parcelas",
            "demand_multiplier",
            "weekend_factor",
        ]
        with open(target_path, mode="w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(radio_rows)

    # 5. Generar barrio rows (rollup)
    barrio_rows = []
    for barrio, counts in sorted(barrio_counts.items()):
        total_b = sum(counts.values())
        if total_b == 0:
            continue

        def b_pct(c: str) -> float:
            return round(counts.get(c, 0) / total_b * 100.0, 2)

        rm = b_pct("residential_multifamily")
        rs = b_pct("residential_singlefamily")
        cm = b_pct("commercial")
        of = b_pct("office")
        ind = b_pct("industrial")
        zt = _derive_zone_type(rm, rs, cm, of, ind, thresholds)
        dm = _compute_demand_multiplier(rm, rs, cm, of, ind, category_weights)
        wf = _compute_weekend_factor(rm, rs, cm, of, ind, category_weights)
        barrio_rows.append(
            {
                "barrio": barrio,
                "zone_type": zt,
                "res_multifamily_pct": rm,
                "res_singlefamily_pct": rs,
                "commercial_pct": cm,
                "office_pct": of,
                "industrial_pct": ind,
                "total_parcelas": total_b,
                "demand_multiplier": dm,
                "weekend_factor": wf,
            }
        )

    for target_path in set(
        [
            out_barrio,
            ROOT / "datos" / "simulator" / "land_use" / "land_use_by_barrio.csv",
        ]
    ):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "barrio",
            "zone_type",
            "res_multifamily_pct",
            "res_singlefamily_pct",
            "commercial_pct",
            "office_pct",
            "industrial_pct",
            "total_parcelas",
            "demand_multiplier",
            "weekend_factor",
        ]
        with open(target_path, mode="w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(barrio_rows)

    elapsed = time.time() - t0
    match_pct = (matched_parcels / total_active * 100) if total_active > 0 else 0
    print(
        f"[OK] Usos del suelo procesados exitosamente: {len(radio_rows)} radios censales "
        f"({matched_parcels:,}/{total_active:,} parcelas asignadas, {match_pct:.2f}%) en {elapsed:.2f}s."
    )

    return {
        "status": "success",
        "radios_count": len(radio_rows),
        "parcels_matched": matched_parcels,
        "match_percentage": round(match_pct, 2),
        "elapsed_seconds": round(elapsed, 2),
        "streets_source": source,
    }


def process_land_use_command(
    session=None,
    land_use_csv: str | Path | None = None,
    calles_geojson: str | Path | None = None,
    radios_csv: str | Path | None = None,
    config_yaml: str | Path | None = None,
    output_radio_csv: str | Path | None = None,
    output_barrio_csv: str | Path | None = None,
    force: bool = False,
) -> dict[str, object]:
    """
    Función síncrona / CLI wrapper para invocar el procesamiento.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Si ya estamos en un loop (ej: durante seed.py async), usamos create_task o await
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(
                lambda: asyncio.run(
                    process_land_use_async(
                        session=session,
                        land_use_csv=land_use_csv,
                        calles_geojson=calles_geojson,
                        radios_csv=radios_csv,
                        config_yaml=config_yaml,
                        output_radio_csv=output_radio_csv,
                        output_barrio_csv=output_barrio_csv,
                        force=force,
                    )
                )
            ).result()
    else:
        return asyncio.run(
            process_land_use_async(
                session=session,
                land_use_csv=land_use_csv,
                calles_geojson=calles_geojson,
                radios_csv=radios_csv,
                config_yaml=config_yaml,
                output_radio_csv=output_radio_csv,
                output_barrio_csv=output_barrio_csv,
                force=force,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Comando de procesamiento de usos del suelo por radio censal"
    )
    parser.add_argument(
        "--land-use", default=None, help="Ruta al CSV de usos del suelo"
    )
    parser.add_argument("--calles", default=None, help="Ruta a calles.geojson")
    parser.add_argument("--radios", default=None, help="Ruta al CSV de radios censales")
    parser.add_argument("--config", default=None, help="Ruta al YAML de configuración")
    parser.add_argument("--force", action="store_true", help="Forzar reprocesamiento")
    args = parser.parse_args()

    process_land_use_command(
        land_use_csv=args.land_use,
        calles_geojson=args.calles,
        radios_csv=args.radios,
        config_yaml=args.config,
        force=args.force,
    )


if __name__ == "__main__":
    main()
