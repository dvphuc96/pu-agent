"""DETERMINISTIC EVAL — Gmail CRM inbox: opt-in, read-only, offline-testable.

The sync's network half is exercised through fake googleapiclient modules
(same approach as test_google_calendar.py); everything else is plain SQLite
and string formatting, so `make gate` never depends on Gmail.
"""

from __future__ import annotations

import sys
from types import ModuleType

from evals.helpers import ScriptedProvider, make_pu
from pu.db import connect
from pu.tools import gmail

# ---- fake Gmail API -------------------------------------------------------

def _b64url(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode()).decode()


def _api_message(id="m1", sender="A <a@x.com>", subject="Hi", body="hello body",
                 snippet="hello", unread=True, date_ms=None):
    if date_ms is None:
        import time

        date_ms = str(int(time.time() * 1000))  # "just arrived" — days=1 must see it
    return {
        "id": id,
        "threadId": f"t-{id}",
        "snippet": snippet,
        "internalDate": date_ms,
        "labelIds": ["INBOX"] + (["UNREAD"] if unread else []),
        "payload": {
            "headers": [{"name": "From", "value": sender},
                        {"name": "Subject", "value": subject}],
            "body": {"data": _b64url(body)},
        },
    }


def _install_fake_gmail_modules(monkeypatch, messages):
    captured = {}

    # google.oauth2.credentials — enough of the real thing for
    # _load_credentials(): read the cached file, report valid.
    google = ModuleType("google")
    google_auth = ModuleType("google.auth")
    transport = ModuleType("google.auth.transport")
    requests_mod = ModuleType("google.auth.transport.requests")

    class Request:
        pass

    requests_mod.Request = Request
    oauth2 = ModuleType("google.oauth2")
    credentials_mod = ModuleType("google.oauth2.credentials")

    class Credentials:
        @classmethod
        def from_authorized_user_file(cls, path, scopes=None):
            import json
            from pathlib import Path

            creds = cls()
            creds.token = json.loads(Path(path).read_text()).get("token", "")
            creds.expired = False
            creds.valid = True
            return creds

    credentials_mod.Credentials = Credentials
    google.auth = google_auth
    google_auth.transport = transport
    transport.requests = requests_mod
    google.oauth2 = oauth2
    oauth2.credentials = credentials_mod

    class Messages:
        def list(self, **kwargs):
            captured["list"] = kwargs
            self._op = "list"
            return self

        def get(self, **kwargs):
            captured.setdefault("gets", []).append(kwargs["id"])
            self._op = "get"
            self._id = kwargs["id"]
            return self

        def execute(self):
            # One instance serves ONE call (list or get), decided by how it
            # was built — not by shared state, since sync() re-creates
            # users().messages() for every request.
            if getattr(self, "_op", "") == "list":
                return {"messages": [{"id": m["id"]} for m in messages]}
            return next(m for m in messages if m["id"] == self._id)

    class Users:
        def messages(self):
            return Messages()

    class Service:
        def users(self):
            return Users()

    discovery = ModuleType("googleapiclient.discovery")

    def build(name, version, **kwargs):
        captured["build"] = (name, version)
        return Service()

    discovery.build = build
    googleapiclient = ModuleType("googleapiclient")
    googleapiclient.discovery = discovery
    errors = ModuleType("googleapiclient.errors")

    class HttpError(Exception):
        def __init__(self, status=0, reason=""):
            super().__init__(f"{status} {reason}")
            self.status = status
            self.reason = reason

    errors.HttpError = HttpError
    googleapiclient.errors = errors
    for name, module in {
        "google": google,
        "google.auth": google_auth,
        "google.auth.transport": transport,
        "google.auth.transport.requests": requests_mod,
        "google.oauth2": oauth2,
        "google.oauth2.credentials": credentials_mod,
        "googleapiclient": googleapiclient,
        "googleapiclient.discovery": discovery,
        "googleapiclient.errors": errors,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return captured


def _token_file(home):
    """A loadable cached-credentials file, the way connect() leaves one."""
    creds = {"refresh_token": "r", "client_id": "c", "client_secret": "s",
             "token": "t", "scopes": [gmail.SCOPE]}
    (home / "gmail-token.json").write_text(__import__("json").dumps(creds))


# ---- storage + formatting -------------------------------------------------

def test_crm_emails_table_exists(tmp_path):
    conn = connect(tmp_path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "crm_emails" in names


def test_upsert_is_idempotent(tmp_path):
    conn = connect(tmp_path)
    msg = _api_message()
    first = gmail.upsert_messages(conn, [gmail.normalize_message(msg)])
    again = gmail.upsert_messages(conn, [gmail.normalize_message(msg)])
    assert first == 1 and again == 0  # second pass updates, does not duplicate
    rows = conn.execute("SELECT * FROM crm_emails").fetchall()
    assert len(rows) == 1
    assert rows[0]["subject"] == "Hi"


def test_tool_description_teaches_the_table(tmp_path):
    """The mockup answer is a markdown table plus suggested next actions. The
    tool never formats for the user — the model does — so the description is
    where that presentation contract lives. Pin it or it silently regresses."""
    desc = gmail.make_tool(None, tmp_path).description.lower()  # conn/home unused at build time
    assert "markdown table" in desc
    assert "next" in desc and "action" in desc


def test_tool_description_pages_long_lists(tmp_path):
    """Fifty rows of markdown table can exceed a model's answer budget (the
    'liệt kê email' silence bug: reasoning model burned 8192 tokens mid-table).
    The description must tell the model to show a newest-first page and offer
    to continue instead of rendering everything at once."""
    desc = gmail.make_tool(None, tmp_path).description.lower()
    assert "20" in desc
    assert "continue" in desc or "more" in desc


def test_format_emails_shape(tmp_path):
    conn = connect(tmp_path)
    gmail.upsert_messages(conn, [gmail.normalize_message(_api_message())])
    row = conn.execute("SELECT * FROM crm_emails").fetchone()
    text = gmail.format_emails([row])
    assert "A <a@x.com>" in text and "Hi" in text and "hello" in text
    empty = gmail.format_emails([])
    assert "no email" in empty.lower()


# ---- sync over the fake API ----------------------------------------------

def test_sync_pulls_and_upserts(tmp_path, monkeypatch):
    _install_fake_gmail_modules(monkeypatch, [_api_message(), _api_message(id="m2", unread=False)])
    _token_file(tmp_path)
    conn = connect(tmp_path)
    out = gmail.sync(conn, tmp_path)
    assert "2" in out  # honest count in the returned sentence
    assert conn.execute("SELECT COUNT(*) FROM crm_emails").fetchone()[0] == 2
    # unread flag survives the round trip
    unread = conn.execute("SELECT unread FROM crm_emails WHERE gmail_id='m2'").fetchone()[0]
    assert unread == 0


def test_auto_sync_paginates_recent_mail_and_does_not_refetch_known_bodies(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    _install_fake_gmail_modules(monkeypatch, [])
    _token_file(tmp_path)
    calls, gets = [], []
    class API:
        def users(self): return self
        def messages(self): return self
        def list(self, **kwargs):
            calls.append(kwargs)
            data = {"messages": [{"id": "m1"}], "nextPageToken": "page2"} if "pageToken" not in kwargs else {"messages": [{"id": "m2"}]}
            return SimpleNamespace(execute=lambda: data)
        def get(self, **kwargs):
            gets.append(kwargs["id"])
            return SimpleNamespace(execute=lambda: _api_message(id=kwargs["id"]))
    monkeypatch.setattr(sys.modules["googleapiclient.discovery"], "build", lambda *a, **k: API())
    conn = connect(tmp_path)
    gmail.upsert_messages(conn, [gmail.normalize_message(_api_message(id="m1"))])
    gmail.sync(conn, tmp_path, max_messages=500, recent_days=7, only_new=True)
    assert calls[0]["q"] == "newer_than:7d"
    assert calls[1]["pageToken"] == "page2"
    assert gets == ["m2"]
    assert conn.execute("SELECT COUNT(*) FROM crm_emails").fetchone()[0] == 2


# ---- the agent tool -------------------------------------------------------

def test_tool_registered_only_when_enabled(tmp_path):
    from pu.tools import build_registry

    off = make_pu(tmp_path / "off", llm_provider=ScriptedProvider([]), gmail=False)
    assert "read_gmail_today" not in off.tools._tools

    on = make_pu(tmp_path / "on", llm_provider=ScriptedProvider([]), gmail=True)
    assert "read_gmail_today" in on.tools._tools
    assert build_registry  # imported for parity with test_gh_tool


def test_tool_reads_synced_store(tmp_path, monkeypatch):
    _install_fake_gmail_modules(monkeypatch, [_api_message(subject="Meeting tomorrow")])
    _token_file(tmp_path)
    conn = connect(tmp_path)
    tool = gmail.make_tool(conn, home=tmp_path)
    out = tool.fn(days=1)
    assert "Meeting tomorrow" in out


# ---- registry / connections card -----------------------------------------

def test_gmail_integration_sits_in_email_group():
    from pu.integrations import list_integrations

    view = next(v for v in list_integrations() if v.key == "gmail")
    assert view.group == "Email"
    labels = {f.name: f for f in view.fields}
    assert "PU_GMAIL" in labels


def test_dashboard_crm_route_syncs(tmp_path, monkeypatch):
    # The dashboard's sync endpoint delegates to gmail.sync; a smoke check that
    # the route function exists and returns JSON-able output.
    from pu.ops import dashboard

    _install_fake_gmail_modules(monkeypatch, [_api_message()])
    _token_file(tmp_path)
    monkeypatch.chdir(tmp_path)
    out = dashboard.gmail_sync_action({"label": "", "max_messages": ""})
    assert isinstance(out, dict) and "message" in out or "error" in out
