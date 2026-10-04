import sys
import os
import csv
import time
import pandas as pd
import shapely.wkt
from shapely.geometry import Point
from shapely.strtree import STRtree
from shapely.ops import transform
import pyproj

sys.stdout.reconfigure(encoding="utf-8")

LARGO_CONTENEDOR_M = 1.87
ANCHO_CONTENEDOR_M = 1.50
MARGEN_LATERAL_M = 1.00
SEPARACION_ENTRE_CONT_M = 0.20
RESERVA_OCHAVA_EXTREMO_M = 10.0

ANCHOS_CALLE_POR_TIPO = {
    "CALLE": 12.0,
    "AVENIDA": 26.0,
    "BOULEVARD": 35.0,
    "PASAJE": 7.5,
    "CALLE PEATONAL": 8.0,
    "PASAJE PÚBLICO": 7.0,
    "AUTOPISTA": 40.0,
}

ANCHO_GARAGE_M = 3.0
BUFFER_GARAGE_CONTRA_M = 5.0
BUFFER_GARAGE_FAVOR_M = 1.0
BLOQUEO_GARAGE_TOTAL_M = BUFFER_GARAGE_CONTRA_M + ANCHO_GARAGE_M + BUFFER_GARAGE_FAVOR_M

BLOQUEO_PARADA_M = 15.0
BLOQUEO_PUESTO_M = 5.0
BLOQUEO_POSTE_CPM_M = 2.0


def calcular_capacidad_intervalos(intervalos_libres):
    total_contenedores = 0
    slot_unitario = LARGO_CONTENEDOR_M + SEPARACION_ENTRE_CONT_M
    offset = 2 * MARGEN_LATERAL_M - SEPARACION_ENTRE_CONT_M

    for inicio, fin in intervalos_libres:
        longitud_util = fin - inicio
        if longitud_util >= (LARGO_CONTENEDOR_M + 2 * MARGEN_LATERAL_M):
            n = int((longitud_util - offset) // slot_unitario)
            if n > 0:
                total_contenedores += n

    return total_contenedores


def unir_intervalos(intervalos):
    if not intervalos:
        return []
    intervalos_ordenados = sorted(intervalos, key=lambda x: x[0])
    unificados = [intervalos_ordenados[0]]
    for actual in intervalos_ordenados[1:]:
        prev_inicio, prev_fin = unificados[-1]
        if actual[0] <= prev_fin:
            unificados[-1] = (prev_inicio, max(prev_fin, actual[1]))
        else:
            unificados.append(actual)
    return unificados


def main():
    print(
        "Iniciando generación de dataset con calles transversales en esquinas/ochavas..."
    )
    start_time = time.time()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    datos_dir = os.path.join(base_dir, "datos")

    wgs84_to_utm = pyproj.Transformer.from_crs(
        "EPSG:4326", "EPSG:32721", always_xy=True
    ).transform

    est_path = os.path.join(datos_dir, "estacionamiento_via_publica.csv")
    df_est = pd.read_csv(est_path, sep=";", encoding="utf-8")
    print(f"Total de tramos viales cargados: {len(df_est)}")

    print(
        "Construyendo índice espacial de la red vial para detección de esquinas transversales..."
    )
    all_street_geoms = []
    all_street_names = []

    for idx, row in df_est.iterrows():
        try:
            g = shapely.wkt.loads(row["WKT"])
            g_utm = transform(wgs84_to_utm, g)
            line = g_utm.geoms[0] if g_utm.geom_type == "MultiLineString" else g_utm
            all_street_geoms.append(line)
            all_street_names.append(str(row["calle"]).strip().upper())
        except Exception:
            all_street_geoms.append(None)
            all_street_names.append("")

    valid_geoms = [g for g in all_street_geoms if g is not None]
    valid_names = [
        n for g, n in zip(all_street_geoms, all_street_names) if g is not None
    ]
    tree_calles = STRtree(valid_geoms)

    ciclo_path = os.path.join(datos_dir, "ciclovias.csv")
    ciclo_geoms = []
    if os.path.exists(ciclo_path):
        df_ciclo = pd.read_csv(ciclo_path, encoding="utf-8")
        for geom_str in df_ciclo["geometry"].dropna():
            try:
                g = shapely.wkt.loads(geom_str)
                g_utm = transform(wgs84_to_utm, g)
                ciclo_geoms.append(g_utm)
            except Exception:
                pass
    tree_ciclovias = STRtree(ciclo_geoms) if ciclo_geoms else None

    gar_path = os.path.join(datos_dir, "garajes-comerciales.csv")
    garaje_pts = []
    garaje_alturas = []
    garaje_calles = []
    if os.path.exists(gar_path):
        df_gar = pd.read_csv(gar_path, sep=",", encoding="utf-8")
        for _, row in df_gar.iterrows():
            pt = Point(row["long"], row["lat"])
            pt_utm = transform(wgs84_to_utm, pt)
            garaje_pts.append(pt_utm)
            garaje_alturas.append(row.get("calle_altura", ""))
            garaje_calles.append(str(row.get("calle_nombre", "")).strip().upper())
    tree_garajes = STRtree(garaje_pts) if garaje_pts else None

    caj_path = os.path.join(datos_dir, "cajones-para-carga-y-descarga.csv")
    cajones_pts = []
    cajones_alturas = []
    cajones_calles = []
    if os.path.exists(caj_path):
        df_caj = pd.read_csv(caj_path, encoding="utf-8")
        for _, row in df_caj.iterrows():
            try:
                pt = Point(row["long"], row["lat"])
                pt_utm = transform(wgs84_to_utm, pt)
                cajones_pts.append(pt_utm)
                cajones_alturas.append(row.get("calle_altura", ""))
                cajones_calles.append(str(row.get("calle_nombre", "")).strip().upper())
            except Exception:
                pass
    tree_cajones = STRtree(cajones_pts) if cajones_pts else None

    mob_path = os.path.join(datos_dir, "mobiliario-urbano.csv")
    paradas_pts = []
    paradas_alturas = []
    paradas_calles = []
    puestos_pts = []
    puestos_alturas = []
    puestos_calles = []

    if os.path.exists(mob_path):
        df_mob = pd.read_csv(mob_path, sep=";", encoding="utf-8")
        df_mob["X_num"] = df_mob["X"].astype(str).str.replace(",", ".").astype(float)
        df_mob["Y_num"] = df_mob["Y"].astype(str).str.replace(",", ".").astype(float)
        df_mob = df_mob.dropna(subset=["X_num", "Y_num"])

        for _, row in df_mob.iterrows():
            elem = str(row["ELEMENTO"]).upper()
            pt = Point(row["X_num"], row["Y_num"])
            pt_utm = transform(wgs84_to_utm, pt)
            alt = row.get("Altura", "")
            c_nom = str(row.get("calle", "")).strip().upper()

            if "REFUGIO" in elem or "BUS" in elem:
                paradas_pts.append(pt_utm)
                paradas_alturas.append(alt)
                paradas_calles.append(c_nom)
            elif "PUESTO" in elem:
                puestos_pts.append(pt_utm)
                puestos_alturas.append(alt)
                puestos_calles.append(c_nom)

    tree_paradas = STRtree(paradas_pts) if paradas_pts else None
    tree_puestos = STRtree(puestos_pts) if puestos_pts else None

    print("Procesando tramos, identificando esquinas transversales y restricciones...")
    registros_unificados = []

    for idx, row in df_est.iterrows():
        geom_utm = all_street_geoms[idx]
        if geom_utm is None:
            continue

        longitud_total = round(geom_utm.length, 2)
        if longitud_total < 5.0:
            continue

        calle_nombre = str(row["calle"]).strip()
        calle_nombre_upper = calle_nombre.upper()

        lado_raw = (
            str(row["lado"]).strip().upper() if pd.notna(row["lado"]) else "DESCONOCIDO"
        )
        if "IZQ" in lado_raw:
            acera_lado = "IZQUIERDO"
        elif "DER" in lado_raw:
            acera_lado = "DERECHO"
        elif "AMB" in lado_raw:
            acera_lado = "AMBOS"
        else:
            acera_lado = lado_raw

        tipo_via = (
            str(row["tipo_calle"]).upper() if pd.notna(row["tipo_calle"]) else "CALLE"
        )

        regla_gen = (
            str(row["regla_general"]).upper() if pd.notna(row["regla_general"]) else ""
        )
        normativa = str(row["normativa"]).upper() if pd.notna(row["normativa"]) else ""

        coords = list(geom_utm.coords)
        pt_inicio = Point(coords[0])
        pt_fin = Point(coords[-1])

        cand_ini_idx = tree_calles.query(pt_inicio.buffer(15.0))
        cand_ini_valid = [
            i
            for i in cand_ini_idx
            if valid_names[i] != calle_nombre_upper
            and valid_geoms[i].distance(pt_inicio) <= 15.0
        ]
        calles_trans_ini = sorted(list(set(valid_names[i] for i in cand_ini_valid)))

        cand_fin_idx = tree_calles.query(pt_fin.buffer(15.0))
        cand_fin_valid = [
            i
            for i in cand_fin_idx
            if valid_names[i] != calle_nombre_upper
            and valid_geoms[i].distance(pt_fin) <= 15.0
        ]
        calles_trans_fin = sorted(list(set(valid_names[i] for i in cand_fin_valid)))

        tiene_esquina_ini = len(calles_trans_ini) > 0
        tiene_esquina_fin = len(calles_trans_fin) > 0

        if (
            tiene_esquina_ini
            and tiene_esquina_fin
            and set(calles_trans_ini) == set(calles_trans_fin)
        ):
            d_ini = min(valid_geoms[i].distance(pt_inicio) for i in cand_ini_valid)
            d_fin = min(valid_geoms[i].distance(pt_fin) for i in cand_fin_valid)
            if d_ini <= d_fin:
                tiene_esquina_fin = False
            else:
                tiene_esquina_ini = False

        intervalos_bloqueados = []

        if tiene_esquina_ini:
            bloq_ini = min(RESERVA_OCHAVA_EXTREMO_M, longitud_total)
            intervalos_bloqueados.append((0.0, bloq_ini))

        if tiene_esquina_fin:
            bloq_fin_desde = max(
                0.0, round(longitud_total - RESERVA_OCHAVA_EXTREMO_M, 2)
            )
            intervalos_bloqueados.append((bloq_fin_desde, longitud_total))

        if tree_garajes is not None:
            cand_gar = tree_garajes.query(geom_utm.buffer(12.0))
            for g_idx in cand_gar:
                g_calle = garaje_calles[g_idx]
                if (
                    g_calle
                    and g_calle != calle_nombre_upper
                    and g_calle not in calle_nombre_upper
                    and calle_nombre_upper not in g_calle
                ):
                    continue
                g_pt = garaje_pts[g_idx]
                if geom_utm.distance(g_pt) <= 10.0:
                    s = geom_utm.project(g_pt)
                    ini = max(0.0, s - BUFFER_GARAGE_CONTRA_M)
                    fin = min(
                        longitud_total, s + ANCHO_GARAGE_M + BUFFER_GARAGE_FAVOR_M
                    )
                    intervalos_bloqueados.append((ini, fin))

        if tree_paradas is not None:
            cand_par = tree_paradas.query(geom_utm.buffer(12.0))
            for p_idx in cand_par:
                p_calle = paradas_calles[p_idx]
                if (
                    p_calle
                    and p_calle != calle_nombre_upper
                    and p_calle not in calle_nombre_upper
                    and calle_nombre_upper not in p_calle
                ):
                    continue
                p_pt = paradas_pts[p_idx]
                if geom_utm.distance(p_pt) <= 10.0:
                    s = geom_utm.project(p_pt)
                    ini = max(0.0, s - (BLOQUEO_PARADA_M / 2.0))
                    fin = min(longitud_total, s + (BLOQUEO_PARADA_M / 2.0))
                    intervalos_bloqueados.append((ini, fin))

        if tree_puestos is not None:
            cand_pst = tree_puestos.query(geom_utm.buffer(12.0))
            for pst_idx in cand_pst:
                pst_calle = puestos_calles[pst_idx]
                if (
                    pst_calle
                    and pst_calle != calle_nombre_upper
                    and pst_calle not in calle_nombre_upper
                    and calle_nombre_upper not in pst_calle
                ):
                    continue
                pst_pt = puestos_pts[pst_idx]
                if geom_utm.distance(pst_pt) <= 10.0:
                    s = geom_utm.project(pst_pt)
                    ini = max(0.0, s - (BLOQUEO_PUESTO_M / 2.0))
                    fin = min(longitud_total, s + (BLOQUEO_PUESTO_M / 2.0))
                    intervalos_bloqueados.append((ini, fin))

        if tree_cajones is not None:
            cand_caj = tree_cajones.query(geom_utm.buffer(12.0))
            for c_idx in cand_caj:
                c_calle = cajones_calles[c_idx]
                if (
                    c_calle
                    and c_calle != calle_nombre_upper
                    and c_calle not in calle_nombre_upper
                    and calle_nombre_upper not in c_calle
                ):
                    continue
                c_pt = cajones_pts[c_idx]
                if geom_utm.distance(c_pt) <= 10.0:
                    s = geom_utm.project(c_pt)
                    ini = max(0.0, s - 5.0)
                    fin = min(longitud_total, s + 5.0)
                    intervalos_bloqueados.append((ini, fin))

        tiene_ciclovia = False
        if tree_ciclovias is not None:
            candidatos_ciclo = tree_ciclovias.query(geom_utm.buffer(8.0))
            for c_idx in candidatos_ciclo:
                if geom_utm.distance(ciclo_geoms[c_idx]) <= 6.0:
                    tiene_ciclovia = True
                    break

        # En CABA las ciclovías corren principalmente por el margen izquierdo.
        # Si este tramo corresponde a la acera DERECHA, la ciclovía izquierda NO anula la calzada derecha.
        ciclovia_afecta_este_lado = tiene_ciclovia and (acera_lado != "DERECHO")

        if normativa != "":
            prohibido_estacionar = "PROHIBIDO" in normativa
        else:
            prohibido_estacionar = "PROHIBIDO" in regla_gen

        es_peatonal = "PEATONAL" in tipo_via or "PASAJE" in tipo_via

        permite_calzada = (
            (not prohibido_estacionar)
            and (not ciclovia_afecta_este_lado)
            and (not es_peatonal)
        )

        # Resolución 1/SSHU/19 - Ubicación en Acera (Excepción):
        # Solo se permite subirlos a la acera en avenidas o calles donde esté estrictamente prohibido estacionar
        # las 24 horas, o cuando las condiciones técnicas de la calzada impidan la recolección.
        # NUNCA en calles peatonales o pasajes donde el camión no opera o no hay vereda reglamentaria.
        es_via_apta_acera = (
            tipo_via in ["AVENIDA", "BOULEVARD"]
            or (tipo_via == "CALLE" and ANCHOS_CALLE_POR_TIPO.get(tipo_via, 12.0) >= 10.0)
        )
        permite_acera = (not permite_calzada) and (not es_peatonal) and es_via_apta_acera

        intervalos_bloq_unificados = unir_intervalos(intervalos_bloqueados)
        longitud_bloqueada = sum(fin - ini for ini, fin in intervalos_bloq_unificados)
        longitud_bloqueada = min(longitud_total, round(longitud_bloqueada, 2))

        intervalos_libres = []
        cursor = 0.0
        for b_ini, b_fin in intervalos_bloq_unificados:
            if b_ini > cursor:
                intervalos_libres.append((cursor, b_ini))
            cursor = max(cursor, b_fin)
        if cursor < longitud_total:
            intervalos_libres.append((cursor, longitud_total))

        # CÁLCULO DE CAPACIDAD BAJO RESOLUCIÓN CONJUNTA N° 1/SSHU/19:
        # 1. Calzada: Límite estricto de 4 metros lineales = MÁXIMO 2 CONTENEDORES DE 3.200L
        if permite_calzada:
            espacio_slots = calcular_capacidad_intervalos(intervalos_libres)
            cap_calzada = min(2, espacio_slots)
        else:
            cap_calzada = 0

        # 2. Acera (Vereda Excepcional): Límite estricto de 11 metros lineales = MÁXIMO 2 CONTENEDORES GRANDES DE 3.200L
        # Requiere que exista espacio libre continuo no bloqueado por garajes, paradas, cajones u ochavas.
        if permite_acera:
            espacio_slots_acera = calcular_capacidad_intervalos(intervalos_libres)
            cap_acera = min(2, espacio_slots_acera)
        else:
            cap_acera = 0

        # Capacidad física reglamentaria total del tramo (valores factibles: 0, 1 o 2)
        max_contenedores = max(cap_calzada, cap_acera)

        registros_unificados.append(
            {
                "segment_id": idx,
                "calle_nombre": calle_nombre,
                "tipo_via": tipo_via,
                "acera_lado": acera_lado,
                "ancho_calle_m": ANCHOS_CALLE_POR_TIPO.get(tipo_via, 12.0),
                "permite_calzada": "SI" if permite_calzada else "NO",
                "permite_acera": "SI" if permite_acera else "NO",
                "longitud_total_m": longitud_total,
                "espacio_bloqueado_m": longitud_bloqueada,
                "espacio_disponible_m": round(
                    max(0.0, longitud_total - longitud_bloqueada), 2
                ),
                "MAX_CONTENEDORES": max_contenedores,
                "geometry_wkt": str(row["WKT"]),
            }
        )

    df_salida = pd.DataFrame(registros_unificados)
    salida_path = os.path.join(datos_dir, "restricciones_contenedores.csv")
    df_salida.to_csv(
        salida_path, sep=";", index=False, encoding="utf-8", quoting=csv.QUOTE_ALL
    )

    elapsed = round(time.time() - start_time, 2)
    print(f"\nArchivo regenerado con éxito en {elapsed} segundos.")
    print(f"Ruta: {salida_path}")
    print(f"Total de registros: {len(df_salida)}")


if __name__ == "__main__":
    main()
