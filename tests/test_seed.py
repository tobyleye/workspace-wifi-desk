from datetime import datetime

from app.config import PLANS, TZ
from app.seed import DEMO_MEMBERS, seed
from app.store import SheetStore
from tests.fake_sheets import FakeSheetsBackend

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=TZ)


def make_store():
    backend = FakeSheetsBackend()
    store = SheetStore(backend, cache_seconds=0)
    store.ensure_tabs()
    return backend, store


def test_seeds_only_when_members_empty():
    backend, store = make_store()
    assert seed(store, NOW) is True
    assert len(store.snapshot(fresh=True).members) == len(DEMO_MEMBERS) == 4
    assert seed(store, NOW) is False
    assert len(store.snapshot(fresh=True).members) == 4


def test_seed_does_not_touch_a_sheet_with_real_members():
    backend, store = make_store()
    backend.type_row("Members", [1, "real_member", "Real Member", ""])
    assert seed(store, NOW) is False
    assert len(backend.tabs["Members"]) == 2


def test_seed_adds_one_member_per_plan_and_no_codes():
    backend, store = make_store()
    seed(store, NOW)
    snap = store.snapshot(fresh=True)
    assert sorted(s.plan_id for s in snap.subscriptions) == sorted(PLANS)
    assert snap.codes == []


def test_seed_adds_a_demo_guide_tab_once():
    backend, store = make_store()
    seed(store, NOW)
    guide = backend.tabs["Demo guide"]
    assert guide[0] == ["Username", "Plan"]
    assert [r[0] for r in guide[1:]] == [m.contact_id for m in DEMO_MEMBERS]
    assert guide[1] == ["ada_obi", "Monthly (3 days a week)"]
    # Re-seeding after clearing the members does not duplicate the guide.
    for title in ("Members", "Subscriptions"):
        backend.tabs[title] = backend.tabs[title][:1]
    seed(store, NOW)
    assert len(backend.tabs["Demo guide"]) == 1 + len(DEMO_MEMBERS)


def test_seed_writes_text_only():
    backend, store = make_store()
    seed(store, NOW)
    for _, rows in backend.appends:
        for row in rows:
            assert all(isinstance(v, str) for v in row)


def test_main_runs_on_its_own(monkeypatch, capsys):
    backend = FakeSheetsBackend()
    monkeypatch.setenv("SPREADSHEET_ID", "sheet")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
    monkeypatch.setattr("app.config.now", lambda: NOW)
    monkeypatch.setattr("app.store.GspreadBackend", lambda *a, **k: backend)
    from app import seed as seed_module

    seed_module.main()
    out = capsys.readouterr().out
    assert "Added 4 demo members, all starting Monday, 16 November 2026:" in out
    assert "Kemi Lawal" in out and "admin/clock?to=2026-11-16" in out
    assert len(backend.tabs["Members"]) == 5
    seed_module.main()
    assert "already has members" in capsys.readouterr().out
    assert len(backend.tabs["Members"]) == 5
