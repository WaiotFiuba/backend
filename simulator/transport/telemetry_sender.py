from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from http.client import HTTPException
from itertools import islice
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from simulator.domain.entities import Measurement
from simulator.exporters.files import api_payload_from_measurement
from simulator.simulation.state import SimulationResult


class BackendDeliveryError(RuntimeError):
    pass


class StreamingInterrupted(RuntimeError):
    def __init__(self, report: DeliveryReport):
        self.report = report
        super().__init__("Streaming interrumpido por el usuario.")


@dataclass(frozen=True)
class DeliveryReport:
    sent: int
    updated: int
    not_found: int
    requests: int

    def to_record(self) -> dict[str, int]:
        return {
            "sent": self.sent,
            "updated": self.updated,
            "not_found": self.not_found,
            "requests": self.requests,
        }


def send_result_batch(
    result: SimulationResult,
    backend_url: str,
    path: str = "/digital-twin/telemetry/batch",
    batch_size: int = 100,
    token: str | None = None,
    timeout_seconds: float = 10,
    post_json: Callable[[str, dict[str, object], str | None, float], dict[str, object]]
    | None = None,
) -> DeliveryReport:
    if batch_size <= 0:
        raise ValueError("batch_size debe ser mayor a 0.")

    sender = post_json or _post_json
    endpoint = _endpoint_url(backend_url, path)
    report = _MutableReport()

    for batch in _chunks(_payloads(result.measurements), batch_size):
        response = sender(endpoint, {"measurements": batch}, token, timeout_seconds)
        report.add(response, sent=len(batch))

    return report.freeze()


def send_measurements_batch(
    measurements: Iterable[Measurement],
    backend_url: str,
    path: str = "/digital-twin/telemetry/batch",
    batch_size: int = 30000,
    token: str | None = None,
    timeout_seconds: float = 120,
    post_json: Callable[[str, dict[str, object], str | None, float], dict[str, object]]
    | None = None,
) -> DeliveryReport:
    if batch_size <= 0:
        raise ValueError("batch_size debe ser mayor a 0.")

    sender = post_json or _post_json
    endpoint = _endpoint_url(backend_url, path)
    report = _MutableReport()

    t_start = time.perf_counter()
    payload_list = list(_payloads(measurements))
    t_payloads = time.perf_counter()

    for batch in _chunks(payload_list, batch_size):
        response = sender(endpoint, {"measurements": batch}, token, timeout_seconds)
        report.add(response, sent=len(batch))

    t_total = time.perf_counter() - t_start
    print(
        f"[PERF WORKER HTTP] Envio {report.sent} mediciones | Total: {t_total:.3f}s | "
        f"Dict serialization: {t_payloads - t_start:.3f}s | "
        f"HTTP requests ({report.requests}): {t_total - (t_payloads - t_start):.3f}s",
        flush=True,
    )

    return report.freeze()


async def deliver_tick_measurements(
    measurements: list[Measurement],
    backend_url: str,
    batch_size: int,
) -> DeliveryReport:
    """Envia las mediciones de un tick y espera la confirmacion del backend."""
    if not measurements:
        return DeliveryReport(sent=0, updated=0, not_found=0, requests=0)

    return await asyncio.to_thread(
        send_measurements_batch,
        measurements,
        backend_url,
        batch_size=batch_size,
    )


def stream_result(
    result: SimulationResult,
    backend_url: str,
    path: str = "/digital-twin/telemetry",
    delay_seconds: float = 0.0,
    speedup: float | None = None,
    token: str | None = None,
    timeout_seconds: float = 10,
    sleep: Callable[[float], None] = time.sleep,
    post_json: Callable[[str, dict[str, object], str | None, float], dict[str, object]]
    | None = None,
) -> DeliveryReport:
    if delay_seconds < 0:
        raise ValueError("delay_seconds no puede ser negativo.")
    if speedup is not None and speedup <= 0:
        raise ValueError("speedup debe ser mayor a 0.")

    sender = post_json or _post_json
    endpoint = _endpoint_url(backend_url, path)
    report = _MutableReport()
    previous: Measurement | None = None

    try:
        for measurement in sorted(result.measurements, key=lambda item: item.timestamp):
            if previous is not None:
                delay = _delay_between(previous, measurement, delay_seconds, speedup)
                if delay > 0:
                    sleep(delay)
            response = sender(
                endpoint,
                api_payload_from_measurement(measurement),
                token,
                timeout_seconds,
            )
            report.add(response, sent=1)
            previous = measurement
    except KeyboardInterrupt as exc:
        raise StreamingInterrupted(report.freeze()) from exc

    return report.freeze()


class _MutableReport:
    def __init__(self) -> None:
        self.sent = 0
        self.updated = 0
        self.not_found = 0
        self.requests = 0

    def add(self, response: dict[str, object], sent: int) -> None:
        self.sent += sent
        self.updated += int(response.get("updated", 0))
        self.not_found += int(response.get("not_found", 0))
        self.requests += 1

    def freeze(self) -> DeliveryReport:
        return DeliveryReport(
            sent=self.sent,
            updated=self.updated,
            not_found=self.not_found,
            requests=self.requests,
        )


def _payloads(measurements: Iterable[Measurement]) -> Iterable[dict[str, object]]:
    for measurement in measurements:
        yield api_payload_from_measurement(measurement)


def _chunks(
    values: Iterable[dict[str, object]],
    size: int,
) -> Iterable[list[dict[str, object]]]:
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def _delay_between(
    previous: Measurement,
    current: Measurement,
    delay_seconds: float,
    speedup: float | None,
) -> float:
    if speedup is None:
        return delay_seconds
    simulated_seconds = max(
        0.0, (current.timestamp - previous.timestamp).total_seconds()
    )
    return simulated_seconds / (speedup * 60)


def _post_json(
    url: str,
    payload: dict[str, object],
    token: str | None,
    timeout_seconds: float,
) -> dict[str, object]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

    request = Request(
        url,
        data=data_bytes,
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise BackendDeliveryError(
            f"El backend respondio HTTP {exc.code} al enviar telemetria a {url}: {body}"
        ) from exc
    except (HTTPException, TimeoutError, URLError, OSError) as exc:
        raise BackendDeliveryError(
            f"No se pudo enviar telemetria a {url}. Verifica que el backend este levantado."
        ) from exc

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise BackendDeliveryError(
            f"El backend {url} no devolvio JSON valido."
        ) from exc

    if not isinstance(data, dict):
        raise BackendDeliveryError(
            f"El backend {url} devolvio una respuesta inesperada."
        )
    return data


def _endpoint_url(backend_url: str, path: str) -> str:
    return urljoin(_ensure_trailing_slash(backend_url), path.lstrip("/"))


def _ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else value + "/"
