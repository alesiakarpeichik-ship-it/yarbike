"""Работа с OpenRouteService: построение кольцевого веломаршрута."""
from __future__ import annotations

import json
import logging
import math
import random
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)

ORS_URL = "https://api.openrouteservice.org/v2/directions/cycling-regular/geojson"

# Коды покрытия ORS extra_info=surface (см. документацию OpenRouteService)
SURFACE_LABELS = {
    1: "асфальт/твёрдое покрытие",
    2: "покрытие без уточнения",
    3: "асфальт",
    4: "бетон",
    5: "брусчатка",
    6: "металл",
    7: "дерево",
    8: "уплотнённый гравий",
    9: "мелкий гравий",
    10: "гравий",
    11: "грунт",
    12: "земля",
    13: "лёд",
    14: "тротуарная плитка",
    15: "песок",
    16: "щепа",
    17: "трава",
    18: "газонная решётка",
}


class RouteServiceError(Exception):
    """Ошибка получения или разбора маршрута."""


@dataclass
class RouteResult:
    geojson: dict[str, Any]
    coordinates: list[tuple[float, float]]  # (lon, lat), как отдаёт ORS
    distance_m: float
    duration_s: float
    street_names: list[str]
    street_segments: list[tuple[str, float]]  # (название, дистанция в метрах), по ходу движения
    surface_summary: list[str]
    seed: int
    start_name: str


def choose_start_point(last_start_name: str | None) -> dict[str, Any]:
    """Выбирает точку старта/финиша, по возможности не повторяя ту,
    что использовалась в прошлый раз, чтобы маршруты были разнообразнее."""
    candidates = config.START_POINTS
    if last_start_name and len(candidates) > 1:
        filtered = [p for p in candidates if p["name"] != last_start_name]
        if filtered:
            candidates = filtered
    return random.choice(candidates)


def compute_target_distance_km(departure_dt: datetime) -> float:
    deadline_dt = departure_dt.replace(
        hour=config.RETURN_DEADLINE.hour,
        minute=config.RETURN_DEADLINE.minute,
        second=0,
        microsecond=0,
    )
    available_minutes = (deadline_dt - departure_dt).total_seconds() / 60
    available_minutes -= config.RETURN_BUFFER_MINUTES
    available_minutes = max(available_minutes, 0.0)
    target_km = available_minutes / 60 * config.ASSUMED_CYCLING_SPEED_KMH
    return min(max(target_km, config.MIN_ROUTE_KM), config.MAX_ROUTE_KM)


async def _post_directions(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Authorization": config.OPENROUTESERVICE_API_KEY,
        "Content-Type": "application/json",
    }
    try:
        response = await client.post(ORS_URL, json=body, headers=headers, timeout=25.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("Ошибка запроса к OpenRouteService: %s", exc)
        raise RouteServiceError("Не удалось построить маршрут") from exc
    return response.json()


async def _request_round_trip(
    client: httpx.AsyncClient,
    seed: int,
    length_km: float,
    start_lat: float,
    start_lon: float,
) -> dict[str, Any]:
    body = {
        "coordinates": [[start_lon, start_lat]],
        "instructions": True,
        "instructions_format": "text",
        "language": "ru",
        "extra_info": ["surface"],
        "options": {
            "round_trip": {
                "length": length_km * 1000,
                "points": config.ROUND_TRIP_POINTS,
                "seed": seed,
            },
            "avoid_features": ["steps", "ferries"],
        },
    }
    return await _post_directions(client, body)


async def _request_via_waypoint(
    client: httpx.AsyncClient,
    start_lat: float,
    start_lon: float,
    waypoint_lat: float,
    waypoint_lon: float,
) -> dict[str, Any]:
    """Обычный (не round_trip) маршрут по трём точкам: старт → точка
    (например, столовая) → тот же старт. В отличие от round_trip, здесь
    можно задать конкретную точку, через которую должен пройти маршрут."""
    body = {
        "coordinates": [
            [start_lon, start_lat],
            [waypoint_lon, waypoint_lat],
            [start_lon, start_lat],
        ],
        "instructions": True,
        "instructions_format": "text",
        "language": "ru",
        "extra_info": ["surface"],
        "options": {"avoid_features": ["steps", "ferries"]},
    }
    return await _post_directions(client, body)


def _build_street_segments(properties: dict[str, Any]) -> list[tuple[str, float]]:
    """Склеивает подряд идущие шаги ORS в сегменты по названию улицы,
    суммируя реальную дистанцию из ответа API. Безымянные шаги (name == "-")
    присоединяются к предыдущему именованному сегменту."""
    segments: list[list[Any]] = []
    for segment in properties.get("segments", []):
        for step in segment.get("steps", []):
            name = (step.get("name") or "").strip()
            distance = float(step.get("distance") or 0.0)
            if name and name != "-":
                if segments and segments[-1][0] == name:
                    segments[-1][1] += distance
                else:
                    segments.append([name, distance])
            elif segments:
                segments[-1][1] += distance
            else:
                segments.append(["участок без названия", distance])
    return [(name, dist) for name, dist in segments]


def _summarize_surface(properties: dict[str, Any]) -> list[str]:
    extras = properties.get("extras", {})
    surface = extras.get("surface")
    if not surface:
        return []
    summary_items = surface.get("summary", [])
    result = []
    for item in sorted(summary_items, key=lambda x: -x.get("amount", 0)):
        label = SURFACE_LABELS.get(item.get("value"), "неизвестное покрытие")
        amount = item.get("amount", 0)
        result.append(f"{label} — {amount:.0f}%")
    return result


def parse_route(raw: dict[str, Any], seed: int, start_name: str = "") -> RouteResult:
    features = raw.get("features")
    if not features:
        raise RouteServiceError("OpenRouteService не вернул маршрут")

    feature = features[0]
    properties = feature.get("properties", {})
    summary = properties.get("summary", {})
    geometry = feature.get("geometry", {})
    coords_raw = geometry.get("coordinates", [])
    coordinates = [(c[0], c[1]) for c in coords_raw]

    distance_m = float(summary.get("distance", 0.0))
    duration_s = float(summary.get("duration", 0.0))

    street_names: list[str] = []
    for segment in properties.get("segments", []):
        for step in segment.get("steps", []):
            name = (step.get("name") or "").strip()
            if name and name != "-" and name not in street_names:
                street_names.append(name)

    surface_summary = _summarize_surface(properties)
    street_segments = _build_street_segments(properties)

    if distance_m <= 0 or not coordinates:
        raise RouteServiceError("OpenRouteService вернул некорректный маршрут")

    return RouteResult(
        geojson=raw,
        coordinates=coordinates,
        distance_m=distance_m,
        duration_s=duration_s,
        street_names=street_names,
        street_segments=street_segments,
        surface_summary=surface_summary,
        seed=seed,
        start_name=start_name,
    )


# Максимальная длина ссылки geojson.io, чтобы гарантированно оставить место
# под остальной текст сообщения в пределах лимита Telegram (4096 символов).
GEOJSON_IO_URL_BUDGET = 1500
RDP_START_EPSILON_M = 3.0
RDP_MAX_ITERATIONS = 25


def _project_to_local_meters(lon: float, lat: float, ref_lat: float) -> tuple[float, float]:
    r = 6371000.0
    x = math.radians(lon) * math.cos(math.radians(ref_lat)) * r
    y = math.radians(lat) * r
    return x, y


def _perpendicular_distance_m(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    x0, y0 = point
    x1, y1 = start
    x2, y2 = end
    if (x1, y1) == (x2, y2):
        return math.hypot(x0 - x1, y0 - y1)
    numerator = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
    denominator = math.hypot(y2 - y1, x2 - x1)
    return numerator / denominator


def _douglas_peucker(points: list[tuple], epsilon_m: float) -> list[tuple]:
    """points — кортежи (x_m, y_m, lon, lat). Возвращает подмножество точек,
    сохраняющее форму линии в пределах epsilon_m (в метрах), а не просто
    каждую N-ю точку — так повороты и изгибы дороги не срезаются."""
    if len(points) < 3:
        return points

    start, end = points[0], points[-1]
    max_dist = 0.0
    split_index = 0
    for i in range(1, len(points) - 1):
        dist = _perpendicular_distance_m(points[i][:2], start[:2], end[:2])
        if dist > max_dist:
            max_dist = dist
            split_index = i

    if max_dist > epsilon_m:
        left = _douglas_peucker(points[: split_index + 1], epsilon_m)
        right = _douglas_peucker(points[split_index:], epsilon_m)
        return left[:-1] + right
    return [start, end]


def build_route_map_link(route: RouteResult) -> str | None:
    """Ссылка на бесплатный просмотрщик geojson.io с реальной геометрией
    маршрута, упрощённой алгоритмом Дугласа-Пекера (сохраняет повороты,
    в отличие от отбора каждой N-й точки) под лимит длины сообщения Telegram."""
    coords = route.coordinates
    if not coords:
        return None

    ref_lat = sum(lat for _, lat in coords) / len(coords)
    points = [
        (*_project_to_local_meters(lon, lat, ref_lat), lon, lat) for lon, lat in coords
    ]

    epsilon = RDP_START_EPSILON_M
    url = None
    for _ in range(RDP_MAX_ITERATIONS):
        simplified = _douglas_peucker(points, epsilon)
        geometry = {
            "type": "LineString",
            "coordinates": [[round(lon, 5), round(lat, 5)] for *_xy, lon, lat in simplified],
        }
        encoded = urllib.parse.quote(json.dumps(geometry, separators=(",", ":")), safe="")
        url = f"https://geojson.io/#data=data:application/json,{encoded}"
        if len(url) <= GEOJSON_IO_URL_BUDGET or len(simplified) <= 2:
            return url
        epsilon *= 1.6
    return url


def validate_route(route: RouteResult, departure_dt: datetime) -> bool:
    distance_km = route.distance_m / 1000
    if not (config.MIN_ROUTE_KM - 3 <= distance_km <= config.MAX_ROUTE_KM + 3):
        return False
    return_dt = departure_dt + timedelta(seconds=route.duration_s)
    deadline_dt = departure_dt.replace(
        hour=config.RETURN_DEADLINE.hour,
        minute=config.RETURN_DEADLINE.minute,
        second=0,
        microsecond=0,
    ) - timedelta(minutes=config.RETURN_BUFFER_MINUTES)
    return return_dt <= deadline_dt


def route_signature(route: RouteResult) -> set[tuple[float, float]]:
    return {(round(lon, 3), round(lat, 3)) for lon, lat in route.coordinates}


def is_too_similar(new_route: RouteResult, last_record: dict[str, Any] | None) -> bool:
    if not last_record:
        return False
    if new_route.seed == last_record.get("seed"):
        return True
    old_sig = {tuple(pair) for pair in last_record.get("signature", [])}
    new_sig = route_signature(new_route)
    if not old_sig or not new_sig:
        return False
    intersection = len(old_sig & new_sig)
    union = len(old_sig | new_sig)
    similarity = intersection / union if union else 0.0
    return similarity >= config.ROUTE_SIMILARITY_THRESHOLD


async def build_route(
    client: httpx.AsyncClient,
    departure_dt: datetime,
    last_record: dict[str, Any] | None,
    start_point: dict[str, Any],
) -> RouteResult:
    target_km = compute_target_distance_km(departure_dt)
    attempts = 0
    used_seeds: set[int] = set()
    last_error: Exception | None = None

    while attempts < config.MAX_ROUTE_ATTEMPTS:
        attempts += 1
        seed = random.randint(1, 1_000_000)
        while seed in used_seeds:
            seed = random.randint(1, 1_000_000)
        used_seeds.add(seed)

        try:
            raw = await _request_round_trip(
                client, seed, target_km, start_point["lat"], start_point["lon"]
            )
            route = parse_route(raw, seed, start_name=start_point["name"])
        except RouteServiceError as exc:
            last_error = exc
            continue

        if not validate_route(route, departure_dt):
            target_km = max(config.MIN_ROUTE_KM, target_km * 0.8)
            last_error = RouteServiceError("Маршрут не укладывается в требуемое время/дистанцию")
            continue

        if is_too_similar(route, last_record):
            last_error = RouteServiceError("Маршрут слишком похож на предыдущий")
            continue

        return route

    raise RouteServiceError("Не удалось построить подходящий маршрут") from last_error


def first_leg_duration_s(route: RouteResult) -> float | None:
    """Длительность первого отрезка маршрута (старт → точка) в секундах —
    только для маршрутов, построенных через build_route_via_waypoint.
    Для обычных round_trip-маршрутов возвращает None."""
    try:
        segments = route.geojson["features"][0]["properties"]["segments"]
    except (KeyError, IndexError, TypeError):
        return None
    if len(segments) < 2:
        return None
    return float(segments[0].get("duration", 0.0))


async def build_route_via_waypoint(
    client: httpx.AsyncClient,
    start_point: dict[str, Any],
    waypoint_lat: float,
    waypoint_lon: float,
) -> RouteResult:
    """Строит маршрут старт → заданная точка → старт (например, через
    столовую), а не случайный round_trip. Без повторных попыток внутри —
    при неудаче вызывающий код должен попробовать другую точку."""
    raw = await _request_via_waypoint(
        client, start_point["lat"], start_point["lon"], waypoint_lat, waypoint_lon
    )
    # seed здесь не используется ORS (это не round_trip), но нужен как
    # уникальный идентификатор для сравнения "слишком похожих" маршрутов.
    seed = random.randint(1, 1_000_000)
    return parse_route(raw, seed, start_name=start_point["name"])
