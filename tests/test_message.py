from datetime import date, datetime
from zoneinfo import ZoneInfo

import config
from services.attractions import AttractionMatch
from services.food import FoodMatch
from services.message import format_message, format_unsafe_message
from services.routes import RouteResult
from services.weather import WeatherWindow

TZ = ZoneInfo(config.TIMEZONE_NAME)


def _sample_window():
    start = datetime(2026, 9, 9, 7, 0, tzinfo=TZ)
    end = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)
    return WeatherWindow(
        day=date(2026, 9, 9),
        start_dt=start,
        end_dt=end,
        avg_temperature=18.0,
        avg_apparent_temperature=17.0,
        max_precipitation_probability=10.0,
        total_precipitation=0.0,
        max_wind_speed=12.0,
        max_wind_gust=20.0,
        weather_codes={1},
        safe=True,
        unsafe_reason=None,
        score=5.0,
    )


# 9 сегментов: проверяем, что в сообщении останутся только 7 самых длинных
# (в порядке проезда), а два самых коротких свернутся в одну строку.
SAMPLE_SEGMENTS = [
    ("Первомайская улица", 900.0),
    ("Советская улица", 1500.0),
    ("Переулок А", 50.0),
    ("Улица Б", 1200.0),
    ("Улица В", 300.0),
    ("Улица Г", 100.0),
    ("Улица Д", 800.0),
    ("Улица Е", 60.0),
    ("Улица Ё", 400.0),
]


def _sample_route(unpaved_percentage=20.0):
    return RouteResult(
        geojson={},
        coordinates=[(39.89, 57.62), (39.90, 57.63)],
        distance_m=14000.0,
        duration_s=3000.0,
        street_names=[name for name, _ in SAMPLE_SEGMENTS],
        street_segments=SAMPLE_SEGMENTS,
        surface_summary=["асфальт — 80%", "грунт — 20%"],
        unpaved_percentage=unpaved_percentage,
        seed=123,
        start_name="Волковская площадь",
    )


def test_format_message_contains_all_sections():
    window = _sample_window()
    route = _sample_route()
    food = FoodMatch(
        name="Столовая №1",
        address="ул. Свободы, 1",
        hours_text="Пн-Пт 08:00-20:00",
        yandex_url="https://yandex.ru/maps/1",
        distance_m=150.0,
    )
    attractions = [
        AttractionMatch(
            name="Ярославский музей-заповедник",
            category="музей",
            address="",
            yandex_url="https://yandex.ru/maps/?text=museum",
            distance_m=120.0,
        ),
        AttractionMatch(
            name="Смотровая площадка",
            category="",
            address="",
            yandex_url="не указано",
            distance_m=300.0,
        ),
    ]
    text = format_message(window, route, food, "Тестовое объяснение выбора.", attractions)

    for fragment in [
        "🚲 Утренняя велопрогулка по Ярославлю",
        "📅 День и дата:",
        "⏰ Выезд: 07:00",
        "🏁 Возвращение:",
        "📏 Расстояние: 14.0 км",
        "🌤 ПОГОДА",
        "Температура:",
        "🚦 МАРШРУТ",
        "Старт: Волковская площадь",
        '<a href="https://geojson.io/',
        ">Маршрут на карте</a>",
        "1. Первомайская улица — 900 м",
        "2. Советская улица — 1.5 км",
        "3. Улица Б — 1.2 км",
        "4. Улица В — 300 м",
        "5. Улица Г — 100 м",
        "6. Улица Д — 800 м",
        "7. Улица Ё — 400 м",
        "и ещё 2 участков",
        "Уровень сложности: Лёгкий — Асфальт, слабый ветер, без осадков — ничего осложняющего не ожидается.",
        "Финиш: Волковская площадь",
        "🍽 ГДЕ ПОЕСТЬ",
        "Название: Столовая №1",
        'Яндекс Карты: <a href="https://yandex.ru/maps/1">открыть</a>',
        "🗺 ЧТО ПОСМОТРЕТЬ ПО ДОРОГЕ",
        '- Ярославский музей-заповедник (музей) — <a href="https://yandex.ru/maps/?text=museum">на карте</a>',
        "- Смотровая площадка",
        "Тестовое объяснение выбора.",
    ]:
        assert fragment in text, fragment

    assert "Точка старта на карте" not in text
    # самая короткая улица (Переулок А, 50 м) не должна попасть в топ-7
    assert "Переулок А" not in text
    # у "Смотровая площадка" нет категории и некорректная ссылка — строка простая, без <a href>
    viewpoint_line = next(line for line in text.splitlines() if "Смотровая площадка" in line)
    assert viewpoint_line == "- Смотровая площадка"


def test_format_message_no_food_available():
    window = _sample_window()
    route = _sample_route()
    text = format_message(window, route, None, "Объяснение.")
    assert "Открытой столовой рядом с маршрутом в это время нет" in text
    assert "Интересных мест рядом с маршрутом не нашлось" in text


def test_format_message_escapes_html_special_chars():
    window = _sample_window()
    route = _sample_route()
    food = FoodMatch(
        name="Кафе <Тест> & Co",
        address="ул. Свободы, 1",
        hours_text="09:00-20:00",
        yandex_url="https://yandex.ru/maps/1",
        distance_m=100.0,
    )
    text = format_message(window, route, food, "Объяснение.")
    assert "Кафе <Тест> & Co" not in text
    assert "Кафе &lt;Тест&gt; &amp; Co" in text


def test_format_message_shows_non_url_yandex_field_as_plain_text():
    window = _sample_window()
    route = _sample_route()
    food = FoodMatch(
        name="Столовая №1",
        address="ул. Свободы, 1",
        hours_text="09:00-20:00",
        yandex_url="нет карточки в Яндекс Картах",
        distance_m=100.0,
    )
    text = format_message(window, route, food, "Объяснение.")
    assert "Яндекс Карты: нет карточки в Яндекс Картах" in text
    assert "<a href" not in text.split("ГДЕ ПОЕСТЬ")[1]


def test_difficulty_medium_with_one_hard_factor():
    window = _sample_window()
    window.max_precipitation_probability = 45.0  # выше DIFFICULTY_HIGH_RAIN_PROBABILITY_PCT
    route = _sample_route(unpaved_percentage=5.0)
    text = format_message(window, route, None, "Объяснение.")
    assert "Уровень сложности: Средний — Учтите: высокая вероятность дождя." in text


def test_difficulty_hard_with_two_factors():
    window = _sample_window()
    window.max_wind_gust = 40.0  # выше DIFFICULTY_STRONG_GUST_KMH
    route = _sample_route(unpaved_percentage=30.0)  # выше DIFFICULTY_UNPAVED_THRESHOLD_PCT
    text = format_message(window, route, None, "Объяснение.")
    assert (
        "Уровень сложности: Сложный — Учтите: грунтовое покрытие на части пути, сильный ветер."
        in text
    )


def test_difficulty_mentions_soft_factors_without_raising_level():
    window = _sample_window()
    window.max_wind_gust = 28.0  # выше NOTABLE, ниже STRONG
    route = _sample_route(unpaved_percentage=5.0)
    text = format_message(window, route, None, "Объяснение.")
    assert "Уровень сложности: Лёгкий — Учтите: заметный ветер." in text


def test_format_unsafe_message():
    reasons = {date(2026, 9, 9): "гроза", date(2026, 9, 10): "сильный дождь"}
    text = format_unsafe_message(reasons)
    assert "безопасный маршрут построить нельзя" in text
    assert "гроза" in text
    assert "сильный дождь" in text
