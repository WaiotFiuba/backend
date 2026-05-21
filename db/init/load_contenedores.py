import json
import os
import sys
import time

import psycopg2

# Esperar a que PostGIS esté listo
max_retries = 10
retry_count = 0
conn = None

postgres_config = {
    "dbname": os.environ.get("POSTGRES_DB", "waiot_map"),
    "user": os.environ.get("POSTGRES_USER", "waiot"),
    "password": os.environ.get("POSTGRES_PASSWORD", "waiot_pass"),
    "port": os.environ.get("POSTGRES_PORT", "5432"),
}

configured_host = os.environ.get("POSTGRES_HOST")
print("Esperando a que PostGIS esté listo...")
while retry_count < max_retries and conn is None:
    try:
        if configured_host:
            conn = psycopg2.connect(host=configured_host, **postgres_config)
        else:
            # Dentro del contenedor de initdb, el socket Unix está disponible
            conn = psycopg2.connect(host="/var/run/postgresql", **postgres_config)
        print("Conexión a PostGIS establecida")
        break
    except psycopg2.OperationalError as e:
        retry_count += 1
        wait_time = min(5, retry_count)
        print(
            f"  Intento {retry_count}/{max_retries}: Reintentando en {wait_time}s... ({e})"
        )
        time.sleep(wait_time)

if conn is None:
    print("No se pudo conectar a PostGIS después de varios intentos")
    sys.exit(1)

cur = conn.cursor()

print("\nCargando contenedores desde archivo...")
json_file = "datos/contenedores.json"

if not os.path.exists(json_file):
    print(f"Archivo no encontrado: {json_file}")
    cur.close()
    conn.close()
    sys.exit(1)

with open(json_file) as f:
    data = json.load(f)

features = data.get("features", [])
print(f"  → {len(features)} contenedores encontrados")

insert_sql = """
    INSERT INTO contenedores (
        site_id, address, container_type,
        latitude, longitude, ubicacion
    )
    VALUES (
        %s, %s, %s,
        %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)
    )
    ON CONFLICT (site_id) DO NOTHING
"""

count = 0
skipped = 0
errors = 0

for idx, feat in enumerate(features):
    try:
        props = feat.get("properties", {})
        coords = feat.get("geometry", {}).get("coordinates", [])

        if len(coords) < 2:
            skipped += 1
            continue

        lon, lat = coords[0], coords[1]

        cur.execute(
            insert_sql,
            (
                props.get("Id"),  # site_id
                props.get("DireccionNormalizada"),  # address
                props.get("CodEquipa"),  # container_type
                lat,  # latitude
                lon,  # longitude
                lon,
                lat,  # ST_MakePoint(lng, lat)
            ),
        )
        count += 1
    except Exception as e:
        print(f"Error en contenedor {idx}: {e}")
        errors += 1
        continue

try:
    conn.commit()
    print("\nCarga completada:")
    print(f"  ->{count} contenedores insertados")
    print(f"  ->{skipped} sin coordenadas (omitidos)")
    if errors > 0:
        print(f"{errors} errores durante la carga")
except Exception as e:
    print(f"Error al hacer commit: {e}")
    conn.rollback()
finally:
    cur.close()
    conn.close()
