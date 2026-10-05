import csv
import json
import pytest
from scripts.calcular_capacidad_sitios import StandaloneSiteCapacityCalculator


@pytest.fixture
def synthetic_restrictions_csv(tmp_path):
    csv_file = tmp_path / "test_restricciones_contenedores.csv"

    fieldnames = [
        "segment_id",
        "municipio",
        "calle_nombre",
        "altura_desde",
        "altura_hasta",
        "esquina_inicio",
        "esquina_fin",
        "acera_lado",
        "tipo_via",
        "ancho_calle_m",
        "permite_calzada",
        "permite_acera",
        "longitud_total_m",
        "espacio_bloqueado_m",
        "espacio_disponible_m",
        "MAX_CONTENEDORES",
        "max_contenedores_calzada",
        "max_contenedores_acera",
        "restricciones_json",
        "geometry_wkt",
    ]

    rows = [
        {
            "segment_id": 1001,
            "municipio": "CABA",
            "calle_nombre": "AVENIDA_HABILITADA",
            "altura_desde": 100,
            "altura_hasta": 200,
            "esquina_inicio": "CALLE_ALFA",
            "esquina_fin": "CALLE_BETA",
            "acera_lado": "DERECHO",
            "tipo_via": "AVENIDA",
            "ancho_calle_m": 26.0,
            "permite_calzada": "SI",
            "permite_acera": "SI",
            "longitud_total_m": 120.0,
            "espacio_bloqueado_m": 20.0,
            "espacio_disponible_m": 100.0,
            "MAX_CONTENEDORES": 5,
            "max_contenedores_calzada": 5,
            "max_contenedores_acera": 5,
            "restricciones_json": json.dumps(
                [
                    {
                        "tipo": "OCHAVA",
                        "descripcion": "Reserva esquinas",
                        "metros_ocupados": 20.0,
                    }
                ]
            ),
            "geometry_wkt": "LINESTRING(-58.4000 -34.6000, -58.4000 -34.6010)",
        },
        {
            "segment_id": 1002,
            "municipio": "CABA",
            "calle_nombre": "PASAJE_INHABILITADO",
            "altura_desde": 1,
            "altura_hasta": 50,
            "esquina_inicio": "CALLE_GAMMA",
            "esquina_fin": "CALLE_DELTA",
            "acera_lado": "IZQUIERDO",
            "tipo_via": "PASAJE",
            "ancho_calle_m": 6.0,
            "permite_calzada": "NO",
            "permite_acera": "NO",
            "longitud_total_m": 40.0,
            "espacio_bloqueado_m": 40.0,
            "espacio_disponible_m": 0.0,
            "MAX_CONTENEDORES": 0,
            "max_contenedores_calzada": 0,
            "max_contenedores_acera": 0,
            "restricciones_json": json.dumps(
                [
                    {
                        "tipo": "PROHIBICION_ESTACIONAR",
                        "descripcion": "Prohibido",
                        "afecta_calzada": True,
                    }
                ]
            ),
            "geometry_wkt": "LINESTRING(-58.4100 -34.6100, -58.4100 -34.6105)",
        },
    ]

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    return str(csv_file)


@pytest.fixture
def calculator(synthetic_restrictions_csv):
    return StandaloneSiteCapacityCalculator(csv_path=synthetic_restrictions_csv)


def test_sitio_con_cupo_disponible(calculator):
    sitios = [
        {
            "id": 1,
            "name": "Sitio Con Capacidad",
            "latitude": -34.6005,
            "longitude": -58.4000,
            "contenedores_actuales": 2,
        }
    ]

    resultados = calculator.calcular_capacidad_sitios(sitios)
    res = resultados[0]

    assert res["segment_id"] == 1001
    assert res["calle_nombre"] == "AVENIDA_HABILITADA"
    assert res["esquina_inicio"] == "CALLE_ALFA"
    assert res["esquina_fin"] == "CALLE_BETA"
    assert res["ancho_calle_m"] == 26.0
    assert res["MAX_CONTENEDORES"] == 5
    assert res["cupo_disponible"] == 3
    assert res["puede_ingresar_nuevo_contenedor"] is True
    assert len(res["restricciones"]) == 1


def test_sitio_alcanza_limite_capacidad(calculator):
    sitios = [
        {
            "id": 2,
            "name": "Sitio al Límite",
            "latitude": -34.6005,
            "longitude": -58.4000,
            "contenedores_actuales": 5,
        }
    ]

    resultados = calculator.calcular_capacidad_sitios(sitios)
    res = resultados[0]

    assert res["MAX_CONTENEDORES"] == 5
    assert res["cupo_disponible"] == 0
    assert res["puede_ingresar_nuevo_contenedor"] is False


def test_sitio_sobrecargado_excede_capacidad(calculator):
    sitios = [
        {
            "id": 3,
            "name": "Sitio Sobrecargado",
            "latitude": -34.6005,
            "longitude": -58.4000,
            "contenedores_actuales": 9,
        }
    ]

    resultados = calculator.calcular_capacidad_sitios(sitios)
    res = resultados[0]

    assert res["MAX_CONTENEDORES"] == 5
    assert res["cupo_disponible"] == 0
    assert res["puede_ingresar_nuevo_contenedor"] is False


def test_sitio_en_tramo_sin_capacidad_total(calculator):
    sitios = [
        {
            "id": 4,
            "name": "Sitio en Tramo Bloqueado",
            "latitude": -34.6102,
            "longitude": -58.4100,
            "contenedores_actuales": 0,
        }
    ]

    resultados = calculator.calcular_capacidad_sitios(sitios)
    res = resultados[0]

    assert res["segment_id"] == 1002
    assert res["calle_nombre"] == "PASAJE_INHABILITADO"
    assert res["MAX_CONTENEDORES"] == 1
    assert res["cupo_disponible"] == 0
    assert res["puede_ingresar_nuevo_contenedor"] is False


def test_sitio_sin_coordenadas(calculator):
    sitios = [
        {
            "id": 5,
            "name": "Sitio Nulo",
            "latitude": None,
            "longitude": None,
            "contenedores_actuales": 1,
        }
    ]

    resultados = calculator.calcular_capacidad_sitios(sitios)
    # Al no tener coordenadas es un error y se omite
    assert len(resultados) == 0


def test_sitio_huerfano_fuera_de_cobertura(calculator):
    sitios = [
        {
            "id": 6,
            "name": "Sitio Fuera de Rango",
            "latitude": -34.8000,
            "longitude": -58.8000,
            "contenedores_actuales": 0,
        }
    ]

    resultados = calculator.calcular_capacidad_sitios(sitios)
    res = resultados[0]

    assert res["MAX_CONTENEDORES"] == 1
    assert res["cupo_disponible"] == 0
    assert res["puede_ingresar_nuevo_contenedor"] is False
    assert res["capacidad_status"] == "SIN_TRAMO_CERCANO"


def test_conteo_exacto_sitios_sin_capacidad_esperada(calculator):
    lote_sitios = [
        {
            "id": 101,
            "latitude": -34.6005,
            "longitude": -58.4000,
            "contenedores_actuales": 2,
        },  # Con cupo (5 - 2 = 3)
        {
            "id": 102,
            "latitude": -34.6005,
            "longitude": -58.4000,
            "contenedores_actuales": 5,
        },  # Al tope (cupo 0)
        {
            "id": 103,
            "latitude": -34.6005,
            "longitude": -58.4000,
            "contenedores_actuales": 8,
        },  # Excedido (cupo 0)
        {
            "id": 104,
            "latitude": -34.6102,
            "longitude": -58.4100,
            "contenedores_actuales": 0,
        },  # Tramo 0 cap (cupo 0)
        {
            "id": 105,
            "latitude": None,
            "longitude": None,
            "contenedores_actuales": 0,
        },  # Sin coords (se omite)
        {
            "id": 106,
            "latitude": -34.8000,
            "longitude": -58.8000,
            "contenedores_actuales": 0,
        },  # Fuera de tramo
    ]

    resultados = calculator.calcular_capacidad_sitios(lote_sitios)

    sitios_con_cupo = [
        s for s in resultados if s.get("puede_ingresar_nuevo_contenedor") is True
    ]
    sitios_sin_cupo_o_inhabilitados = [
        s for s in resultados if not s.get("puede_ingresar_nuevo_contenedor", False)
    ]

    assert len(sitios_con_cupo) == 1
    assert sitios_con_cupo[0]["id"] == 101
    assert sitios_con_cupo[0]["cupo_disponible"] == 3

    assert len(sitios_sin_cupo_o_inhabilitados) == 4
    assert {s["id"] for s in sitios_sin_cupo_o_inhabilitados} == {
        102,
        103,
        104,
        106,
    }
