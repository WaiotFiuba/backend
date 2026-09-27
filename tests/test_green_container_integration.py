from datetime import UTC, datetime

from app.core.map_migrations import (
    _container_type_for_file,
    _normalize_source_serie_id,
)
from app.services.map.container_service import _row_to_container


def test_container_type_mapping_is_fixed_for_black_and_green_sources():
    assert (
        _container_type_for_file("contenedores_verdes.json", "LATERAL")
        == "RSU Fracción Seca - Carga Lateral"
    )
    assert (
        _container_type_for_file("contenedores_verdes.json", "DESCONOCIDO")
        == "RSU Fracción Seca - Carga Lateral"
    )
    assert (
        _container_type_for_file("contenedores_negros.json", "BILATERAL")
        == "RSU Fracción Húmeda - Carga Bilateral"
    )
    assert (
        _container_type_for_file("contenedores_negros.json", "SOTERRADO")
        == "RSU Fracción Húmeda - Semi Soterrado"
    )
    assert (
        _container_type_for_file("contenedores_negros.json", "DESCONOCIDO")
        == "RSU Fracción Húmeda - Carga Lateral"
    )


def test_source_serie_id_is_prefixed_when_input_is_raw():
    assert (
        _normalize_source_serie_id("1371", "contenedores_verdes")
        == "contenedores_verdes|1371"
    )
    assert (
        _normalize_source_serie_id("contenedores_verdes|1371", "contenedores_verdes")
        == "contenedores_verdes|1371"
    )


def test_container_response_fallback_uses_existing_serie_id_without_black_prefix():
    row = {
        "id": 10,
        "site_id": None,
        "serie_id": "contenedores_verdes|1371",
        "site_name": None,
        "address": "ARCOS 1371",
        "device_imei": None,
        "latitude": -34.0,
        "longitude": -58.0,
        "current_level": 42,
        "available": True,
        "last_reading": None,
        "updated_at": datetime.now(UTC),
        "zone": None,
        "density_factor": None,
        "container_type_id": 2,
        "container_type": "RSU Fracción Seca - Carga Lateral",
        "height_cm": 145,
        "volume_m3": 3.2,
        "overflow_zone_cm": 20,
        "waste_type_name": "RSU Fracción Seca (Reciclables)",
        "waste_type_color": "#009B3A",
    }

    container = _row_to_container(row)

    assert container.site_id == "contenedores_verdes|1371"
    assert container.container_type.name == "RSU Fracción Seca - Carga Lateral"
    assert (
        container.container_type.waste_types[0].name
        == "RSU Fracción Seca (Reciclables)"
    )
