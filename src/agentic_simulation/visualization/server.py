"""FastAPI service for sampling and controlling the V1 world."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agentic_simulation.config import AppConfig
from agentic_simulation.simulation import SimulationBuilder

STATIC_DIRECTORY = Path(__file__).parent / "static"
LOGGER = logging.getLogger(__name__)


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RegenerateRequest(ControlRequest):
    seed: int = Field(ge=0, strict=True)


class RunRequest(ControlRequest):
    ticks: int = Field(ge=1, le=1_000_000, strict=True)


class SpeedRequest(ControlRequest):
    ticks_per_second: float = Field(gt=0, le=1000, strict=True)


class SimulationController:
    """Run a simulation independently of connected visualization clients."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._simulation = SimulationBuilder.build(config)
        self._ticks_per_second = config.visualization.ticks_per_second
        self._broadcast_every = config.visualization.broadcast_every_ticks
        self._lock = asyncio.Lock()
        self._play_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._run_id = str(uuid4())
        self._revision = 0
        self._target_tick: int | None = None
        self._last_tick_ms = 0.0
        self._error: str | None = None

    @property
    def running(self) -> bool:
        return self._play_event.is_set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="world-clock")

    def play(self, ticks: int | None = None) -> None:
        self._require_healthy()
        if ticks is not None and ticks < 1:
            raise ValueError("ticks must be positive")
        if ticks is not None and self.running:
            raise RuntimeError("pause the world before starting a bounded run")
        self._target_tick = None if ticks is None else self._simulation.tick + ticks
        self.start()
        self._play_event.set()
        self._control_changed()

    def pause(self) -> None:
        self._play_event.clear()
        self._target_tick = None
        self._control_changed()

    def set_speed(self, ticks_per_second: float) -> None:
        validated = SpeedRequest(ticks_per_second=ticks_per_second)
        self._ticks_per_second = validated.ticks_per_second
        self._control_changed()

    async def step(self) -> dict[str, Any]:
        self._require_healthy()
        if self.running:
            raise RuntimeError("pause the world before stepping")
        self._target_tick = None
        return await self._advance_and_frame(force_publish=True)

    async def regenerate(self, seed: int) -> dict[str, Any]:
        async with self._lock:
            world_config = self._config.world.model_copy(update={"seed": seed})
            config = self._config.model_copy(update={"world": world_config})
            simulation = SimulationBuilder.build(config)
            self._play_event.clear()
            self._wake_event.set()
            self._target_tick = None
            self._error = None
            self._last_tick_ms = 0.0
            self._config = config
            self._simulation = simulation
            self._run_id = str(uuid4())
            self._revision += 1
            frame = self._decorate(self._simulation.visualization_frame())
        self._publish(frame)
        return frame

    async def frame(self) -> dict[str, Any]:
        async with self._lock:
            return self._decorate(self._simulation.visualization_frame())

    async def status(self) -> dict[str, Any]:
        async with self._lock:
            return {
                **self._control_state(),
                "kind": "status",
                "run_id": self._run_id,
                "revision": self._revision,
                "control": self._control_state(),
                "subscribers": len(self._subscribers),
                "state_hash": None if self._error else self._simulation.state_hash(),
            }

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def close(self) -> None:
        self.pause()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await self._play_event.wait()
            self._wake_event.clear()
            started = loop.time()
            await self._advance_and_frame()
            remaining = 1.0 / self._ticks_per_second - (loop.time() - started)
            # Always yield, even when a costly tick exceeds the target interval.
            # Otherwise the simulation clock can starve HTTP and shutdown tasks.
            await asyncio.sleep(0)
            if remaining > 0 and self.running:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake_event.wait(), remaining)

    async def _advance_and_frame(
        self, *, force_publish: bool = False
    ) -> dict[str, Any]:
        async with self._lock:
            started = asyncio.get_running_loop().time()
            try:
                self._simulation.advance()
                update = self._simulation.visualization_update()
            except Exception:
                LOGGER.exception("Simulation tick failed")
                self._error = (
                    "Simulation stopped after a tick failed. "
                    "Regenerate to start again; see the server log for details."
                )
                self._play_event.clear()
                self._target_tick = None
                self._revision += 1
                update = self._decorate({"kind": "status"})
                self._publish(update)
                return update
            self._last_tick_ms = (asyncio.get_running_loop().time() - started) * 1000
            self._revision += 1
            finished = (
                self._target_tick is not None
                and self._simulation.tick >= self._target_tick
            )
            if finished:
                self._play_event.clear()
            update = self._decorate(update)
        if force_publish or finished or update["tick"] % self._broadcast_every == 0:
            self._publish(update)
        return update

    def _require_healthy(self) -> None:
        if self._error:
            raise RuntimeError(self._error)

    def _control_state(self) -> dict[str, Any]:
        return {
            "state": "error"
            if self._error
            else "running"
            if self.running
            else "paused",
            "running": self.running,
            "tick": self._simulation.tick,
            "seed": self._config.world.seed,
            "ticks_per_second": self._ticks_per_second,
            "last_tick_ms": round(self._last_tick_ms, 2),
            "target_tick": self._target_tick,
            "remaining_ticks": max(0, self._target_tick - self._simulation.tick)
            if self._target_tick is not None
            else None,
            "error": self._error,
        }

    def _decorate(self, frame: dict[str, Any]) -> dict[str, Any]:
        return {
            **frame,
            "run_id": self._run_id,
            "revision": self._revision,
            "control": self._control_state(),
        }

    def _control_changed(self) -> None:
        self._revision += 1
        self._wake_event.set()
        # Include current dynamic state so controls also catch up sampled viewers.
        update = (
            {"kind": "status"}
            if self._error
            else self._simulation.visualization_update()
        )
        self._publish(self._decorate(update))

    def _publish(self, frame: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            outgoing = frame
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    previous = queue.get_nowait()
                    if frame.get("kind") == "status":
                        # An error/control notice must not discard a pending map.
                        outgoing = self._decorate(previous)
                    elif (
                        previous.get("kind") == "world"
                        and frame.get("kind") == "update"
                    ):
                        # A replacement map must survive a slow client's dropped frames.
                        outgoing = self._decorate(
                            self._simulation.visualization_frame()
                        )
            queue.put_nowait(outgoing)


def create_app(config: AppConfig | None = None) -> FastAPI:
    settings = config or AppConfig()
    controller = SimulationController(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await controller.close()

    app = FastAPI(title="Agentic Simulation World", version="0.1.0", lifespan=lifespan)
    app.state.simulation_controller = controller
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
    app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html")

    @app.get("/api/world")
    async def world_frame() -> dict[str, Any]:
        return await controller.frame()

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return await controller.status()

    @app.post("/api/control/play")
    async def play() -> dict[str, Any]:
        try:
            controller.play()
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return await controller.status()

    @app.post("/api/control/run")
    async def run(request: RunRequest) -> dict[str, Any]:
        try:
            controller.play(request.ticks)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return await controller.status()

    @app.post("/api/control/speed")
    async def speed(request: SpeedRequest) -> dict[str, Any]:
        controller.set_speed(request.ticks_per_second)
        return await controller.status()

    @app.post("/api/control/pause")
    async def pause() -> dict[str, Any]:
        controller.pause()
        return await controller.status()

    @app.post("/api/control/step")
    async def step() -> dict[str, Any]:
        try:
            return await controller.step()
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/control/regenerate")
    async def regenerate(request: RegenerateRequest) -> dict[str, Any]:
        return await controller.regenerate(request.seed)

    @app.websocket("/ws/world")
    async def world_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = controller.subscribe()
        tasks: list[asyncio.Task[None]] = []

        async def send_updates() -> None:
            while True:
                try:
                    update = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    update = {"kind": "heartbeat"}
                await websocket.send_json(update)

        async def receive_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        try:
            await websocket.send_json(await controller.frame())
            tasks = [
                asyncio.create_task(send_updates()),
                asyncio.create_task(receive_disconnect()),
            ]
            finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in finished:
                task.result()
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            controller.unsubscribe(queue)

    return app
