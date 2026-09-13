"""Coordinate conversions used at the Web UI boundary.

The Web UI and saved routes use WGS-84 coordinates. The legacy CLI route
pipeline remains unchanged; its historical BD-09 conversion is still the
default in :mod:`run`.
"""

from __future__ import annotations

import math
from typing import Literal

CoordinateMode = Literal["automatic", "wgs84", "gcj02"]

_PI = math.pi
_A = 6378245.0
_EE = 0.00669342162296594323


def is_in_mainland_china(lat: float, lng: float) -> bool:
    """Return whether a point is in the mainland China conversion region."""

    if not (3.86 <= lat <= 53.55 and 73.66 <= lng <= 135.05):
        return False
    # Keep the mainland policy explicit; the broad bounding box also covers
    # Taiwan, Hong Kong, and Macao, which have different map policies.
    if 21.5 <= lat <= 25.5 and 119.0 <= lng <= 122.5:
        return False
    return not (22.0 <= lat <= 22.7 and 113.7 <= lng <= 114.5)


def _transform_lat(x: float, y: float) -> float:
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    value += 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    value += (160.0 * math.sin(y / 12.0 * _PI) + 320.0 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return value


def _transform_lng(x: float, y: float) -> float:
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    value += 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    value += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return value


def _delta(lat: float, lng: float) -> tuple[float, float]:
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * _PI
    magic = 1.0 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / (_A * (1.0 - _EE) / (magic * sqrt_magic) * _PI)
    d_lng = (d_lng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * _PI)
    return d_lat, d_lng


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """Convert one WGS-84 point to GCJ-02, or return it unchanged outside China."""

    if not is_in_mainland_china(lat, lng):
        return lat, lng
    d_lat, d_lng = _delta(lat, lng)
    return lat + d_lat, lng + d_lng


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    """Convert one GCJ-02 point to WGS-84, or return it unchanged outside China."""

    if not is_in_mainland_china(lat, lng):
        return lat, lng
    d_lat, d_lng = _delta(lat, lng)
    return lat - d_lat, lng - d_lng


def to_simulation(point: dict[str, float], mode: CoordinateMode = "automatic") -> dict[str, float]:
    """Convert a canonical Web UI point to the coordinate sent to DVT."""

    if mode == "wgs84":
        return point.copy()
    if mode not in {"automatic", "gcj02"}:
        raise ValueError(f"unsupported coordinate mode: {mode}")
    lat, lng = wgs84_to_gcj02(point["lat"], point["lng"])
    return {"lat": lat, "lng": lng}
