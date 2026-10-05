from __future__ import annotations

import asyncio
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from simulator.domain.entities import (
    SimulationSession,
    SimulationStatus,
)

logger = logging.getLogger(__name__)


class SimulationSessionClient:
    """
    Cliente de los endpoints del backend para la gestión de sesiones de simulación.
    """

    def __init__(self, backend_url: str) -> None:
        self._base_url = backend_url.rstrip("/") + "/digital-twin/worker/"

    async def fetch_active_session(self) -> SimulationSession | None:
        data = await asyncio.to_thread(_request_json, self._url("active-session"))
        if data is None:
            return None
        return SimulationSession.model_validate(data)

    async def fail_interrupted_sessions(self) -> bool:
        res = await asyncio.to_thread(
            _request_json, self._url("fail-interrupted"), {}, "POST", 30.0
        )
        return res is not None

    async def update_progress(
        self, simulation_id: int, progress: dict[str, object]
    ) -> dict | None:
        return await asyncio.to_thread(
            _request_json,
            self._url(f"simulations/{simulation_id}/progress"),
            progress,
            "PATCH",
            30.0,
        )

    async def finish_session(
        self,
        simulation_id: int,
        status: SimulationStatus,
        error_message: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            _request_json,
            self._url(f"simulations/{simulation_id}/finish"),
            {"status": status, "error_message": error_message},
            "POST",
            30.0,
        )

    def _url(self, path: str) -> str:
        return urljoin(self._base_url, path)


def _request_json(
    url: str,
    payload: dict | None = None,
    method: str = "GET",
    timeout: float = 10.0,
) -> dict | None:
    headers = {"Accept": "application/json"}
    data_bytes = None
    if method != "GET":
        headers["Content-Type"] = "application/json"
        data_bytes = (
            json.dumps(payload, default=str).encode("utf-8")
            if payload is not None
            else b""
        )
    req = Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data) if data else None
    except HTTPError as e:
        if e.code != 404:
            logger.warning("Error HTTP %s %s: %s", method, url, e)
        return None
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug("Esperando conexion HTTP %s %s: %s", method, url, e)
        return None
