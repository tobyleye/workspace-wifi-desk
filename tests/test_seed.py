from datetime import datetime

from app.config import TZ
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
    assert len(store.snapshot(fresh=True).members) == len(DEMO_MEMBERS) == 7
    assert seed(store, NOW) is False
    assert len(store.snapshot(fresh=True).members) == 7


def test_seed_does_not_touch_a_sheet_with_real_members():
    backend, store = make_store()
    backend.type_row("Members", [1, "real_member", "Real Member", ""])
    assert seed(store, NOW) is False
    assert len(backend.tabs["Members"]) == 2


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
    assert "Added 7 demo members. On Thursday, 19 November 2026:" in out
    assert "Tunde Bakare" in out and "admin/clock?to=2026-11-19" in out
    assert len(backend.tabs["Members"]) == 8
    seed_module.main()
    assert "already has members" in capsys.readouterr().out
    assert len(backend.tabs["Members"]) == 8
