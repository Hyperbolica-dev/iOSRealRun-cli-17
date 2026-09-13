"""Local FastAPI web interface for iOSRealRun."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import re
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from platformdirs import user_data_dir
from pydantic import BaseModel, Field
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider

import config
from driver import connect
from init import init, route, tunnel
from iosrealrun import cli

logger = logging.getLogger(__name__)
WEB_ASSETS = Path(__file__).with_name("web_static")
ROUTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class RoutePoint(BaseModel):
    lat: float
    lng: float


class SimulationRequest(BaseModel):
    udid: str = Field(min_length=1)
    points: list[RoutePoint]
    speed: float = Field(default=3.3, gt=0, le=20)


class RouteRequest(BaseModel):
    points: list[RoutePoint]


class StopRequest(BaseModel):
    udid: str = Field(min_length=1)


class DeviceSelectionError(Exception):
    pass


def normalize_points(points: list[RoutePoint], *, require_two: bool = True) -> list[dict[str, float]]:
    if require_two and len(points) < 2:
        raise ValueError("a route requires at least two points")
    normalized = []
    for point in points:
        if not math.isfinite(point.lat) or not math.isfinite(point.lng):
            raise ValueError("route coordinates must be finite numbers")
        if not -90 <= point.lat <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= point.lng <= 180:
            raise ValueError("longitude must be between -180 and 180")
        normalized.append({"lat": point.lat, "lng": point.lng})
    return normalized


def normalize_route_name(name: str) -> str:
    if not ROUTE_NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise ValueError("invalid route name")
    return name if name.endswith(".txt") else f"{name}.txt"


def default_routes_dir() -> Path:
    return Path(user_data_dir("iosrealrun")) / "routes"


class RouteStorage:
    def __init__(self, directory: Path | str | None = None):
        self.directory = Path(directory) if directory is not None else default_routes_dir()

    def _path(self, name: str) -> Path:
        safe_name = normalize_route_name(name)
        self.directory.mkdir(parents=True, exist_ok=True)
        directory = self.directory.resolve()
        path = (directory / safe_name).resolve()
        if path.parent != directory:
            raise ValueError("invalid route name")
        return path

    def list(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(path.name for path in self.directory.iterdir() if path.is_file() and path.suffix == ".txt")

    def load(self, name: str) -> list[dict[str, float]]:
        path = self._path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return route.get_route(path)

    def save(self, name: str, points: list[RoutePoint]) -> str:
        normalized = normalize_points(points)
        path = self._path(name)
        content = ",".join(
            json.dumps({"lng": str(point["lng"]), "lat": str(point["lat"])}, separators=(",", ":"))
            for point in normalized
        )
        path.write_text(content, encoding="utf-8")
        return path.name

    def delete(self, name: str) -> None:
        path = self._path(name)
        if not path.is_file():
            raise FileNotFoundError(name)
        path.unlink()


def _same_udid(left: str, right: str) -> bool:
    return left.replace("-", "").lower() == right.replace("-", "").lower()


@dataclass
class SimulationSession:
    udid: str
    points: list[dict[str, float]]
    speed: float
    state: str = "idle"
    backend: str | None = None
    loop_count: int = 0
    current_index: int | None = None
    started_at: float | None = None
    last_error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)

    def status(self) -> dict[str, Any]:
        elapsed = 0.0 if self.started_at is None else max(0.0, asyncio.get_running_loop().time() - self.started_at)
        return {
            "udid": self.udid,
            "state": self.state,
            "backend": self.backend,
            "route_points": len(self.points),
            "speed": self.speed,
            "loop_count": self.loop_count,
            "current_index": self.current_index,
            "elapsed_seconds": elapsed,
            "last_error": self.last_error,
        }


class SimulationManager:
    """Own one cancellable simulation task per selected device."""

    def __init__(self, routes: RouteStorage | None = None):
        self.routes = routes or RouteStorage()
        self.sessions: dict[str, SimulationSession] = {}
        self._lock = asyncio.Lock()

    async def list_devices(self) -> list[dict[str, Any]]:
        devices = await connect.discover_devices()
        result = []
        for device in devices:
            info: dict[str, Any] = {
                "udid": device.serial,
                "name": None,
                "version": None,
                "locked": None,
                "developer_mode": None,
                "connection_type": device.connection_type,
            }
            try:
                info.update(await connect.get_usbmux_device_info(device.serial))
            except Exception as error:  # noqa: BLE001 - metadata is best-effort
                logger.info("device metadata unavailable for %s: %s", device.serial, error)
                info["metadata_error"] = str(error)
            result.append(info)
        return result

    async def _require_device(self, udid: str) -> str:
        devices = await connect.discover_devices()
        match = next((device.serial for device in devices if _same_udid(device.serial, udid)), None)
        if match is None:
            raise DeviceSelectionError(f"device not found over USB: {udid}")
        return match

    async def start(self, udid: str, points: list[RoutePoint], speed: float) -> dict[str, Any]:
        normalized = normalize_points(points)
        serial = await self._require_device(udid)
        key = serial.replace("-", "").lower()
        async with self._lock:
            existing = self.sessions.get(key)
            if existing and existing.state in {"connecting", "running", "stopping"}:
                raise RuntimeError("a simulation is already active for this device")
            session = SimulationSession(serial, normalized, speed, state="connecting")
            self.sessions[key] = session
            session.task = asyncio.create_task(self._run_session(session))
        return session.status()

    async def _run_session(self, session: SimulationSession) -> None:
        try:
            device = await init.init(serial=session.udid)
            handle = tunnel.create(serial=device.udid)
            async with handle as rsd:
                session.backend = handle.backend
                async with DvtProvider(rsd) as dvt:
                    session.state = "running"
                    session.started_at = asyncio.get_running_loop().time()

                    def progress(index: int, _total: int) -> None:
                        session.current_index = index

                    def loop_finished(count: int) -> None:
                        session.loop_count = count

                    await cli.simulate_route(
                        dvt,
                        session.points,
                        session.speed,
                        on_progress=progress,
                        on_loop=loop_finished,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            session.state = "error"
            session.last_error = str(error)
            logger.exception("simulation failed for %s", session.udid)

    async def stop(self, udid: str) -> dict[str, Any]:
        key = udid.replace("-", "").lower()
        session = self.sessions.get(key)
        if session is None:
            return {"udid": udid, "state": "idle"}
        if session.task is not None and not session.task.done():
            session.state = "stopping"
            session.task.cancel()
            try:
                await session.task
            except asyncio.CancelledError:
                pass
        session.state = "idle"
        session.task = None
        return session.status()

    async def status(self, udid: str | None = None) -> dict[str, Any]:
        if udid is not None:
            key = udid.replace("-", "").lower()
            session = self.sessions.get(key)
            return session.status() if session else {"udid": udid, "state": "idle"}
        return {"sessions": [session.status() for session in self.sessions.values()]}

    async def shutdown(self) -> None:
        await asyncio.gather(*(self.stop(session.udid) for session in list(self.sessions.values())))


def create_app(routes_dir: Path | str | None = None, manager: SimulationManager | None = None) -> FastAPI:
    simulation_manager = manager or SimulationManager(RouteStorage(routes_dir))

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await simulation_manager.shutdown()

    app = FastAPI(title="iOSRealRun", lifespan=lifespan)
    app.state.simulation_manager = simulation_manager
    app.mount("/static", StaticFiles(directory=WEB_ASSETS), name="static")

    @app.get("/", response_class=FileResponse)
    async def index() -> FileResponse:
        return FileResponse(WEB_ASSETS / "index.html")

    @app.get("/api/devices")
    async def devices() -> list[dict[str, Any]]:
        try:
            return await simulation_manager.list_devices()
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"device discovery failed: {error}") from error

    @app.post("/api/devices/refresh")
    async def refresh_devices() -> list[dict[str, Any]]:
        return await devices()

    @app.get("/api/status")
    async def status(udid: str | None = None) -> dict[str, Any]:
        return await simulation_manager.status(udid)

    @app.post("/api/simulation/start", status_code=202)
    async def start_simulation(request: SimulationRequest) -> dict[str, Any]:
        try:
            return await simulation_manager.start(request.udid, request.points, request.speed)
        except DeviceSelectionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/simulation/stop")
    async def stop_simulation(request: StopRequest) -> dict[str, Any]:
        return await simulation_manager.stop(request.udid)

    @app.get("/api/routes")
    async def list_routes() -> list[str]:
        return simulation_manager.routes.list()

    @app.get("/api/routes/default")
    async def load_default_route() -> dict[str, Any]:
        return {"name": Path(config.config.routeConfig).name, "points": route.get_route()}

    @app.get("/api/routes/{name}")
    async def load_route(name: str) -> dict[str, Any]:
        try:
            points = simulation_manager.routes.load(name)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="route not found") from error
        return {"name": normalize_route_name(name), "points": points}

    @app.put("/api/routes/{name}", status_code=201)
    async def save_route(name: str, request: RouteRequest) -> dict[str, Any]:
        try:
            saved_name = simulation_manager.routes.save(name, request.points)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"name": saved_name}

    @app.delete("/api/routes/{name}")
    async def delete_route(name: str) -> dict[str, str]:
        try:
            simulation_manager.routes.delete(name)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="route not found") from error
        return {"name": normalize_route_name(name), "status": "deleted"}

    return app


app = create_app()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local iOSRealRun web UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    parser.add_argument("--open-browser", action="store_true", help="open the UI in the default browser")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning("binding beyond localhost exposes an unauthenticated UI: %s", args.host)
    url = f"http://{args.host}:{args.port}"
    print(f"iOSRealRun UI listening at {url}", flush=True)
    if args.open_browser:
        webbrowser.open(url)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
