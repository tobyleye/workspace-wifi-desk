"""In-memory stand-in for GspreadBackend. Values are kept exactly as written (like RAW input),
and hand-typed rows can be added with any cell types Sheets would return (ints, serials, text)."""

from __future__ import annotations

import asyncio

from app.store import StoreError


class FakeSheetsBackend:
    def __init__(self):
        self.tabs: dict[str, list[list]] = {}
        self.reads = 0
        self.appends: list[tuple[str, list[list]]] = []
        self.fail_reads = False
        self.fail_appends = False

    def list_tabs(self) -> list[str]:
        return list(self.tabs)

    def add_tab(self, title: str, headers: list[str]) -> None:
        self.tabs[title] = []
        self.append_rows(title, [headers])

    def read_tabs(self, titles: list[str]) -> dict[str, list[list]]:
        if self.fail_reads:
            raise StoreError("Google Sheets read failed: simulated")
        self.reads += 1
        return {t: [list(r) for r in self.tabs.get(t, [])] for t in titles}

    def append_rows(self, title: str, rows: list[list]) -> None:
        if self.fail_appends:
            raise StoreError("Google Sheets append failed: simulated")
        self.appends.append((title, [list(r) for r in rows]))
        self.tabs.setdefault(title, []).extend(list(r) for r in rows)

    def type_row(self, title: str, row: list) -> None:
        """Someone typing a row straight into the sheet."""
        self.tabs.setdefault(title, []).append(list(row))


class FakeUnifi:
    """Records calls; optional delay to let concurrent requests overlap."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.outage = False
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self._n = 0

    async def create_voucher(self, name: str, time_limit_minutes: int, guest_limit: int) -> dict:
        from app.unifi import UnifiError

        await asyncio.sleep(self.delay)
        if self.outage:
            raise UnifiError("simulated outage")
        self._n += 1
        voucher = {"id": f"v{self._n}", "code": f"{1000000000 + self._n}", "name": name,
                   "count": 1, "timeLimitMinutes": time_limit_minutes, "authorizedGuestLimit": guest_limit}
        self.created.append(voucher)
        return {"id": voucher["id"], "code": voucher["code"]}

    async def delete_voucher_quietly(self, voucher_id: str) -> None:
        self.deleted.append(voucher_id)
