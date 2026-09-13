-- =========================================================================
-- 1. FUNCIÓN GEOGRÁFICA: Sincroniza la tabla espejo de un contenedor
-- =========================================================================
CREATE OR REPLACE FUNCTION auto_assign_container_geo_info()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO caba_container_spatial_metadata (
        container_id,
        comuna_id,
        barrio_id,
        manzana_id,
        geom
    )
    VALUES (
        NEW.id,
        (
            SELECT id
            FROM comunas
            WHERE ST_Within(NEW.geom, comunas.geom)
            LIMIT 1
        ),
        (
            SELECT id
            FROM barrios
            WHERE ST_Within(NEW.geom, barrios.geom)
            LIMIT 1
        ),
        COALESCE(
            (
                SELECT id
                FROM manzanas
                WHERE ST_Within(NEW.geom, manzanas.geom)
                LIMIT 1
            ),
            (
                SELECT id
                FROM manzanas
                ORDER BY manzanas.geom <-> NEW.geom
                LIMIT 1
            )
        ),
        NEW.geom
    )
    ON CONFLICT (container_id) DO UPDATE SET
        comuna_id = EXCLUDED.comuna_id,
        barrio_id = EXCLUDED.barrio_id,
        manzana_id = EXCLUDED.manzana_id,
        geom = EXCLUDED.geom;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =========================================================================
-- 2. TRIGGER: Se dispara en inserts o cuando se mueve la geometría
-- =========================================================================
CREATE OR REPLACE TRIGGER trigger_container_geo_update
AFTER INSERT OR UPDATE OF geom ON containers
FOR EACH ROW
EXECUTE FUNCTION auto_assign_container_geo_info();
