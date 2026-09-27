import sys
import os
import json
import time
import pandas as pd
import numpy as np
import shapely.wkt
from shapely.geometry import Point, LineString, MultiLineString
from shapely.strtree import STRtree
from shapely.ops import transform
import pyproj

sys.stdout.reconfigure(encoding='utf-8')

LARGO_CONTENEDOR_M = 1.87
ANCHO_CONTENEDOR_M = 1.50
MARGEN_LATERAL_M = 1.00
SEPARACION_ENTRE_CONT_M = 0.20
RESERVA_OCHAVA_EXTREMO_M = 10.0

ANCHOS_CALLE_POR_TIPO = {
    'CALLE': 12.0,
    'AVENIDA': 26.0,
    'BOULEVARD': 35.0,
    'PASAJE': 7.5,
    'CALLE PEATONAL': 8.0,
    'PASAJE PÚBLICO': 7.0,
    'AUTOPISTA': 40.0
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
    print("Iniciando generación de dataset con calles transversales en esquinas/ochavas...")
    start_time = time.time()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    datos_dir = os.path.join(base_dir, 'datos')

    wgs84_to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32721", always_xy=True).transform

    est_path = os.path.join(datos_dir, 'estacionamiento_via_publica.csv')
    df_est = pd.read_csv(est_path, sep=';', encoding='utf-8')
    print(f"Total de tramos viales cargados: {len(df_est)}")

    print("Construyendo índice espacial de la red vial para detección de esquinas transversales...")
    all_street_geoms = []
    all_street_names = []
    
    for idx, row in df_est.iterrows():
        try:
            g = shapely.wkt.loads(row['WKT'])
            g_utm = transform(wgs84_to_utm, g)
            line = g_utm.geoms[0] if g_utm.geom_type == 'MultiLineString' else g_utm
            all_street_geoms.append(line)
            all_street_names.append(str(row['calle']).strip().upper())
        except Exception:
            all_street_geoms.append(None)
            all_street_names.append('')

    valid_geoms = [g for g in all_street_geoms if g is not None]
    valid_names = [n for g, n in zip(all_street_geoms, all_street_names) if g is not None]
    tree_calles = STRtree(valid_geoms)

    ciclo_path = os.path.join(datos_dir, 'ciclovias.csv')
    ciclo_geoms = []
    if os.path.exists(ciclo_path):
        df_ciclo = pd.read_csv(ciclo_path, encoding='utf-8')
        for geom_str in df_ciclo['geometry'].dropna():
            try:
                g = shapely.wkt.loads(geom_str)
                g_utm = transform(wgs84_to_utm, g)
                ciclo_geoms.append(g_utm)
            except Exception:
                pass
    tree_ciclovias = STRtree(ciclo_geoms) if ciclo_geoms else None

    gar_path = os.path.join(datos_dir, 'garajes-comerciales.csv')
    garaje_pts = []
    garaje_alturas = []
    if os.path.exists(gar_path):
        df_gar = pd.read_csv(gar_path, sep=',', encoding='utf-8')
        for _, row in df_gar.iterrows():
            pt = Point(row['long'], row['lat'])
            pt_utm = transform(wgs84_to_utm, pt)
            garaje_pts.append(pt_utm)
            garaje_alturas.append(row.get('calle_altura', ''))
    tree_garajes = STRtree(garaje_pts) if garaje_pts else None

    mob_path = os.path.join(datos_dir, 'mobiliario-urbano.csv')
    paradas_pts = []
    paradas_alturas = []
    puestos_pts = []
    puestos_alturas = []
    postes_pts = []

    if os.path.exists(mob_path):
        df_mob = pd.read_csv(mob_path, sep=';', encoding='utf-8')
        df_mob['X_num'] = df_mob['X'].astype(str).str.replace(',', '.').astype(float)
        df_mob['Y_num'] = df_mob['Y'].astype(str).str.replace(',', '.').astype(float)
        df_mob = df_mob.dropna(subset=['X_num', 'Y_num'])

        for _, row in df_mob.iterrows():
            elem = str(row['ELEMENTO']).upper()
            pt = Point(row['X_num'], row['Y_num'])
            pt_utm = transform(wgs84_to_utm, pt)
            alt = row.get('Altura', '')
            
            if 'REFUGIO' in elem or 'BUS' in elem:
                paradas_pts.append(pt_utm)
                paradas_alturas.append(alt)
            elif 'PUESTO' in elem:
                puestos_pts.append(pt_utm)
                puestos_alturas.append(alt)
            else:
                postes_pts.append(pt_utm)

    tree_paradas = STRtree(paradas_pts) if paradas_pts else None
    tree_puestos = STRtree(puestos_pts) if puestos_pts else None
    tree_postes = STRtree(postes_pts) if postes_pts else None

    print("Procesando tramos, identificando esquinas transversales y restricciones...")
    registros_unificados = []

    for idx, row in df_est.iterrows():
        geom_utm = all_street_geoms[idx]
        if geom_utm is None:
            continue

        longitud_total = round(geom_utm.length, 2)
        if longitud_total < 5.0:
            continue

        calle_nombre = str(row['calle']).strip()
        calle_nombre_upper = calle_nombre.upper()
        
        altura_str = str(row['altura']) if pd.notna(row['altura']) else ''
        altura_desde = ''
        altura_hasta = ''
        if '-' in altura_str:
            partes = altura_str.split('-')
            try:
                altura_desde = int(partes[0].strip())
                altura_hasta = int(partes[1].strip())
            except Exception:
                pass

        lado_raw = str(row['lado']).strip().upper() if pd.notna(row['lado']) else 'DESCONOCIDO'
        if 'IZQ' in lado_raw:
            acera_lado = 'IZQUIERDO'
        elif 'DER' in lado_raw:
            acera_lado = 'DERECHO'
        elif 'AMB' in lado_raw:
            acera_lado = 'AMBOS'
        else:
            acera_lado = lado_raw

        tipo_via = str(row['tipo_calle']).upper() if pd.notna(row['tipo_calle']) else 'CALLE'
        ancho_calle_m = ANCHOS_CALLE_POR_TIPO.get(tipo_via, 12.0)

        regla_gen = str(row['regla_general']).upper() if pd.notna(row['regla_general']) else ''
        normativa = str(row['normativa']).upper() if pd.notna(row['normativa']) else ''

        coords = list(geom_utm.coords)
        pt_inicio = Point(coords[0])
        pt_fin = Point(coords[-1])

        cand_ini_idx = tree_calles.query(pt_inicio.buffer(25.0))
        calles_trans_ini = sorted(list(set(valid_names[i] for i in cand_ini_idx if valid_names[i] != calle_nombre_upper)))
        cruce_inicio_str = ', '.join(calles_trans_ini) if calles_trans_ini else 'Bocacalle inicio'

        cand_fin_idx = tree_calles.query(pt_fin.buffer(25.0))
        calles_trans_fin = sorted(list(set(valid_names[i] for i in cand_fin_idx if valid_names[i] != calle_nombre_upper)))
        cruce_fin_str = ', '.join(calles_trans_fin) if calles_trans_fin else 'Bocacalle fin'

        restricciones = []
        intervalos_bloqueados = []

        if longitud_total > (2 * RESERVA_OCHAVA_EXTREMO_M):
            intervalos_ochava = [
                {
                  "ubicacion": f"Esquina inicio con calle {cruce_inicio_str}",
                  "interseccion_calle": cruce_inicio_str,
                  "desde_metro": 0.0,
                  "hasta_metro": RESERVA_OCHAVA_EXTREMO_M
                },
                {
                  "ubicacion": f"Esquina fin con calle {cruce_fin_str}",
                  "interseccion_calle": cruce_fin_str,
                  "desde_metro": round(longitud_total - RESERVA_OCHAVA_EXTREMO_M, 2),
                  "hasta_metro": longitud_total
                }
            ]
            intervalos_bloqueados.append((0.0, RESERVA_OCHAVA_EXTREMO_M))
            intervalos_bloqueados.append((longitud_total - RESERVA_OCHAVA_EXTREMO_M, longitud_total))
            metros_ochava = 2 * RESERVA_OCHAVA_EXTREMO_M
        else:
            intervalos_ochava = [
                {
                    "ubicacion": f"Cuadra corta entre {cruce_inicio_str} y {cruce_fin_str}",
                    "desde_metro": 0.0,
                    "hasta_metro": longitud_total
                }
            ]
            intervalos_bloqueados.append((0.0, longitud_total))
            metros_ochava = longitud_total

        restricciones.append({
            "tipo": "OCHAVA",
            "descripcion": "Reserva de visibilidad en esquinas (chaflán) y sendas peatonales",
            "esquina_inicio": cruce_inicio_str,
            "esquina_fin": cruce_fin_str,
            "intervalos_metros": intervalos_ochava,
            "metros_ocupados": round(metros_ochava, 2)
        })

        if tree_garajes is not None and longitud_total > (2 * RESERVA_OCHAVA_EXTREMO_M):
            cand_gar = tree_garajes.query(geom_utm.buffer(12.0))
            for g_idx in cand_gar:
                g_pt = garaje_pts[g_idx]
                if geom_utm.distance(g_pt) <= 10.0:
                    s = geom_utm.project(g_pt)
                    ini = max(0.0, s - BUFFER_GARAGE_CONTRA_M)
                    fin = min(longitud_total, s + ANCHO_GARAGE_M + BUFFER_GARAGE_FAVOR_M)
                    intervalos_bloqueados.append((ini, fin))
                    alt_gar = garaje_alturas[g_idx]
                    restricciones.append({
                        "tipo": "GARAJE",
                        "descripcion": "Acceso vehicular comercial con cono de visibilidad (5m contra tránsito y 1m a favor)",
                        "ubicacion_en_cuadra": f"Altura {alt_gar}" if alt_gar else f"A {round(s, 1)}m de esquina {cruce_inicio_str}",
                        "intervalo_metros": {"desde_metro": round(ini, 2), "hasta_metro": round(fin, 2)},
                        "metros_ocupados": round(fin - ini, 2)
                    })

        if tree_paradas is not None and longitud_total > (2 * RESERVA_OCHAVA_EXTREMO_M):
            cand_par = tree_paradas.query(geom_utm.buffer(12.0))
            for p_idx in cand_par:
                p_pt = paradas_pts[p_idx]
                if geom_utm.distance(p_pt) <= 10.0:
                    s = geom_utm.project(p_pt)
                    ini = max(0.0, s - (BLOQUEO_PARADA_M / 2.0))
                    fin = min(longitud_total, s + (BLOQUEO_PARADA_M / 2.0))
                    intervalos_bloqueados.append((ini, fin))
                    alt_par = paradas_alturas[p_idx]
                    restricciones.append({
                        "tipo": "PARADA_TRANSPORTE",
                        "descripcion": "Parada de transporte público / Refugio / Cajón amarillo",
                        "ubicacion_en_cuadra": f"Altura {alt_par}" if alt_par else f"A {round(s, 1)}m de esquina {cruce_inicio_str}",
                        "intervalo_metros": {"desde_metro": round(ini, 2), "hasta_metro": round(fin, 2)},
                        "metros_ocupados": round(fin - ini, 2)
                    })

        if tree_puestos is not None and longitud_total > (2 * RESERVA_OCHAVA_EXTREMO_M):
            cand_pst = tree_puestos.query(geom_utm.buffer(12.0))
            for pst_idx in cand_pst:
                pst_pt = puestos_pts[pst_idx]
                if geom_utm.distance(pst_pt) <= 10.0:
                    s = geom_utm.project(pst_pt)
                    ini = max(0.0, s - (BLOQUEO_PUESTO_M / 2.0))
                    fin = min(longitud_total, s + (BLOQUEO_PUESTO_M / 2.0))
                    intervalos_bloqueados.append((ini, fin))
                    alt_pst = puestos_alturas[pst_idx]
                    restricciones.append({
                        "tipo": "MOBILIARIO_FIJO",
                        "descripcion": "Puesto de diarios / flores fijo en acera",
                        "ubicacion_en_cuadra": f"Altura {alt_pst}" if alt_pst else f"A {round(s, 1)}m de esquina {cruce_inicio_str}",
                        "intervalo_metros": {"desde_metro": round(ini, 2), "hasta_metro": round(fin, 2)},
                        "metros_ocupados": round(fin - ini, 2)
                    })

        tiene_ciclovia = False
        if tree_ciclovias is not None:
            candidatos_ciclo = tree_ciclovias.query(geom_utm.buffer(8.0))
            for c_idx in candidatos_ciclo:
                if geom_utm.distance(ciclo_geoms[c_idx]) <= 6.0:
                    tiene_ciclovia = True
                    break

        if tiene_ciclovia:
            restricciones.append({
                "tipo": "CICLOVIA",
                "descripcion": f"Carril exclusivo de ciclovía/bicisenda en calzada entre {cruce_inicio_str} y {cruce_fin_str}",
                "ubicacion_en_cuadra": "Toda la cuadra",
                "intervalo_metros": {"desde_metro": 0.0, "hasta_metro": longitud_total},
                "afecta_calzada": True
            })

        if normativa != '':
            prohibido_estacionar = ('PROHIBIDO' in normativa)
            detalle_normativo = normativa
        else:
            prohibido_estacionar = ('PROHIBIDO' in regla_gen)
            detalle_normativo = regla_gen

        if prohibido_estacionar:
            restricciones.append({
                "tipo": "PROHIBICION_ESTACIONAR",
                "descripcion": f"Normativa restrictiva de estacionamiento en calzada ({detalle_normativo})",
                "ubicacion_en_cuadra": f"Toda la cuadra (entre {cruce_inicio_str} y {cruce_fin_str})",
                "intervalo_metros": {"desde_metro": 0.0, "hasta_metro": longitud_total},
                "afecta_calzada": True
            })

        es_peatonal = ('PEATONAL' in tipo_via or 'PASAJE' in tipo_via)
        if es_peatonal:
            restricciones.append({
                "tipo": "CALLE_PEATONAL_O_PASAJE",
                "descripcion": f"Arteria angosta o peatonal exclusiva ({tipo_via})",
                "ubicacion_en_cuadra": "Toda la cuadra",
                "intervalo_metros": {"desde_metro": 0.0, "hasta_metro": longitud_total},
                "afecta_calzada": True
            })

        permite_calzada = (not prohibido_estacionar) and (not tiene_ciclovia) and (not es_peatonal)
        permite_acera = True

        intervalos_bloq_unificados = unir_intervalos(intervalos_bloqueados)
        longitud_bloqueada = sum(fin - ini for ini, fin in intervalos_bloq_unificados)
        longitud_bloqueada = min(longitud_total, round(longitud_bloqueada, 2))
        longitud_disponible = max(0.0, round(longitud_total - longitud_bloqueada, 2))

        intervalos_libres = []
        cursor = 0.0
        for b_ini, b_fin in intervalos_bloq_unificados:
            if b_ini > cursor:
                intervalos_libres.append((cursor, b_ini))
            cursor = max(cursor, b_fin)
        if cursor < longitud_total:
            intervalos_libres.append((cursor, longitud_total))

        cap_calzada = calcular_capacidad_intervalos(intervalos_libres) if permite_calzada else 0
        cap_acera = calcular_capacidad_intervalos(intervalos_libres)
        max_contenedores = cap_calzada if permite_calzada else cap_acera

        registros_unificados.append({
            'segment_id': row['id'],
            'municipio': 'CABA',
            'calle_nombre': calle_nombre,
            'altura_desde': altura_desde,
            'altura_hasta': altura_hasta,
            'esquina_inicio': cruce_inicio_str,
            'esquina_fin': cruce_fin_str,
            'acera_lado': acera_lado,
            'tipo_via': tipo_via,
            'ancho_calle_m': ancho_calle_m,
            'permite_calzada': 'SI' if permite_calzada else 'NO',
            'permite_acera': 'SI' if permite_acera else 'NO',
            'longitud_total_m': longitud_total,
            'espacio_bloqueado_m': longitud_bloqueada,
            'espacio_disponible_m': longitud_disponible,
            'MAX_CONTENEDORES': max_contenedores,
            'max_contenedores_calzada': cap_calzada,
            'max_contenedores_acera': cap_acera,
            'restricciones_json': json.dumps(restricciones, ensure_ascii=False),
            'geometry_wkt': str(row['WKT'])
        })

    df_salida = pd.DataFrame(registros_unificados)
    salida_path = os.path.join(datos_dir, 'restricciones_contenedores.csv')
    df_salida.to_csv(salida_path, sep=';', index=False, encoding='utf-8')

    elapsed = round(time.time() - start_time, 2)
    print(f"\nArchivo regenerado con éxito en {elapsed} segundos.")
    print(f"Ruta: {salida_path}")
    print(f"Total de registros: {len(df_salida)}")


if __name__ == '__main__':
    main()
