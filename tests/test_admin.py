from datetime import date, datetime

import pytest

from app import admin
from app.config import TZ
from app.seed import seed
from app.store import SheetStore
from tests.fake_sheets import FakeSheetsBackend


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr("app.config.now", lambda: datetime(2026, 11, 19, 10, 0, tzinfo=TZ))
    s = SheetStore(FakeSheetsBackend(), cache_seconds=0)
    s.ensure_tabs()
    return s


def test_add_member_then_add_another_plan(store):
    out = admin.add_member(store, "ada_obi", "Ada Obi", "month_3day", None)
    assert "Added member #1 Ada Obi (ada_obi)" in out
    assert "2026-11-19 to 2026-12-18" in out
    out = admin.add_member(store, " Ada_Obi ", "Ada Obi", "week_full", date(2026, 12, 19))
    assert "already exists" in out and "2026-12-19 to 2026-12-25" in out
    snap = store.snapshot(fresh=True)
    assert len(snap.members) == 1 and len(snap.subscriptions) == 2


def test_list_shows_plan_state(store):
    seed(store, admin.config.now())
    text = admin.list_members(store)
    assert "Bola Ade (bola_ade)" in text and "[ended]" in text and "[active]" in text


def test_unknown_plan_is_refused(store):
    with pytest.raises(ValueError):
        admin.add_member(store, "ada_obi", "Ada Obi", "gold", None)
