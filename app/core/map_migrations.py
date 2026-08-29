from pathlib import Path
import json
from geoalchemy2 import WKTElement
from sqlalchemy import select

from app.core.map_database import MapSessionLocal
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.waste_type import WasteType
from app.commands.import_neighborhood_demographics import (
    import_neighborhood_demographics,
)


async def seed_map_data(recluster: bool = False) -> None:
    """Siembra los datos iniciales necesarios para el sistema de mapa."""
    print("\n--- INICIANDO SCRIPT DE SIEMBRA DE DATOS DE MAPA ---")
    datos_dir = Path(__file__).resolve().parent.parent.parent / "db" / "datos"
    if not datos_dir.exists():
        print(f"[ERROR] El directorio {datos_dir} NO existe. Abortando siembra.")
        return

    async with MapSessionLocal() as session:
        # 1. VERIFICACIÓN INICIAL: Si ya hay contenedores, verificamos si ya tienen sitios o si se solicita re-clustering
        print("[1/4] Verificando si ya existen contenedores en la base de datos...")
        stmt_check = select(Container).limit(1)
        res_check = await session.execute(stmt_check)
        if res_check.scalar_one_or_none():
            print("[INFO] Ya existen contenedores registrados en la DB.")
            from app.models.map.site import Site

            stmt_site_check = select(Site).limit(1)
            res_site_check = await session.execute(stmt_site_check)
            if not res_site_check.scalar_one_or_none() or recluster:
                print(
                    "[5/6] Ejecutando clustering espacial para agrupar contenedores existentes en Sitios (Sites)..."
                )
                from app.services.map.site_clustering_service import (
                    cluster_and_create_sites,
                )

                calles_file = datos_dir / "calles.geojson"
                sites_count = await cluster_and_create_sites(
                    session,
                    calles_path=calles_file,
                    distance_threshold_m=20.0,
                    clear_existing=True,
                )
                print(
                    f"[OK] Agrupamiento finalizado: {sites_count} sitios creados para los contenedores existentes."
                )
            else:
                print("[INFO] Los sitios ya se encuentran registrados en la DB.")
            await _seed_neighborhood_demographics(datos_dir)
            return
        print("[OK] Tabla 'containers' vacía. Procediendo con la carga.")

        # ==========================================
        # 2. CARGA DE WASTE TYPES (Húmedo y Seco)
        # ==========================================
        print("\n[2/4] Iniciando carga de tipos de residuos (WasteType)...")
        wt_file = datos_dir / "waste_types.json"
        if wt_file.exists():
            print(f"[DEBUG] Leyendo archivo: {wt_file.name}")
            with wt_file.open("r", encoding="utf-8") as fh:
                items = json.load(fh)

            wt_added_count = 0
            for item in items:
                name = item.get("name")
                if not name:
                    print(
                        "[WARNING] Se encontró un item sin 'name' en waste_types.json. Saltando."
                    )
                    continue

                stmt = select(WasteType).where(WasteType.name == name)
                res = await session.execute(stmt)
                if res.scalar_one_or_none():
                    print(f"[INFO] WasteType '{name}' ya existe en la DB. Saltando.")
                    continue

                wt = WasteType(
                    name=name,
                    description=item.get("description"),
                    color=item.get("color"),
                )
                session.add(wt)
                wt_added_count += 1

            if wt_added_count > 0:
                print(
                    f"[SQL] Commiteando {wt_added_count} nuevos registros en 'waste_types'..."
                )
                await session.commit()
                print("[OK] Carga de WasteType finalizada exitosamente.")
            else:
                print("[INFO] No se agregaron nuevos WasteTypes.")
        else:
            print(
                f"[WARNING] Archivo {wt_file.name} NO encontrado. Cargando tipos de residuos predeterminados..."
            )
            default_wts = [
                {
                    "name": "RSU Fracción Húmeda",
                    "description": "Residuos sólidos urbanos húmedos",
                    "color": "#27ae60",
                },
                {
                    "name": "RSU Fracción Seca (Reciclables)",
                    "description": "Residuos sólidos urbanos secos reciclables",
                    "color": "#3498db",
                },
            ]
            wt_added_count = 0
            for item in default_wts:
                name = item["name"]
                stmt = select(WasteType).where(WasteType.name == name)
                res = await session.execute(stmt)
                if res.scalar_one_or_none():
                    continue
                wt = WasteType(
                    name=name, description=item["description"], color=item["color"]
                )
                session.add(wt)
                wt_added_count += 1
            if wt_added_count > 0:
                await session.commit()
                print("[OK] Carga de WasteType predeterminados finalizada.")

        # ==========================================
        # 3. CARGA DE CONTAINER TYPES (Lateral, Bilateral, Soterrado)
        # ==========================================
        print("\n[3/4] Iniciando carga de tipos de contenedores (ContainerType)...")
        ct_file = datos_dir / "container_types.json"
        if ct_file.exists():
            print(f"[DEBUG] Leyendo archivo: {ct_file.name}")
            with ct_file.open("r", encoding="utf-8") as fh:
                items = json.load(fh)

            ct_added_count = 0
            for item in items:
                name = item.get("name")
                if not name:
                    print(
                        "[WARNING] Se encontró un item sin 'name' en container_types.json. Saltando."
                    )
                    continue

                stmt = select(ContainerType).where(ContainerType.name == name)
                res = await session.execute(stmt)
                if res.scalar_one_or_none():
                    print(
                        f"[INFO] ContainerType '{name}' ya existe en la DB. Saltando."
                    )
                    continue

                ct = ContainerType(
                    name=name,
                    description=item.get("description"),
                    height_cm=item.get("height_cm"),
                    volume_m3=item.get("volume_m3"),
                    overflow_zone_cm=item.get("overflow_zone_cm"),
                )

                # Relacionamos en memoria el ContainerType con su WasteType correspondiente
                if "Fracción Húmeda" in name:
                    stmt_wt = select(WasteType).where(
                        WasteType.name == "RSU Fracción Húmeda"
                    )
                    res_wt = await session.execute(stmt_wt)
                    wt_obj = res_wt.scalar_one_or_none()
                    if wt_obj:
                        ct.waste_types.append(wt_obj)
                        print(
                            f"[LINK] Tipo '{name}' enlazado con 'RSU Fracción Húmeda'"
                        )
                elif "Fracción Seca" in name:
                    stmt_wt = select(WasteType).where(
                        WasteType.name == "RSU Fracción Seca (Reciclables)"
                    )
                    res_wt = await session.execute(stmt_wt)
                    wt_obj = res_wt.scalar_one_or_none()
                    if wt_obj:
                        ct.waste_types.append(wt_obj)
                        print(
                            f"[LINK] Tipo '{name}' enlazado con 'RSU Fracción Seca (Reciclables)'"
                        )

                session.add(ct)
                ct_added_count += 1

            if ct_added_count > 0:
                print(
                    f"[SQL] Commiteando {ct_added_count} nuevos registros en 'container_types'..."
                )
                await session.commit()
                print("[OK] Carga de ContainerType finalizada exitosamente.")
            else:
                print("[INFO] No se agregaron nuevos ContainerTypes.")
        else:
            print(
                f"[WARNING] Archivo {ct_file.name} NO encontrado. Cargando tipos de contenedores predeterminados..."
            )
            default_cts = [
                {
                    "name": "RSU Fracción Húmeda - Carga Lateral",
                    "description": "Carga Lateral Húmedo",
                    "height_cm": 145,
                    "volume_m3": 3.2,
                    "overflow_zone_cm": 20,
                },
                {
                    "name": "RSU Fracción Húmeda - Carga Bilateral",
                    "description": "Carga Bilateral Húmedo",
                    "height_cm": 165,
                    "volume_m3": 4.0,
                    "overflow_zone_cm": 25,
                },
                {
                    "name": "RSU Fracción Húmeda - Semi Soterrado",
                    "description": "Semi Soterrado Húmedo",
                    "height_cm": 120,
                    "volume_m3": 5.0,
                    "overflow_zone_cm": 15,
                },
                {
                    "name": "RSU Fracción Seca - Carga Lateral",
                    "description": "Carga Lateral Seco",
                    "height_cm": 145,
                    "volume_m3": 3.2,
                    "overflow_zone_cm": 20,
                },
            ]
            ct_added_count = 0
            for item in default_cts:
                name = item["name"]
                stmt = select(ContainerType).where(ContainerType.name == name)
                res = await session.execute(stmt)
                if res.scalar_one_or_none():
                    continue
                ct = ContainerType(
                    name=name,
                    description=item["description"],
                    height_cm=item["height_cm"],
                    volume_m3=item["volume_m3"],
                    overflow_zone_cm=item["overflow_zone_cm"],
                )

                if "Fracción Húmeda" in name:
                    stmt_wt = select(WasteType).where(
                        WasteType.name == "RSU Fracción Húmeda"
                    )
                    res_wt = await session.execute(stmt_wt)
                    wt_obj = res_wt.scalar_one_or_none()
                    if wt_obj:
                        ct.waste_types.append(wt_obj)
                elif "Fracción Seca" in name:
                    stmt_wt = select(WasteType).where(
                        WasteType.name == "RSU Fracción Seca (Reciclables)"
                    )
                    res_wt = await session.execute(stmt_wt)
                    wt_obj = res_wt.scalar_one_or_none()
                    if wt_obj:
                        ct.waste_types.append(wt_obj)

                session.add(ct)
                ct_added_count += 1
            if ct_added_count > 0:
                await session.commit()
                print("[OK] Carga de ContainerType predeterminados finalizada.")

        # ==========================================
        # 4. CARGA DE CONTENEDORES NEGROS (GeoJSON)
        # ==========================================
        print("\n[4/4] Iniciando carga masiva de contenedores desde GeoJSON...")
        cont_file = datos_dir / "contenedores_negros.json"
        if cont_file.exists():
            print(f"[DEBUG] Leyendo archivo masivo: {cont_file.name}")
            with cont_file.open("r", encoding="utf-8") as fh:
                geo = json.load(fh)

            features = geo.get("features", [])
            total_features = len(features)
            print(
                f"[INFO] Se encontraron {total_features} elementos (features) para procesar."
            )

            # Contadores para estadísticas del log final
            stats = {"success": 0, "no_id": 0, "duplicate": 0, "bad_geom": 0}
            existing_serie_ids = set(
                (
                    await session.execute(
                        select(Container.serie_id).where(
                            Container.serie_id.is_not(None)
                        )
                    )
                ).scalars()
            )
            cached_types = {
                container_type.name: container_type
                for container_type in (
                    await session.execute(select(ContainerType))
                ).scalars()
            }

            for index, f in enumerate(features):
                props = f.get("properties", {})
                raw_id = props.get("Id") or f.get("id")

                if not raw_id:
                    stats["no_id"] += 1
                    continue
                serie_id = str(raw_id)

                if serie_id in existing_serie_ids:
                    stats["duplicate"] += 1
                    continue
                existing_serie_ids.add(serie_id)

                geom = f.get("geometry", {})
                coords = geom.get("coordinates") or []
                if not coords or len(coords) < 2:
                    stats["bad_geom"] += 1
                    continue
                lon, lat = coords[0], coords[1]

                # Mapeamos según CodEquipa de BA Data
                cod_equipa = str(props.get("CodEquipa", "")).upper()
                if cod_equipa == "LATERAL":
                    type_target = "RSU Fracción Húmeda - Carga Lateral"
                elif cod_equipa == "BILATERAL":
                    type_target = "RSU Fracción Húmeda - Carga Bilateral"
                elif cod_equipa == "SOTERRADO":
                    type_target = "RSU Fracción Húmeda - Semi Soterrado"
                else:
                    type_target = "RSU Fracción Húmeda - Carga Lateral"

                ct = cached_types.get(type_target)
                if not ct:
                    print(
                        f"[WARNING] El tipo '{type_target}' no existía en DB. Creándolo al vuelo."
                    )
                    ct = ContainerType(name=type_target)
                    stmt_wt = select(WasteType).where(
                        WasteType.name == "RSU Fracción Húmeda"
                    )
                    res_wt = await session.execute(stmt_wt)
                    wt_humeda = res_wt.scalar_one_or_none()
                    if wt_humeda:
                        ct.waste_types.append(wt_humeda)
                    session.add(ct)
                    await session.flush()
                    cached_types[type_target] = ct

                container = Container(
                    serie_id=serie_id,
                    address=props.get("DireccionNormalizada"),
                    description=props.get("Descripcion")
                    or f"Contenedor RSU Húmedo {cod_equipa}",
                    latitude=lat,
                    longitude=lon,
                    geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
                    available=True,
                    container_type=ct,
                )
                session.add(container)
                stats["success"] += 1

                # Imprimimos progreso intermedio cada 1000 registros para saber que sigue vivo
                if stats["success"] % 1000 == 0:
                    print(
                        f" -> Procesados {index + 1}/{total_features} elementos... (Agregados: {stats['success']})"
                    )

            # Log resumido antes de mandar los cambios a la base
            print("\n[PROCESAMIENTO TERMINADO]")
            print(f" - Insertados con éxito: {stats['success']}")
            print(f" - Omitidos por ID faltante: {stats['no_id']}")
            print(f" - Omitidos por estar duplicados en la DB: {stats['duplicate']}")
            print(f" - Omitidos por geometría inválida/vacía: {stats['bad_geom']}")

            if stats["success"] > 0:
                print("[SQL] Ejecutando commit masivo en la tabla 'containers'...")
                await session.commit()
                print("[OK] Datos guardados físicamente de manera correcta.")

                # Agrupación automática en Sitios (Sites) usando calles.geojson
                print(
                    "\n[5/6] Ejecutando agrupamiento de contenedores en Sitios (Sites)..."
                )
                from app.services.map.site_clustering_service import (
                    cluster_and_create_sites,
                )

                calles_file = datos_dir / "calles.geojson"
                sites_count = await cluster_and_create_sites(
                    session, calles_path=calles_file
                )
                print(f"[OK] Agrupamiento finalizado: {sites_count} sitios creados.")
            else:
                print(
                    "[INFO] No se envió nada a la base de datos porque no hubo registros nuevos válidos."
                )
        else:
            print(f"[WARNING] Archivo {cont_file.name} NO encontrado.")

    await _seed_neighborhood_demographics(datos_dir)
    print("\n--- SCRIPT DE SIEMBRA FINALIZADO ---")  #     container = Container(
    #         site_id=site_id,
    #         site_name=f"Sitio Sintético {zone_name} {i + 1}",
    #         address=f"Av. Siempreviva {100 + i * 10}, {zone_name}",
    #         description=f"Contenedor sintético de prueba en {zone_name}",
    #         latitude=lat,
    #         longitude=lng,
    #         # Creamos el punto geométrico en formato WKT (Well-Known Text) con el SRID geográfico estándar 4326
    #         geom=WKTElement(f"POINT({lng} {lat})", srid=4326),
    #         available=True,
    #         # Asignamos un porcentaje de llenado aleatorio para simular lecturas reales
    #         current_level=rng.randint(0, 95),
    #         # Asignamos el tipo de contenedor de forma balanceada entre los tipos disponibles en la DB
    #         container_type=cts[i % len(cts)],
    #     )
    #     session.add(container)
    #     stats["success"] += 1

    # # Si se añadieron nuevos registros, confirmamos los cambios físicos en la base de datos
    # if stats["success"] > 0:
    #     print(f"[OK] Se generaron {stats['success']} contenedores sintéticos de respaldo.")
    #     await session.commit()

    await _seed_neighborhood_demographics(datos_dir)
    print("\n--- SCRIPT DE SIEMBRA FINALIZADO ---")


async def _seed_neighborhood_demographics(datos_dir: Path) -> None:
    print("\n[5/5] Iniciando carga de poblacion por barrio...")
    population_file = datos_dir / "poblacion_barrios.csv"

    if not population_file.exists():
        print(
            "[WARNING] No se cargaron datos demograficos. Archivo faltante: "
            "poblacion_barrios.csv"
        )
        return

    report = await import_neighborhood_demographics(population_csv_path=population_file)
    print(
        "[OK] Poblacion cargada. "
        f"Importados/actualizados: {report['imported']}; "
        f"sin correspondencia: {len(report['unmatched'])}; "
        f"densidad mediana: {report['median_density_per_km2']} hab/km2."
    )
    if report["unmatched"]:
        print(
            "[WARNING] Barrios sin poblacion asociada: "
            + ", ".join(report["unmatched"])
        )
