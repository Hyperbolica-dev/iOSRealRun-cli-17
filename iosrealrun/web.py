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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from geopy.distance import geodesic
from platformdirs import user_data_dir
from pydantic import BaseModel, Field
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider

import config
from driver import connect
from init import init, route, tunnel
from iosrealrun import cli
from iosrealrun.coordinates import gcj02_to_wgs84, web_wgs84_to_location
from run import legacy_bd09_route_to_wgs84
from util import route as route_parser

logger = logging.getLogger(__name__)
WEB_ASSETS = Path(__file__).with_name("web_static")
ROUTE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_ROUTE_UPLOAD_BYTES = 1024 * 1024


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


class CoordinateDiagnosticRequest(BaseModel):
    point: RoutePoint


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


def route_distance(points: list[dict[str, float]]) -> float:
    return sum(
        geodesic(
            (points[index - 1]["lat"], points[index - 1]["lng"]),
            (points[index]["lat"], points[index]["lng"]),
        ).m
        for index in range(1, len(points))
    )


def parse_imported_route(content: bytes, coordinate_system: str) -> list[dict[str, float]]:
    if coordinate_system not in {"wgs84", "bd09", "gcj02"}:
        raise ValueError("不支持的坐标格式，请选择 WGS-84、旧版 iOSRealRun（BD-09）或 GCJ-02")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("路线文件必须是 UTF-8 文本") from error
    try:
        parsed = route_parser.parse_route(text)
    except (KeyError, SyntaxError, TypeError, ValueError) as error:
        raise ValueError("路线文件格式错误，请使用现有 iOSRealRun 文本路线格式") from error
    if len(parsed) < 2:
        raise ValueError("路线至少需要两个点")
    try:
        points = normalize_points([RoutePoint(**point) for point in parsed])
    except (TypeError, ValueError) as error:
        message = str(error)
        if message.startswith("latitude"):
            message = "纬度必须在 -90 到 90 之间"
        elif message.startswith("longitude"):
            message = "经度必须在 -180 到 180 之间"
        raise ValueError(f"路线文件中的坐标无效：{message}") from error
    if coordinate_system == "bd09":
        points = [legacy_bd09_route_to_wgs84(point) for point in points]
    elif coordinate_system == "gcj02":
        points = [
            {"lat": wgs_lat, "lng": wgs_lng}
            for wgs_lat, wgs_lng in (gcj02_to_wgs84(point["lat"], point["lng"]) for point in points)
        ]
    return normalize_points([RoutePoint(**point) for point in points])


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

    @staticmethod
    def serialize(points: list[dict[str, float]]) -> str:
        return ",".join(
            json.dumps({"lng": str(point["lng"]), "lat": str(point["lat"])}, separators=(",", ":"))
            for point in points
        )

    @classmethod
    def roundtrip(cls, points: list[dict[str, float]]) -> list[dict[str, float]]:
        """Round-trip points through the existing route representation."""

        return route_parser.parse_route(cls.serialize(points))

    def save(self, name: str, points: list[RoutePoint]) -> str:
        normalized = normalize_points(points)
        path = self._path(name)
        path.write_text(self.serialize(normalized), encoding="utf-8")
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

    async def start(
        self,
        udid: str,
        points: list[RoutePoint],
        speed: float,
    ) -> dict[str, Any]:
        normalized = normalize_points(points)
        serial = await self._require_device(udid)
        key = serial.replace("-", "").lower()
        async with self._lock:
            existing = self.sessions.get(key)
            if existing and existing.state in {"connecting", "running", "stopping"}:
                raise RuntimeError("a simulation is already active for this device")
            session = SimulationSession(
                serial,
                normalized,
                speed,
                state="connecting",
            )
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
                        coordinate_transform=web_wgs84_to_location,
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

    @app.post("/api/diagnostic/coordinates")
    async def coordinate_diagnostic(request: CoordinateDiagnosticRequest) -> dict[str, Any]:
        submitted = normalize_points([request.point], require_two=False)[0]
        stored = simulation_manager.routes.roundtrip([submitted])[0]
        simulation_point = web_wgs84_to_location(stored)
        result = {
            "clicked": submitted,
            "submitted": submitted,
            "stored": stored,
            "location_simulation_set": simulation_point,
        }
        logger.info(
            "coordinate diagnostic: clicked=%s submitted=%s stored=%s "
            "LocationSimulation.set=%s mode=%s",
            result["clicked"],
            result["submitted"],
            result["stored"],
            result["location_simulation_set"],
            "WGS-84",
        )
        return result

    @app.post("/api/routes/import")
    async def import_route(
        file: UploadFile = File(...),  # noqa: B008 - FastAPI declares multipart fields this way
        coordinate_system: str = Form("wgs84"),
    ) -> dict[str, Any]:
        content = await file.read(MAX_ROUTE_UPLOAD_BYTES + 1)
        if len(content) > MAX_ROUTE_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="文件过大，路线文本不能超过 1 MiB")
        try:
            points = parse_imported_route(content, coordinate_system)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "filename": file.filename,
            "coordinate_system": coordinate_system,
            "points": points,
            "point_count": len(points),
            "distance_meters": route_distance(points),
        }

    @app.post("/api/simulation/start", status_code=202)
    async def start_simulation(request: SimulationRequest) -> dict[str, Any]:
        try:
            return await simulation_manager.start(
                request.udid,
                request.points,
                request.speed,
            )
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
        # HNroute.txt predates the Web UI and follows the CLI's historical
        # BD-09 input convention. Convert only at this Web UI boundary so the
        # map receives the canonical WGS-84 representation.
        points = [legacy_bd09_route_to_wgs84(point) for point in route.get_route()]
        return {"name": Path(config.config.routeConfig).name, "points": points}

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

DEFAULT_PORT = 17865


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local iOSRealRun web UI")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default: {DEFAULT_PORT})")
    parser.add_argument("--open-browser", action="store_true", help="open the UI in the default browser")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        logger.warning("binding beyond localhost exposes an unauthenticated UI: %s", args.host)
    url = f"http://{args.host}:{args.port}"
    print(f"iOSRealRun UI listening at {url}", flush=True)
    if args.open_browser:
        webbrowser.open(url)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)
