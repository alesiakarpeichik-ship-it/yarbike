"""Общие геометрические вычисления (расстояния по сфере)."""
from __future__ import annotations

import math


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def min_distance_to_route(
    lat: float, lon: float, route_coords: list[tuple[float, float]]
) -> float:
    return min(haversine_m(lat, lon, c_lat, c_lon) for c_lon, c_lat in route_coords)
