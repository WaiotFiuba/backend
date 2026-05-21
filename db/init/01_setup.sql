CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS contenedores (
    id              SERIAL PRIMARY KEY,

    -- Campos del dataset CABA
    site_id         TEXT UNIQUE NOT NULL,
    address         TEXT,
    container_type  TEXT,
    latitude        FLOAT NOT NULL,
    longitude       FLOAT NOT NULL,
    ubicacion       GEOMETRY(Point, 4326) NOT NULL,

    -- Campos de Waiot/sensores (se completan después)
    serie_id        TEXT,
    description     TEXT,
    current_level   FLOAT,
    site_name       TEXT,
    device_imei     TEXT,
    available       BOOLEAN DEFAULT TRUE,

    -- Metadata
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contenedores_ubicacion
    ON contenedores USING GIST (ubicacion);

CREATE INDEX IF NOT EXISTS idx_contenedores_site_id
    ON contenedores (site_id);
