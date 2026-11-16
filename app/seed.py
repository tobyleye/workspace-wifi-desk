"""Add the made-up demo members to the sheet. Run on its own:

    python -m app.seed

Creates the tabs if needed, then adds one demo member per plan, all starting on DEMO_DAY. No access
codes are seeded: they appear in the AccessCodes tab as members request them during the demo.
Also adds a "Demo guide" tab listing each member's username and plan (the app never reads it).
Does nothing if the Members tab already has members.
To re-seed, delete the data rows in all three tabs (keep the headers) and run it again.

Move the server's clock to DEMO_DAY before demoing:  POST /admin/clock?to=2026-11-16
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime

from . import config, rules
from .config import PLANS
from .store import SheetStore, StoreError, TABS, open_store

DEMO_DAY = date(2026, 11, 16)  # a Monday: every plan starts here


@dataclass(frozen=True)
class DemoMember:
    contact_id: str
    name: str
    plan_id: str
    demo: str  # what to expect as the clock moves forward (printed by `main`)

    @property
    def start(self) -> date:
        return DEMO_DAY

    @property
    def end(self) -> date:
        return rules.plan_end_date(self.plan_id, self.start)


DEMO_MEMBERS = [
    DemoMember("ada_obi", "Ada Obi", "month_3day",
               "Mon 16, Tue 17, Wed 18 Nov: a new code each day. Thu 19 Nov: refused, 3 days used this "
               "week. Mon 23 Nov: gets a code again (new week). Plan ends Tue 15 Dec."),
    DemoMember("chidi_eze", "Chidi Eze", "month_full",
               "Mon 16 Nov: one code for the whole plan. Any later day: the same code again. "
               "Plan ends Tue 15 Dec; from Wed 16 Dec: plan expired."),
    DemoMember("kemi_lawal", "Kemi Lawal", "week_3day",
               "Mon 16, Tue 17, Wed 18 Nov: a new code each day. Thu 19 Nov: refused, all 3 days of "
               "the plan used. Plan ends Sun 22 Nov; from Mon 23 Nov: plan expired."),
    DemoMember("zainab_bello", "Zainab Bello", "week_full",
               "Mon 16 Nov: one code for the week. Any later day that week: the same code again. "
               "Plan ends Sun 22 Nov; from Mon 23 Nov: plan expired."),
]

GUIDE_TAB = "Demo guide"
GUIDE_HEADERS = ["Username", "Plan"]


def demo_rows(now: datetime) -> dict[str, list[list[str]]]:
    """Rows for Members and Subscriptions. Every value is text, as the store writes it."""
    stamp = now.isoformat(timespec="seconds")
    members, subs = [], []
    for i, m in enumerate(DEMO_MEMBERS, start=1):  # member i has plan i
        members.append([str(i), m.contact_id, m.name, stamp])
        subs.append([str(i), str(i), m.plan_id, m.start.isoformat(), m.end.isoformat(), stamp])
    return {"Members": members, "Subscriptions": subs}


def guide_rows() -> list[list[str]]:
    """One row per demo member for the Demo guide tab."""
    return [[m.contact_id, PLANS[m.plan_id].name] for m in DEMO_MEMBERS]


def write_guide(store: SheetStore) -> None:
    """Create the Demo guide tab if missing, and fill it if it has no rows yet."""
    backend = store.backend
    if GUIDE_TAB not in backend.list_tabs():
        backend.add_tab(GUIDE_TAB, GUIDE_HEADERS)  # also writes the header row
    existing = backend.read_tabs([GUIDE_TAB])[GUIDE_TAB]
    if len(existing) <= 1:  # just the header, or an empty tab
        backend.append_rows(GUIDE_TAB, ([] if existing else [GUIDE_HEADERS]) + guide_rows())


def seed(store: SheetStore, now: datetime) -> bool:
    """Add the demo rows if Members is empty. Returns whether anything was added."""
    if store.snapshot(fresh=True).members:
        return False
    rows = demo_rows(now)
    for title in TABS:
        if rows.get(title):
            store.append_rows(title, rows[title])
    write_guide(store)
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
    print(f"Added {len(DEMO_MEMBERS)} demo members, all starting {rules.display_date(DEMO_DAY)} {DEMO_DAY.year}:")
    for m in DEMO_MEMBERS:
        print(f"  {m.contact_id:<13} {m.name:<13} {PLANS[m.plan_id].name:<24} {m.demo}")
    print(f'Usernames and plans are listed in the "{GUIDE_TAB}" tab.')
    print(f"Move the server's clock there to demo: POST /admin/clock?to={DEMO_DAY.isoformat()}")


if __name__ == "__main__":
    main()
