import asyncio
from datetime import date, datetime, timedelta

import pytest

from app.access import AccessDesk
from app.config import TZ
from app.seed import DEMO_DAY, DEMO_MEMBERS, seed
from app.store import SheetStore
from tests.fake_sheets import FakeSheetsBackend, FakeUnifi

MON = date(2026, 11, 16)  # a Monday
CONTACT = "ada_obi"


def at(d: date, hour=10, minute=0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


def day(n: int) -> date:
    return MON + timedelta(days=n)


class World:
    def __init__(self, unifi_delay=0.0):
        self.backend = FakeSheetsBackend()
        self.store = SheetStore(self.backend, cache_seconds=0)
        self.store.ensure_tabs()
        self.unifi = FakeUnifi(delay=unifi_delay)
        self.desk = AccessDesk(self.store, self.unifi)

    def member(self, plan_id, start, contact_id=CONTACT, name="Ada Obi"):
        snap = self.store.snapshot(fresh=True)
        m = snap.member_by_contact_id(contact_id) or self.store.add_member(contact_id, name, at(start))
        self.store.add_subscription(m.id, plan_id, start, at(start))
        return m

    def request(self, when: datetime, contact_id=CONTACT):
        return asyncio.run(self.desk.request_code(contact_id, when))

    def expiry(self, when: datetime, contact_id=CONTACT):
        return asyncio.run(self.desk.get_expiry(contact_id, when))

    def code_rows(self):
        return self.backend.tabs["AccessCodes"][1:]


def test_daily_code_voucher_details():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    r = w.request(at(MON, 10, 0))
    assert r["status"] == "issued"
    assert r["first_name"] == "Ada" and r["name"] == "Ada Obi"
    assert r["max_devices"] == 2
    v = w.unifi.created[0]
    assert v["count"] == 1
    assert v["authorizedGuestLimit"] == 2
    assert v["timeLimitMinutes"] == 13 * 60 + 59
    assert v["name"] == f"Ada Obi · {CONTACT} · 2026-11-16"
    assert r["code"] == v["code"]
    assert r["valid_for"] == "2026-11-16"
    assert r["valid_for_display"] == "Monday, 16 November"
    assert (r["days_used"], r["days_allowed"], r["days_left"]) == (1, 3, 2)
    assert r["end_date"] == "2026-11-30" and r["end_date_display"] == "Monday, 30 November"
    assert r["ends_soon"] is False
    row = w.code_rows()[0]
    assert row[3] == "2026-11-16" and row[4] == v["code"] and row[6] == "2026-11-16"


def test_same_day_request_resends_same_code_without_using_a_day():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    first = w.request(at(MON, 9, 30))
    again = w.request(at(MON, 15, 0))
    assert again["status"] == "already_issued"
    assert again["code"] == first["code"]
    assert (again["days_used"], again["days_left"]) == (1, 2)
    assert len(w.unifi.created) == 1
    assert len(w.code_rows()) == 1


def test_month_3day_weekly_cap_and_reset():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    for n, left in ((0, 2), (1, 1), (3, 0)):
        r = w.request(at(day(n)))
        assert r["status"] == "issued" and r["days_left"] == left
    r = w.request(at(day(4)))  # Friday: 4th day this week
    assert r["status"] == "weekly_limit_reached"
    assert r["days_used"] == ["2026-11-16", "2026-11-17", "2026-11-19"]
    assert r["resets_on"] == "2026-11-23" and r["resets_on_display"] == "Monday, 23 November"
    assert r["first_name"] == "Ada"
    assert len(w.unifi.created) == 3
    # Saturday is still the same week.
    assert w.request(at(day(5)))["status"] == "weekly_limit_reached"
    # Next Monday: fresh allowance, and unused days don't carry over.
    r = w.request(at(day(7)))
    assert r["status"] == "issued" and r["days_used"] == 1 and r["days_left"] == 2


def test_month_3day_unused_days_do_not_carry_over():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    w.request(at(day(0)))  # used 1 of 3 last week
    for n in (7, 8, 9):
        assert w.request(at(day(n)))["status"] == "issued"
    assert w.request(at(day(10)))["status"] == "weekly_limit_reached"


def test_month_3day_plan_ending_wednesday():
    w = World()
    w.member("month_3day", date(2026, 10, 19))  # ends Wednesday 18 November
    for n in (0, 1, 2):
        r = w.request(at(day(n)))
        assert r["status"] == "issued"
    assert r["ends_soon"] is True
    r = w.request(at(day(3)))
    assert r["status"] == "plan_expired"
    assert r["last_plan_name"] == "Monthly (3 days a week)"
    assert r["last_end_date"] == "2026-11-18"
    assert r["last_end_date_display"] == "Wednesday, 18 November"


def test_week_3day_across_two_calendar_weeks():
    thu = date(2026, 11, 19)
    w = World()
    w.member("week_3day", thu)  # Thu 19 -> Wed 25
    for d in (thu, date(2026, 11, 21), date(2026, 11, 23)):  # Thu, Sat, next Mon
        r = w.request(at(d))
        assert r["status"] == "issued"
    assert r["days_left"] == 0
    r = w.request(at(date(2026, 11, 24)))
    assert r["status"] == "plan_allowance_used"
    assert r["days_used"] == ["2026-11-19", "2026-11-21", "2026-11-23"]
    assert r["end_date"] == "2026-11-25" and r["end_date_display"] == "Wednesday, 25 November"


def test_week_3day_same_day_is_not_a_second_day():
    w = World()
    w.member("week_3day", MON)
    w.request(at(day(0)))
    w.request(at(day(0), 18))
    r = w.request(at(day(1)))
    assert r["days_used"] == 2 and r["days_left"] == 1


def test_full_plan_one_code_until_end_date():
    w = World()
    w.member("week_full", MON)  # ends Sunday 22 November
    r = w.request(at(MON, 10, 0))
    assert r["status"] == "issued"
    v = w.unifi.created[0]
    assert v["timeLimitMinutes"] == 6 * 24 * 60 + 13 * 60 + 59
    assert v["name"] == f"Ada Obi · {CONTACT} · 2026-11-16–2026-11-22"
    assert v["authorizedGuestLimit"] == 2 and v["count"] == 1
    assert "days_left" not in r
    assert r["valid_for"] == "2026-11-16 to 2026-11-22"
    assert r["valid_for_display"] == "Monday, 16 November to Sunday, 22 November"
    later = w.request(at(day(3)))
    assert later["status"] == "already_issued" and later["code"] == r["code"]
    assert len(w.unifi.created) == 1
    row = w.code_rows()[0]
    assert row[3] == "" and row[6] == "2026-11-22"


def test_overlapping_plans_prefer_full_plan():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    w.member("week_full", MON)
    r = w.request(at(MON))
    assert r["plan_name"] == "Weekly" and "days_left" not in r


def test_overlapping_daily_plans_prefer_latest_end_date():
    w = World()
    w.member("week_3day", date(2026, 11, 12))
    w.member("month_3day", date(2026, 11, 1))
    assert w.request(at(MON))["plan_name"] == "Monthly (3 days a week)"


def test_plan_expired_and_not_a_member():
    w = World()
    w.member("month_full", date(2026, 10, 1))  # ended 31 October
    r = w.request(at(MON))
    assert r["status"] == "plan_expired"
    assert r["last_plan_name"] == "Monthly" and r["last_end_date"] == "2026-10-31"
    assert r["last_end_date_display"] == "Saturday, 31 October"
    assert w.request(at(MON), contact_id="someone_else") == {"status": "not_a_member"}
    assert w.unifi.created == []


def test_member_with_plan_starting_later_is_sent_to_reception():
    w = World()
    w.member("week_full", day(7))
    r = w.request(at(MON))
    assert r["status"] == "not_a_member" and r["first_name"] == "Ada"


@pytest.mark.parametrize("typed,given", [
    ("ada_obi", "ada_obi"), ("Ada_Obi", "ada_obi"), ("  ada_obi ", "ADA_OBI"), ("ada_obi", " ada_obi\n"),
    (2348031234567, "2348031234567"), (2348031234567.0, "2348031234567"), ("2348031234567", "2348031234567"),
])
def test_contact_id_is_trimmed_and_case_insensitive(typed, given):
    w = World()
    w.backend.type_row("Members", [1, typed, "Ada Obi", ""])
    w.backend.type_row("Subscriptions", [1, 1, "month_full", "2026-11-01", ""])
    assert w.request(at(MON), contact_id=given)["status"] == "issued"


@pytest.mark.parametrize("given", ["ada", "ada_obi_2", "ada obi", "+2348031234567", ""])
def test_contact_id_must_otherwise_match_exactly(given):
    w = World()
    w.backend.type_row("Members", [1, "ada_obi", "Ada Obi", ""])
    w.backend.type_row("Members", [2, 2348031234567, "Phone Member", ""])
    w.backend.type_row("Subscriptions", [1, 1, "month_full", "2026-11-01", ""])
    w.backend.type_row("Subscriptions", [2, 2, "month_full", "2026-11-01", ""])
    assert w.request(at(MON), contact_id=given) == {"status": "not_a_member"}


def test_unifi_outage_records_nothing_and_next_request_works():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    w.unifi.outage = True
    r = w.request(at(MON))
    assert r["status"] == "wifi_system_unavailable" and r["detail"]
    assert w.code_rows() == []
    w.unifi.outage = False
    r = w.request(at(MON, 10, 5))
    assert r["status"] == "issued" and r["days_used"] == 1


def test_sheets_read_failure_is_reported_not_raised():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    w.backend.fail_reads = True
    assert w.request(at(MON))["status"] == "wifi_system_unavailable"
    assert w.expiry(at(MON))["status"] == "wifi_system_unavailable"
    assert w.unifi.created == []


def test_sheets_write_failure_cancels_the_voucher():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    w.backend.fail_appends = True
    r = w.request(at(MON))
    assert r["status"] == "wifi_system_unavailable"
    assert w.unifi.deleted == [w.unifi.created[0]["id"]]
    w.backend.fail_appends = False
    assert w.code_rows() == []


@pytest.mark.parametrize("days_before_end,soon", [(4, False), (3, True), (1, True), (0, True)])
def test_ends_soon(days_before_end, soon):
    w = World()
    w.member("week_full", MON)  # ends 22 November
    when = at(date(2026, 11, 22) - timedelta(days=days_before_end))
    assert w.request(when)["ends_soon"] is soon


def test_simultaneous_requests_give_one_code_and_delete_the_spare():
    w = World(unifi_delay=0.05)
    w.member("month_3day", date(2026, 11, 1))

    async def both():
        return await asyncio.gather(w.desk.request_code(CONTACT, at(MON)), w.desk.request_code(CONTACT, at(MON)))

    a, b = asyncio.run(both())
    assert {a["status"], b["status"]} == {"issued", "already_issued"}
    assert a["code"] == b["code"]
    assert len(w.unifi.created) == 2
    assert len(w.unifi.deleted) == 1
    assert w.unifi.deleted[0] != next(v["id"] for v in w.unifi.created if v["code"] == a["code"])
    assert len(w.code_rows()) == 1
    assert a["days_used"] == b["days_used"] == 1


def test_get_plan_expiry_active_daily_plan():
    w = World()
    w.member("month_3day", date(2026, 11, 1))
    w.request(at(day(0)))
    r = w.expiry(at(day(1)))
    assert r["status"] == "active"
    assert r["plan_name"] == "Monthly (3 days a week)"
    assert r["end_date"] == "2026-11-30" and r["end_date_display"] == "Monday, 30 November"
    assert (r["days_used"], r["days_allowed"], r["days_left"]) == (1, 3, 2)
    assert r["allowance_period"] == "week"
    assert r["first_name"] == "Ada"


def test_get_plan_expiry_full_plan_and_expired_and_unknown():
    w = World()
    w.member("week_full", MON)
    r = w.expiry(at(MON))
    assert r["status"] == "active" and "days_left" not in r and r["end_date_display"] == "Sunday, 22 November"
    r = w.expiry(at(day(7)))
    assert r["status"] == "plan_expired"
    assert r["last_plan_name"] == "Weekly" and r["last_end_date_display"] == "Sunday, 22 November"
    assert w.expiry(at(MON), contact_id="nobody") == {"status": "not_a_member"}


def test_demo_members_behave_as_described():
    """On DEMO_DAY each seeded member is in the state the seed file and README describe."""
    thu = DEMO_DAY
    w = World()
    seed(w.store, at(thu, 8))
    expect = {
        "ada_obi": ("issued", "Ada"),
        "tunde_bakare": ("weekly_limit_reached", "Tunde"),
        "chidi_eze": ("issued", "Chidi"),
        "bola_ade": ("plan_expired", "Bola"),
        "kemi_lawal": ("issued", "Kemi"),
        "femi_ojo": ("plan_allowance_used", "Femi"),
        "zainab_bello": ("issued", "Zainab"),
    }
    results = {c: w.request(at(thu), contact_id=c) for c in expect}
    for contact_id, (status, first) in expect.items():
        assert (results[contact_id]["status"], results[contact_id]["first_name"]) == (status, first), contact_id
    assert results["ada_obi"]["days_left"] == 1
    assert results["kemi_lawal"]["days_left"] == 0 and results["kemi_lawal"]["ends_soon"] is True
    assert results["zainab_bello"]["ends_soon"] is True
    assert results["bola_ade"]["last_end_date"] == "2026-11-16"
    for m in DEMO_MEMBERS:
        assert m.state.split(":")[0] == results[m.contact_id]["status"], m.name
