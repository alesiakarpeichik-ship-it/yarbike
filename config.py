"""Настройки бота. Секреты берутся только из переменных окружения (.env)."""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SEND_HOUR = int(os.getenv("SEND_HOUR", "15"))
SEND_MINUTE = int(os.getenv("SEND_MINUTE", "0"))

# --- OpenRouteService ---
OPENROUTESERVICE_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY", "")

# --- Часовой пояс ---
TIMEZONE_NAME = "Europe/Moscow"

# --- Старт/финиш маршрута ---
# Несколько точек в историческом центре Ярославля — перед каждой генерацией
# бот выбирает одну (не повторяя точку из прошлого раза), чтобы маршруты
# получались разнообразнее. Координаты приблизительные, по открытым
# картографическим данным — рекомендуется проверить каждую через Яндекс.Карты
# (ПКМ по точке → «Что здесь?») и при необходимости скорректировать.
START_POINTS = [
    {"name": "Волковская площадь", "lat": 57.6270, "lon": 39.8948},
    {"name": "Волжская набережная (у Стрелки)", "lat": 57.6205, "lon": 39.8850},
    {"name": "Богоявленская площадь", "lat": 57.6210, "lon": 39.8940},
    {"name": "Красная площадь", "lat": 57.6280, "lon": 39.8720},
    {"name": "Октябрьская площадь", "lat": 57.6520, "lon": 39.8930},
    {"name": "КЗЦ «Миллениум»", "lat": 57.6180, "lon": 39.8890},
]

# Точка для запроса погоды — центр Ярославля. Все точки старта находятся
# в пределах нескольких км друг от друга, погода на таком масштабе
# практически не отличается, поэтому используется одна общая точка.
WEATHER_LAT = 57.6250
WEATHER_LON = 39.8880

# Пользователь может прислать свою геолокацию как точку старта. Погода
# всё равно считается для центра Ярославля (см. выше), поэтому произвольную
# точку дальше этого радиуса от центра не принимаем — иначе показанная
# погода не будет соответствовать реальному месту старта.
MAX_CUSTOM_START_DISTANCE_FROM_CENTER_M = 15000.0

# --- Утреннее окно ---
MORNING_WINDOW_START = time(7, 0)
MORNING_WINDOW_END = time(9, 30)
MIN_WINDOW_MINUTES = 90
MAX_WINDOW_MINUTES = 150
FORECAST_DAYS = 3

# --- Пороги безопасности погоды ---
MAX_SAFE_WIND_GUST_KMH = 54.0
# Порог "сильный дождь" по классификации Met Office / NWS (> 7.6 мм/ч)
HEAVY_RAIN_MM_PER_HOUR = 7.6
# Коды погоды WMO (используются Open-Meteo)
THUNDERSTORM_CODES = {95, 96, 99}
FREEZING_PRECIP_CODES = {56, 57, 66, 67}
HEAVY_RAIN_WEATHER_CODES = {65, 82}

# --- Параметры маршрута ---
MIN_ROUTE_KM = 10.0
MAX_ROUTE_KM = 18.0
# Допущение о средней скорости спокойной велопрогулки для расчёта
# целевой длины маршрута под доступное время.
ASSUMED_CYCLING_SPEED_KMH = 16.0
RETURN_DEADLINE = time(9, 30)
RETURN_BUFFER_MINUTES = 15
MAX_ROUTE_ATTEMPTS = 3
ROUND_TRIP_POINTS = 3
# В сообщении показываются только самые протяжённые улицы маршрута,
# остальные сворачиваются в одну итоговую строку.
MAX_DISPLAY_STREET_SEGMENTS = 7

# --- Уровень сложности маршрута ---
# Пороги эвристические (как и ASSUMED_CYCLING_SPEED_KMH выше), считаются
# по реальным данным маршрута (доля непокрытых асфальтом участков из ORS)
# и погоды в выбранном окне (вероятность дождя, порывы ветра из Open-Meteo).
DIFFICULTY_UNPAVED_THRESHOLD_PCT = 25.0
DIFFICULTY_HIGH_RAIN_PROBABILITY_PCT = 40.0
DIFFICULTY_NOTABLE_RAIN_PROBABILITY_PCT = 20.0
DIFFICULTY_STRONG_GUST_KMH = 35.0
DIFFICULTY_NOTABLE_GUST_KMH = 25.0

# --- Столовые ---
FOOD_CSV_PATH = BASE_DIR / "food_places.csv"
FOOD_MAX_DISTANCE_M = 700.0
# Насколько далеко от старта может быть столовая, чтобы маршрут специально
# проложили через неё (а не просто проверили постфактум, что она рядом).
MAX_FOOD_WAYPOINT_DISTANCE_M = 4000.0

# --- Достопримечательности ---
# Одноразовая выгрузка из OpenStreetMap (см. scripts/fetch_attractions.py),
# бот не обращается к OpenStreetMap во время работы.
ATTRACTIONS_CSV_PATH = BASE_DIR / "attractions.csv"
ATTRACTION_MAX_DISTANCE_M = 400.0
MAX_ATTRACTIONS_DISPLAY = 3

# --- Хранилище ---
DATA_DIR = BASE_DIR / "data"
STORAGE_FILE = DATA_DIR / "storage.json"
LAST_ROUTE_GEOJSON_FILE = DATA_DIR / "last_route.geojson"
ROUTE_HISTORY_LIMIT = 5
ROUTE_SIMILARITY_THRESHOLD = 0.9

# --- Планировщик ---
SEND_INTERVAL_DAYS = 2  # "через день" = минимум 2 календарных дня между рассылками
