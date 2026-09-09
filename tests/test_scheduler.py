from datetime import date

import config
from scheduler import should_send_today


def test_should_send_when_never_sent():
    assert should_send_today(date(2026, 9, 9), None) is True


def test_should_not_send_next_day():
    last_sent = date(2026, 9, 8)
    today = date(2026, 9, 9)
    assert should_send_today(today, last_sent) is False


def test_should_send_after_interval():
    last_sent = date(2026, 9, 7)
    today = date(2026, 9, 9)
    assert (today - last_sent).days >= config.SEND_INTERVAL_DAYS
    assert should_send_today(today, last_sent) is True
