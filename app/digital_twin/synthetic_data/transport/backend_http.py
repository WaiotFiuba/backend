from __future__ import annotations

import json
import time
from dataclasses import dataclass
from http.client import HTTPException
from itertools import islice
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app.digital_twin.synthetic_data.domain.entities import Measurement
from app.digital_twin.synthetic_data.exporters.files import api_payload_from_measurement
from app.digital_twin.synthetic_data.simulation.engine import SimulationResult


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
    post_json: Callable[[str, dict[str, object], str | None, float], dict[str, object]] | None = None,
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
    batch_size: int = 250,
    token: str | None = None,
    timeout_seconds: float = 30,
    post_json: Callable[[str, dict[str, object], str | None, float], dict[str, object]] | None = None,
) -> DeliveryReport:
    if batch_size <= 0:
        raise ValueError("batch_size debe ser mayor a 0.")

    sender = post_json or _post_json
    endpoint = _endpoint_url(backend_url, path)
    report = _MutableReport()

    for batch in _chunks(_payloads(measurements), batch_size):
        response = sender(endpoint, {"measurements": batch}, token, timeout_seconds)
        report.add(response, sent=len(batch))

    return report.freeze()


def stream_result(
    result: SimulationResult,
    backend_url: str,
    path: str = "/digital-twin/telemetry",
    delay_seconds: float = 0.0,
    speedup: float | None = None,
    token: str | None = None,
    timeout_seconds: float = 10,
    sleep: Callable[[float], None] = time.sleep,
    post_json: Callable[[str, dict[str, object], str | None, float], dict[str, object]] | None = None,
) -> DeliveryReport:
    if delay_seconds < 0:
        raise ValueError("delay_seconds no puede ser negativo.")
    if speedup is not None and speedup <= 0:
        raise ValueError("speedup debe ser mayor a 0.")

    sender = post_json or _post_json
    endpoint = _endpoint_url(backend_url, path)
    measurements = sorted(result.measurements, key=lambda item: item.timestamp)
    report = _MutableReport()
    previous: Measurement | None = None

    try:
        for measurement in measurements:
            if previous is not None:
                sleep(_delay_between(previous, measurement, delay_seconds, speedup))
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
    for measurement in sorted(measurements, key=lambda item: item.timestamp):
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
    simulated_seconds = max(0.0, (current.timestamp - previous.timestamp).total_seconds())
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

    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
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
        raise BackendDeliveryError(f"El backend {url} no devolvio JSON valido.") from exc

    if not isinstance(data, dict):
        raise BackendDeliveryError(f"El backend {url} devolvio una respuesta inesperada.")
    return data


def _endpoint_url(backend_url: str, path: str) -> str:
    return urljoin(_ensure_trailing_slash(backend_url), path.lstrip("/"))


def _ensure_trailing_slash(value: str) -> str:
    return value if value.endswith("/") else value + "/"
