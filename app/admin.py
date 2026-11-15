"""Small CLI for the member sheet (uses the same env vars as the server).

    python -m app.admin add-member <contact_id> "<name>" <plan_id> [start YYYY-MM-DD]
    python -m app.admin list

`add-member` adds the member if the contact_id is new, then adds a subscription (start defaults to today).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from . import config, rules
from .config import PLANS
from .store import SheetStore, StoreError, open_store


def add_member(store: SheetStore, contact_id: str, name: str, plan_id: str, start: date | None) -> str:
    now = config.now()
    start = start or now.date()
    if plan_id not in PLANS:
        raise ValueError(f"Unknown plan_id {plan_id!r}. Use one of: {', '.join(PLANS)}")
    member = store.snapshot(fresh=True).member_by_contact_id(contact_id)
    lines = []
    if member is None:
        member = store.add_member(contact_id, name, now)
        lines.append(f"Added member #{member.id} {member.name} ({member.contact_id})")
    else:
        lines.append(f"Member #{member.id} {member.name} ({member.contact_id}) already exists")
    sub = store.add_subscription(member.id, plan_id, start, now)
    lines.append(f"Added {sub.plan.name} plan #{sub.id}: {sub.start_date} to {sub.end_date}")
    return "\n".join(lines)


def list_members(store: SheetStore) -> str:
    snap = store.snapshot(fresh=True)
    today = config.now().date()
    lines = []
    for m in snap.members:
        lines.append(f"#{m.id} {m.name} ({m.contact_id})")
        for s in snap.subscriptions_for(m.id):
            state = "active" if rules.is_active(s.start_date, s.end_date, today) else (
                "ended" if s.end_date < today else "starts later")
            used = len(snap.codes_for(s.id))
            lines.append(f"    plan #{s.id} {s.plan.name}: {s.start_date} to {s.end_date} [{state}], codes issued: {used}")
    return "\n".join(lines) or "No members yet."


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.admin", description="Manage the Corner Desk member sheet.")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add-member", help="Add a member (if new) and a plan")
    add.add_argument("contact_id", help="WhatsApp number in production, or the simulator chat name")
    add.add_argument("name")
    add.add_argument("plan_id", choices=list(PLANS))
    add.add_argument("start", nargs="?", type=date.fromisoformat, help="YYYY-MM-DD, default today")
    sub.add_parser("list", help="List members and their plans")
    args = parser.parse_args(argv)

    try:
        store = open_store(config.Settings.load())
        if args.command == "add-member":
            print(add_member(store, args.contact_id, args.name, args.plan_id, args.start))
        else:
            print(list_members(store))
    except (StoreError, ValueError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
