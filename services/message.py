"""Форматирование итогового сообщения для Telegram (HTML parse mode)."""
from __future__ import annotations

import html
from datetime import date, timedelta

import config
from services.attractions import AttractionMatch
from services.food import FoodMatch
from services.routes import RouteResult, build_route_map_link
from services.weather import WeatherWindow

WEEKDAY_NAMES_RU = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _format_street_segments(segments: list[tuple[str, float]]) -> list[str]:
    """Показывает только самые протяжённые улицы (config.MAX_DISPLAY_STREET_SEGMENTS
    штук), в порядке проезда, а не в порядке убывания длины."""
    if not segments:
        return ["данные по улицам недоступны"]

    max_display = config.MAX_DISPLAY_STREET_SEGMENTS
    indexed = list(enumerate(segments))
    top = sorted(indexed, key=lambda item: -item[1][1])[:max_display]
    top_in_order = sorted(top, key=lambda item: item[0])
    top_positions = {idx for idx, _ in top_in_order}

    lines = []
    for i, (_, (name, dist)) in enumerate(top_in_order, start=1):
        dist_str = f"{dist / 1000:.1f} км" if dist >= 1000 else f"{dist:.0f} м"
        lines.append(f"{i}. {_esc(name)} — {dist_str}")

    remainder = [seg for idx, seg in indexed if idx not in top_positions]
    if remainder:
        remainder_total_km = sum(d for _, d in remainder) / 1000
        lines.append(f"...и ещё {len(remainder)} участков (~{remainder_total_km:.1f} км суммарно)")

    return lines


def _format_duration(seconds: float) -> str:
    total_minutes = round(seconds / 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def _compute_difficulty(route: RouteResult, window: WeatherWindow) -> tuple[str, str]:
    """Уровень сложности по реальным данным: доля непокрытых асфальтом
    участков маршрута (ORS) + вероятность дождя и порывы ветра в выбранном
    окне (Open-Meteo). Не выдумывает ничего — только эти числа."""
    score = 0
    reasons: list[str] = []

    if route.unpaved_percentage >= config.DIFFICULTY_UNPAVED_THRESHOLD_PCT:
        score += 1
        reasons.append("грунтовое покрытие на части пути")

    if window.max_precipitation_probability >= config.DIFFICULTY_HIGH_RAIN_PROBABILITY_PCT:
        score += 1
        reasons.append("высокая вероятность дождя")
    elif window.max_precipitation_probability >= config.DIFFICULTY_NOTABLE_RAIN_PROBABILITY_PCT:
        reasons.append("небольшая вероятность дождя")

    if window.max_wind_gust >= config.DIFFICULTY_STRONG_GUST_KMH:
        score += 1
        reasons.append("сильный ветер")
    elif window.max_wind_gust >= config.DIFFICULTY_NOTABLE_GUST_KMH:
        reasons.append("заметный ветер")

    if score >= 2:
        level = "Сложный"
    elif score == 1:
        level = "Средний"
    else:
        level = "Лёгкий"

    if reasons:
        explanation = "Учтите: " + ", ".join(reasons) + "."
    else:
        explanation = "Асфальт, слабый ветер, без осадков — ничего осложняющего не ожидается."

    return level, explanation


def format_message(
    window: WeatherWindow,
    route: RouteResult,
    food: FoodMatch | None,
    explanation: str,
    attractions: list[AttractionMatch] | None = None,
) -> str:
    departure_dt = window.start_dt
    return_dt = departure_dt + timedelta(seconds=route.duration_s)
    weekday_name = WEEKDAY_NAMES_RU[departure_dt.weekday()]
    date_str = departure_dt.strftime("%d.%m.%Y")
    distance_km = route.distance_m / 1000

    difficulty_level, difficulty_explanation = _compute_difficulty(route, window)
    map_link = build_route_map_link(route)

    lines = [
        "🚲 Утренняя велопрогулка по Ярославлю",
        f"📅 День и дата: {weekday_name}, {date_str}",
        f"⏰ Выезд: {departure_dt.strftime('%H:%M')}",
        f"🏁 Возвращение: {return_dt.strftime('%H:%M')}",
        f"📏 Расстояние: {distance_km:.1f} км",
        f"⏱ Расчётное время: {_format_duration(route.duration_s)}",
        "",
        "🌤 ПОГОДА",
        f"Температура: {window.avg_temperature:.0f} °C",
        f"Ощущается: {window.avg_apparent_temperature:.0f} °C",
        f"Вероятность осадков: {window.max_precipitation_probability:.0f}%",
        f"Количество осадков: {window.total_precipitation:.1f} мм",
        f"Ветер: {window.max_wind_speed:.0f} км/ч",
        f"Максимальные порывы: {window.max_wind_gust:.0f} км/ч",
        "",
        "🚦 МАРШРУТ",
        f"Старт: {_esc(route.start_name)}",
    ]

    if map_link:
        lines.append(f'<a href="{_esc(map_link)}">Маршрут на карте</a>')

    lines.append("Основные точки и улицы:")
    lines.extend(_format_street_segments(route.street_segments))
    lines.append(f"Уровень сложности: {difficulty_level} — {difficulty_explanation}")
    lines.append(f"Финиш: {_esc(route.start_name)}")
    lines.append("")
    lines.append("🍽 ГДЕ ПОЕСТЬ")

    if food:
        arrival_str = return_dt.strftime("%H:%M")
        if food.yandex_url.startswith("http"):
            yandex_line = f'Яндекс Карты: <a href="{_esc(food.yandex_url)}">открыть</a>'
        else:
            yandex_line = f"Яндекс Карты: {_esc(food.yandex_url)}"
        lines.extend(
            [
                f"Название: {_esc(food.name)}",
                f"Адрес: {_esc(food.address)}",
                f"Работает: {_esc(food.hours_text)}",
                f"Предполагаемое время заезда: {arrival_str}",
                yandex_line,
            ]
        )
    else:
        lines.append("Открытой столовой рядом с маршрутом в это время нет")

    lines.append("")
    lines.append("🗺 ЧТО ПОСМОТРЕТЬ ПО ДОРОГЕ")
    if attractions:
        for place in attractions:
            category_part = f" ({_esc(place.category)})" if place.category else ""
            if place.yandex_url.startswith("http"):
                link_part = f' — <a href="{_esc(place.yandex_url)}">на карте</a>'
            else:
                link_part = ""
            lines.append(f"- {_esc(place.name)}{category_part}{link_part}")
    else:
        lines.append("Интересных мест рядом с маршрутом не нашлось")

    lines.extend(["", _esc(explanation)])

    return "\n".join(lines)


def format_unsafe_message(unsafe_reasons: dict[date, str]) -> str:
    lines = [
        "🚲 Утренняя велопрогулка по Ярославлю",
        "",
        "На ближайшие три дня безопасный маршрут построить нельзя.",
        "Причины по дням:",
    ]
    for day, reason in sorted(unsafe_reasons.items()):
        lines.append(f"- {day.strftime('%d.%m.%Y')}: {_esc(reason)}")
    lines.append("")
    lines.append("Как только погода станет безопаснее, бот предложит маршрут снова.")
    return "\n".join(lines)
