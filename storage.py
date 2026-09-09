"""Хранение истории маршрутов и даты последней рассылки (локальный JSON)."""
from __future__ import annotations

import json
import threading
from datetime import date
from typing import Any

import config

_lock = threading.Lock()


def _default_state() -> dict[str, Any]:
    return {"routes": [], "last_broadcast_date": None}


def load_state() -> dict[str, Any]:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not config.STORAGE_FILE.exists():
        return _default_state()
    try:
        with config.STORAGE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _default_state()
    data.setdefault("routes", [])
    data.setdefault("last_broadcast_date", None)
    return data


def save_state(state: dict[str, Any]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = config.STORAGE_FILE.with_suffix(".tmp")
    with _lock:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp_path.replace(config.STORAGE_FILE)


def get_last_route() -> dict[str, Any] | None:
    state = load_state()
    routes = state.get("routes", [])
    return routes[-1] if routes else None


def add_route_record(record: dict[str, Any]) -> None:
    state = load_state()
    routes = state.get("routes", [])
    routes.append(record)
    state["routes"] = routes[-config.ROUTE_HISTORY_LIMIT :]
    save_state(state)


def get_last_broadcast_date() -> date | None:
    state = load_state()
    raw = state.get("last_broadcast_date")
    return date.fromisoformat(raw) if raw else None


def set_last_broadcast_date(value: date) -> None:
    state = load_state()
    state["last_broadcast_date"] = value.isoformat()
    save_state(state)
