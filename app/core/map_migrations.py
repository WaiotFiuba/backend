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


async def seed_map_data() -> None:
    print("\n--- INICIANDO SCRIPT DE SIEMBRA DE DATOS DE MAPA ---")

    base = Path(__file__).resolve().parents[2]
    datos_dir = base / "db" / "datos"

    print(f"[DEBUG] Ruta base calculada: {base}")
    print(f"[DEBUG] Buscando directorio de datos en: {datos_dir.resolve()}")

    if not datos_dir.exists():
        print(f"[ERROR] El directorio {datos_dir} NO existe. Abortando siembra.")
        return

    async with MapSessionLocal() as session:
        # 1. VERIFICACIÓN INICIAL: Si ya hay contenedores, asumimos que el seed ya corrió
        print("[1/4] Verificando si ya existen contenedores en la base de datos...")
        stmt_check = select(Container).limit(1)
        res_check = await session.execute(stmt_check)
        if res_check.scalar_one_or_none():
            print(
                "[INFO] Ya existen contenedores registrados en la DB. Saltando siembra para evitar duplicados."
            )
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
            existing_site_ids = set(
                (await session.execute(select(Container.site_id))).scalars()
            )
            cached_types = {
                container_type.name: container_type
                for container_type in (
                    await session.execute(select(ContainerType))
                ).scalars()
            }

            for index, f in enumerate(features):
                props = f.get("properties", {})
                site_id = props.get("Id") or f.get("id")

                if not site_id:
                    stats["no_id"] += 1
                    continue
                site_id = str(site_id)

                if site_id in existing_site_ids:
                    stats["duplicate"] += 1
                    continue
                existing_site_ids.add(site_id)

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
                    site_id=site_id,
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
            else:
                print(
                    "[INFO] No se envió nada a la base de datos porque no hubo registros nuevos válidos."
                )
        else:
            print(
                f"[WARNING] Archivo {cont_file.name} NO encontrado. Generando contenedores sintéticos de respaldo..."
            )

            # CABA_ZONES = [
            #     ("Palermo", -34.5832, -58.4243),
            #     ("Recoleta", -34.5889, -58.3974),
            #     ("Almagro", -34.6093, -58.4210),
            #     ("Caballito", -34.6180, -58.4410),
            #     ("Flores", -34.6282, -58.4633),
            # ]

            # # Esto es solo para que aparezcan las zonas cargadas las varibables container.zone en el front.
            # # for idx, (zone_name, base_lat, base_lng) in enumerate(CABA_ZONES):
            # #     stmt_barrio_check = select(NeighborhoodDemographic).where(NeighborhoodDemographic.neighborhood == zone_name)
            # #     res_barrio = await session.execute(stmt_barrio_check)
            # #     if not res_barrio.scalar_one_or_none():
            # #         lng_min, lng_max = base_lng - 0.005, base_lng + 0.005
            # #         lat_min, lat_max = base_lat - 0.005, base_lat + 0.005
            # #         wkt_geom = f"MULTIPOLYGON((({lng_min} {lat_min}, {lng_max} {lat_min}, {lng_max} {lat_max}, {lng_min} {lat_max}, {lng_min} {lat_min})))"

            # #         densities = [1.2, 1.5, 1.3, 1.1, 0.9]
            # #         factor = densities[idx % len(densities)]

            # #         barrio = NeighborhoodDemographic(
            # #             neighborhood=zone_name,
            # #             commune=f"Comuna {idx + 1}",
            # #             population=150000 + idx * 20000,
            # #             year=2010,
            # #             source="Censo 2010 Sintético",
            # #             area_km2=4.0,
            # #             density_per_km2=37500.0,
            # #             density_factor=factor,
            # #             geom=WKTElement(wkt_geom, srid=4326),
            # #         )
            # #         session.add(barrio)
            # # await session.flush()

            # stmt_cts = select(ContainerType)
            # res_cts = await session.execute(stmt_cts)
            # cts = res_cts.scalars().all()
            # if not cts:
            #     default_ct = ContainerType(
            #         name="RSU Fracción Húmeda - Carga Lateral",
            #         description="Contenedor estándar carga lateral",
            #         height_cm=145,
            #         volume_m3=3.2,
            #         overflow_zone_cm=20,
            #     )
            #     session.add(default_ct)
            #     await session.flush()
            #     cts = [default_ct]

            # # Inicializamos el generador de números aleatorios con una semilla fija (42) para que las posiciones sean reproducibles
            # rng = random.Random(42)
            # stats = {"success": 0}
            # for i in range(120):
            #     # Seleccionamos una de las coordenadas base de forma cíclica (round-robin)
            #     _, base_lat, base_lng = CABA_ZONES[i % len(CABA_ZONES)]
            #     # Generamos una pequeña variación aleatoria de latitud y longitud alrededor de la coordenada base
            #     # Limitamos a un desplazamiento de 0.004 para que caiga dentro de su respectivo barrio de 0.005
            #     lat = base_lat + rng.uniform(-0.004, 0.004)
            #     lng = base_lng + rng.uniform(-0.004, 0.004)
            #     # Formateamos el ID del sitio con relleno de ceros (ej: SITE-SYNTH-0001)
            #     site_id = f"SITE-SYNTH-{i + 1:04d}"

            #     # Verificamos si ya existe un contenedor con este site_id en la base de datos para evitar duplicados
            #     stmt_dup = select(Container).where(Container.site_id == site_id)
            #     res_dup = await session.execute(stmt_dup)
            #     if res_dup.scalar_one_or_none():
            #         continue

            #     # Determinamos la zona de CABA_ZONES más cercana a la coordenada (lat, lng) generada
            #     closest_zone_info = min(
            #         CABA_ZONES,
            #         key=lambda z: (z[1] - lat) ** 2 + (z[2] - lng) ** 2
            #     )
            #     zone_name = closest_zone_info[0]

            #     # Creamos el objeto Container con datos simulados legibles y geolocalización PostGIS
            #     container = Container(
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
