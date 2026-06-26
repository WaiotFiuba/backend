-- =========================================================================
-- 1. FUNCIÓN GEOGRÁFICA: Calcula barrio_id y block_id para el tacho
-- =========================================================================
CREATE OR REPLACE FUNCTION auto_assign_container_geo_info()
RETURNS TRIGGER AS $$
BEGIN
    -- A) Buscar el Barrio por intersección espacial (ST_Within)
    SELECT id INTO NEW.barrio_id
    FROM barrios
    WHERE ST_Within(NEW.geom, barrios.geom)
    LIMIT 1;

    -- B) Buscar la Manzana (Block) más cercana en un radio de ~50 metros
    SELECT id INTO NEW.block_id
    FROM blocks
    WHERE ST_DWithin(NEW.geom, blocks.geom, 0.0005)
    ORDER BY ST_Distance(NEW.geom, blocks.geom) ASC
    LIMIT 1;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =========================================================================
-- 2. TRIGGER: Se dispara en inserts o cuando se mueve la geometría
-- =========================================================================
CREATE OR REPLACE TRIGGER trigger_container_geo_update
BEFORE INSERT OR UPDATE OF geom ON caba_container_spatial_metadata
FOR EACH ROW
EXECUTE FUNCTION auto_assign_container_geo_info();
