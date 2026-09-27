-- postgres:16-bookworm no habilita PostGIS solo. Este script corre antes que import_geojson.sh
-- (orden alfabetico en docker-entrypoint-initdb.d) para que la extension ya
-- exista cuando Alembic cree las columnas Geometry.
CREATE EXTENSION IF NOT EXISTS postgis;
