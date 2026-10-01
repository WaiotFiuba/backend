from __future__ import annotations

import asyncio
import contextlib
import logging

from simulator.domain.entities import Measurement
from simulator.transport.backend_http import (
    DeliveryReport,
    send_measurements_batch,
)

logger = logging.getLogger(__name__)

_SHUTDOWN = object()


async def deliver_tick_measurements(
    measurements: list[Measurement],
    backend_url: str,
    batch_size: int,
) -> DeliveryReport:
    if not measurements:
        return DeliveryReport(sent=0, updated=0, not_found=0, requests=0)

    return await asyncio.to_thread(
        send_measurements_batch,
        measurements,
        backend_url,
        batch_size=batch_size,
    )


class DeliveryPipeline:
    """Cola acotada + consumidor unico que entrega mediciones al backend.

    Desacopla la generacion de ticks (que debe seguir el ritmo de
    ``speedup``/tiempo real para que los controles en vivo funcionen) de la
    entrega HTTP, que puede quedar atras. El consumidor drena de forma no
    bloqueante todo lo que ya este disponible en la cola al despertar, por lo
    que bajo carga normal entrega ~1 tick por request y, si el backend se
    atrasa, combina varios ticks en una sola entrega sin necesidad de un
    umbral configurado aparte.
    """

    def __init__(
        self,
        simulation_id: int,
        backend_url: str,
        total_periods: int,
        batch_size: int,
        maxsize: int,
    ) -> None:
        self._simulation_id = simulation_id
        self._backend_url = backend_url
        self._total_periods = total_periods
        self._batch_size = batch_size
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._consume())

    async def enqueue(self, period: int, measurements: list[Measurement]) -> None:
        """Encola las mediciones de un tick. Bloquea si la cola esta llena
        (backpressure real sobre la generacion, solo ante sobrecarga)."""
        await self._queue.put((period, measurements))

    async def aclose(self, timeout: float | None = None) -> None:
        """Shutdown prolijo: entrega lo que quede en cola antes de retornar."""
        if self._task is None:
            return
        await self._queue.put(_SHUTDOWN)
        try:
            if timeout is not None:
                await asyncio.wait_for(self._task, timeout=timeout)
            else:
                await self._task
        except TimeoutError:
            logger.warning(
                "Simulacion %s: drenado de entregas pendientes excedio el timeout "
                "(%ss); cancelando consumidor con %s items sin confirmar.",
                self._simulation_id,
                timeout,
                self._queue.qsize(),
            )
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def cancel(self) -> None:
        """Shutdown inmediato, sin esperar drenado (worker cancelado)."""
        if self._task is not None:
            self._task.cancel()

    async def _consume(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _SHUTDOWN:
                self._queue.task_done()
                return

            batch: list[tuple[int, list[Measurement]]] = [item]
            shutdown_requested = False
            while True:
                try:
                    nxt = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is _SHUTDOWN:
                    self._queue.task_done()
                    shutdown_requested = True
                    break
                batch.append(nxt)

            await self._deliver(batch)
            for _ in batch:
                self._queue.task_done()

            if shutdown_requested:
                return

    async def _deliver(self, batch: list[tuple[int, list[Measurement]]]) -> None:
        periods = [period for period, _ in batch]
        measurements: list[Measurement] = [
            measurement
            for _, tick_measurements in batch
            for measurement in tick_measurements
        ]
        try:
            report = await deliver_tick_measurements(
                measurements, self._backend_url, self._batch_size
            )
            logger.debug(
                "Simulacion %s ticks %s-%s/%s entrega combinada (%s ticks, %s "
                "mediciones): enviadas=%s actualizadas=%s no_encontradas=%s.",
                self._simulation_id,
                periods[0],
                periods[-1],
                self._total_periods or "∞",
                len(periods),
                len(measurements),
                report.sent,
                report.updated,
                report.not_found,
            )
        except Exception:
            logger.exception(
                "Simulacion %s ticks %s-%s: error enviando mediciones (lote de %s ticks).",
                self._simulation_id,
                periods[0],
                periods[-1],
                len(periods),
            )
