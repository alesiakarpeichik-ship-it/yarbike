"""Чтение проверенных заведений из CSV и поиск ближайшего к маршруту.

Формат CSV (реальный экспорт пользователя, разделитель ';', кавычки):
Название;Тип;Адрес;Район / ориентир;Дни работы;Открытие;Закрытие;
Подходит утром 07:30-09:30;latitude, longitude;Ссылка на Яндекс Карты;
Статус проверки;Комментарий для AI

Координаты хранятся ОДНОЙ колонкой "latitude, longitude" со значением вида
"57.630111, 39.871634" (широта, долгота через запятую).

Колонка "Комментарий для AI" сознательно НЕ читается: бот не использует
ИИ во время работы, поэтому вся логика опирается только на структурные
поля (Дни работы / Открытие / Закрытие / Статус проверки / latitude, longitude).
Заведение считается подтверждённым, если поле "Статус проверки" непустое.
"""
from __future__ import annotations

import csv
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import config

logger = logging.getLogger(__name__)

TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")

# В реальном файле пользователя широта и долгота хранятся одной колонкой.
COORDINATES_COLUMN = "latitude, longitude"

# Единственные распознаваемые графики дней недели в исходных данных.
# Всё остальное (например "уточнить", "не рекомендовать без проверки")
# намеренно считается неподтверждённым — код не додумывает смысл текста.
DAY_PATTERNS: dict[str, set[int]] = {
    "ежедневно": {0, 1, 2, 3, 4, 5, 6},
    "пн–пт": {0, 1, 2, 3, 4},
    "пн-пт": {0, 1, 2, 3, 4},
}


@dataclass
class FoodMatch:
    name: str
    address: str
    hours_text: str
    yandex_url: str
    distance_m: float


@dataclass
class FoodCandidate:
    """Проверенное заведение с координатами — кандидат, через который
    можно проложить маршрут (а не просто проверить постфактум)."""

    place: dict[str, str]
    lat: float
    lon: float
    distance_from_start_m: float

    @property
    def name(self) -> str:
        return (self.place.get("Название") or "").strip()


def load_places(path: Path | None = None) -> list[dict[str, str]]:
    csv_path = path or config.FOOD_CSV_PATH
    if not csv_path.exists():
        logger.warning("Файл со столовыми не найден: %s", csv_path)
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        return [row for row in reader]


def _parse_time_or_none(value: str | None) -> time | None:
    if not value:
        return None
    value = value.strip()
    if not TIME_RE.match(value):
        return None
    hours, minutes = value.split(":")
    hours, minutes = int(hours), int(minutes)
    if hours > 23 or minutes > 59:
        return None
    if hours == 24:
        hours = 0
    return time(hours, minutes)


def _parse_day_pattern(value: str | None) -> set[int] | None:
    if not value:
        return None
    return DAY_PATTERNS.get(value.strip().lower())


def is_confirmed(place: dict[str, str]) -> bool:
    """Заведение пригодно к рекомендации: поле "Статус проверки" непустое,
    график дней распознан, часы открытия/закрытия корректны."""
    if not (place.get("Статус проверки") or "").strip():
        return False
    if _parse_day_pattern(place.get("Дни работы")) is None:
        return False
    open_t = _parse_time_or_none(place.get("Открытие"))
    close_t = _parse_time_or_none(place.get("Закрытие"))
    return open_t is not None and close_t is not None


def _parse_coordinates(place: dict[str, str]) -> tuple[float, float] | None:
    raw = (place.get(COORDINATES_COLUMN) or "").strip()
    if not raw:
        return None
    parts = raw.split(",")
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None
    return lat, lon


def is_open_at(place: dict[str, str], dt: datetime) -> bool:
    if not is_confirmed(place):
        return False
    weekdays = _parse_day_pattern(place.get("Дни работы"))
    if dt.weekday() not in weekdays:
        return False
    open_t = _parse_time_or_none(place.get("Открытие"))
    close_t = _parse_time_or_none(place.get("Закрытие"))
    t = dt.time()
    if close_t < open_t:
        # график через полночь (например 07:00-00:00 или 05:00-04:00)
        return t >= open_t or t <= close_t
    return open_t <= t <= close_t


def format_hours_text(place: dict[str, str]) -> str:
    days_raw = (place.get("Дни работы") or "").strip()
    open_str = (place.get("Открытие") or "").strip()
    close_str = (place.get("Закрытие") or "").strip()
    return f"{days_raw.capitalize()} {open_str}–{close_str}"


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


def get_places_open_on_weekday(
    places: list[dict[str, str]], weekday: int
) -> list[dict[str, str]]:
    """Подтверждённые заведения, чей распознанный график включает этот день
    недели (без проверки конкретного времени — точное время известно только
    после построения маршрута)."""
    result = []
    for place in places:
        if not is_confirmed(place):
            continue
        weekdays = _parse_day_pattern(place.get("Дни работы"))
        if weekdays and weekday in weekdays:
            result.append(place)
    return result


def find_candidates_near_point(
    places: list[dict[str, str]],
    lat: float,
    lon: float,
    max_distance_m: float,
) -> list[FoodCandidate]:
    """Заведения с координатами в пределах max_distance_m от точки (например,
    от старта маршрута) — кандидаты, чтобы проложить маршрут именно через них."""
    candidates = []
    for place in places:
        coords = _parse_coordinates(place)
        if coords is None:
            continue
        place_lat, place_lon = coords
        distance = haversine_m(lat, lon, place_lat, place_lon)
        if distance <= max_distance_m:
            candidates.append(FoodCandidate(place, place_lat, place_lon, distance))
    return candidates


def find_food_near_route(
    places: list[dict[str, str]],
    route_coords: list[tuple[float, float]],
    arrival_dt: datetime,
    max_distance_m: float | None = None,
) -> FoodMatch | None:
    threshold = max_distance_m if max_distance_m is not None else config.FOOD_MAX_DISTANCE_M
    best: FoodMatch | None = None

    for place in places:
        if not is_open_at(place, arrival_dt):
            continue
        coords = _parse_coordinates(place)
        if coords is None:
            # Координат нет или они некорректны — расстояние до маршрута
            # проверить нельзя, заведение честно не учитывается.
            continue
        lat, lon = coords
        if not route_coords:
            continue
        distance = min_distance_to_route(lat, lon, route_coords)
        if distance > threshold:
            continue
        if best is None or distance < best.distance_m:
            best = FoodMatch(
                name=(place.get("Название") or "").strip(),
                address=(place.get("Адрес") or "").strip(),
                hours_text=format_hours_text(place),
                yandex_url=(place.get("Ссылка на Яндекс Карты") or "").strip(),
                distance_m=distance,
            )
    return best
