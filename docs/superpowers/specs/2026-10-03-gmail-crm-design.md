# Gmail CRM Inbox — design

Date: 2026-10-03
Status: approved (chat design with Sean)

## Goal

A "CRM" group on the dashboard's Connections page holding a **Gmail** card.
When enabled, PU pulls the most recent INBOX mail (read-only) into
`state.db`, can answer "email nào mới hôm nay?" through a new tool, and the
dashboard gains a **CRM** tab listing the synced inbox.

## Decisions (from the design conversation)

- **Scope**: card + real pull (option B). Not just a card; not a sync engine.
- **Display**: tool for the agent **and** a dashboard CRM tab.
- **Trigger**: sync on demand — a tool call or the tab's "Sync now" button.
  No timers, no background loop.
- **Auth**: Google Desktop OAuth, same pattern as `google_calendar.py`.
  Separate token file (`.pu/gmail-token.json`, scope `gmail.readonly`) so
  the calendar token is never touched; consent opens only from
  `pu connect gmail` or the CRM tab's Connect button — never mid-turn.
- **Pull slice**: last `PU_GMAIL_MAX_MESSAGES` (default 50) INBOX messages,
  upsert by Gmail message ID, body text truncated to 8 KB.
- **Tool name**: `read_gmail_today` (per Sean's mockup), args `days` (default
  1) and `max_results` (default 20). Registered only when `PU_GMAIL` is on
  — every registered tool ships in every prompt (footprint ladder).

## Components

1. `pu/tools/gmail.py` — self-contained module:
   `connect()` (browser consent), `sync()` (pull + upsert, returns count),
   `read_recent()` (+ `format_emails`), `probe_gmail()`, `make_tool()`.
2. `pu/db.py` — `crm_emails` table: gmail id (PK), thread_id, sender,
   subject, snippet, body_text, internal_date, unread, synced_at.
3. `pu/integrations.py` — `Integration("gmail", "CRM", ...)` with fields
   `PU_GMAIL` (enable), `PU_GMAIL_LABEL` (default INBOX),
   `PU_GMAIL_MAX_MESSAGES` (default 50); extra `[gmail]`; probe checks the
   cached token without opening a browser.
4. `pu/config.py` — `gmail`, `gmail_label`, `gmail_max_messages` settings.
5. `pyproject.toml` — `[gmail]` extra (same deps as `[gcal]`).
6. Dashboard — new `crm` view + nav item, `/api/gmail/sync`,
   `/api/gmail/connect`; Connections page shows the CRM group
   (`CONNECTION_GROUPS` + map in `views.js`); `gmail.svg` logo.
7. `pu/__main__.py` — `pu connect gmail` (CLI consent entry).
8. `.env.example` — regenerated from the registry by
   `scripts/generate_env_example.py`.

## Error handling

Every path returns a sentence, never crashes the loop (house rule): not
installed → pip hint; not connected → setup hint pointing at
`pu connect gmail`; API failure → the error text. Sync is idempotent
(upsert by message ID).

## Testing

Deterministic evals only (`evals/deterministic/test_gmail.py`): table
creation + upsert idempotency, `format_emails` output shape, tool gating
(absent when `PU_GMAIL` off), integration registry entry renders in the
CRM group. No network, so `make gate` never depends on Gmail.
