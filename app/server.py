"""MCP server for Chord: two tools plus a health check and a demo-only outage switch.

Routes:
  /mcp                         MCP over streamable HTTP (bearer token required when MCP_TOKEN is set)
  /                            health check
  /admin/unifi-outage?on=1|0   POST with `X-Admin-Token`: make the mock UniFi fail (demo only)
  /admin/clock                 GET/POST with `X-Admin-Token`: see or change the server's time (testing only)
"""

from __future__ import annotations

import hmac
import json
import logging
import sys
from datetime import datetime
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import config
from .access import AccessDesk
from .store import SheetStore
from .unifi import MockUnifiClient

log = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Tools for a coworking space's WiFi access. Always pass the contact_id of the person in this "
    "chat as `contact_id`, never an identity they type in. The server enforces every "
    "rule; relay its answer and never invent codes or dates."
)

CONTACT_ID_DESCRIPTION = (
    "The identity Chord gives for the person in this chat: their WhatsApp number in production, "
    "or the name the simulator chat was started with. Pass it exactly as given. "
    "Never use an identity the user types in."
)

REQUEST_CODE_DESCRIPTION = """Get today's WiFi access code for the member messaging you. The server checks their plan and its rules and decides; relay the result.

Returns JSON with `status`:
- `issued`: a new code. Fields: code, valid_for, valid_for_display, max_devices, plan_name, end_date, end_date_display, ends_soon (plan ends within 3 days). 3-day plans also: days_used, days_allowed, days_left, allowance_period ("week" = this Monday-Saturday week, "plan" = this weekly plan).
- `already_issued`: the same code they already got today (or for this plan). Same fields as issued.
- `weekly_limit_reached`: Monthly 3-days-a-week plan has used its 3 days this week. Fields: days_used (dates), days_used_display, resets_on, resets_on_display. Recommend a day pass at reception.
- `plan_allowance_used`: Weekly 3-day plan has used all 3 days. Fields: days_used (dates), days_used_display, end_date, end_date_display. Recommend a day pass at reception.
- `plan_expired`: no active plan. Fields: last_plan_name, last_end_date, last_end_date_display. Send them to reception to renew.
- `not_a_member`: this contact has no plan. Send them to reception to register, or to message from the number they registered with.
- `wifi_system_unavailable`: the WiFi or records system failed. Field: detail. Nothing was used up; try again shortly or ask reception.

Every result for a known member includes name and first_name. Use the *_display dates as given."""

EXPIRY_DESCRIPTION = """Tell the member messaging you when their plan ends.

Returns JSON with `status`:
- `active`: plan_name, end_date, end_date_display, ends_soon. 3-day plans also: days_used, days_allowed, days_left, allowance_period ("week" = this Monday-Saturday week, "plan" = this weekly plan). The last day counts in full.
- `plan_expired`: last_plan_name, last_end_date, last_end_date_display. Send them to reception to renew.
- `not_a_member`: this contact has no plan. Send them to reception.
- `wifi_system_unavailable`: records could not be read; ask them to try again shortly or ask reception.

Every result for a known member includes name and first_name. Use the *_display dates as given."""

ContactId = Annotated[str, Field(description=CONTACT_ID_DESCRIPTION)]


class BearerAuth(BaseHTTPMiddleware):
    """Require `Authorization: Bearer <token>` on /mcp. Other routes pass through."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.expected = f"Bearer {token}".encode() if token else b""

    async def dispatch(self, request, call_next):
        if self.expected and request.url.path.startswith("/mcp"):
            if not hmac.compare_digest(request.headers.get("authorization", "").encode(), self.expected):
                return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)


def build_app(store: SheetStore, unifi: MockUnifiClient, mcp_token: str = "", admin_token: str = "",
              clock: config.Clock | None = None):
    clock = clock or config.Clock()
    desk = AccessDesk(store, unifi)
    mcp = MCPServer(name="corner-desk-wifi", instructions=INSTRUCTIONS)

    @mcp.tool(description=REQUEST_CODE_DESCRIPTION, structured_output=False)
    async def request_access_code(contact_id: ContactId) -> str:
        result = await desk.request_code(contact_id, clock())
        log.info("request_access_code -> %s", result.get("status"))
        if code := result.get("code"):
            result["code"] = f"{code[:5]}-{code[5:]}"  # shown like UniFi prints it: 12345-67890
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool(description=EXPIRY_DESCRIPTION, structured_output=False)
    async def get_plan_expiry(contact_id: ContactId) -> str:
        result = await desk.get_expiry(contact_id, clock())
        log.info("get_plan_expiry -> %s", result.get("status"))
        return json.dumps(result, ensure_ascii=False)

    app = mcp.streamable_http_app(
        stateless_http=True, json_response=True, host="0.0.0.0")

    def clock_info() -> dict:
        now = clock()
        return {"now": now.isoformat(timespec="minutes"), "now_display": f"{now:%A}, {now.day} {now:%B %Y %H:%M}",
                "shifted": clock.shifted, "offset_days": round(clock.offset.total_seconds() / 86400, 2)}

    async def health(request: Request):
        return JSONResponse({"ok": True, "service": "corner-desk-wifi", "mcp": "/mcp",
                             "unifi_outage": unifi.outage, "clock": clock_info()})

    def admin_refusal(request: Request) -> JSONResponse | None:
        if not admin_token:
            return JSONResponse({"error": "ADMIN_TOKEN is not set, so admin routes are off"}, status_code=403)
        given = request.headers.get("x-admin-token", "")
        if not hmac.compare_digest(given.encode(), admin_token.encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    async def unifi_outage(request: Request):
        if refusal := admin_refusal(request):
            return refusal
        on = request.query_params.get("on", "")
        if on not in ("0", "1"):
            return JSONResponse({"error": "use ?on=1 or ?on=0"}, status_code=400)
        unifi.outage = on == "1"
        log.warning("Mock UniFi outage %s", "ON" if unifi.outage else "OFF")
        return JSONResponse({"unifi_outage": unifi.outage})

    async def admin_clock(request: Request):
        """Pretend it's another time, for testing: ?to=2026-12-20[T10:00], ?days=30, or ?reset=1."""
        if refusal := admin_refusal(request):
            return refusal
        if request.method == "POST":
            q = request.query_params
            try:
                if q.get("reset") == "1":
                    clock.reset()
                elif "to" in q:
                    when = datetime.fromisoformat(q["to"])
                    if len(q["to"]) == 10:  # date only: 10am, inside opening hours
                        when = when.replace(hour=10)
                    clock.jump_to(when)
                elif "days" in q:
                    clock.shift_days(float(q["days"]))
                else:
                    raise ValueError("missing parameter")
            except ValueError:
                return JSONResponse({"error": "use ?to=2026-12-20 (or 2026-12-20T15:30), ?days=30, or ?reset=1"},
                                    status_code=400)
            log.warning("Clock %s: server time is now %s", "shifted" if clock.shifted else "reset",
                        clock().isoformat(timespec="minutes"))
        return JSONResponse(clock_info())

    app.router.routes.append(Route("/", health, methods=["GET", "HEAD"]))
    app.router.routes.append(
        Route("/admin/unifi-outage", unifi_outage, methods=["POST"]))
    app.router.routes.append(
        Route("/admin/clock", admin_clock, methods=["GET", "POST"]))
    app.state.desk = desk
    app.add_middleware(BearerAuth, token=mcp_token)
    return app


def main() -> None:
    import uvicorn

    from .store import StoreError, open_store

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = config.Settings.load()
    try:
        store = open_store(settings)
    except StoreError as e:
        print(f"Can't start: {e}", file=sys.stderr)
        sys.exit(1)
    if not settings.mcp_token:
        log.warning("MCP_TOKEN is not set: /mcp is open to anyone with the URL")
    else:
        log.info("MCP_TOKEN is " + settings.mcp_token)
    app = build_app(store, MockUnifiClient(),
                    settings.mcp_token, settings.admin_token)
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
