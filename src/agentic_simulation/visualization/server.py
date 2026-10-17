"""FastAPI service for sampling and controlling the V1 world."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agentic_simulation.config import AgentsConfig, AppConfig, LifeConfig, WorldConfig
from agentic_simulation.simulation import SimulationBuilder, SimulationEngine
from agentic_simulation.simulation.checkpoint import (
    MAX_CHECKPOINT_BYTES,
    checkpoint_bytes,
    restore_checkpoint,
)

from .results import ExecutionRecord, ResultStore, report_html

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


CORE_CATALOG = (
    {
        "id": "random",
        "label": "Random movement",
        "description": "Moves randomly or idles. Does not eat or reproduce.",
    },
    {
        "id": "genetic",
        "label": "Reactive forager",
        "description": "Seeks visible food; reproduces using genome traits. No memory.",
    },
)


class NewExperimentRequest(ControlRequest):
    expected_run_id: str = Field(min_length=1)
    world: WorldConfig
    agents: AgentsConfig
    life: LifeConfig
    ticks: int = Field(ge=1, le=1_000_000, strict=True)

    def configuration(self, current: AppConfig) -> AppConfig:
        return AppConfig(
            world=self.world,
            agents=self.agents,
            life=self.life,
            visualization=current.visualization,
        )


class SimulationController:
    """Run a simulation independently of connected visualization clients."""

    def __init__(
        self,
        config: AppConfig,
        results_directory: Path | None = None,
        *,
        initial_simulation: SimulationEngine | None = None,
    ) -> None:
        self._config = config
        if initial_simulation is not None and initial_simulation.config != config:
            raise ValueError(
                "restored simulation configuration does not match server configuration"
            )
        self._simulation = initial_simulation or SimulationBuilder.build(config)
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
        self._horizon = 1000
        self.results = ResultStore(results_directory) if results_directory else None
        self._record: ExecutionRecord | None = None
        self._result_frame = self._simulation.visualization_update()
        self._latest_result: dict[str, Any] | None = None
        self._completed_result_id: str | None = None
        self._results_error: str | None = None
        self._checkpoint_origin: dict[str, Any] | None = (
            {"tick": self._simulation.tick, "state_hash": self._simulation.state_hash()}
            if initial_simulation is not None
            else None
        )

    async def checkpoint(self) -> tuple[bytes, int]:
        async with self._lock:
            self._require_healthy()
            if self.running:
                raise RuntimeError("Pause the current run before saving a checkpoint.")
            return checkpoint_bytes(self._simulation), self._simulation.tick

    async def restore(self, data: bytes, expected_run_id: str) -> dict[str, Any]:
        async with self._lock:
            if expected_run_id != self._run_id:
                raise RuntimeError("The active run changed. Review it and retry.")
            if self.running:
                raise RuntimeError(
                    "Pause the current run before restoring a checkpoint."
                )
            simulation = restore_checkpoint(data)
            self._simulation = simulation
            self._config = simulation.config
            self._ticks_per_second = simulation.config.visualization.ticks_per_second
            self._broadcast_every = (
                simulation.config.visualization.broadcast_every_ticks
            )
            self._target_tick = None
            self._horizon = 1000
            self._last_tick_ms = 0.0
            self._error = None
            self._results_error = None
            self._result_frame = simulation.visualization_update()
            self._checkpoint_origin = {
                "tick": simulation.tick,
                "state_hash": simulation.state_hash(),
            }
            self._run_id = str(uuid4())
            self._revision += 1
            frame = self._decorate(simulation.visualization_frame())
        self._publish(frame)
        return frame

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
        if self.running:
            return
        target = None if ticks is None else self._simulation.tick + ticks
        self._begin_record(target)
        self._target_tick = target
        self.start()
        self._play_event.set()
        self._control_changed()

    def pause(self) -> None:
        self._play_event.clear()
        self._finish_record("interrupted", "Manually paused before completion.")
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
            self._finish_record("interrupted", "World replaced by regeneration.")
            self._wake_event.set()
            self._target_tick = None
            self._error = None
            self._last_tick_ms = 0.0
            self._config = config
            self._simulation = simulation
            self._result_frame = simulation.visualization_update()
            self._run_id = str(uuid4())
            self._checkpoint_origin = None
            self._revision += 1
            frame = self._decorate(self._simulation.visualization_frame())
        self._publish(frame)
        return frame

    async def new_experiment(self, request: NewExperimentRequest) -> dict[str, Any]:
        async with self._lock:
            if request.expected_run_id != self._run_id:
                raise RuntimeError(
                    "The active run changed. Review its setup and retry."
                )
            if self.running:
                raise RuntimeError(
                    "Pause the current run before starting a new experiment."
                )
            config = request.configuration(self._config)
            # Build before replacing anything: rejected setups preserve the old run.
            simulation = SimulationBuilder.build(config)
            record = ExecutionRecord(
                self.results,
                str(uuid4()),
                config.model_dump(mode="json"),
                simulation.visualization_update(),
                request.ticks,
                simulation.state_hash(),
            )
            self._config = config
            self._simulation = simulation
            self._horizon = request.ticks
            self._target_tick = request.ticks
            self._error = None
            self._last_tick_ms = 0.0
            self._run_id = record.initial["run_id"]
            self._record = record
            self._checkpoint_origin = None
            self._result_frame = simulation.visualization_update()
            self._results_error = None
            self._revision += 1
            self.start()
            self._play_event.set()
            self._wake_event.set()
            frame = self._decorate(simulation.visualization_frame())
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
        self._play_event.clear()
        self._finish_record("interrupted", "Server shut down before completion.")
        self._target_tick = None
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
                self._finish_record(
                    "failed", "Simulation tick failed; last successful sample shown."
                )
                self._target_tick = None
                self._revision += 1
                update = self._decorate({"kind": "status"})
                self._publish(update)
                return update
            self._result_frame = {
                "tick": update["tick"],
                "agent_statistics": update["agent_statistics"],
            }
            if self._record:
                try:
                    self._record.observe(self._result_frame)
                except OSError:
                    LOGGER.exception("Could not save experiment metrics")
                    self._play_event.clear()
                    self._finish_record("failed", "Saving experiment metrics failed.")
                    self._target_tick = None
                    self._results_error = (
                        "Saving results failed. Check disk space and permissions."
                    )
                    force_publish = True
            self._last_tick_ms = (asyncio.get_running_loop().time() - started) * 1000
            self._revision += 1
            finished = (
                self._target_tick is not None
                and self._simulation.tick >= self._target_tick
            )
            if finished:
                self._play_event.clear()
                self._finish_record("complete", "Reached the requested target tick.")
            update = self._decorate(update)
        if force_publish or finished or update["tick"] % self._broadcast_every == 0:
            self._publish(update)
        return update

    def _require_healthy(self) -> None:
        if self._error:
            raise RuntimeError(self._error)

    def _begin_record(self, target_tick: int | None) -> None:
        self._record = ExecutionRecord(
            self.results,
            self._run_id,
            self._config.model_dump(mode="json"),
            self._simulation.visualization_update(),
            target_tick,
            self._simulation.state_hash(),
            checkpoint_origin=self._checkpoint_origin,
        )
        self._results_error = None

    def _finish_record(self, status: str, reason: str) -> None:
        if self._record is None:
            return
        record, self._record = self._record, None
        try:
            result = record.finish(
                self._result_frame,
                status,
                reason,
                None if status == "failed" else self._simulation.state_hash(),
            )
            self._latest_result = result["summary"]
            if status == "complete":
                self._completed_result_id = record.result_id
        except OSError:
            LOGGER.exception("Could not finalize experiment results")
            self._results_error = (
                "Saving results failed. Completion was not recorded. "
                "Check disk space and permissions."
            )

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
            "core": self._config.agents.brain,
            "horizon": self._horizon,
            "ticks_per_second": self._ticks_per_second,
            "last_tick_ms": round(self._last_tick_ms, 2),
            "target_tick": self._target_tick,
            "remaining_ticks": max(0, self._target_tick - self._simulation.tick)
            if self._target_tick is not None
            else None,
            "error": self._error,
            "active_result_id": self._record.result_id if self._record else None,
            "completed_result_id": self._completed_result_id,
            "latest_result": self._latest_result,
            "results_error": self._results_error,
            "checkpoint_origin": self._checkpoint_origin,
        }

    def _decorate(self, frame: dict[str, Any]) -> dict[str, Any]:
        return {
            **frame,
            **(
                {
                    "run_configuration": self._config.model_dump(mode="json"),
                    "core_catalog": CORE_CATALOG,
                }
                if frame.get("kind") == "world"
                else {}
            ),
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


def create_app(
    config: AppConfig | None = None,
    *,
    results_directory: Path = Path("runs/browser"),
    initial_simulation: SimulationEngine | None = None,
) -> FastAPI:
    settings = config or (
        initial_simulation.config if initial_simulation else AppConfig()
    )
    controller = SimulationController(
        settings, results_directory, initial_simulation=initial_simulation
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await controller.close()

    app = FastAPI(title="Agentic Simulation World", version="0.1.0", lifespan=lifespan)
    app.state.simulation_controller = controller
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)
    app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")

    @app.exception_handler(OSError)
    async def storage_error(_: Any, error: OSError) -> JSONResponse:
        LOGGER.error("Result storage unavailable: %s", error)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Cannot save results. Check disk space and permissions."
            },
        )

    def saved_result(result_id: str) -> dict[str, Any]:
        assert controller.results is not None
        try:
            return controller.results.read(result_id)
        except (FileNotFoundError, ValueError, KeyError) as error:
            raise HTTPException(status_code=404, detail="Result not found") from error

    @app.get("/api/results")
    async def results_index() -> dict[str, Any]:
        assert controller.results is not None
        return {"results": controller.results.list_results()}

    @app.get("/api/results/{result_id}")
    async def result_detail(result_id: str) -> dict[str, Any]:
        return saved_result(result_id)

    @app.get("/api/results/{result_id}/report", response_class=HTMLResponse)
    async def result_report(result_id: str) -> str:
        return report_html(saved_result(result_id))

    @app.get("/api/results/{result_id}/download")
    async def result_download(result_id: str) -> Response:
        saved_result(result_id)
        assert controller.results is not None
        return Response(
            controller.results.archive(result_id),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{result_id}.zip"'},
        )

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

    @app.get("/api/checkpoint")
    async def download_checkpoint() -> Response:
        try:
            data, tick = await controller.checkpoint()
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="tick-{tick}.alife"'
            },
        )

    @app.post("/api/control/restore-checkpoint")
    async def upload_checkpoint(
        request: Request, expected_run_id: str
    ) -> dict[str, Any]:
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_CHECKPOINT_BYTES:
                raise HTTPException(
                    status_code=413, detail="Checkpoint exceeds 64 MiB."
                )
            data.extend(chunk)
        try:
            return await controller.restore(bytes(data), expected_run_id)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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

    @app.post("/api/control/new-experiment")
    async def new_experiment(request: NewExperimentRequest) -> dict[str, Any]:
        try:
            return await controller.new_experiment(request)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
