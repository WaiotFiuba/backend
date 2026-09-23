from __future__ import annotations

import argparse
import asyncio
import json

from app.core.map_database import MapSessionLocal
from app.services.map.site_feature_service import (
    CABA_DEMOGRAPHICS_SOURCE,
    refresh_caba_site_features,
)


async def refresh_site_features(source: str = CABA_DEMOGRAPHICS_SOURCE) -> dict:
    async with MapSessionLocal() as session:
        result = await refresh_caba_site_features(session, source=source)
        await session.commit()
    return {
        "source": result.source,
        "matched_sites": result.matched_sites,
        "skipped_sites": result.skipped_sites,
        "features_written": result.features_written,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresca features genericas de sitios desde adaptadores territoriales."
    )
    parser.add_argument("--source", default=CABA_DEMOGRAPHICS_SOURCE)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(refresh_site_features(source=args.source)), indent=2))


if __name__ == "__main__":
    main()
