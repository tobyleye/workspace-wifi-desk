"""Plans, timezone, settings from env vars, and the server clock."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import find_dotenv, load_dotenv

TZ = ZoneInfo("Africa/Lagos")

# Each code works on this many devices (UniFi `authorizedGuestLimit`).
MAX_DEVICES = 2
# Daily plans allow this many days per allowance period.
DAILY_DAYS_ALLOWED = 3
# `ends_soon` is true when the plan ends within this many days (today included).
ENDS_SOON_DAYS = 3


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    length: str  # "week" (7 days incl. start) or "month" (start -> same date next month - 1 day)
    kind: str  # "full" (one code for the whole plan) or "daily" (one code per day used)
    allowance_period: str | None = None  # daily plans only: "week" (Mon-Sat) or "plan"

    @property
    def is_daily(self) -> bool:
        return self.kind == "daily"


PLANS: dict[str, Plan] = {
    "week_full": Plan("week_full", "Weekly", "week", "full"),
    "week_3day": Plan("week_3day", "Weekly (3 days)", "week", "daily", allowance_period="plan"),
    "month_full": Plan("month_full", "Monthly", "month", "full"),
    "month_3day": Plan("month_3day", "Monthly (3 days a week)", "month", "daily", allowance_period="week"),
}


def now() -> datetime:
    """Real current time in Lagos."""
    return datetime.now(TZ)


class Clock:
    """The server's time: real time plus an offset set through the `/admin/clock` route.

    That route is the only way to change the time. The offset keeps time moving, so after jumping
    to a date the clock ticks on from there. It lives in memory: a restart goes back to real time.
    `base` replaces the real clock in tests.
    """

    def __init__(self, base=None):
        self.base = base or (lambda: now())
        self.offset = timedelta(0)

    def __call__(self) -> datetime:
        return self.base() + self.offset

    def jump_to(self, when: datetime) -> None:
        when = when.replace(tzinfo=TZ) if when.tzinfo is None else when
        self.offset = when - self.base()

    def shift_days(self, days: float) -> None:
        self.offset = timedelta(days=days)

    def reset(self) -> None:
        self.offset = timedelta(0)

    @property
    def shifted(self) -> bool:
        return self.offset != timedelta(0)


class ConfigError(SystemExit):
    """Required settings are missing. Stops the program with a message, no traceback."""


def _require(problems: list[str], name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        problems.append(f"{name} is not set")
    return value


@dataclass(frozen=True)
class Settings:
    spreadsheet_id: str
    service_account_json: str
    service_account_file: str
    cache_seconds: float
    mcp_token: str
    admin_token: str
    port: int

    @classmethod
    def load(cls) -> "Settings":
        """Read `.env` from the current folder or a parent (if there is one; real env vars win), then the environment."""
        load_dotenv(find_dotenv(usecwd=True))
        return cls.from_env()

    @classmethod
    def from_env(cls) -> "Settings":
        """Settings from env vars. Raises `ConfigError` listing every missing required one."""
        env = os.environ
        problems: list[str] = []
        spreadsheet_id = _require(problems, "SPREADSHEET_ID")
        sa_json = env.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        sa_file = env.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
        if not sa_json and not sa_file:
            problems.append("GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE is not set")
        elif sa_file and not sa_json and not os.path.isfile(sa_file):
            problems.append(f"GOOGLE_SERVICE_ACCOUNT_FILE points to a missing file: {sa_file}")
        if problems:
            raise ConfigError(
                "Configuration error:\n  - " + "\n  - ".join(problems)
                + "\nSet them in the environment or in a .env file (see .env.example and README.md)."
            )
        return cls(
            spreadsheet_id=spreadsheet_id,
            service_account_json=sa_json,
            service_account_file=sa_file,
            cache_seconds=float(env.get("CACHE_SECONDS") or 5),
            mcp_token=env.get("MCP_TOKEN", "").strip(),
            admin_token=env.get("ADMIN_TOKEN", "").strip(),
            port=int(env.get("PORT") or 8000),
        )
