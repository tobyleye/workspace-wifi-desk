"""Add the made-up demo members to the sheet. Run on its own:

    python -m app.seed

Creates the tabs if needed, then adds the demo members below: each with their plan and the days
they already got a code. Does nothing if the Members tab already has members.
To re-seed, delete the data rows in all three tabs (keep the headers) and run it again.

The dates are fixed. Each member is in the state described next to them on DEMO_DAY, so move the
server's clock there before demoing:  POST /admin/clock?to=2026-11-19
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime

from . import config, rules
from .config import PLANS
from .store import SheetStore, StoreError, TABS, open_store

DEMO_DAY = date(2026, 11, 19)  # a Thursday


@dataclass(frozen=True)
class DemoMember:
    contact_id: str
    name: str
    plan_id: str
    start: date
    code_days: tuple[date, ...] = ()  # days they already got a code (daily plans)
    state: str = ""  # what happens on DEMO_DAY

    @property
    def end(self) -> date:
        return rules.plan_end_date(self.plan_id, self.start)


def d(day: int, month: int = 11) -> date:
    return date(2026, month, day)


DEMO_MEMBERS = [
    DemoMember("ada_obi", "Ada Obi", "month_3day", d(9), (d(16),),
               "issued: 1 day used this week (Mon 16), 1 left after today"),
    DemoMember("tunde_bakare", "Tunde Bakare", "month_3day", d(9), (d(16), d(17), d(18)),
               "weekly_limit_reached: used Mon, Tue, Wed; resets Mon 23 Nov"),
    DemoMember("chidi_eze", "Chidi Eze", "month_full", d(14), (),
               "issued: one code for the whole plan (ends 13 Dec)"),
    DemoMember("bola_ade", "Bola Ade", "month_full", d(17, 10), (),
               "plan_expired: ended Mon 16 Nov"),
    DemoMember("kemi_lawal", "Kemi Lawal", "week_3day", d(13), (d(13), d(14)),
               "issued: last day of the plan, 0 days left, ends_soon"),
    DemoMember("femi_ojo", "Femi Ojo", "week_3day", d(15), (d(16), d(17), d(18)),
               "plan_allowance_used: all 3 days used (plan ends Sat 21 Nov)"),
    DemoMember("zainab_bello", "Zainab Bello", "week_full", d(15), (),
               "issued: ends Sat 21 Nov, so ends_soon"),
]


def demo_rows(now: datetime) -> dict[str, list[list[str]]]:
    """Rows for all three tabs. Every value is text, as the store writes it."""
    stamp = now.isoformat(timespec="seconds")
    members, subs, codes = [], [], []
    for i, m in enumerate(DEMO_MEMBERS, start=1):  # member i has plan i
        members.append([str(i), m.contact_id, m.name, stamp])
        subs.append([str(i), str(i), m.plan_id, m.start.isoformat(), m.end.isoformat(), stamp])
        for day in m.code_days:
            n = len(codes) + 1
            code = f"{(i * 7919 + day.toordinal() * 104729) % 10**10:010d}"
            codes.append([str(n), str(i), str(i), day.isoformat(), code, f"demo-{n}", day.isoformat(), stamp])
    return {"Members": members, "Subscriptions": subs, "AccessCodes": codes}


def seed(store: SheetStore, now: datetime) -> bool:
    """Add the demo rows if Members is empty. Returns whether anything was added."""
    if store.snapshot(fresh=True).members:
        return False
    rows = demo_rows(now)
    for title in TABS:
        if rows[title]:
            store.append_rows(title, rows[title])
    return True


def main() -> None:
    settings = config.Settings.load()
    try:
        store = open_store(settings)
        if not seed(store, config.now()):
            print("Members tab already has members; nothing added. To re-seed, delete the data rows "
                  "in all three tabs (keep the headers) and run this again.")
            return
    except StoreError as e:
        sys.exit(f"Error: {e}")
    print(f"Added {len(DEMO_MEMBERS)} demo members. On {rules.display_date(DEMO_DAY)} {DEMO_DAY.year}:")
    for m in DEMO_MEMBERS:
        print(f"  {m.contact_id:<13} {m.name:<13} {PLANS[m.plan_id].name:<24} {m.state}")
    print(f"Move the server's clock there to demo: POST /admin/clock?to={DEMO_DAY.isoformat()}")


if __name__ == "__main__":
    main()
