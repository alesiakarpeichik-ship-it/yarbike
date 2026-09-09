"""Точка входа Telegram-бота."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import timedelta

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import config
import storage
from scheduler import setup_scheduler
from services import attractions, food, message as message_service, routes, weather
from services.food import haversine_m
from services.routes import RouteResult, RouteServiceError
from services.weather import DayWeatherResult, WeatherServiceError, WeatherWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

_user_locks: dict[int, asyncio.Lock] = {}
_awaiting_location: dict[int, bool] = {}
_pending_selection: dict[int, dict] = {}

CUSTOM_START_CALLBACK = "startpoint:custom"


def _get_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _user_locks:
        _user_locks[chat_id] = asyncio.Lock()
    return _user_locks[chat_id]


def _start_point_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=point["name"], callback_data=f"startpoint:{i}")]
        for i, point in enumerate(config.START_POINTS)
    ]
    buttons.append(
        [InlineKeyboardButton(text="📍 Выбрать место старта", callback_data=CUSTOM_START_CALLBACK)]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _location_request_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Отправить геолокацию", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _day_choice_keyboard(day_results: list[DayWeatherResult]) -> InlineKeyboardMarkup:
    buttons = []
    for i, result in enumerate(day_results):
        weekday_name = message_service.WEEKDAY_NAMES_RU[result.day.weekday()]
        date_str = result.day.strftime("%d.%m")
        mark = "✅" if result.window is not None else "⚠️"
        buttons.append(
            [InlineKeyboardButton(text=f"{mark} {weekday_name} {date_str}", callback_data=f"day:{i}")]
        )
    buttons.append(
        [InlineKeyboardButton(text="🎲 Пусть бот выберет лучший день", callback_data="day:auto")]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _try_build_route_via_food(
    client: httpx.AsyncClient,
    window: WeatherWindow,
    start_point: dict,
    last_record: dict | None,
    places: list[dict[str, str]],
) -> tuple[RouteResult, food.FoodMatch] | None:
    """Пытается проложить маршрут так, чтобы он прошёл через одно из открытых
    проверенных заведений (а не просто проверить постфактум, что что-то
    оказалось рядом). Возвращает None, если ни один кандидат не подошёл —
    тогда вызывающий код должен построить обычный round_trip маршрут."""
    weekday_places = food.get_places_open_on_weekday(places, window.start_dt.weekday())
    target_km = routes.compute_target_distance_km(window.start_dt)
    max_detour_m = min(config.MAX_FOOD_WAYPOINT_DISTANCE_M, target_km * 1000 * 0.5)
    candidates = food.find_candidates_near_point(
        weekday_places, start_point["lat"], start_point["lon"], max_detour_m
    )
    if not candidates:
        return None

    last_food_name = last_record.get("food_name") if last_record else None
    random.shuffle(candidates)
    candidates.sort(key=lambda c: c.name == last_food_name)

    for candidate in candidates[: config.MAX_ROUTE_ATTEMPTS]:
        try:
            attempt = await routes.build_route_via_waypoint(
                client, start_point, candidate.lat, candidate.lon
            )
        except RouteServiceError:
            continue

        if not routes.validate_route(attempt, window.start_dt):
            continue

        leg_s = routes.first_leg_duration_s(attempt)
        if leg_s is None:
            continue
        arrival_dt = window.start_dt + timedelta(seconds=leg_s)
        if not food.is_open_at(candidate.place, arrival_dt):
            continue

        if routes.is_too_similar(attempt, last_record):
            continue

        food_match = food.FoodMatch(
            name=candidate.name,
            address=(candidate.place.get("Адрес") or "").strip(),
            hours_text=food.format_hours_text(candidate.place),
            yandex_url=(candidate.place.get("Ссылка на Яндекс Карты") or "").strip(),
            distance_m=0.0,
            routed_specifically=True,
        )
        return attempt, food_match

    return None


async def generate_route_message(
    client: httpx.AsyncClient,
    forced_start_point: dict | None = None,
    forced_window: WeatherWindow | None = None,
) -> str:
    if forced_window is not None:
        window = forced_window
    else:
        raw_forecast = await weather.fetch_raw_forecast(client)
        entries = weather.parse_forecast(raw_forecast)
        result = weather.find_best_morning_window(entries)
        if result.all_unsafe or result.window is None:
            return message_service.format_unsafe_message(result.unsafe_reasons)
        window = result.window

    last_record = storage.get_last_route()
    start_point = forced_start_point or routes.choose_start_point(
        last_record.get("start_name") if last_record else None
    )
    places = food.load_places()

    via_food = await _try_build_route_via_food(client, window, start_point, last_record, places)
    if via_food:
        route, food_match = via_food
    else:
        route = await routes.build_route(client, window.start_dt, last_record, start_point)
        return_dt = window.start_dt + timedelta(seconds=route.duration_s)
        food_match = food.find_food_near_route(places, route.coordinates, return_dt)

    storage.add_route_record(
        {
            "seed": route.seed,
            "signature": [list(pair) for pair in routes.route_signature(route)],
            "date": window.day.isoformat(),
            "start_name": route.start_name,
            "food_name": food_match.name if food_match else None,
        }
    )
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with config.LAST_ROUTE_GEOJSON_FILE.open("w", encoding="utf-8") as f:
        json.dump(route.geojson, f, ensure_ascii=False, indent=2)

    attraction_places = attractions.load_attractions()
    attraction_matches = attractions.find_attractions_near_route(attraction_places, route.coordinates)

    return message_service.format_message(
        window, route, food_match, attraction_matches, auto_selected=forced_window is None
    )


async def _handle_route_request(
    chat_id: int,
    reply_to: Message | None,
    start_point: dict | None = None,
    forced_window: WeatherWindow | None = None,
) -> None:
    lock = _get_lock(chat_id)
    if lock.locked():
        if reply_to:
            await reply_to.answer("Маршрут уже формируется, подождите несколько секунд.")
        return

    async with lock:
        try:
            async with httpx.AsyncClient() as client:
                text = await generate_route_message(
                    client, forced_start_point=start_point, forced_window=forced_window
                )
        except WeatherServiceError:
            text = "Не удалось получить прогноз погоды. Попробуйте немного позже."
        except RouteServiceError:
            text = "Не удалось построить маршрут. Попробуйте немного позже."
        except Exception:
            logger.exception("Непредвиденная ошибка при формировании маршрута")
            text = "Произошла ошибка при формировании маршрута. Попробуйте немного позже."

        await bot.send_message(chat_id, text, parse_mode="HTML")


async def _offer_day_choice(chat_id: int, reply_target: Message, start_point: dict) -> None:
    """После выбора точки старта показывает клавиатуру с ближайшими днями
    (с пометкой безопасности погоды) и кнопкой "пусть бот выберет сам"."""
    lock = _get_lock(chat_id)
    if lock.locked():
        await reply_target.answer("Маршрут уже формируется, подождите несколько секунд.")
        return

    async with lock:
        try:
            async with httpx.AsyncClient() as client:
                raw_forecast = await weather.fetch_raw_forecast(client)
            entries = weather.parse_forecast(raw_forecast)
            day_results = weather.evaluate_all_days(entries)
        except WeatherServiceError:
            await reply_target.answer("Не удалось получить прогноз погоды. Попробуйте немного позже.")
            return
        except Exception:
            logger.exception("Непредвиденная ошибка при получении погоды")
            await reply_target.answer("Произошла ошибка. Попробуйте немного позже.")
            return

    _pending_selection[chat_id] = {"start_point": start_point, "day_results": day_results}
    await reply_target.answer(
        "На какой день строим маршрут?",
        reply_markup=_day_choice_keyboard(day_results),
    )


@dp.message(Command("start"))
async def handle_start(message: Message) -> None:
    await message.answer(
        "Привет! Я подберу один безопасный утренний велосипедный маршрут "
        "по Ярославлю с учётом погоды. Команда /route — построить маршрут снова.\n\n"
        "Откуда начнём?",
        reply_markup=_start_point_keyboard(),
    )


@dp.message(Command("route"))
async def handle_route(message: Message) -> None:
    await message.answer("Откуда начнём маршрут?", reply_markup=_start_point_keyboard())


@dp.callback_query(F.data.startswith("startpoint:"))
async def handle_start_point_choice(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    chat_id = callback.message.chat.id

    if callback.data == CUSTOM_START_CALLBACK:
        _awaiting_location[chat_id] = True
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Пришлите геолокацию точки старта: 📎 → «Локация», или кнопкой ниже.",
            reply_markup=_location_request_keyboard(),
        )
        return

    try:
        index = int(callback.data.split(":", 1)[1])
        start_point = config.START_POINTS[index]
    except (ValueError, IndexError):
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await _offer_day_choice(chat_id, callback.message, start_point)


@dp.message(F.location)
async def handle_location(message: Message) -> None:
    chat_id = message.chat.id
    if not _awaiting_location.get(chat_id):
        return
    _awaiting_location[chat_id] = False

    lat, lon = message.location.latitude, message.location.longitude
    distance_from_center = haversine_m(config.WEATHER_LAT, config.WEATHER_LON, lat, lon)
    if distance_from_center > config.MAX_CUSTOM_START_DISTANCE_FROM_CENTER_M:
        await message.answer(
            "Эта точка слишком далеко от Ярославля — погода считается для центра города, "
            "и маршрут отсюда был бы недостоверным. Пришлите точку в пределах города.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    start_point = {"name": "точка на карте (геолокация)", "lat": lat, "lon": lon}
    await message.answer("Геолокация получена.", reply_markup=ReplyKeyboardRemove())
    await _offer_day_choice(chat_id, message, start_point)


@dp.callback_query(F.data.startswith("day:"))
async def handle_day_choice(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    chat_id = callback.message.chat.id

    pending = _pending_selection.get(chat_id)
    if not pending:
        await callback.message.answer("Сессия выбора устарела. Начните заново: /route")
        return

    start_point = pending["start_point"]
    day_results: list[DayWeatherResult] = pending["day_results"]
    choice = callback.data.split(":", 1)[1]

    if choice == "auto":
        _pending_selection.pop(chat_id, None)
        await callback.message.edit_reply_markup(reply_markup=None)
        await _handle_route_request(chat_id, callback.message, start_point=start_point)
        return

    try:
        index = int(choice)
        result = day_results[index]
    except (ValueError, IndexError):
        return

    if result.window is None:
        weekday_name = message_service.WEEKDAY_NAMES_RU[result.day.weekday()]
        date_str = result.day.strftime("%d.%m.%Y")
        await callback.message.answer(
            f"{weekday_name}, {date_str}: обычная велопрогулка небезопасна — "
            f"{result.unsafe_reason}. Выберите другой день кнопкой выше."
        )
        return

    _pending_selection.pop(chat_id, None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await _handle_route_request(
        chat_id, callback.message, start_point=start_point, forced_window=result.window
    )


async def send_scheduled_route() -> None:
    if not config.TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID не задан, рассылка пропущена")
        return
    chat_id = int(config.TELEGRAM_CHAT_ID)
    await _handle_route_request(chat_id, None)


async def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")

    await bot.delete_webhook(drop_pending_updates=True)

    scheduler = setup_scheduler(send_scheduled_route)
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
