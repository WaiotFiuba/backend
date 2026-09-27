import json
from pathlib import Path

from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.commands.import_neighborhood_demographics import (
    import_neighborhood_demographics,
)
from app.core.map_database import MapSessionLocal
from app.models.map.container import Container
from app.models.map.container_type import ContainerType
from app.models.map.waste_type import WasteType

HUMEDO_WASTE_TYPE = "RSU Fracción Húmeda"
SECO_WASTE_TYPE = "RSU Fracción Seca (Reciclables)"
HUMEDO_LATERAL_TYPE = "RSU Fracción Húmeda - Carga Lateral"
HUMEDO_BILATERAL_TYPE = "RSU Fracción Húmeda - Carga Bilateral"
HUMEDO_SOTERRADO_TYPE = "RSU Fracción Húmeda - Semi Soterrado"
SECO_LATERAL_TYPE = "RSU Fracción Seca - Carga Lateral"


async def seed_map_data(recluster: bool = False) -> None:
    """Siembra los datos iniciales necesarios para el sistema de mapa."""
    print("\n--- INICIANDO SCRIPT DE SIEMBRA DE DATOS DE MAPA ---")
    datos_dir = _resolve_seed_data_dir()
    if datos_dir is None:
        project_root = Path(__file__).resolve().parents[2]
        print(
            "[ERROR] No se encontro directorio de datos. "
            f"Rutas esperadas: {project_root / 'datos'} o {project_root / 'db' / 'datos'}."
        )
        return
    print(f"[INFO] Usando directorio de datos: {datos_dir}")

    async with MapSessionLocal() as session:
        # 1. VERIFICACIÓN INICIAL: Si ya hay contenedores, verificamos si ya tienen sitios o si se solicita re-clustering
        print("[1/4] Verificando si ya existen contenedores en la base de datos...")
        stmt_check = select(Container).limit(1)
        res_check = await session.execute(stmt_check)
        has_existing_containers = res_check.scalar_one_or_none() is not None
        if has_existing_containers:
            print("[INFO] Ya existen contenedores registrados en la DB.")
            from app.models.map.site import Site

            stmt_site_check = select(Site).limit(1)
            res_site_check = await session.execute(stmt_site_check)
            has_existing_sites = res_site_check.scalar_one_or_none() is not None
        else:
            has_existing_sites = False
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

        cached_types = {
            container_type.name: container_type
            for container_type in (
                await session.execute(
                    select(ContainerType).options(
                        selectinload(ContainerType.waste_types)
                    )
                )
            ).scalars()
        }
        await _ensure_container_type_waste_links(session, cached_types)
        await session.commit()

        # ==========================================
        # 4. CARGA DE CONTENEDORES DESDE GeoJSON
        # ==========================================
        print("\n[4/4] Iniciando carga masiva de contenedores desde GeoJSON...")
        imported_count = await _seed_container_sources(session, datos_dir, cached_types)
        if imported_count > 0:
            print("[SQL] Ejecutando commit masivo en la tabla 'containers'...")
            await session.commit()
            print("[OK] Datos guardados físicamente de manera correcta.")
        else:
            print(
                "[INFO] No se envió nada a la base de datos porque no hubo registros nuevos válidos."
            )

        if imported_count > 0 or recluster or not has_existing_sites:
            print(
                "\n[5/6] Ejecutando agrupamiento de contenedores en Sitios (Sites)..."
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
            print(f"[OK] Agrupamiento finalizado: {sites_count} sitios creados.")
        elif has_existing_containers:
            print("[INFO] Los sitios ya se encuentran registrados en la DB.")

    await _seed_neighborhood_demographics(datos_dir)
    print("\n--- SCRIPT DE SIEMBRA FINALIZADO ---")


def _resolve_seed_data_dir() -> Path | None:
    project_root = Path(__file__).resolve().parents[2]
    candidates = (
        project_root / "datos",
        project_root / "db" / "datos",
    )
    expected_files = (
        "contenedores_negros.json",
        "contenedores_verdes.json",
        "waste_types.json",
        "container_types.json",
        "poblacion_barrios.csv",
    )
    for candidate in candidates:
        if candidate.exists() and any(
            (candidate / name).exists() for name in expected_files
        ):
            return candidate
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


async def _seed_container_sources(session, datos_dir: Path, cached_types: dict) -> int:
    existing_serie_ids = set(
        (
            await session.execute(
                select(Container.serie_id).where(Container.serie_id.is_not(None))
            )
        ).scalars()
    )
    imported_total = 0

    imported_total += await _seed_container_source(
        session=session,
        datos_dir=datos_dir,
        filename="contenedores_negros.json",
        serie_prefix="contenedores_negros",
        description_label="Húmedo",
        existing_serie_ids=existing_serie_ids,
        cached_types=cached_types,
    )
    imported_total += await _seed_container_source(
        session=session,
        datos_dir=datos_dir,
        filename="contenedores_verdes.json",
        serie_prefix="contenedores_verdes",
        description_label="Seco",
        existing_serie_ids=existing_serie_ids,
        cached_types=cached_types,
    )

    return imported_total


async def _seed_container_source(
    session,
    datos_dir: Path,
    filename: str,
    serie_prefix: str,
    description_label: str,
    existing_serie_ids: set[str],
    cached_types: dict[str, ContainerType],
) -> int:
    cont_file = datos_dir / filename
    if not cont_file.exists():
        print(f"[WARNING] Archivo {cont_file.name} NO encontrado.")
        return 0

    print(f"[DEBUG] Leyendo archivo masivo: {cont_file.name}")
    with cont_file.open("r", encoding="utf-8") as fh:
        geo = json.load(fh)

    features = geo.get("features", [])
    total_features = len(features)
    print(
        f"[INFO] {cont_file.name}: se encontraron {total_features} elementos (features) para procesar."
    )

    stats = {"success": 0, "no_id": 0, "duplicate": 0, "bad_geom": 0}
    for index, feature in enumerate(features):
        props = feature.get("properties", {})
        raw_id = props.get("Id") or feature.get("id")

        if not raw_id:
            stats["no_id"] += 1
            continue
        serie_id = _normalize_source_serie_id(str(raw_id), serie_prefix)

        if serie_id in existing_serie_ids:
            stats["duplicate"] += 1
            continue
        existing_serie_ids.add(serie_id)

        geom = feature.get("geometry", {})
        coords = geom.get("coordinates") or []
        if not coords or len(coords) < 2:
            stats["bad_geom"] += 1
            continue
        lon, lat = coords[0], coords[1]

        cod_equipa = str(props.get("CodEquipa", "")).upper()
        type_target = _container_type_for_file(filename, cod_equipa)
        container_type = cached_types.get(type_target)
        if container_type is None:
            raise RuntimeError(
                f"Falta el tipo de contenedor '{type_target}'. "
                "Revisá db/datos/container_types.json y volvé a ejecutar la siembra."
            )

        container = Container(
            serie_id=serie_id,
            address=props.get("DireccionNormalizada"),
            description=props.get("Descripcion")
            or f"Contenedor RSU {description_label} {cod_equipa}",
            latitude=lat,
            longitude=lon,
            geom=WKTElement(f"POINT({lon} {lat})", srid=4326),
            available=True,
            container_type=container_type,
        )
        session.add(container)
        stats["success"] += 1

        if stats["success"] % 1000 == 0:
            print(
                f" -> {cont_file.name}: procesados {index + 1}/{total_features} elementos... "
                f"(Agregados: {stats['success']})"
            )

    print(f"\n[PROCESAMIENTO TERMINADO] {cont_file.name}")
    print(f" - Insertados con éxito: {stats['success']}")
    print(f" - Omitidos por ID faltante: {stats['no_id']}")
    print(f" - Omitidos por estar duplicados en la DB: {stats['duplicate']}")
    print(f" - Omitidos por geometría inválida/vacía: {stats['bad_geom']}")
    return stats["success"]


def _normalize_source_serie_id(raw_id: str, serie_prefix: str) -> str:
    if "|" in raw_id:
        return raw_id
    return f"{serie_prefix}|{raw_id}"


def _container_type_for_file(filename: str, cod_equipa: str) -> str:
    if filename == "contenedores_verdes.json":
        return SECO_LATERAL_TYPE

    if cod_equipa == "BILATERAL":
        return HUMEDO_BILATERAL_TYPE
    if cod_equipa == "SOTERRADO":
        return HUMEDO_SOTERRADO_TYPE
    return HUMEDO_LATERAL_TYPE


async def _ensure_container_type_waste_links(
    session,
    cached_types: dict[str, ContainerType],
) -> None:
    for type_name, container_type in cached_types.items():
        waste_type_name = _waste_type_name_for_container_type(type_name)
        if waste_type_name is None:
            continue

        waste_type = await _get_waste_type(session, waste_type_name)
        if waste_type and all(
            wt.id != waste_type.id for wt in container_type.waste_types
        ):
            container_type.waste_types.append(waste_type)
            await session.flush()


def _waste_type_name_for_container_type(type_name: str) -> str | None:
    if "Fracción Seca" in type_name:
        return SECO_WASTE_TYPE
    if "Fracción Húmeda" in type_name:
        return HUMEDO_WASTE_TYPE
    return None


async def _get_waste_type(session, waste_type_name: str) -> WasteType | None:
    result = await session.execute(
        select(WasteType).where(WasteType.name == waste_type_name)
    )
    return result.scalar_one_or_none()


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
