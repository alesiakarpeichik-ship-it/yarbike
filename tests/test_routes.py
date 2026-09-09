import json
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from services.routes import (
    build_route_map_link,
    choose_start_point,
    compute_target_distance_km,
    first_leg_duration_s,
    is_too_similar,
    parse_route,
    route_signature,
    validate_route,
)

TZ = ZoneInfo(config.TIMEZONE_NAME)


def _sample_geojson(distance_m=12000.0, duration_s=3000.0, coords=None):
    coords = coords or [[39.89, 57.62], [39.90, 57.63], [39.89, 57.62]]
    return {
        "features": [
            {
                "geometry": {"coordinates": coords},
                "properties": {
                    "summary": {"distance": distance_m, "duration": duration_s},
                    "segments": [
                        {
                            "steps": [
                                {"name": "Первомайская улица", "distance": 500.0},
                                {"name": "-", "distance": 50.0},
                                {"name": "Республиканская улица", "distance": 700.0},
                                {"name": "Первомайская улица", "distance": 300.0},
                            ]
                        }
                    ],
                    "extras": {
                        "surface": {
                            "summary": [
                                {"value": 3, "amount": 80.0, "distance": 9600.0},
                                {"value": 11, "amount": 20.0, "distance": 2400.0},
                            ]
                        }
                    },
                },
            }
        ]
    }


def test_parse_route_extracts_streets_and_surface():
    route = parse_route(_sample_geojson(), seed=42)
    assert route.distance_m == 12000.0
    assert route.duration_s == 3000.0
    assert route.street_names == ["Первомайская улица", "Республиканская улица"]
    assert any("асфальт" in s for s in route.surface_summary)
    assert route.seed == 42


def test_parse_route_builds_street_segments_merging_unnamed_steps():
    route = parse_route(_sample_geojson(), seed=42)
    # безымянный шаг (50 м) присоединяется к предыдущему сегменту
    assert route.street_segments == [
        ("Первомайская улица", 550.0),
        ("Республиканская улица", 700.0),
        ("Первомайская улица", 300.0),
    ]


def test_build_route_map_link_stays_within_telegram_budget():
    many_coords = [[39.89 + i * 0.0001, 57.62 + i * 0.0001] for i in range(500)]
    route = parse_route(_sample_geojson(coords=many_coords), seed=1)
    link = build_route_map_link(route)
    assert link is not None
    assert link.startswith("https://geojson.io/#data=data:application/json,")
    assert len(link) <= 4096


def test_build_route_map_link_preserves_sharp_corner():
    # L-образный маршрут: длинный прямой участок на восток, затем поворот
    # строго на север. Наивная выборка каждой N-й точки может срезать угол
    # через "здания"; Дуглас-Пекер обязан сохранить точку поворота.
    east_leg = [[39.85 + i * 0.001, 57.62] for i in range(200)]
    north_leg = [[39.85 + 199 * 0.001, 57.62 + i * 0.001] for i in range(1, 200)]
    coords = east_leg + north_leg
    route = parse_route(_sample_geojson(coords=coords), seed=1)

    link = build_route_map_link(route)
    assert link is not None

    encoded = link.split(",", 1)[1]
    geometry = json.loads(urllib.parse.unquote(encoded))
    kept_points = geometry["coordinates"]

    corner_lon, corner_lat = coords[199]
    assert any(
        abs(lon - corner_lon) < 0.0005 and abs(lat - corner_lat) < 0.0005
        for lon, lat in kept_points
    ), "точка поворота должна сохраниться после упрощения"


def test_first_leg_duration_s_none_for_single_segment_route():
    route = parse_route(_sample_geojson(), seed=1)
    assert first_leg_duration_s(route) is None


def test_first_leg_duration_s_reads_first_of_two_segments():
    raw = _sample_geojson()
    raw["features"][0]["properties"]["segments"] = [
        {"duration": 900.0, "steps": []},
        {"duration": 600.0, "steps": []},
    ]
    route = parse_route(raw, seed=1)
    assert first_leg_duration_s(route) == 900.0


def test_choose_start_point_avoids_immediate_repeat():
    for _ in range(20):
        last_name = config.START_POINTS[0]["name"]
        chosen = choose_start_point(last_name)
        assert chosen["name"] != last_name


def test_choose_start_point_returns_valid_point_when_no_history():
    chosen = choose_start_point(None)
    assert chosen in config.START_POINTS


def test_compute_target_distance_km_clamped_to_range():
    departure = datetime(2026, 9, 9, 7, 0, tzinfo=TZ)
    target = compute_target_distance_km(departure)
    assert config.MIN_ROUTE_KM <= target <= config.MAX_ROUTE_KM


def test_validate_route_rejects_late_return():
    departure = datetime(2026, 9, 9, 7, 0, tzinfo=TZ)
    route = parse_route(_sample_geojson(distance_m=15000.0, duration_s=3 * 3600), seed=1)
    assert validate_route(route, departure) is False


def test_validate_route_accepts_reasonable_route():
    departure = datetime(2026, 9, 9, 7, 0, tzinfo=TZ)
    route = parse_route(_sample_geojson(distance_m=15000.0, duration_s=60 * 60), seed=1)
    assert validate_route(route, departure) is True


def test_is_too_similar_same_seed():
    route = parse_route(_sample_geojson(), seed=7)
    last_record = {"seed": 7, "signature": []}
    assert is_too_similar(route, last_record) is True


def test_is_too_similar_overlapping_geometry():
    route = parse_route(_sample_geojson(), seed=7)
    sig = [list(pair) for pair in route_signature(route)]
    last_record = {"seed": 999, "signature": sig}
    assert is_too_similar(route, last_record) is True


def test_is_not_similar_when_different():
    route = parse_route(_sample_geojson(coords=[[39.89, 57.62], [39.95, 57.66]]), seed=7)
    last_record = {"seed": 999, "signature": [[10.0, 10.0], [11.0, 11.0]]}
    assert is_too_similar(route, last_record) is False
