import pytest
from scripts.calcular_capacidad_sitios import StandaloneSiteCapacityCalculator


@pytest.fixture(scope="module")
def real_calculator():
    return StandaloneSiteCapacityCalculator()


def test_sitios_reales_caba_longitud_acotada(real_calculator):
    # Sitios con coordenadas reales en distintos barrios de CABA:
    sitios_reales = [
        {
            "id": 1,
            "name": "Sitio Arias 3450 (Saavedra)",
            "latitude": -34.545914,
            "longitude": -58.483065,
            "contenedores_actuales": 2,
        },
        {
            "id": 2,
            "name": "Sitio Av. Cabildo 2200 (Belgrano)",
            "latitude": -34.562300,
            "longitude": -58.456600,
            "contenedores_actuales": 1,
        },
        {
            "id": 3,
            "name": "Sitio Obelisco / Av. Corrientes 1050 (Centro)",
            "latitude": -34.603700,
            "longitude": -58.381600,
            "contenedores_actuales": 3,
        },
        {
            "id": 4,
            "name": "Sitio Plaza de Mayo / Balcarce 50 (Monserrat)",
            "latitude": -34.608300,
            "longitude": -58.371200,
            "contenedores_actuales": 0,
        },
        {
            "id": 5,
            "name": "Sitio Av. Rivadavia 5000 (Caballito)",
            "latitude": -34.619800,
            "longitude": -58.438500,
            "contenedores_actuales": 4,
        },
    ]

    resultados = real_calculator.calcular_capacidad_sitios(sitios_reales)
    assert len(resultados) == 5

    for res in resultados:
        # Verificar que MAX_CONTENEDORES sea un entero positivo
        assert isinstance(res["MAX_CONTENEDORES"], int)
        assert res["MAX_CONTENEDORES"] >= 1

        # Verificar que longitud_total_m esté calculada de las coordenadas reales y acotada entre 5 y 400 m
        assert "longitud_total_m" in res
        longitud = res["longitud_total_m"]
        assert isinstance(longitud, float)
        assert 5.0 <= longitud <= 400.0, f"Longitud {longitud} fuera del rango [5.0, 400.0] para {res['name']}"

        # Verificar cálculo coherente de cupo
        assert res["cupo_disponible"] == max(0, res["MAX_CONTENEDORES"] - res["contenedores_actuales"])
        assert res["puede_colocar_mas"] == (res["cupo_disponible"] > 0)
        assert res["puede_ingresar_nuevo_contenedor"] == (res["cupo_disponible"] > 0)


def test_sitio_fuera_de_caba(real_calculator):
    # Coordenadas en medio del Río de la Plata o La Plata (lejos de CABA)
    sitios = [
        {
            "id": 99,
            "name": "Sitio Fuera de Rango",
            "latitude": -34.900000,
            "longitude": -57.950000,
            "contenedores_actuales": 2,
        }
    ]
    res = real_calculator.calcular_capacidad_sitios(sitios)[0]
    assert res["capacidad_status"] == "SIN_TRAMO_CERCANO"
    assert res["puede_colocar_mas"] is False
    assert res["cupo_disponible"] == 0
    assert res["longitud_total_m"] == 0.0


def test_sitio_con_coordenadas_nulas(real_calculator):
    sitios = [
        {"id": 100, "name": "Sitio Sin Coords", "latitude": None, "longitude": None}
    ]
    res = real_calculator.calcular_capacidad_sitios(sitios)
    assert len(res) == 0
