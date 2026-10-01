from __future__ import annotations

import json
from http.client import HTTPException
from urllib.parse import urlencode, urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from simulator.topology import (
    SimulationTopology,
    topology_from_backend_api,
)


class BackendConnectionError(RuntimeError):
    pass


def load_topology_from_backend_api(
    backend_url: str,
    containers_path: str = "/map/containers/",
    limit: int | None = None,
    page_size: int = 1000,
    timeout_seconds: float = 10,
    token: str | None = None,
) -> SimulationTopology:
    base_url = urljoin(_ensure_trailing_slash(backend_url), containers_path.lstrip("/"))
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = _fetch_all_containers(
        base_url=base_url,
        headers=headers,
        limit=limit,
        page_size=page_size,
        timeout_seconds=timeout_seconds,
    )

    return topology_from_backend_api(payload)


def _fetch_all_containers(
    base_url: str,
    headers: dict[str, str],
    limit: int | None,
    page_size: int,
    timeout_seconds: float,
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    offset = 0

    while True:
        current_limit = (
            min(page_size, limit - len(payload)) if limit is not None else page_size
        )
        if current_limit <= 0:
            return payload

        page = _fetch_container_page(
            url=_with_query(base_url, {"limit": current_limit, "offset": offset}),
            headers=headers,
            timeout_seconds=timeout_seconds,
        )
        payload.extend(page)

        if len(page) < current_limit:
            return payload

        offset += len(page)


def _fetch_container_page(
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
) -> list[dict[str, object]]:
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            page = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise BackendConnectionError(
            f"No se pudo leer contenedores desde {url}. "
            f"El backend respondio HTTP {exc.code}."
        ) from exc
    except (HTTPException, TimeoutError, URLError, OSError) as exc:
        raise BackendConnectionError(
            f"No se pudo conectar al backend en {url}. "
            "Verifica que el backend este levantado y que --backend-url sea correcto."
        ) from exc
    except json.JSONDecodeError as exc:
        raise BackendConnectionError(
            f"El endpoint {url} respondio contenido que no es JSON valido."
        ) from exc

    if isinstance(page, dict) and "items" in page and isinstance(page["items"], list):
        return page["items"]

    if not isinstance(page, list):
        raise BackendConnectionError(
            f"El endpoint {url} no devolvio una lista de contenedores."
        )

    return page


def _with_query(url: str, params: dict[str, int]) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def _ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else value + "/"
