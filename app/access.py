"""Every rule decision: who gets a WiFi code, and what a plan's status is.

The model never decides whether a code is allowed. It only relays these results.

Flow of `request_code`:
  1. Under the lock: read the sheet and decide (refuse, re-send an existing code, or a new one is needed).
  2. Without the lock: ask UniFi for a voucher. If that fails, nothing is recorded.
  3. Under the lock: fresh read and decide again. If another request won the race, delete the spare
     voucher and return what that request recorded. Otherwise append the code row.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime

from . import rules
from .config import DAILY_DAYS_ALLOWED, ENDS_SOON_DAYS, MAX_DEVICES
from .store import AccessCode, Member, SheetStore, Snapshot, StoreError, Subscription
from .unifi import UnifiError

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- helpers


def pick_subscription(subs: list[Subscription], today: date) -> Subscription | None:
    """The active plan; with overlaps, full plans beat daily ones, then the latest end date."""
    active = [s for s in subs if rules.is_active(s.start_date, s.end_date, today)]
    if not active:
        return None
    return max(active, key=lambda s: (not s.plan.is_daily, s.end_date, s.id))


def last_ended(subs: list[Subscription], today: date) -> Subscription | None:
    ended = [s for s in subs if s.end_date < today]
    return max(ended, key=lambda s: (s.end_date, s.id)) if ended else None


def with_display(result: dict) -> dict:
    """Add a `<field>_display` next to every date field so the model never formats dates."""
    for key in list(result):
        value = result[key]
        if isinstance(value, date):
            result[key] = value.isoformat()
            result[f"{key}_display"] = rules.display_date(value)
        elif isinstance(value, list) and value and all(isinstance(v, date) for v in value):
            result[key] = [v.isoformat() for v in value]
            result[f"{key}_display"] = [rules.display_date(v) for v in value]
    return result


def who(member: Member) -> dict:
    return {"name": member.name, "first_name": member.first_name}


def allowance_days(sub: Subscription, codes: list[AccessCode], today: date) -> list[date]:
    """Days with a code in the current allowance period of a daily plan (sorted)."""
    if sub.plan.allowance_period == "week":
        lo, hi = rules.week_start(today), today
    else:
        lo, hi = sub.start_date, sub.end_date
    return sorted({c.issued_for_date for c in codes if c.issued_for_date and lo <= c.issued_for_date <= hi})


def existing_code(sub: Subscription, codes: list[AccessCode], today: date) -> AccessCode | None:
    if sub.plan.is_daily:
        found = [c for c in codes if c.issued_for_date == today]
    else:
        found = [c for c in codes if c.issued_for_date is None]
    return min(found, key=lambda c: c.id) if found else None


def no_plan_result(member: Member, subs: list[Subscription], today: date) -> dict:
    last = last_ended(subs, today)
    if last is None:
        # Known member with no plan that has started or ended (e.g. a plan that starts later).
        return with_display({"status": "not_a_member", **who(member),
                             "detail": "This number is registered but has no current plan."})
    return with_display({"status": "plan_expired", **who(member),
                         "last_plan_name": last.plan.name, "last_end_date": last.end_date})


# --------------------------------------------------------------------------- decision


@dataclass
class NeedNewCode:
    member: Member
    sub: Subscription
    name: str
    minutes: int
    valid_until: date
    issued_for: date | None


def code_result(status: str, member: Member, sub: Subscription, code: AccessCode, today: date,
                days_used: list[date]) -> dict:
    plan = sub.plan
    result = {
        "status": status,
        **who(member),
        "code": code.code,
        "max_devices": MAX_DEVICES,
        "plan_name": plan.name,
        "plan_id": plan.id,
        "end_date": sub.end_date,
        "ends_soon": 0 <= (sub.end_date - today).days <= ENDS_SOON_DAYS,
    }
    if plan.is_daily:
        used = len(days_used)
        result.update({
            "valid_for": today,
            "days_used": used,
            "days_allowed": DAILY_DAYS_ALLOWED,
            "days_left": max(0, DAILY_DAYS_ALLOWED - used),
            "allowance_period": plan.allowance_period,
        })
        return with_display(result)
    result = with_display(result)
    result["valid_for"] = f"{sub.start_date.isoformat()} to {sub.end_date.isoformat()}"
    result["valid_for_display"] = f"{rules.display_date(sub.start_date)} to {rules.display_date(sub.end_date)}"
    return result


def decide(snap: Snapshot, contact_id: str, now: datetime) -> dict | NeedNewCode:
    today = now.date()
    member = snap.member_by_contact_id(contact_id)
    if member is None:
        return {"status": "not_a_member"}
    subs = snap.subscriptions_for(member.id)
    sub = pick_subscription(subs, today)
    if sub is None:
        return no_plan_result(member, subs, today)

    plan = sub.plan
    codes = snap.codes_for(sub.id)
    existing = existing_code(sub, codes, today)
    days = allowance_days(sub, codes, today) if plan.is_daily else []
    if existing:
        return code_result("already_issued", member, sub, existing, today, days)

    if plan.is_daily and len(days) >= DAILY_DAYS_ALLOWED:
        if plan.allowance_period == "week":
            return with_display({"status": "weekly_limit_reached", **who(member), "plan_name": plan.name,
                                 "days_used": days, "days_allowed": DAILY_DAYS_ALLOWED,
                                 "resets_on": rules.next_monday(today), "end_date": sub.end_date})
        return with_display({"status": "plan_allowance_used", **who(member), "plan_name": plan.name,
                             "days_used": days, "days_allowed": DAILY_DAYS_ALLOWED, "end_date": sub.end_date})

    if plan.is_daily:
        return NeedNewCode(member, sub, f"{member.name} · {member.contact_id} · {today.isoformat()}",
                           rules.minutes_until_end_of_day(now, today), today, today)
    return NeedNewCode(member, sub,
                       f"{member.name} · {member.contact_id} · {sub.start_date.isoformat()}–{sub.end_date.isoformat()}",
                       rules.minutes_until_end_of_day(now, sub.end_date), sub.end_date, None)


def unavailable(detail: str) -> dict:
    return {"status": "wifi_system_unavailable", "detail": detail}


# --------------------------------------------------------------------------- service


class AccessDesk:
    def __init__(self, store: SheetStore, unifi):
        self.store = store
        self.unifi = unifi
        self._lock = asyncio.Lock()

    async def _snapshot(self, fresh: bool) -> Snapshot:
        return await asyncio.to_thread(self.store.snapshot, fresh)

    async def request_code(self, contact_id: str, now: datetime) -> dict:
        today = now.date()
        try:
            async with self._lock:
                decision = decide(await self._snapshot(False), contact_id, now)
        except StoreError as e:
            log.error("Sheets read failed: %s", e)
            return unavailable("The member records could not be read. Nothing was used up.")
        if isinstance(decision, dict):
            return decision

        try:
            voucher = await self.unifi.create_voucher(decision.name, decision.minutes, MAX_DEVICES)
        except UnifiError as e:
            log.error("UniFi failed for member %s: %s", decision.member.id, e)
            return unavailable("The WiFi controller did not respond. Nothing was recorded or used up.")

        try:
            async with self._lock:
                snap = await self._snapshot(True)
                again = decide(snap, contact_id, now)
                if not isinstance(again, NeedNewCode) or again.sub.id != decision.sub.id:
                    # Another request recorded a code meanwhile (or the plan changed): keep theirs.
                    await self.unifi.delete_voucher_quietly(voucher["id"])
                    return again if isinstance(again, dict) else unavailable("Please try again.")
                code = await asyncio.to_thread(
                    self.store.add_access_code, snap, decision.member.id, decision.sub.id, decision.issued_for,
                    voucher["code"], voucher["id"], decision.valid_until, now,
                )
        except StoreError as e:
            log.error("Sheets write failed: %s", e)
            await self.unifi.delete_voucher_quietly(voucher["id"])
            return unavailable("The code could not be recorded, so it was cancelled. Nothing was used up.")

        days = allowance_days(decision.sub, snap.codes_for(decision.sub.id) + [code], today) \
            if decision.sub.plan.is_daily else []
        return code_result("issued", decision.member, decision.sub, code, today, days)

    async def get_expiry(self, contact_id: str, now: datetime) -> dict:
        today = now.date()
        try:
            snap = await self._snapshot(False)
        except StoreError as e:
            log.error("Sheets read failed: %s", e)
            return unavailable("The member records could not be read right now.")
        member = snap.member_by_contact_id(contact_id)
        if member is None:
            return {"status": "not_a_member"}
        subs = snap.subscriptions_for(member.id)
        sub = pick_subscription(subs, today)
        if sub is None:
            return no_plan_result(member, subs, today)
        result = {"status": "active", **who(member), "plan_name": sub.plan.name, "plan_id": sub.plan_id,
                  "end_date": sub.end_date, "ends_soon": 0 <= (sub.end_date - today).days <= ENDS_SOON_DAYS}
        if sub.plan.is_daily:
            used = len(allowance_days(sub, snap.codes_for(sub.id), today))
            result.update({"days_used": used, "days_allowed": DAILY_DAYS_ALLOWED,
                           "days_left": max(0, DAILY_DAYS_ALLOWED - used),
                           "allowance_period": sub.plan.allowance_period})
        return with_display(result)
