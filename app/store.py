"""Google Sheets store: Members, Subscriptions, AccessCodes.

`GspreadBackend` is the only code that touches gspread. `SheetStore` turns rows into typed
records and only ever appends rows; it never rewrites a tab, so rows reception adds by hand
at the same time are never overwritten.
"""

from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Protocol

from . import rules
from .config import PLANS

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

TABS: dict[str, list[str]] = {
    "Members": ["id", "contact_id", "name", "created_at"],
    "Subscriptions": ["id", "member_id", "plan_id", "start_date", "end_date", "created_at"],
    "AccessCodes": [
        "id", "member_id", "subscription_id", "issued_for_date", "code",
        "voucher_id", "valid_until_date", "created_at",
    ],
}

SHEETS_EPOCH = date(1899, 12, 30)


class StoreError(Exception):
    """Google Sheets could not be read or written."""


# --------------------------------------------------------------------------- records


@dataclass(frozen=True)
class Member:
    id: int
    contact_id: str
    name: str
    created_at: str = ""

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.split() else ""


@dataclass(frozen=True)
class Subscription:
    id: int
    member_id: int
    plan_id: str
    start_date: date
    end_date: date
    created_at: str = ""

    @property
    def plan(self):
        return PLANS[self.plan_id]


@dataclass(frozen=True)
class AccessCode:
    id: int
    member_id: int
    subscription_id: int
    issued_for_date: date | None  # the day, for daily plans; None for full plans
    code: str
    voucher_id: str
    valid_until_date: date | None
    created_at: str = ""


@dataclass
class Snapshot:
    members: list[Member] = field(default_factory=list)
    subscriptions: list[Subscription] = field(default_factory=list)
    codes: list[AccessCode] = field(default_factory=list)

    def member_by_contact_id(self, contact_id: object) -> Member | None:
        """Trim whitespace, then match case-insensitively (`Ada_Obi` finds `ada_obi`)."""
        wanted = contact_key(contact_id)
        if not wanted:
            return None
        for m in self.members:
            if contact_key(m.contact_id) == wanted:
                return m
        return None

    def subscriptions_for(self, member_id: int) -> list[Subscription]:
        return [s for s in self.subscriptions if s.member_id == member_id]

    def codes_for(self, subscription_id: int) -> list[AccessCode]:
        return [c for c in self.codes if c.subscription_id == subscription_id]

    def next_id(self, tab: str) -> int:
        rows = {"Members": self.members, "Subscriptions": self.subscriptions, "AccessCodes": self.codes}[tab]
        return max((r.id for r in rows), default=0) + 1


# --------------------------------------------------------------------------- cell parsing


def parse_date(value: object) -> date | None:
    """Date serials (epoch 1899-12-30), `YYYY-MM-DD` and `DD/MM/YYYY` text -> date."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return SHEETS_EPOCH + timedelta(days=int(value))
    text = str(value).strip()
    if not text:
        return None
    for fmt, chunk in (("%Y-%m-%d", text[:10]), ("%d/%m/%Y", text.split()[0])):
        try:
            return datetime.strptime(chunk, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date: {value!r}")


def parse_int(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("not an id")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"not an id: {value!r}")
        return int(value)
    return int(str(value).strip())


def parse_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def contact_key(value: object) -> str:
    """How contact_ids are compared: as text (a number typed into Sheets counts as its digits),
    trimmed, case-insensitive."""
    return parse_text(value).casefold()


def parse_code(value: object) -> str:
    text = parse_text(value)
    # A code typed by hand may lose its leading zeros; codes are always 10 digits.
    return text.zfill(10) if text.isdigit() and len(text) < 10 else text


def _rows_as_dicts(tab: str, rows: list[list]) -> list[tuple[int, dict]]:
    """Map rows by the header row (so columns can be reordered); returns (sheet row number, dict)."""
    if not rows:
        return []
    header = [parse_text(h).lower() for h in rows[0]]
    if "id" not in header:  # no header row: assume our column order
        header, data, first = TABS[tab], rows, 1
    else:
        data, first = rows[1:], 2
    out = []
    for offset, row in enumerate(data):
        if not any(parse_text(v) for v in row):
            continue
        out.append((first + offset, {h: (row[i] if i < len(row) else "") for i, h in enumerate(header) if h}))
    return out


def parse_snapshot(raw: dict[str, list[list]]) -> Snapshot:
    snap = Snapshot()
    for row_no, r in _rows_as_dicts("Members", raw.get("Members", [])):
        try:
            contact_id = parse_text(r.get("contact_id"))
            if not contact_id:
                raise ValueError("no contact_id")
            snap.members.append(Member(parse_int(r.get("id")), contact_id, parse_text(r.get("name")), parse_text(r.get("created_at"))))
        except (ValueError, TypeError) as e:
            log.warning("Skipping Members row %s: %s", row_no, e)

    for row_no, r in _rows_as_dicts("Subscriptions", raw.get("Subscriptions", [])):
        try:
            plan_id = parse_text(r.get("plan_id"))
            if plan_id not in PLANS:
                raise ValueError(f"unknown plan_id {plan_id!r}")
            start = parse_date(r.get("start_date"))
            if start is None:
                raise ValueError("no start_date")
            end = parse_date(r.get("end_date")) or rules.plan_end_date(plan_id, start)
            snap.subscriptions.append(Subscription(
                parse_int(r.get("id")), parse_int(r.get("member_id")), plan_id, start, end,
                parse_text(r.get("created_at")),
            ))
        except (ValueError, TypeError) as e:
            log.warning("Skipping Subscriptions row %s: %s", row_no, e)

    for row_no, r in _rows_as_dicts("AccessCodes", raw.get("AccessCodes", [])):
        try:
            snap.codes.append(AccessCode(
                parse_int(r.get("id")), parse_int(r.get("member_id")), parse_int(r.get("subscription_id")),
                parse_date(r.get("issued_for_date")), parse_code(r.get("code")), parse_text(r.get("voucher_id")),
                parse_date(r.get("valid_until_date")), parse_text(r.get("created_at")),
            ))
        except (ValueError, TypeError) as e:
            log.warning("Skipping AccessCodes row %s: %s", row_no, e)
    return snap


# --------------------------------------------------------------------------- backends


class SheetsBackend(Protocol):
    def list_tabs(self) -> list[str]: ...
    def add_tab(self, title: str, headers: list[str]) -> None: ...
    def read_tabs(self, titles: list[str]) -> dict[str, list[list]]: ...
    def append_rows(self, title: str, rows: list[list[str]]) -> None: ...


class GspreadBackend:
    """The only code that talks to Google. Every call failure becomes `StoreError`."""

    def __init__(self, spreadsheet_id: str, service_account_json: str = "", service_account_file: str = ""):
        import gspread

        self._gspread = gspread
        try:
            if service_account_json:
                client = gspread.service_account_from_dict(json.loads(service_account_json), scopes=SCOPES)
            else:
                client = gspread.service_account(filename=service_account_file, scopes=SCOPES)
            self._sheet = client.open_by_key(spreadsheet_id)
        except json.JSONDecodeError as e:
            raise StoreError(f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: {e}") from e
        except Exception as e:
            raise StoreError(
                f"Could not open the spreadsheet ({type(e).__name__}: {e}). Check SPREADSHEET_ID and that "
                "the sheet is shared with the service account's email as Editor."
            ) from e

    def _call(self, what: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            raise StoreError(f"Google Sheets {what} failed: {type(e).__name__}: {e}") from e

    def list_tabs(self) -> list[str]:
        return [ws.title for ws in self._call("list tabs", self._sheet.worksheets)]

    def add_tab(self, title: str, headers: list[str]) -> None:
        self._call("add tab", self._sheet.add_worksheet, title=title, rows=1000, cols=len(headers))
        self.append_rows(title, [headers])

    def read_tabs(self, titles: list[str]) -> dict[str, list[list]]:
        result = self._call(
            "read",
            self._sheet.values_batch_get,
            [f"'{t}'" for t in titles],
            params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"},
        )
        ranges = result.get("valueRanges", [])
        return {t: (ranges[i].get("values", []) if i < len(ranges) else []) for i, t in enumerate(titles)}

    def append_rows(self, title: str, rows: list[list[str]]) -> None:
        self._call(
            "append",
            self._sheet.values_append,
            f"'{title}'!A1",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            body={"values": rows},
        )


# --------------------------------------------------------------------------- store


def _fmt(d: date | None) -> str:
    return d.isoformat() if d else ""


class SheetStore:
    def __init__(self, backend: SheetsBackend, cache_seconds: float = 5, clock: Callable[[], float] = _time.monotonic):
        self.backend = backend
        self.cache_seconds = cache_seconds
        self._clock = clock
        self._cache: Snapshot | None = None
        self._cache_at = 0.0

    def ensure_tabs(self) -> None:
        """Create any missing tab, and add the header row to an empty one."""
        existing = set(self.backend.list_tabs())
        for title, headers in TABS.items():
            if title not in existing:
                self.backend.add_tab(title, headers)
        empty = [t for t, rows in self.backend.read_tabs(list(TABS)).items() if not rows]
        for title in empty:
            self.backend.append_rows(title, [TABS[title]])
        self._cache = None

    def snapshot(self, fresh: bool = False) -> Snapshot:
        """All three tabs in one read. Cached for `cache_seconds` unless `fresh`."""
        if not fresh and self._cache is not None and self._clock() - self._cache_at < self.cache_seconds:
            return self._cache
        snap = parse_snapshot(self.backend.read_tabs(list(TABS)))
        self._cache, self._cache_at = snap, self._clock()
        return snap

    def append_rows(self, title: str, rows: list[list[str]]) -> None:
        """Append rows at the bottom of a tab (never overwrites)."""
        self._cache = None  # whatever happens, the next read goes to Google
        self.backend.append_rows(title, rows)

    # Each write starts from a fresh read (for the next id). Callers serialise writes.

    def add_member(self, contact_id: str, name: str, now: datetime) -> Member:
        snap = self.snapshot(fresh=True)
        contact_id = parse_text(contact_id)
        if not contact_id:
            raise ValueError("contact_id is empty")
        if snap.member_by_contact_id(contact_id):
            raise ValueError(f"A member with contact_id {contact_id} already exists")
        m = Member(snap.next_id("Members"), contact_id, name.strip(), now.isoformat(timespec="seconds"))
        self.append_rows("Members", [[str(m.id), m.contact_id, m.name, m.created_at]])
        return m

    def add_subscription(self, member_id: int, plan_id: str, start: date, now: datetime) -> Subscription:
        if plan_id not in PLANS:
            raise ValueError(f"Unknown plan_id {plan_id!r}. Use one of: {', '.join(PLANS)}")
        snap = self.snapshot(fresh=True)
        s = Subscription(snap.next_id("Subscriptions"), member_id, plan_id, start,
                         rules.plan_end_date(plan_id, start), now.isoformat(timespec="seconds"))
        self.append_rows("Subscriptions", [[str(s.id), str(s.member_id), s.plan_id, _fmt(s.start_date),
                                        _fmt(s.end_date), s.created_at]])
        return s

    def add_access_code(self, snap: Snapshot, member_id: int, subscription_id: int, issued_for_date: date | None,
                        code: str, voucher_id: str, valid_until_date: date, now: datetime) -> AccessCode:
        """Append a code row. `snap` must be a fresh snapshot taken under the caller's lock."""
        c = AccessCode(snap.next_id("AccessCodes"), member_id, subscription_id, issued_for_date, code, voucher_id,
                       valid_until_date, now.isoformat(timespec="seconds"))
        self.append_rows("AccessCodes", [[str(c.id), str(c.member_id), str(c.subscription_id), _fmt(c.issued_for_date),
                                      c.code, c.voucher_id, _fmt(c.valid_until_date), c.created_at]])
        return c


def open_store(settings) -> SheetStore:
    """Connect to the sheet from `Settings` and make sure the tabs exist. Raises `StoreError`."""
    backend = GspreadBackend(settings.spreadsheet_id, settings.service_account_json, settings.service_account_file)
    store = SheetStore(backend, cache_seconds=settings.cache_seconds)
    store.ensure_tabs()
    return store
