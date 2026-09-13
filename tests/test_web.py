import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import config
from init import route
from iosrealrun import web
from iosrealrun.coordinates import wgs84_to_gcj02
from run import legacy_bd09_route_to_wgs84

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


def test_web_saved_route_reloads_identical_wgs84_precision(tmp_path):
    points = [
        {"lat": 30.52802386594508, "lng": 120.7335575167566},
        {"lat": 30.528128854802127, "lng": 120.73356200828415},
    ]
    with make_client(tmp_path) as client:
        save_response = client.put("/api/routes/wgs-route", json={"points": points})
        load_response = client.get("/api/routes/wgs-route.txt")

    assert save_response.status_code == 201
    assert load_response.json()["points"] == points


def test_main_page_and_static_assets_load(tmp_path):
    with make_client(tmp_path) as client:
        page = client.get("/")
        stylesheet = client.get("/static/style.css")
        script = client.get("/static/app.js")
        default_route = client.get("/api/routes/default")

    assert page.status_code == 200
    assert "leaflet" in page.text.lower()
    assert stylesheet.status_code == 200
    assert "route-actions" in page.text
    assert "white-space: nowrap" in stylesheet.text
    assert "min-width: 9rem" in stylesheet.text
    assert script.status_code == 200
    assert "api/simulation/start" in script.text
    assert default_route.status_code == 200
    assert len(default_route.json()["points"]) > 1
    assert default_route.json()["points"][0] == legacy_bd09_route_to_wgs84(route.get_route(config.config.routeConfig)[0])


def test_route_import_control_and_endpoint_are_available(tmp_path):
    route_text = b'{"lat":30.52802386594508,"lng":120.7335575167566},{"lat":30.528128854802127,"lng":120.73356200828415}'
    with make_client(tmp_path) as client:
        page = client.get("/")
        script = client.get("/static/app.js")
        response = client.post(
            "/api/routes/import",
            files={"file": ("route.txt", route_text, "text/plain")},
            data={"coordinate_system": "wgs84"},
        )
        route_names = client.get("/api/routes")

    assert 'id="route-file"' in page.text
    assert 'value="wgs84"' in page.text
    assert "导入路线文件" in page.text
    assert "保存当前路线" in page.text
    assert "/api/routes/import" in script.text
    assert response.status_code == 200
    assert response.json()["saved_name"] == "route.txt"
    assert response.json()["points"] == [
        {"lat": 30.52802386594508, "lng": 120.7335575167566},
        {"lat": 30.528128854802127, "lng": 120.73356200828415},
    ]
    assert response.json()["point_count"] == 2
    assert route_names.json() == ["route.txt"]


def test_import_filename_becomes_safe_saved_route_name(tmp_path):
    route_text = b'{"lat":30.5,"lng":120.7},{"lat":30.51,"lng":120.71}'
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/routes/import",
            files={"file": ("../ZJG-East-C.txt", route_text)},
        )

    assert response.status_code == 200
    assert response.json()["saved_name"] == "ZJG-East-C.txt"
    assert not (tmp_path / "ZJG-East-C.txt").exists()


def test_duplicate_import_gets_unique_name_without_overwrite(tmp_path):
    first = b'{"lat":30.5,"lng":120.7},{"lat":30.51,"lng":120.71}'
    second = b'{"lat":31.5,"lng":121.7},{"lat":31.51,"lng":121.71}'
    with make_client(tmp_path) as client:
        first_response = client.post("/api/routes/import", files={"file": ("same.txt", first)})
        second_response = client.post("/api/routes/import", files={"file": ("same.txt", second)})
        first_route = client.get("/api/routes/same.txt")
        second_route = client.get("/api/routes/same-2.txt")
        route_names = client.get("/api/routes")

    assert first_response.json()["saved_name"] == "same.txt"
    assert second_response.json()["saved_name"] == "same-2.txt"
    assert first_route.json()["points"][0] == {"lat": 30.5, "lng": 120.7}
    assert second_route.json()["points"][0] == {"lat": 31.5, "lng": 121.7}
    assert route_names.json() == ["same-2.txt", "same.txt"]


def test_wgs84_import_can_be_saved_and_reloaded_without_conversion(tmp_path):
    route_text = b'{"lat":30.52802386594508,"lng":120.7335575167566},{"lat":30.528128854802127,"lng":120.73356200828415}'
    with make_client(tmp_path) as client:
        imported = client.post(
            "/api/routes/import",
            files={"file": ("route.txt", route_text)},
            data={"coordinate_system": "wgs84"},
        ).json()["points"]
        assert client.put("/api/routes/imported", json={"points": imported}).status_code == 201
        reloaded = client.get("/api/routes/imported.txt")

    assert reloaded.json()["points"] == imported


def test_imported_wgs84_points_use_existing_simulation_path(monkeypatch, tmp_path):
    device = SimpleNamespace(serial="device-a", connection_type="USB")
    imported = web.parse_imported_route(
        b'{"lat":30.52802386594508,"lng":120.7335575167566},{"lat":30.528128854802127,"lng":120.73356200828415}',
        "wgs84",
    )
    received = []

    async def discover():
        return [device]

    async def fake_run(session):
        received.extend(session.points)

    monkeypatch.setattr(web.connect, "discover_devices", discover)

    async def exercise():
        manager = web.SimulationManager(web.RouteStorage(tmp_path / "routes"))
        await manager._require_device("device-a")
        monkeypatch.setattr(manager, "_run_session", fake_run)
        session_status = await manager.start(
            "device-a",
            [web.RoutePoint(**point) for point in imported],
            3.3,
        )
        session = manager.sessions["devicea"]
        await session.task
        return session_status

    assert asyncio.run(exercise())["state"] == "connecting"
    assert received == imported


def test_legacy_bd09_import_converts_each_point_once(monkeypatch, tmp_path):
    raw_points = [{"lat": 30.5, "lng": 120.7}, {"lat": 30.51, "lng": 120.71}]
    calls = []

    def convert_once(point):
        calls.append(point)
        return {"lat": point["lat"] + 1, "lng": point["lng"] + 1}

    monkeypatch.setattr(web, "legacy_bd09_route_to_wgs84", convert_once)
    route_text = b'{"lat":30.5,"lng":120.7},{"lat":30.51,"lng":120.71}'
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/routes/import",
            files={"file": ("legacy.txt", route_text)},
            data={"coordinate_system": "bd09"},
        )
        saved_route = client.get("/api/routes/legacy.txt")

    assert response.status_code == 200
    assert calls == raw_points
    assert response.json()["saved_name"] == "legacy.txt"
    assert response.json()["points"] == [
        {"lat": 31.5, "lng": 121.7},
        {"lat": 31.51, "lng": 121.71},
    ]
    assert saved_route.json()["points"] == response.json()["points"]


def test_gcj02_import_converts_to_wgs84_once(tmp_path):
    original = [(30.52802386594508, 120.7335575167566), (30.528128854802127, 120.73356200828415)]
    gcj_points = [wgs84_to_gcj02(*point) for point in original]
    route_text = ",".join(
        f'{{"lat":{lat!r},"lng":{lng!r}}}' for lat, lng in gcj_points
    ).encode()
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/routes/import",
            files={"file": ("gcj.txt", route_text)},
            data={"coordinate_system": "gcj02"},
        )

    assert response.status_code == 200
    imported = response.json()["points"]
    assert imported[0]["lat"] == pytest.approx(original[0][0], abs=2e-6)
    assert imported[0]["lng"] == pytest.approx(original[0][1], abs=5e-6)


@pytest.mark.parametrize(
    ("filename", "content", "expected_detail"),
    [
        ("bad.txt", b'{"lat":91,"lng":120},{"lat":30,"lng":120}', "纬度"),
        ("bad.txt", b"not a route", "路线文件格式错误"),
        ("empty.txt", b"", "路线至少需要两个点"),
    ],
)
def test_route_import_rejects_invalid_files(tmp_path, filename, content, expected_detail):
    with make_client(tmp_path) as client:
        response = client.post("/api/routes/import", files={"file": (filename, content)})

    assert response.status_code == 422
    assert expected_detail in response.json()["detail"]


def test_route_import_rejects_oversized_files(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/routes/import",
            files={"file": ("large.txt", b"x" * (web.MAX_ROUTE_UPLOAD_BYTES + 1))},
        )

    assert response.status_code == 413
    assert "文件过大" in response.json()["detail"]


def test_failed_import_does_not_create_saved_route(tmp_path):
    with make_client(tmp_path) as client:
        response = client.post("/api/routes/import", files={"file": ("bad.txt", b"not a route")})
        routes = client.get("/api/routes")

    assert response.status_code == 422
    assert routes.json() == []


def test_default_legacy_route_is_converted_once_for_web(monkeypatch, tmp_path):
    raw_points = [{"lat": 30.5, "lng": 120.7}, {"lat": 30.51, "lng": 120.71}]
    calls = []

    def convert_once(point):
        calls.append(point)
        return {"lat": point["lat"] + 1, "lng": point["lng"] + 1}

    monkeypatch.setattr(web.route, "get_route", lambda: raw_points)
    monkeypatch.setattr(web, "legacy_bd09_route_to_wgs84", convert_once)

    with make_client(tmp_path) as client:
        response = client.get("/api/routes/default")

    assert response.status_code == 200
    assert calls == raw_points
    assert response.json()["points"] == [
        {"lat": 31.5, "lng": 121.7},
        {"lat": 31.51, "lng": 121.71},
    ]


def test_web_cli_uses_new_default_port():
    args = web.create_parser().parse_args([])

    assert web.DEFAULT_PORT == 17865
    assert args.port == 17865
    assert "17865" in web.create_parser().format_help()


def test_coordinate_diagnostic_roundtrips_precision_and_reports_boundary(tmp_path):
    point = {"lat": 30.52802386594508, "lng": 120.7335575167566}
    with make_client(tmp_path) as client:
        response = client.post(
            "/api/diagnostic/coordinates",
            json={"point": point},
        )

    assert response.status_code == 200
    diagnostic = response.json()
    assert diagnostic["clicked"] == point
    assert diagnostic["submitted"] == point
    assert diagnostic["stored"] == point
    assert diagnostic["location_simulation_set"] == point


def test_coordinate_diagnostic_does_not_convert_mainland_china(tmp_path):
    point = {"lat": 30.52802386594508, "lng": 120.7335575167566}
    with make_client(tmp_path) as client:
        response = client.post("/api/diagnostic/coordinates", json={"point": point})

    assert response.status_code == 200
    diagnostic = response.json()
    assert diagnostic["stored"] == point
    assert diagnostic["location_simulation_set"] == point


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
        assert kwargs["coordinate_transform"](POINTS[0]) == POINTS[0]
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
