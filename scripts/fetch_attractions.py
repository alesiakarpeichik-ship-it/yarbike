"""Одноразовая (запускается вручную при необходимости обновить базу)
выгрузка реальных именованных точек интереса Ярославля из OpenStreetMap
через бесплатный Overpass API (без ключа) — музеи, памятники, смотровые
площадки, арт-объекты и т.п.

Бот НЕ обращается к Overpass во время работы — только читает получившийся
attractions.csv, как и food_places.csv. Чтобы обновить базу, запустите:

    python scripts/fetch_attractions.py

Результат перезапишет attractions.csv в корне проекта. Стоит просмотреть
файл после генерации и удалить лишнее/неточное вручную.
"""
from __future__ import annotations

import csv
import sys
import urllib.parse
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_PATH = BASE_DIR / "attractions.csv"

# Ограничивающий прямоугольник вокруг Ярославля (юг, запад, север, восток)
BBOX = "57.55,39.75,57.71,39.97"

QUERY = f"""
[out:json][timeout:60];
(
  node["tourism"~"museum|attraction|viewpoint|artwork|gallery"]({BBOX});
  way["tourism"~"museum|attraction|viewpoint|artwork|gallery"]({BBOX});
  node["historic"]({BBOX});
  way["historic"]({BBOX});
);
out center tags;
"""

CATEGORY_LABELS = {
    "museum": "музей",
    "viewpoint": "смотровая площадка",
    "monastery": "монастырь",
    "city_gate": "городские ворота",
    "citywalls": "крепостная стена",
    "attraction": "достопримечательность",
    "artwork": "арт-объект",
    "gallery": "галерея",
    "memorial": "памятник",
    "monument": "монумент",
    "ruins": "руины",
    "wayside_shrine": "часовня",
    "wayside_cross": "поклонный крест",
    "epigraph": "памятная надпись",
    "vehicle": "техника-памятник",
    "locomotive": "техника-памятник",
}
# Значения тегов без содержательного смысла ("historic=yes") — пропускаем.
SKIP_VALUES = {"yes"}

FIELDNAMES = ["Название", "Категория", "Адрес", "latitude", "longitude", "Ссылка на Яндекс Карты", "osm_id"]


def fetch_elements() -> list[dict]:
    headers = {"User-Agent": "yarbike-attractions-fetch/1.0 (personal project)", "Accept": "*/*"}
    response = httpx.post(
        "https://overpass-api.de/api/interpreter", data={"data": QUERY}, headers=headers, timeout=90
    )
    response.raise_for_status()
    return response.json().get("elements", [])


def build_rows(elements: list[dict]) -> list[dict]:
    rows = []
    seen = set()
    for el in elements:
        tags = el.get("tags", {})
        name = (tags.get("name") or "").strip()
        if not name:
            continue

        raw_category = tags.get("tourism") or tags.get("historic")
        if raw_category in SKIP_VALUES:
            continue
        category = CATEGORY_LABELS.get(raw_category, raw_category)

        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lon is None:
            continue

        street = tags.get("addr:street", "")
        house = tags.get("addr:housenumber", "")
        address = f"{street}, {house}".strip(", ") if street else ""

        dedup_key = (name, round(lat, 4), round(lon, 4))
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        yandex_url = (
            f"https://yandex.ru/maps/?text={urllib.parse.quote(name + ' Ярославль')}"
            f"&ll={lon},{lat}&z=17"
        )

        rows.append(
            {
                "Название": name,
                "Категория": category,
                "Адрес": address,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "Ссылка на Яндекс Карты": yandex_url,
                "osm_id": el.get("id"),
            }
        )
    return rows


def main() -> None:
    elements = fetch_elements()
    rows = build_rows(elements)
    if not rows:
        print("Ничего не найдено — файл не изменён.")
        sys.exit(1)

    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Сохранено {len(rows)} точек в {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
