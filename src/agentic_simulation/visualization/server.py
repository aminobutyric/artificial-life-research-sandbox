"""FastAPI service for sampling and controlling the V1 world."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentic_simulation.config import AppConfig
from agentic_simulation.simulation import SimulationBuilder

STATIC_DIRECTORY = Path(__file__).parent / "static"


class RegenerateRequest(BaseModel):
    seed: int = Field(ge=0)


class SimulationController:
    """Run a simulation independently of connected visualization clients."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._simulation = SimulationBuilder.build(config)
        self._ticks_per_second = config.visualization.ticks_per_second
        self._broadcast_every = config.visualization.broadcast_every_ticks
        self._lock = asyncio.Lock()
        self._play_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @property
    def running(self) -> bool:
        return self._play_event.is_set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="world-clock")

    def play(self) -> None:
        self.start()
        self._play_event.set()

    def pause(self) -> None:
        self._play_event.clear()

    async def step(self) -> dict[str, Any]:
        if self.running:
            raise RuntimeError("pause the world before stepping")
        return await self._advance_and_frame()

    async def regenerate(self, seed: int) -> dict[str, Any]:
        async with self._lock:
            world_config = self._config.world.model_copy(update={"seed": seed})
            self._config = self._config.model_copy(update={"world": world_config})
            self._simulation = SimulationBuilder.build(self._config)
            frame = self._simulation.visualization_frame()
        self._publish(frame)
        return frame

    async def frame(self) -> dict[str, Any]:
        async with self._lock:
            return self._simulation.visualization_frame()

    async def status(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "running": self.running,
                "tick": self._simulation.tick,
                "ticks_per_second": self._ticks_per_second,
                "subscribers": len(self._subscribers),
                "state_hash": self._simulation.state_hash(),
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
        interval = 1.0 / self._ticks_per_second
        loop = asyncio.get_running_loop()
        while True:
            await self._play_event.wait()
            started = loop.time()
            await self._advance_and_frame()
            remaining = interval - (loop.time() - started)
            if remaining > 0.0:
                await asyncio.sleep(remaining)

    async def _advance_and_frame(self) -> dict[str, Any]:
        async with self._lock:
            self._simulation.advance()
            update = self._simulation.visualization_update()
        if update["tick"] % self._broadcast_every == 0:
            self._publish(update)
        return update

    def _publish(self, frame: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    previous = queue.get_nowait()
                    if (
                        previous.get("kind") == "world"
                        and frame.get("kind") == "update"
                    ):
                        # A replacement map must survive a slow client's dropped frames.
                        frame = self._simulation.visualization_frame()
            queue.put_nowait(frame)


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
        controller.play()
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
        try:
            await websocket.send_json(await controller.frame())
            while True:
                try:
                    update = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    update = {"kind": "heartbeat"}
                await websocket.send_json(update)
        except WebSocketDisconnect:
            pass
        finally:
            controller.unsubscribe(queue)

    return app
