-- Script para cargar contenedores desde JSON
-- Ejecutar: psql -U waiot -d waiot_map -f /docker-entrypoint-initdb.d/03_load_contenedores.sql

\set ON_ERROR_STOP on

-- Función para cargar GeoJSON
CREATE TEMPORARY TABLE IF NOT EXISTS temp_contenedores AS
SELECT
    NULL::TEXT as site_id,
    NULL::TEXT as address,
    NULL::TEXT as container_type,
    NULL::FLOAT as latitude,
    NULL::FLOAT as longitude;

-- Usar COPY o cargar desde el archivo JSON
-- Nota: Esto requiere que el contenedor tenga jq instalado o que procesemos en Python
-- Por ahora, vamos a usar una aproximación con el script Python mejorado

\echo 'Contenedores table initialized. Use load_contenedores.py to load data.'
