from datetime import datetime
from zoneinfo import ZoneInfo

import config
from services.food import (
    find_candidates_near_point,
    find_food_near_route,
    format_hours_text,
    get_places_open_on_weekday,
    haversine_m,
    is_confirmed,
    is_open_at,
    load_places,
)

TZ = ZoneInfo(config.TIMEZONE_NAME)

CSV_HEADER = (
    "Название;Тип;Адрес;Район / ориентир;Дни работы;Открытие;Закрытие;"
    "Подходит утром 07:30-09:30;latitude, longitude;Ссылка на Яндекс Карты;"
    "Статус проверки;Комментарий для AI\n"
)


def _row(
    name="Столовая №1",
    address="ул. Свободы, 1",
    days="ежедневно",
    open_="08:00",
    close="20:00",
    status="проверено по Яндекс Картам",
    lat="57.6270",
    lon="39.8950",
    url="https://yandex.ru/maps/1",
):
    coords = f"{lat}, {lon}" if lat and lon else ""
    return (
        f'"{name}";"столовая";"{address}";"центр";"{days}";"{open_}";"{close}";'
        f'"да";"{coords}";"{url}";"{status}";"комментарий"'
    )


def _write_csv(tmp_path, rows: list[str]):
    path = tmp_path / "food_places.csv"
    path.write_text(CSV_HEADER + "\n".join(rows), encoding="utf-8")
    return path


def test_is_confirmed_requires_status_days_and_hours():
    assert is_confirmed(
        {"Статус проверки": "проверено", "Дни работы": "ежедневно", "Открытие": "08:00", "Закрытие": "20:00"}
    ) is True
    assert is_confirmed(
        {"Статус проверки": "", "Дни работы": "ежедневно", "Открытие": "08:00", "Закрытие": "20:00"}
    ) is False
    assert is_confirmed(
        {"Статус проверки": "проверено", "Дни работы": "уточнить", "Открытие": "08:00", "Закрытие": "20:00"}
    ) is False
    assert is_confirmed(
        {"Статус проверки": "проверено", "Дни работы": "ежедневно", "Открытие": "не указано", "Закрытие": "20:00"}
    ) is False


def test_is_open_at_respects_day_pattern():
    place = {"Статус проверки": "ок", "Дни работы": "пн–пт", "Открытие": "09:00", "Закрытие": "15:00"}
    wed = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)  # среда
    sat = datetime(2026, 9, 12, 10, 0, tzinfo=TZ)  # суббота
    assert is_open_at(place, wed) is True
    assert is_open_at(place, sat) is False


def test_is_open_at_handles_overnight_schedule():
    place = {"Статус проверки": "ок", "Дни работы": "ежедневно", "Открытие": "07:00", "Закрытие": "00:00"}
    morning = datetime(2026, 9, 9, 8, 0, tzinfo=TZ)
    early_night = datetime(2026, 9, 9, 5, 0, tzinfo=TZ)
    assert is_open_at(place, morning) is True
    assert is_open_at(place, early_night) is False


def test_format_hours_text_uses_csv_values_verbatim():
    place = {"Дни работы": "пн–пт", "Открытие": "09:00", "Закрытие": "15:00"}
    assert format_hours_text(place) == "Пн–пт 09:00–15:00"


def test_haversine_zero_distance():
    assert haversine_m(57.62, 39.89, 57.62, 39.89) == 0


def test_load_places_and_match_route(tmp_path):
    row_ok = _row(name="Столовая №1", lat="57.6270", lon="39.8950")
    row_unconfirmed = _row(name="Столовая Уточнить", days="уточнить", open_="не указано", close="не указано")
    row_far = _row(name="Столовая Далёкая", lat="57.7000", lon="40.0000")
    csv_path = _write_csv(tmp_path, [row_ok, row_unconfirmed, row_far])

    places = load_places(csv_path)
    assert len(places) == 3

    route_coords = [(39.8948, 57.6270), (39.8960, 57.6275)]
    arrival_dt = datetime(2026, 9, 9, 8, 30, tzinfo=TZ)  # среда

    match = find_food_near_route(places, route_coords, arrival_dt, max_distance_m=700)
    assert match is not None
    assert match.name == "Столовая №1"
    assert match.yandex_url == "https://yandex.ru/maps/1"


def test_no_match_without_coordinates(tmp_path):
    row_no_coords = _row(name="Столовая Без Координат", lat="", lon="")
    csv_path = _write_csv(tmp_path, [row_no_coords])
    places = load_places(csv_path)

    route_coords = [(39.8948, 57.6270)]
    arrival_dt = datetime(2026, 9, 9, 8, 30, tzinfo=TZ)

    match = find_food_near_route(places, route_coords, arrival_dt, max_distance_m=700)
    assert match is None


def test_no_match_with_malformed_coordinates(tmp_path):
    row = _row(name="Столовая Кривые Координаты")
    # ломаем координаты вручную: одно число вместо пары "лат, лон"
    row = row.replace('"57.6270, 39.8950"', '"57.6270"')
    csv_path = _write_csv(tmp_path, [row])
    places = load_places(csv_path)

    route_coords = [(39.8948, 57.6270)]
    arrival_dt = datetime(2026, 9, 9, 8, 30, tzinfo=TZ)

    match = find_food_near_route(places, route_coords, arrival_dt, max_distance_m=700)
    assert match is None


def test_no_match_when_not_open(tmp_path):
    row = _row(name="Столовая №1", days="пн–пт")
    csv_path = _write_csv(tmp_path, [row])
    places = load_places(csv_path)

    route_coords = [(39.8948, 57.6270)]
    arrival_dt = datetime(2026, 9, 12, 8, 30, tzinfo=TZ)  # суббота, закрыто

    match = find_food_near_route(places, route_coords, arrival_dt, max_distance_m=700)
    assert match is None


def test_get_places_open_on_weekday_filters_unconfirmed_and_wrong_day(tmp_path):
    row_daily = _row(name="Ежедневная", days="ежедневно")
    row_weekdays = _row(name="Будни", days="пн–пт")
    row_unconfirmed = _row(name="Неясная", days="уточнить", open_="не указано", close="не указано")
    csv_path = _write_csv(tmp_path, [row_daily, row_weekdays, row_unconfirmed])
    places = load_places(csv_path)

    wednesday = 2
    saturday = 5
    names_wed = {p["Название"] for p in get_places_open_on_weekday(places, wednesday)}
    names_sat = {p["Название"] for p in get_places_open_on_weekday(places, saturday)}

    assert names_wed == {"Ежедневная", "Будни"}
    assert names_sat == {"Ежедневная"}


def test_find_candidates_near_point_respects_distance_and_coordinates(tmp_path):
    row_close = _row(name="Рядом", lat="57.6270", lon="39.8950")
    row_far = _row(name="Далеко", lat="57.7000", lon="40.0000")
    row_no_coords = _row(name="Без координат", lat="", lon="")
    csv_path = _write_csv(tmp_path, [row_close, row_far, row_no_coords])
    places = load_places(csv_path)

    candidates = find_candidates_near_point(places, 57.6270, 39.8948, max_distance_m=1000)

    names = {c.name for c in candidates}
    assert names == {"Рядом"}
    assert candidates[0].distance_from_start_m < 1000
