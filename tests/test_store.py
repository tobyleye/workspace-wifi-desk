from datetime import date, datetime

from app.config import TZ
from app.store import TABS, SheetStore, parse_date
from tests.fake_sheets import FakeSheetsBackend

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=TZ)  # a Thursday


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_store(backend=None, cache_seconds=0, clock=None):
    backend = backend or FakeSheetsBackend()
    store = SheetStore(backend, cache_seconds=cache_seconds, clock=clock or Clock())
    store.ensure_tabs()
    return backend, store


def test_creates_tabs_with_headers():
    backend, _ = make_store()
    assert list(backend.tabs) == list(TABS)
    for title, headers in TABS.items():
        assert backend.tabs[title] == [headers]


def test_adds_headers_to_existing_empty_tab_and_keeps_existing_tabs():
    backend = FakeSheetsBackend()
    backend.tabs["Members"] = []
    backend.tabs["Notes"] = [["reception's own tab"]]
    make_store(backend)
    assert backend.tabs["Members"] == [TABS["Members"]]
    assert backend.tabs["Notes"] == [["reception's own tab"]]


def test_survives_restart():
    backend, store = make_store()
    m = store.add_member("ada_obi", "Ada Obi", NOW)
    store.add_subscription(m.id, "month_3day", date(2026, 11, 1), NOW)
    _, restarted = make_store(backend)
    snap = restarted.snapshot()
    assert snap.member_by_contact_id("ada_obi").name == "Ada Obi"
    assert snap.subscriptions[0].end_date == date(2026, 11, 30)


def test_hand_typed_rows_are_read_correctly():
    backend, store = make_store()
    # A WhatsApp number typed as a number (int or float), dates as serials and DD/MM/YYYY text,
    # ids as floats, stray spaces around a contact_id.
    backend.type_row("Members", [1.0, 2348031234567, "Ada Obi", ""])
    backend.type_row("Members", [2, "  Tunde_Bakare ", "Tunde Bakare"])
    backend.type_row("Members", [3, 2348039990000.0, "Chidi Eze"])
    backend.type_row("Subscriptions", [1, 1, "month_3day", 46326, ""])  # serial for 2026-10-31, no end date
    backend.type_row("Subscriptions", [2, 2, "week_full", "16/11/2026", "22/11/2026"])
    backend.type_row("AccessCodes", [1, 1, 1, 46344.0, 123456789, "v1", 46344, ""])
    snap = store.snapshot()
    assert snap.member_by_contact_id("2348031234567").id == 1
    assert snap.member_by_contact_id("tunde_bakare").name == "Tunde Bakare"
    assert snap.member_by_contact_id("2348039990000").name == "Chidi Eze"
    s1, s2 = snap.subscriptions
    assert s1.start_date == date(2026, 10, 31) and s1.end_date == date(2026, 11, 29)  # clamp, then -1 day
    assert s2.start_date == date(2026, 11, 16) and s2.end_date == date(2026, 11, 22)
    code = snap.codes[0]
    assert code.issued_for_date == date(2026, 11, 18)
    assert code.code == "0123456789"


def test_columns_can_be_reordered_and_bad_rows_are_skipped():
    backend = FakeSheetsBackend()
    backend.tabs["Members"] = [["name", "contact_id", "id", "created_at"], ["Ada Obi", "ada_obi", 1, ""],
                               ["No Contact", "", 2, ""], ["", "", "", ""]]
    backend.tabs["Subscriptions"] = [TABS["Subscriptions"], [1, 1, "gold_plan", "2026-11-01", ""]]
    _, store = make_store(backend)
    snap = store.snapshot()
    assert [m.name for m in snap.members] == ["Ada Obi"]
    assert snap.subscriptions == []


def test_parse_date_formats():
    assert parse_date(46344) == date(2026, 11, 18)
    assert parse_date("2026-11-18") == date(2026, 11, 18)
    assert parse_date("18/11/2026") == date(2026, 11, 18)
    assert parse_date("2026-11-18T10:00:00+01:00") == date(2026, 11, 18)
    assert parse_date("") is None


def test_appends_never_overwrite_rows_added_by_someone_else():
    backend, store = make_store()
    store.add_member("ada_obi", "Ada Obi", NOW)
    backend.type_row("Members", [2, "typed_by_reception", "Typed By Reception", ""])
    store.add_member("chidi_eze", "Chidi Eze", NOW)
    rows = backend.tabs["Members"][1:]
    assert [r[2] for r in rows] == ["Ada Obi", "Typed By Reception", "Chidi Eze"]
    assert rows[2][0] == "3"  # next id after reception's row
    # The store only ever appends; it never rewrites a tab.
    assert all(title in TABS for title, _ in backend.appends)


def test_reads_are_cached_and_writes_start_fresh():
    clock = Clock()
    backend, store = make_store(cache_seconds=5, clock=clock)
    reads = backend.reads
    store.snapshot()
    store.snapshot()
    assert backend.reads == reads + 1
    clock.t = 6
    store.snapshot()
    assert backend.reads == reads + 2
    store.snapshot(fresh=True)
    assert backend.reads == reads + 3
    store.add_member("ada_obi", "Ada Obi", NOW)  # fresh read before writing
    assert backend.reads == reads + 4
    store.snapshot()  # cache dropped after a write
    assert backend.reads == reads + 5


def test_contact_ids_and_codes_are_written_as_text():
    backend, store = make_store()
    m = store.add_member(" 2348031234567 ", "Ada Obi", NOW)
    s = store.add_subscription(m.id, "month_3day", date(2026, 11, 1), NOW)
    snap = store.snapshot(fresh=True)
    store.add_access_code(snap, m.id, s.id, NOW.date(), "0012345678", "v1", NOW.date(), NOW)
    for _, rows in backend.appends:
        for row in rows:
            assert all(isinstance(v, str) for v in row)
    assert backend.tabs["Members"][1][1] == "2348031234567"
    assert backend.tabs["AccessCodes"][1][4] == "0012345678"


def test_add_member_keeps_contact_id_as_given():
    backend, store = make_store()
    store.add_member(" Ada_Obi ", "Ada Obi", NOW)
    assert backend.tabs["Members"][1][1] == "Ada_Obi"  # trimmed, case kept


def test_add_member_refuses_empty_contact_id():
    _, store = make_store()
    try:
        store.add_member("   ", "Nobody", NOW)
    except ValueError as e:
        assert "empty" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_add_member_refuses_duplicate_contact_id():
    _, store = make_store()
    store.add_member("ada_obi", "Ada Obi", NOW)
    try:
        store.add_member("ADA_OBI", "Someone Else", NOW)
    except ValueError as e:
        assert "already exists" in str(e)
    else:
        raise AssertionError("expected ValueError")
