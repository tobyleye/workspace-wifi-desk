"""UniFi hotspot vouchers.

`MockUnifiClient` is the only implementation. It follows the official UniFi Network
Integration API endpoint

    POST /proxy/network/integration/v1/sites/{siteId}/hotspot/vouchers

It builds the same request body the real API takes (`count`, `name`, `timeLimitMinutes`,
`authorizedGuestLimit`) and stores voucher objects with the official response fields
(`id`, `createdAt`, `name`, `code`, `authorizedGuestLimit`, `authorizedGuestCount`,
`timeLimitMinutes`, `expired`).

The rest of the app only ever calls two methods:

    await client.create_voucher(name, time_limit_minutes, guest_limit) -> {"id": ..., "code": ...}
    await client.delete_voucher_quietly(voucher_id) -> None

Going live means writing a class with those same two methods that sends the request body
below to the real controller (API key in `X-API-KEY`, the site id in the path; through
UniFi Site Manager's cloud connector if the controller is only reachable in the office),
and raising `UnifiError` when the call fails. Nothing else changes.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime, timezone


class UnifiError(Exception):
    """The WiFi controller could not create the voucher."""


class MockUnifiClient:
    def __init__(self, site_id: str = "default", delay_seconds: float = 0.2):
        self.site_id = site_id
        self.delay_seconds = delay_seconds
        self.outage = False
        self.vouchers: dict[str, dict] = {}
        self.requests: list[dict] = []  # every request body sent, for tests and debugging

    @property
    def endpoint(self) -> str:
        return f"/proxy/network/integration/v1/sites/{self.site_id}/hotspot/vouchers"

    async def create_voucher(self, name: str, time_limit_minutes: int, guest_limit: int) -> dict:
        body = {
            "count": 1,
            "name": name,
            "timeLimitMinutes": int(time_limit_minutes),
            "authorizedGuestLimit": int(guest_limit),
        }
        self.requests.append(body)
        await asyncio.sleep(self.delay_seconds)
        if self.outage:
            raise UnifiError("UniFi controller did not respond (simulated outage)")

        codes_in_use = {v["code"] for v in self.vouchers.values()}
        code = f"{secrets.randbelow(10**10):010d}"
        while code in codes_in_use:
            code = f"{secrets.randbelow(10**10):010d}"

        voucher = {
            "id": str(uuid.uuid4()),
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "name": body["name"],
            "code": code,
            "authorizedGuestLimit": body["authorizedGuestLimit"],
            "authorizedGuestCount": 0,
            "timeLimitMinutes": body["timeLimitMinutes"],
            "expired": False,
        }
        self.vouchers[voucher["id"]] = voucher
        # The real API answers `{"vouchers": [voucher, ...]}` for `count` vouchers.
        return {"id": voucher["id"], "code": voucher["code"]}

    async def delete_voucher_quietly(self, voucher_id: str) -> None:
        """Remove a voucher we don't need. Never raises: a spare voucher is harmless."""
        try:
            await asyncio.sleep(self.delay_seconds / 2)
            if not self.outage:
                self.vouchers.pop(voucher_id, None)
        except Exception:  # pragma: no cover - defensive
            pass
