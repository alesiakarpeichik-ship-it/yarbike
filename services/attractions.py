"""Точки интереса рядом с маршрутом ("что посмотреть по дороге").

Данные — одноразовая выгрузка реальных именованных объектов из OpenStreetMap
(см. scripts/fetch_attractions.py) для Ярославля: музеи, памятники, смотровые
площадки, арт-объекты и т.п. Бот не обращается к OpenStreetMap во время
работы — читает только сохранённый CSV, как и с food_places.csv.

Формат CSV (разделитель ';'):
Название;Категория;Адрес;latitude;longitude;Ссылка на Яндекс Карты;osm_id
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import config
from services.geo import haversine_m, min_distance_to_route

logger = logging.getLogger(__name__)


@dataclass
class AttractionMatch:
    name: str
    category: str
    address: str
    yandex_url: str
    distance_m: float


def load_attractions(path: Path | None = None) -> list[dict[str, str]]:
    csv_path = path or config.ATTRACTIONS_CSV_PATH
    if not csv_path.exists():
        logger.warning("Файл с достопримечательностями не найден: %s", csv_path)
        return []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        return [row for row in reader]


def find_attractions_near_route(
    places: list[dict[str, str]],
    route_coords: list[tuple[float, float]],
    max_distance_m: float | None = None,
    max_count: int | None = None,
) -> list[AttractionMatch]:
    threshold = max_distance_m if max_distance_m is not None else config.ATTRACTION_MAX_DISTANCE_M
    limit = max_count if max_count is not None else config.MAX_ATTRACTIONS_DISPLAY

    if not route_coords:
        return []

    matches: list[AttractionMatch] = []
    for place in places:
        name = (place.get("Название") or "").strip()
        lat_str = (place.get("latitude") or "").strip()
        lon_str = (place.get("longitude") or "").strip()
        if not name or not lat_str or not lon_str:
            continue
        try:
            lat, lon = float(lat_str), float(lon_str)
        except ValueError:
            continue

        distance = min_distance_to_route(lat, lon, route_coords)
        if distance > threshold:
            continue

        matches.append(
            AttractionMatch(
                name=name,
                category=(place.get("Категория") or "").strip(),
                address=(place.get("Адрес") or "").strip(),
                yandex_url=(place.get("Ссылка на Яндекс Карты") or "").strip(),
                distance_m=distance,
            )
        )

    matches.sort(key=lambda m: m.distance_m)
    return matches[:limit]
