import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iosrealrun import web

POINTS = [{"lat": 30.5, "lng": 120.7}, {"lat": 30.51, "lng": 120.71}]


def make_client(tmp_path):
    return TestClient(web.create_app(routes_dir=tmp_path / "routes"))


def test_device_list_response_with_mocked_devices(monkeypatch, tmp_path):
    devices = [SimpleNamespace(serial="device-a", connection_type="USB")]

    async def discover():
        return devices

    async def info(serial):
        return {"udid": serial, "name": "Test iPhone", "version": "18.6.2", "locked": False, "developer_mode": True}

    monkeypatch.setattr(web.connect, "discover_devices", discover)
    monkeypatch.setattr(web.connect, "get_usbmux_device_info", info)

    with make_client(tmp_path) as client:
        response = client.get("/api/devices")

    assert response.status_code == 200
    assert response.json() == [
        {
            "udid": "device-a",
            "name": "Test iPhone",
            "version": "18.6.2",
            "locked": False,
            "developer_mode": True,
            "connection_type": "USB",
        }
    ]


def test_multiple_devices_require_explicit_selection(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post("/api/simulation/start", json={"points": POINTS, "speed": 3.3})

    assert response.status_code == 422
    assert any(error["loc"][-1] == "udid" for error in response.json()["detail"])


def test_invalid_coordinates_and_speed_are_rejected(tmp_path):
    with make_client(tmp_path) as client:
        coordinate_response = client.post(
            "/api/simulation/start",
            json={"udid": "device-a", "points": [{"lat": 91, "lng": 0}, POINTS[1]], "speed": 3.3},
        )
        speed_response = client.post(
            "/api/simulation/start",
            json={"udid": "device-a", "points": POINTS, "speed": 0},
        )

    assert coordinate_response.status_code == 422
    assert speed_response.status_code == 422


def test_route_save_load_and_name_traversal_rejection(tmp_path):
    with make_client(tmp_path) as client:
        save_response = client.put("/api/routes/test-route", json={"points": POINTS})
        list_response = client.get("/api/routes")
        load_response = client.get("/api/routes/test-route.txt")
        traversal_response = client.put("/api/routes/..%2Fescape", json={"points": POINTS})

    assert save_response.status_code == 201
    assert list_response.json() == ["test-route.txt"]
    assert load_response.status_code == 200
    assert load_response.json()["points"] == POINTS
    assert traversal_response.status_code in {400, 404, 422}
    assert not (tmp_path / "escape").exists()


def test_main_page_and_static_assets_load(tmp_path):
    with make_client(tmp_path) as client:
        page = client.get("/")
        stylesheet = client.get("/static/style.css")
        script = client.get("/static/app.js")
        default_route = client.get("/api/routes/default")

    assert page.status_code == 200
    assert "leaflet" in page.text.lower()
    assert stylesheet.status_code == 200
    assert script.status_code == 200
    assert "api/simulation/start" in script.text
    assert default_route.status_code == 200
    assert len(default_route.json()["points"]) > 1


def test_start_stop_has_one_active_session_per_device(monkeypatch, tmp_path):
    events = []
    device = SimpleNamespace(serial="device-a", connection_type="USB")
    release = asyncio.Event()

    async def discover():
        return [device]

    async def fake_init(serial=None):
        return SimpleNamespace(udid=serial)

    class FakeTunnel:
        backend = "userspace/CoreDeviceProxy"

        async def __aenter__(self):
            events.append("tunnel-open")
            return SimpleNamespace(is_in_process_tunnel=True, product_version="18.6.2")

        async def __aexit__(self, *args):
            events.append("tunnel-close")

    class FakeDvt:
        async def __aenter__(self):
            events.append("dvt-open")
            return object()

        async def __aexit__(self, *args):
            events.append("dvt-close")

    async def fake_simulate(*args, **kwargs):
        events.append("simulation-start")
        try:
            await release.wait()
        finally:
            events.append("location-clear")

    monkeypatch.setattr(web.connect, "discover_devices", discover)
    monkeypatch.setattr(web.init, "init", fake_init)
    monkeypatch.setattr(web.tunnel, "create", lambda serial: FakeTunnel())
    monkeypatch.setattr(web, "DvtProvider", lambda rsd: FakeDvt())
    monkeypatch.setattr(web.cli, "simulate_route", fake_simulate)

    async def exercise():
        manager = web.SimulationManager(web.RouteStorage(tmp_path / "routes"))
        started = await manager.start("device-a", [web.RoutePoint(**point) for point in POINTS], 3.3)
        assert started["state"] == "connecting"
        for _ in range(20):
            if (await manager.status("device-a"))["state"] == "running":
                break
            await asyncio.sleep(0)
        assert (await manager.status("device-a"))["state"] == "running"
        with pytest.raises(RuntimeError, match="already active"):
            await manager.start("device-a", [web.RoutePoint(**point) for point in POINTS], 3.3)
        stopped = await manager.stop("device-a")
        assert stopped["state"] == "idle"
        assert events == [
            "tunnel-open",
            "dvt-open",
            "simulation-start",
            "location-clear",
            "dvt-close",
            "tunnel-close",
        ]

    asyncio.run(exercise())
