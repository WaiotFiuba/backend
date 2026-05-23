from pathlib import Path
import json
from geoalchemy2 import WKTElement
from sqlalchemy import select

from app.core.map_database import MapSessionLocal
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.waste_type import WasteType


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
            print(f"[WARNING] Archivo {wt_file.name} NO encontrado. Saltando paso 2.")

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
            print(f"[WARNING] Archivo {ct_file.name} NO encontrado. Saltando paso 3.")

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
            cached_types = {}

            for index, f in enumerate(features):
                props = f.get("properties", {})
                site_id = props.get("Id") or f.get("id")

                if not site_id:
                    stats["no_id"] += 1
                    continue

                # Evitamos duplicados de contenedores individuales
                stmt = select(Container).where(Container.site_id == site_id)
                res = await session.execute(stmt)
                if res.scalar_one_or_none():
                    stats["duplicate"] += 1
                    continue

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

                # Obtenemos el ContainerType correspondiente utilizando la caché local
                if type_target in cached_types:
                    ct = cached_types[type_target]
                else:
                    stmt_ct = select(ContainerType).where(
                        ContainerType.name == type_target
                    )
                    res_ct = await session.execute(stmt_ct)
                    ct = res_ct.scalar_one_or_none()

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
            print(f"[ERROR] Archivo {cont_file.name} NO encontrado. Saltando paso 4.")

    print("\n--- SCRIPT DE SIEMBRA FINALIZADO ---")
