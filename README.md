# Corner Hub WiFi desk

A WhatsApp AI teammate on [Chord](https://tsglabs.com) for a coworking space in Lagos that hands out WiFi access codes. A member
messages it when they arrive to get access to the WiFi. It confirms their subscription and sends back a WiFi code. Built
as an MCP server in Python that Chord calls as its tools.

## What it does

1. **Hands out WiFi codes** after checking the members plan.
2. **Tells members when their plan ends.**
3. **Answers questions** about opening hours, plans and prices (from [skill/SKILL.md](skill/SKILL.md)).
4. **Redirects all other relevant requests to reception.**

The rules live in code, not in the AI. The model calls a tool, the server decides
(`issued`, `weekly_limit_reached`, `plan_expired`, …), and the model explains the answer.
Every rule is unit-tested in [tests/](tests/).

```
WhatsApp ──> Chord teammate (skill/SKILL.md) ──MCP──> this server ──> Google Sheets + UniFi (mock)
```

## Deliberately left out

Things I'd build with more time and resources:

- Payments, subscription renewals and buying plans over WhatsApp using Paystack links
- Store the data in a real database and provide the reception with a dashboard to manage users and subscriptions.

## Run it

Needs Python 3.12, a Google service account key and a Google Sheet shared with that account as Editor
([how to set these up](https://docs.gspread.org/en/latest/oauth2.html#for-bots-using-service-account):
enable the Sheets API, create a service account, download its JSON key, share the sheet with its email).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in SPREADSHEET_ID, key file path, MCP_TOKEN, ADMIN_TOKEN
python -m app.seed          # optional: add 4 demo members, one per plan
python -m app.server        # http://localhost:8000, MCP at /mcp
```

Tests: `pip install -r requirements-dev.txt && python -m pytest -q`

## Assumptions

- Members only message during opening hours (Mon–Fri 9am–8pm, Sat 10am–6pm).
- A member is identified by the `contact_id` Chord gives for the chat: their WhatsApp number in
  production, the chat name in the simulator. Reception records it at sign-up.

## How I used AI

- **Learning Chord (Claude, chat).** I downloaded Chord's API reference and used Claude to go
  through it. That's how I found out that custom tools have to be MCP servers. I first built a small test teammate first to get used
  to the platform.
  - **Planning.** I talked through the problem with Claude. learnt about UniFi voucher API, discussed edge cases etc. I made the calls on scope, rules and assumptions; Claude turned them into a build plan

- **Writing the code.** I gave the plan to Claude Code, which wrote the server, the Sheets store,
  the UniFi mock, the tests. I reviewed it against how the
  workspace actually works.
- **Fixing issues.** When tests failed or the teammate misbehaved in the simulator, I described what
  I saw and had Claude Code find the cause and fix it, then re-ran the tests before trying again.
- **Refining the skill.** I tested the teammate in Chord's WhatsApp simulator and used Claude to
  tighten [skill/SKILL.md](skill/SKILL.md) from what I saw: shorter replies, refusing codes because of the time, not resending codes on "thanks" or "okay",
  and not apologising for problems that didn't happen.

## Links

[Field notes](notes/field-notes.md) · [Go-live note](notes/go-live.md) · [Problem statement](notes/problem.md) ·
[The skill](./skill/SKILL.md)
