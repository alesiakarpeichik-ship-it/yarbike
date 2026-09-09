from services.attractions import find_attractions_near_route, load_attractions

CSV_HEADER = "Название;Категория;Адрес;latitude;longitude;Ссылка на Яндекс Карты;osm_id\n"


def _row(name="Музей", category="музей", address="", lat="57.6270", lon="39.8950", osm_id="1"):
    url = f"https://yandex.ru/maps/?text={name}&ll={lon},{lat}&z=17"
    return f'"{name}";"{category}";"{address}";{lat};{lon};"{url}";{osm_id}'


def _write_csv(tmp_path, rows: list[str]):
    path = tmp_path / "attractions.csv"
    path.write_text(CSV_HEADER + "\n".join(rows), encoding="utf-8")
    return path


def test_load_attractions_reads_rows(tmp_path):
    csv_path = _write_csv(tmp_path, [_row(name="Музей А"), _row(name="Музей Б")])
    places = load_attractions(csv_path)
    assert len(places) == 2
    assert places[0]["Название"] == "Музей А"


def test_find_attractions_near_route_respects_distance_and_limit(tmp_path):
    rows = [
        _row(name="Рядом 1", lat="57.6270", lon="39.8950"),
        _row(name="Рядом 2", lat="57.6271", lon="39.8951"),
        _row(name="Рядом 3", lat="57.6272", lon="39.8952"),
        _row(name="Далеко", lat="57.7000", lon="40.0000"),
    ]
    csv_path = _write_csv(tmp_path, rows)
    places = load_attractions(csv_path)

    route_coords = [(39.8948, 57.6270)]
    matches = find_attractions_near_route(places, route_coords, max_distance_m=1000, max_count=2)

    assert len(matches) == 2  # ограничено max_count, хотя рядом их 3
    names = {m.name for m in matches}
    assert "Далеко" not in names


def test_find_attractions_near_route_sorted_by_distance(tmp_path):
    rows = [
        _row(name="Дальше", lat="57.6280", lon="39.8960"),
        _row(name="Ближе", lat="57.6271", lon="39.8951"),
    ]
    csv_path = _write_csv(tmp_path, rows)
    places = load_attractions(csv_path)

    route_coords = [(39.8948, 57.6270)]
    matches = find_attractions_near_route(places, route_coords, max_distance_m=5000, max_count=5)

    assert [m.name for m in matches] == ["Ближе", "Дальше"]


def test_find_attractions_skips_rows_without_name_or_coords(tmp_path):
    rows = [
        _row(name="", lat="57.6270", lon="39.8950"),
        _row(name="Без координат", lat="", lon=""),
    ]
    csv_path = _write_csv(tmp_path, rows)
    places = load_attractions(csv_path)

    route_coords = [(39.8948, 57.6270)]
    matches = find_attractions_near_route(places, route_coords, max_distance_m=5000)
    assert matches == []


def test_find_attractions_near_route_empty_without_route_coords(tmp_path):
    csv_path = _write_csv(tmp_path, [_row()])
    places = load_attractions(csv_path)
    assert find_attractions_near_route(places, [], max_distance_m=5000) == []
