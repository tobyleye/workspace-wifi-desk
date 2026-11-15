"""Pure rule helpers: plan dates, weeks, code lifetimes. No I/O."""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta

from .config import PLANS, TZ


def add_months(d: date, months: int) -> date:
    """Same day `months` later, clamped to the end of the month (Jan 31 + 1 -> Feb 28/29)."""
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def plan_end_date(plan_id: str, start: date) -> date:
    """Inclusive last day of a plan."""
    plan = PLANS[plan_id]
    if plan.length == "week":
        return start + timedelta(days=6)
    return add_months(start, 1) - timedelta(days=1)


def week_start(d: date) -> date:
    """Monday of the Monday–Saturday week `d` falls in (Sunday belongs to the week before)."""
    return d - timedelta(days=d.weekday())


def next_monday(d: date) -> date:
    return week_start(d) + timedelta(days=7)


def is_active(start: date, end: date, today: date) -> bool:
    """A plan's first and last days both count in full."""
    return start <= today <= end


def minutes_until_end_of_day(now: datetime, last_day: date) -> int:
    """Whole minutes from `now` until 23:59 on `last_day` in Lagos, at least 1."""
    end = datetime.combine(last_day, time(23, 59), tzinfo=TZ)
    minutes = int((end - now.astimezone(TZ)).total_seconds() // 60)
    return max(1, minutes)


def display_date(d: date) -> str:
    """`Saturday, 21 November`: the form the skill shows members."""
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%B')}"
