from datetime import date

import config
from services.weather import evaluate_all_days, find_best_morning_window, parse_forecast


def _make_raw(times, temp, apparent, precip_prob, precip, code, wind, gust):
    return {
        "hourly": {
            "time": times,
            "temperature_2m": temp,
            "apparent_temperature": apparent,
            "precipitation_probability": precip_prob,
            "precipitation": precip,
            "weather_code": code,
            "wind_speed_10m": wind,
            "wind_gusts_10m": gust,
        }
    }


def _times_for_day(day: str) -> list[str]:
    return [f"{day}T{h:02d}:00" for h in range(0, 24)]


def test_parse_forecast_basic():
    times = _times_for_day("2026-09-09")
    n = len(times)
    raw = _make_raw(times, [10] * n, [9] * n, [0] * n, [0] * n, [1] * n, [10] * n, [15] * n)
    entries = parse_forecast(raw)
    assert len(entries) == n
    assert entries[7].dt.hour == 7
    assert entries[7].temperature == 10


def test_picks_safe_120min_window_and_scores_lower_precip_better():
    days = ["2026-09-09", "2026-09-10", "2026-09-11"]
    times = []
    temp = []
    apparent = []
    precip_prob = []
    precip = []
    code = []
    wind = []
    gust = []

    for i, day in enumerate(days):
        day_times = _times_for_day(day)
        times.extend(day_times)
        for h in range(24):
            temp.append(18)
            apparent.append(17)
            # День 0 самый сухой, значит должен быть выбран
            precip_prob.append(0 if i == 0 else 40)
            precip.append(0.0)
            code.append(1)
            wind.append(10)
            gust.append(20)

    raw = _make_raw(times, temp, apparent, precip_prob, precip, code, wind, gust)
    entries = parse_forecast(raw)
    result = find_best_morning_window(entries)

    assert result.all_unsafe is False
    assert result.window is not None
    assert result.window.day == date(2026, 9, 9)
    assert result.window.start_dt.hour == 7
    duration_minutes = (result.window.end_dt - result.window.start_dt).total_seconds() / 60
    assert config.MIN_WINDOW_MINUTES <= duration_minutes <= config.MAX_WINDOW_MINUTES


def test_thunderstorm_makes_all_days_unsafe():
    days = ["2026-09-09", "2026-09-10", "2026-09-11"]
    times = []
    temp, apparent, precip_prob, precip, code, wind, gust = [], [], [], [], [], [], []

    for day in days:
        times.extend(_times_for_day(day))
        for h in range(24):
            temp.append(18)
            apparent.append(17)
            precip_prob.append(90)
            precip.append(5.0)
            code.append(95)  # гроза
            wind.append(10)
            gust.append(20)

    raw = _make_raw(times, temp, apparent, precip_prob, precip, code, wind, gust)
    entries = parse_forecast(raw)
    result = find_best_morning_window(entries)

    assert result.all_unsafe is True
    assert result.window is None
    assert len(result.unsafe_reasons) == 3
    assert all(reason == "гроза" for reason in result.unsafe_reasons.values())


def test_evaluate_all_days_returns_per_day_results_mixed_safety():
    days = ["2026-09-09", "2026-09-10", "2026-09-11"]
    times, temp, apparent, precip_prob, precip, code, wind, gust = [], [], [], [], [], [], [], []

    for i, day in enumerate(days):
        times.extend(_times_for_day(day))
        for h in range(24):
            temp.append(18)
            apparent.append(17)
            precip_prob.append(0)
            precip.append(0.0)
            code.append(95 if i == 0 else 1)  # гроза только в первый день
            wind.append(10)
            gust.append(20)

    raw = _make_raw(times, temp, apparent, precip_prob, precip, code, wind, gust)
    entries = parse_forecast(raw)
    results = evaluate_all_days(entries)

    assert len(results) == 3
    assert results[0].day == date(2026, 9, 9)
    assert results[0].window is None
    assert results[0].unsafe_reason == "гроза"

    assert results[1].window is not None
    assert results[2].window is not None


def test_strong_gusts_are_unsafe():
    times = _times_for_day("2026-09-09")
    n = len(times)
    gust = [20] * n
    gust[7] = 60  # выше MAX_SAFE_WIND_GUST_KMH в 07:00
    gust[8] = 60
    raw = _make_raw(
        times,
        [18] * n,
        [17] * n,
        [0] * n,
        [0.0] * n,
        [1] * n,
        [10] * n,
        gust,
    )
    entries = parse_forecast(raw)
    result = find_best_morning_window(entries)
    assert result.all_unsafe is True
    assert result.unsafe_reasons[date(2026, 9, 9)] == "сильные порывы ветра"
