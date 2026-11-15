from datetime import date, datetime

import pytest

from app import rules
from app.config import TZ


@pytest.mark.parametrize("plan_id,start,end", [
    ("week_full", date(2026, 11, 16), date(2026, 11, 22)),
    ("week_3day", date(2026, 12, 29), date(2027, 1, 4)),
    ("month_full", date(2026, 11, 16), date(2026, 12, 15)),
    ("month_3day", date(2026, 1, 31), date(2026, 2, 27)),   # Jan 31 -> Feb 28, minus a day
    ("month_full", date(2028, 1, 31), date(2028, 2, 28)),   # leap year: Feb 29 - 1
    ("month_full", date(2026, 12, 15), date(2027, 1, 14)),  # Dec -> Jan
    ("month_full", date(2026, 12, 1), date(2026, 12, 31)),
])
def test_plan_end_dates(plan_id, start, end):
    assert rules.plan_end_date(plan_id, start) == end


def test_week_start_is_monday():
    assert rules.week_start(date(2026, 11, 16)) == date(2026, 11, 16)  # Monday
    assert rules.week_start(date(2026, 11, 21)) == date(2026, 11, 16)  # Saturday
    assert rules.week_start(date(2026, 11, 22)) == date(2026, 11, 16)  # Sunday belongs to the week before
    assert rules.next_monday(date(2026, 11, 19)) == date(2026, 11, 23)


def test_active_includes_first_and_last_day():
    start, end = date(2026, 11, 16), date(2026, 11, 22)
    assert rules.is_active(start, end, start)
    assert rules.is_active(start, end, end)
    assert not rules.is_active(start, end, date(2026, 11, 15))
    assert not rules.is_active(start, end, date(2026, 11, 23))


def test_minutes_until_end_of_today():
    now = datetime(2026, 11, 16, 10, 0, tzinfo=TZ)
    assert rules.minutes_until_end_of_day(now, now.date()) == 13 * 60 + 59


def test_minutes_until_end_of_plan():
    now = datetime(2026, 11, 16, 10, 0, tzinfo=TZ)
    assert rules.minutes_until_end_of_day(now, date(2026, 11, 22)) == 6 * 24 * 60 + 13 * 60 + 59


def test_minutes_minimum_is_one():
    assert rules.minutes_until_end_of_day(datetime(2026, 11, 16, 23, 59, 30, tzinfo=TZ), date(2026, 11, 16)) == 1
    assert rules.minutes_until_end_of_day(datetime(2026, 11, 17, 1, 0, tzinfo=TZ), date(2026, 11, 16)) == 1


def test_minutes_uses_lagos_time_for_other_timezones():
    from zoneinfo import ZoneInfo
    now_utc = datetime(2026, 11, 16, 9, 0, tzinfo=ZoneInfo("UTC"))  # 10:00 in Lagos
    assert rules.minutes_until_end_of_day(now_utc, date(2026, 11, 16)) == 13 * 60 + 59


def test_display_date():
    assert rules.display_date(date(2026, 11, 21)) == "Saturday, 21 November"
    assert rules.display_date(date(2026, 12, 1)) == "Tuesday, 1 December"


def test_clock_offset_keeps_ticking():
    from datetime import timedelta
    from app.config import Clock

    real = [datetime(2026, 11, 19, 10, 0, tzinfo=TZ)]
    clock = Clock(lambda: real[0])
    clock.jump_to(datetime(2026, 12, 20, 10, 0))
    assert clock() == datetime(2026, 12, 20, 10, 0, tzinfo=TZ) and clock.shifted
    real[0] += timedelta(minutes=5)
    assert clock() == datetime(2026, 12, 20, 10, 5, tzinfo=TZ)
    clock.reset()
    assert clock() == real[0] and not clock.shifted
