import asyncio
import sys
from pathlib import Path

from app.core.map_migrations import seed_map_data

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main():
    recluster = "--recluster" in sys.argv
    print(
        f"Iniciando siembra de datos de mapa{' (modo re-clustering)' if recluster else ''}..."
    )
    try:
        await seed_map_data(recluster=recluster)
        print("Siembra de datos finalizada exitosamente.")
        from app.services.simulation.collection_schedule_service import (
            save_collection_schedule,
        )

        print("Generando cronograma de recolección para todas las rutas...")
        save_collection_schedule()
        print("Cronograma generado y guardado exitosamente.")

        from app.commands.process_land_use import process_land_use_async

        print("Procesando usos del suelo y perfiles por radio censal...")
        await process_land_use_async()
        print("Usos del suelo y perfiles procesados exitosamente.")
    except Exception as e:
        print(f"Error durante la siembra de datos: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
