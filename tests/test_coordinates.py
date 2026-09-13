import asyncio

import pytest

import run
from iosrealrun.coordinates import (
    gcj02_to_wgs84,
    is_in_mainland_china,
    to_simulation,
    wgs84_to_gcj02,
)


def test_wgs84_to_gcj02_known_beijing_point():
    lat, lng = wgs84_to_gcj02(39.908823, 116.397470)

    assert lat == pytest.approx(39.91022649807321, abs=1e-10)
    assert lng == pytest.approx(116.4037135824225, abs=1e-10)


def test_gcj02_to_wgs84_is_approximate_inverse():
    original = (39.908823, 116.397470)
    converted = wgs84_to_gcj02(*original)

    assert gcj02_to_wgs84(*converted) == pytest.approx(original, abs=2e-6)


def test_conversion_is_identity_outside_mainland_china():
    point = (51.5074, -0.1278)

    assert not is_in_mainland_china(*point)
    assert wgs84_to_gcj02(*point) == point
    assert gcj02_to_wgs84(*point) == point
    assert to_simulation({"lat": point[0], "lng": point[1]}) == {
        "lat": point[0],
        "lng": point[1],
    }


def test_special_regions_outside_mainland_are_not_converted():
    assert not is_in_mainland_china(22.3193, 114.1694)  # Hong Kong
    assert not is_in_mainland_china(23.6978, 120.9605)  # Taiwan


def test_automatic_mode_only_converts_inside_mainland_china():
    china = {"lat": 30.52802386594508, "lng": 120.7335575167566}
    outside = {"lat": 35.6762, "lng": 139.6503}

    assert to_simulation(china) != china
    assert to_simulation(outside) == outside
    assert to_simulation(china, "wgs84") == china
    assert to_simulation(china, "gcj02") == to_simulation(china)


def test_run1_passes_web_coordinate_transform_in_lat_lng_order(monkeypatch):
    captured = []

    async def set_location(_simulation, lat, lng):
        captured.append((lat, lng))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(run.location, "set_location", set_location)
    monkeypatch.setattr(run, "randLoc", lambda points, n: points)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    points = [
        {"lat": 30.5, "lng": 120.7},
        {"lat": 30.51, "lng": 120.71},
    ]
    asyncio.run(
        run.run1(
            object(),
            points,
            3.3,
            dt=0.2,
            coordinate_transform=lambda point: to_simulation(point, "wgs84"),
        )
    )

    assert captured[0] == pytest.approx((points[0]["lat"], points[0]["lng"]))


def test_cli_run1_keeps_legacy_bd09_transform_by_default(monkeypatch):
    captured = []

    async def set_location(_simulation, lat, lng):
        captured.append((lat, lng))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(run.location, "set_location", set_location)
    monkeypatch.setattr(run, "randLoc", lambda points, n: points)
    monkeypatch.setattr(run, "bd09Towgs84", lambda _point: {"lat": 1.25, "lng": 2.5})
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    asyncio.run(run.run1(object(), [{"lat": 30.5, "lng": 120.7}, {"lat": 30.51, "lng": 120.71}], 3.3, dt=0.2))

    assert captured[0] == (1.25, 2.5)
