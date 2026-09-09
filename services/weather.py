"""Работа с Open-Meteo: получение прогноза и выбор безопасного утреннего окна."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

import config

logger = logging.getLogger(__name__)

TZ = ZoneInfo(config.TIMEZONE_NAME)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_FIELDS = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]


class WeatherServiceError(Exception):
    """Ошибка получения или разбора прогноза погоды."""


@dataclass
class HourlyEntry:
    dt: datetime
    temperature: float
    apparent_temperature: float
    precipitation_probability: float
    precipitation: float
    weather_code: int
    wind_speed: float
    wind_gust: float


@dataclass
class WeatherWindow:
    day: date
    start_dt: datetime
    end_dt: datetime
    avg_temperature: float
    avg_apparent_temperature: float
    max_precipitation_probability: float
    total_precipitation: float
    max_wind_speed: float
    max_wind_gust: float
    weather_codes: set[int]
    safe: bool
    unsafe_reason: str | None
    score: float


@dataclass
class BestWindowResult:
    window: WeatherWindow | None
    all_unsafe: bool
    unsafe_reasons: dict[date, str]


async def fetch_raw_forecast(client: httpx.AsyncClient) -> dict[str, Any]:
    params = {
        "latitude": config.WEATHER_LAT,
        "longitude": config.WEATHER_LON,
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": config.TIMEZONE_NAME,
        "wind_speed_unit": "kmh",
        "forecast_days": config.FORECAST_DAYS,
    }
    try:
        response = await client.get(OPEN_METEO_URL, params=params, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("Ошибка запроса к Open-Meteo: %s", exc)
        raise WeatherServiceError("Не удалось получить прогноз погоды") from exc
    return response.json()


def _value_at(hourly: dict[str, Any], key: str, idx: int) -> float:
    values = hourly.get(key, [])
    if idx >= len(values) or values[idx] is None:
        return 0.0
    return float(values[idx])


def parse_forecast(raw: dict[str, Any]) -> list[HourlyEntry]:
    hourly = raw.get("hourly")
    if not hourly or "time" not in hourly:
        raise WeatherServiceError("Некорректный ответ Open-Meteo")

    times = hourly.get("time", [])
    entries: list[HourlyEntry] = []
    for idx, time_str in enumerate(times):
        try:
            naive_dt = datetime.fromisoformat(time_str)
        except ValueError:
            continue
        dt = naive_dt.replace(tzinfo=TZ)
        entries.append(
            HourlyEntry(
                dt=dt,
                temperature=_value_at(hourly, "temperature_2m", idx),
                apparent_temperature=_value_at(hourly, "apparent_temperature", idx),
                precipitation_probability=_value_at(hourly, "precipitation_probability", idx),
                precipitation=_value_at(hourly, "precipitation", idx),
                weather_code=int(_value_at(hourly, "weather_code", idx) or 0),
                wind_speed=_value_at(hourly, "wind_speed_10m", idx),
                wind_gust=_value_at(hourly, "wind_gusts_10m", idx),
            )
        )
    return entries


def _is_hazardous(entry: HourlyEntry) -> str | None:
    if entry.weather_code in config.THUNDERSTORM_CODES:
        return "гроза"
    if entry.weather_code in config.FREEZING_PRECIP_CODES:
        return "ледяные осадки"
    if entry.weather_code in config.HEAVY_RAIN_WEATHER_CODES:
        return "сильный дождь"
    if entry.precipitation > config.HEAVY_RAIN_MM_PER_HOUR:
        return "сильный дождь"
    if entry.wind_gust >= config.MAX_SAFE_WIND_GUST_KMH:
        return "сильные порывы ветра"
    return None


def _morning_entries_by_day(entries: list[HourlyEntry]) -> dict[date, list[HourlyEntry]]:
    by_day: dict[date, list[HourlyEntry]] = {}
    for entry in entries:
        t = entry.dt.time()
        if config.MORNING_WINDOW_START <= t <= config.MORNING_WINDOW_END:
            by_day.setdefault(entry.dt.date(), []).append(entry)
    for day_entries in by_day.values():
        day_entries.sort(key=lambda e: e.dt)
    return by_day


def _evaluate_window(
    day: date, start_dt: datetime, end_dt: datetime, subset: list[HourlyEntry]
) -> WeatherWindow:
    reasons = [r for r in (_is_hazardous(e) for e in subset) if r]
    safe = not reasons
    unsafe_reason = reasons[0] if reasons else None

    avg_temp = sum(e.temperature for e in subset) / len(subset)
    avg_apparent = sum(e.apparent_temperature for e in subset) / len(subset)
    max_precip_prob = max(e.precipitation_probability for e in subset)
    total_precip = sum(e.precipitation for e in subset)
    max_wind_speed = max(e.wind_speed for e in subset)
    max_wind_gust = max(e.wind_gust for e in subset)
    codes = {e.weather_code for e in subset}

    comfort_temp_ref = 18.0
    score = (
        max_precip_prob * 1.0
        + total_precip * 10.0
        + max_wind_speed * 0.5
        + max_wind_gust * 0.3
        + abs(avg_apparent - comfort_temp_ref) * 1.0
    )

    return WeatherWindow(
        day=day,
        start_dt=start_dt,
        end_dt=end_dt,
        avg_temperature=avg_temp,
        avg_apparent_temperature=avg_apparent,
        max_precipitation_probability=max_precip_prob,
        total_precipitation=total_precip,
        max_wind_speed=max_wind_speed,
        max_wind_gust=max_wind_gust,
        weather_codes=codes,
        safe=safe,
        unsafe_reason=unsafe_reason,
        score=score,
    )


def _build_windows_for_day(day: date, day_entries: list[HourlyEntry]) -> list[WeatherWindow]:
    windows: list[WeatherWindow] = []
    n = len(day_entries)
    for i in range(n):
        for j in range(i, n):
            subset = day_entries[i : j + 1]
            start_dt = subset[0].dt
            end_dt = subset[-1].dt + timedelta(hours=1)
            duration_minutes = (end_dt - start_dt).total_seconds() / 60
            if not (config.MIN_WINDOW_MINUTES <= duration_minutes <= config.MAX_WINDOW_MINUTES):
                continue
            if end_dt.time() > config.MORNING_WINDOW_END:
                continue
            windows.append(_evaluate_window(day, start_dt, end_dt, subset))
    return windows


@dataclass
class DayWeatherResult:
    day: date
    window: WeatherWindow | None  # None, если в этот день безопасного окна нет
    unsafe_reason: str | None


def evaluate_all_days(entries: list[HourlyEntry]) -> list[DayWeatherResult]:
    """Оценивает каждый из ближайших дней отдельно (для выбора дня
    пользователем), а не только лучший день в целом."""
    by_day = _morning_entries_by_day(entries)
    results: list[DayWeatherResult] = []

    for day, day_entries in sorted(by_day.items()):
        day_windows = _build_windows_for_day(day, day_entries)
        safe_windows = [w for w in day_windows if w.safe]
        if safe_windows:
            best = min(safe_windows, key=lambda w: w.score)
            results.append(DayWeatherResult(day=day, window=best, unsafe_reason=None))
        else:
            reason = next(
                (w.unsafe_reason for w in day_windows if w.unsafe_reason),
                "нет подходящего по длительности безопасного окна",
            )
            results.append(DayWeatherResult(day=day, window=None, unsafe_reason=reason))

    return results


def find_best_morning_window(entries: list[HourlyEntry]) -> BestWindowResult:
    day_results = evaluate_all_days(entries)
    safe_windows = [r.window for r in day_results if r.window is not None]
    unsafe_reasons = {r.day: r.unsafe_reason for r in day_results if r.window is None}

    if not safe_windows:
        return BestWindowResult(window=None, all_unsafe=True, unsafe_reasons=unsafe_reasons)

    best = min(safe_windows, key=lambda w: w.score)
    return BestWindowResult(window=best, all_unsafe=False, unsafe_reasons=unsafe_reasons)
