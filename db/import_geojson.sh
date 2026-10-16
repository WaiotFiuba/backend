#!/bin/bash
# Script para importar archivos GeoJSON desde /datos hacia PostGIS usando ogr2ogr
# Valida la integridad de cada archivo comparando el conteo de features fuente vs filas en la DB

set -e

# Configuración de conexión obtenida del entorno, con valores por defecto
DB_NAME=${POSTGRES_DB:-waiot_map}
DB_USER=${POSTGRES_USER:-waiot}
DB_PASS=${POSTGRES_PASSWORD:-waiot_pass}

# Si estamos dentro del contenedor Docker durante la inicialización,
# debemos usar sockets Unix (/var/run/postgresql) ya que TCP/IP está deshabilitado temporalmente.
if [ -n "$POSTGRES_HOST" ]; then
    DB_HOST="$POSTGRES_HOST"
elif [ -f /.dockerenv ]; then
    DB_HOST="/var/run/postgresql"
else
    DB_HOST="127.0.0.1"
fi

DB_PORT=${POSTGRES_PORT:-5432}

# Contadores para el resumen final
IMPORT_ERRORS=0
IMPORT_ERRORS_DETAIL=""

echo "===================================================="
echo "Iniciando importación de datos GeoJSON a PostGIS..."
echo "Base de datos: $DB_NAME"
echo "Usuario: $DB_USER"
echo "Host: $DB_HOST:$DB_PORT"
echo "===================================================="

# Verificar si ogr2ogr está instalado
if ! command -v ogr2ogr &> /dev/null; then
    echo "ERROR: ¡ogr2ogr no está instalado en este contenedor!"
    exit 1
fi

# Exportar PGPASSWORD para que psql y ogr2ogr se autentiquen automáticamente
export PGPASSWORD="$DB_PASS"

# Aceptar argumento --force o -f para sobreescribir las tablas existentes
FORCE_OVERWRITE=false
for arg in "$@"; do
    if [ "$arg" = "--force" ] || [ "$arg" = "-f" ]; then
        FORCE_OVERWRITE=true
    fi
done

# Directorio de importación por defecto (dentro del contenedor)
IMPORT_DIR="/datos"
if [ ! -d "$IMPORT_DIR" ]; then
    # Alternativa local si se ejecuta fuera del contenedor montado
    IMPORT_DIR="$(dirname "$0")/../datos/digital_twin"
fi

if [ ! -d "$IMPORT_DIR" ]; then
    echo "ERROR: El directorio de datos '$IMPORT_DIR' no existe."
    exit 1
fi

echo "Buscando archivos GeoJSON en: $IMPORT_DIR"

# Argumentos base para las conexiones de psql
PSQL_ARGS=("-h" "$DB_HOST" "-p" "$DB_PORT" "-U" "$DB_USER" "-d" "$DB_NAME")

# Esperar a que la base de datos esté lista para aceptar conexiones (máximo 30 segundos)
echo "Verificando disponibilidad de la base de datos..."
for i in {1..30}; do
    if pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" &> /dev/null; then
        echo "¡Base de datos lista para recibir conexiones!"
        break
    fi
    echo "Esperando que la base de datos inicie ($i/30)..."
    sleep 1
done

# Iterar sobre todos los archivos geojson del directorio
for filepath in "$IMPORT_DIR"/*.geojson; do
    if [ -f "$filepath" ]; then
        filename=$(basename "$filepath" .geojson)
        echo "----------------------------------------------------"
        echo "Procesando archivo: $filename.geojson"

        # Verificar si la tabla ya existe en el esquema public
        TABLE_EXISTS=$(psql "${PSQL_ARGS[@]}" -t -Ac "SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = '$filename');" 2>/dev/null || echo "false")

        if [ "$TABLE_EXISTS" = "t" ] && [ "$FORCE_OVERWRITE" = "false" ]; then
            echo "La tabla '$filename' ya existe. Omitiendo importación. Usá --force para sobreescribir."
            continue
        fi

        if [ "$TABLE_EXISTS" = "t" ]; then
            echo "La tabla '$filename' ya existe y --force está activo. Sobreescribiendo..."
            OVERWRITE_FLAG="-overwrite"
        else
            echo "La tabla '$filename' no existe. Importando..."
            OVERWRITE_FLAG=""
        fi

        # Importación optimizada usando ogr2ogr
        # -nlt PROMOTE_TO_MULTI: Estandariza geometrías mixtas a MultiPolygon/MultiLineString/MultiPoint
        # -lco GEOMETRY_NAME=geom: Nombra la columna de geometría como 'geom'
        # -lco FID=id: Nombra la columna clave primaria como 'id'
        # -lco SPATIAL_INDEX=NONE: Evita crear el índice dos veces (se crea abajo explícitamente como idx_${filename}_geom)
        # -t_srs EPSG:4326: Fuerza/proyecta a coordenadas WGS84
        # --config PG_USE_COPY YES: Utiliza el método ultra rápido COPY de PostgreSQL para inserciones masivas
        # -skipfailures: Continúa la importación aunque algún feature individual falle
        ogr2ogr -f "PostgreSQL" \
            PG:"host=$DB_HOST port=$DB_PORT dbname=$DB_NAME user=$DB_USER password=$DB_PASS" \
            "$filepath" \
            -nln "$filename" \
            $OVERWRITE_FLAG \
            -lco GEOMETRY_NAME=geom \
            -lco FID=id \
            -lco SPATIAL_INDEX=NONE \
            -nlt PROMOTE_TO_MULTI \
            -t_srs EPSG:4326 \
            --config PG_USE_COPY YES \
            -skipfailures

        # Crear índice espacial para acelerar búsquedas por cercanía.
        psql "${PSQL_ARGS[@]}" -v ON_ERROR_STOP=1 -c "CREATE INDEX IF NOT EXISTS \"idx_${filename}_geom\" ON \"${filename}\" USING GIST (geom);" >/dev/null
        psql "${PSQL_ARGS[@]}" -v ON_ERROR_STOP=1 -c "ANALYZE \"${filename}\";" >/dev/null

        # --- Validación de integridad post-importación ---
        # Contar features en el archivo fuente (rápido con grep; fallback a ogrinfo si da 0)
        SOURCE_COUNT=$(grep -c '"type": "Feature"' "$filepath" 2>/dev/null || true)
        if [ -z "$SOURCE_COUNT" ] || [ "$SOURCE_COUNT" -eq 0 ]; then
            SOURCE_COUNT=$(ogrinfo -al -so "$filepath" 2>/dev/null | grep "Feature Count" | awk '{print $3}')
        fi

        # Contar filas en la tabla de destino
        DB_COUNT=$(psql "${PSQL_ARGS[@]}" -t -Ac "SELECT count(*) FROM \"$filename\";" 2>/dev/null | tr -d ' ')

        if [ -n "$SOURCE_COUNT" ] && [ -n "$DB_COUNT" ]; then
            if [ "$SOURCE_COUNT" -eq "$DB_COUNT" ]; then
                echo "[OK] Integridad verificada: $DB_COUNT/$SOURCE_COUNT registros importados correctamente."
            else
                MISSING=$(( SOURCE_COUNT - DB_COUNT ))
                echo "[ADVERTENCIA] Discrepancia detectada en '$filename': $DB_COUNT/$SOURCE_COUNT registros importados. ($MISSING features fallaron o fueron omitidos)"
                IMPORT_ERRORS=$((IMPORT_ERRORS + 1))
                IMPORT_ERRORS_DETAIL="$IMPORT_ERRORS_DETAIL\n  - $filename: $DB_COUNT/$SOURCE_COUNT registros ($MISSING faltantes)"
            fi
        else
            echo "[INFO] No se pudo verificar el conteo de registros de '$filename'."
        fi
    fi
done

echo "===================================================="
echo "¡Proceso de importación de GeoJSON finalizado!"
echo "===================================================="

# Resumen final de errores de integridad
if [ "${IMPORT_ERRORS:-0}" -gt 0 ]; then
    echo ""
    echo "[RESUMEN] Se detectaron $IMPORT_ERRORS archivo(s) con registros faltantes:"
    echo -e "$IMPORT_ERRORS_DETAIL"
    echo ""
    echo "Para ver los features inválidos de un archivo, ejecutá:"
    echo "  ogrinfo -al <archivo>.geojson | grep -i 'error\|invalid\|warning'"
    exit 1
else
    echo "[RESUMEN] Todos los archivos importados sin pérdida de registros."
fi
