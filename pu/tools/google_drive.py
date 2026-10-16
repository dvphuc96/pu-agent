"""google_drive — import knowledge files from the user's Drive (read-only).

Third member of the Google Desktop-OAuth family (calendar, gmail), same rules:
  bundled client by default, `.pu/credentials.json` overrides;
  own token file (`.pu/drive-token.json`) with the `drive.readonly` scope,
  so connecting Drive touches neither calendar nor Gmail tokens;
  connect() is the ONE place a browser may appear — the CLI/CRM tab, never
  mid-turn.

Supported files are text-only for now (house rule: no new dependencies):
Google Docs (exported to text/plain), .md, .txt. Everything else is named as
unsupported instead of silently imported empty.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pu.memory import knowledge

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_TOKEN_FILE = "drive-token.json"

# mimeType → short kind shown in the picker. Google Docs are exported to
# text/plain at import; loose .md/.txt come down as media.
SUPPORTED_MIME = {
    "application/vnd.google-apps.document": "doc",
    "text/plain": "txt",
    "text/markdown": "md",
}

_INSTALL_HINT = "Google Drive support is not installed — run: pip install -e '.[drive]'"
_SETUP_HINT = (
    "Google Drive is not connected yet. Run `pu connect drive` (or click "
    "Connect in the Memory ▸ Knowledge import dialog) to sign in — read-only, "
    "about ten seconds."
)


def _token_path(home: Path) -> Path:
    return home / _TOKEN_FILE


def _load_credentials(home: Path):
    """Cached token → refresh if stale → None. Never opens a browser."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        return None
    path = _token_path(home)
    if not path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(path), scopes=[SCOPE])
    except Exception:
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            path.write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            return None
    return creds if (creds and creds.valid) else None


def connect(home: Path) -> str:
    """Browser consent, cached token. One of the two allowed consent entry
    points (the other is the Knowledge import dialog)."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return _INSTALL_HINT

    own = home / "credentials.json"
    try:
        if own.exists():
            flow = InstalledAppFlow.from_client_secrets_file(str(own), scopes=[SCOPE])
            client_source = "credentials.json override"
        else:
            from pu.tools.google_calendar import BUNDLED_CLIENT_CONFIG

            bundled = BUNDLED_CLIENT_CONFIG.get("installed", {})
            if not bundled.get("client_id") or not bundled.get("client_secret"):
                return ("Google Drive's bundled OAuth client is not configured yet. "
                        "Drop your own Desktop OAuth client at .pu/credentials.json.")
            flow = InstalledAppFlow.from_client_config(
                BUNDLED_CLIENT_CONFIG, scopes=[SCOPE])
            client_source = "bundled OAuth client"
        creds = flow.run_local_server(port=0)
    except Exception as exc:
        return f"Google sign-in failed or was cancelled ({type(exc).__name__}). Nothing was saved."

    path = _token_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")
    return f"Google Drive connected (read-only via {client_source}). Token cached at {path}."


def probe_drive(home: Path) -> None:
    """Same contract as probe_gmail: raises with a sentence when unusable."""
    try:
        import google_auth_httplib2  # noqa: F401
        import httplib2  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive support is not installed; install with pip install -e '.[drive]'"
        ) from exc
    if not _token_path(home).exists():
        raise RuntimeError(
            "Google Drive is not authorized; complete OAuth with `pu connect drive` and try again"
        )
    if _load_credentials(home) is None:
        raise RuntimeError("Google Drive OAuth token is invalid; reauthorize with `pu connect drive`")


def _service(home: Path):
    creds = _load_credentials(home)
    if creds is None:
        raise RuntimeError(_SETUP_HINT)
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _kind(name: str, mime: str) -> str | None:
    if mime in SUPPORTED_MIME:
        return SUPPORTED_MIME[mime]
    if name.lower().endswith(".md"):
        return "md"
    if name.lower().endswith(".txt"):
        return "txt"
    return None


def list_files(home: Path) -> list[dict]:
    """Text files in the user's Drive that PU can import, newest first.
    Unsupported types are filtered out HERE so the picker only ever offers
    what will actually work."""
    service = _service(home)
    resp = service.files().list(
        q="trashed = false", pageSize=100,
        fields="files(id,name,mimeType)",
        orderBy="modifiedByMeTime desc",
    ).execute()
    out = []
    for item in resp.get("files", []):
        kind = _kind(item.get("name", ""), item.get("mimeType", ""))
        if kind:
            out.append({"id": item["id"], "name": item["name"], "kind": kind})
    return out


def import_file(conn: sqlite3.Connection, home: Path, file_id: str) -> str:
    """Download one file as text and store it as a knowledge source."""
    service = _service(home)
    meta = service.files().get(fileId=file_id, fields="id,name,mimeType").execute()
    name, mime = meta.get("name", f"drive-{file_id}"), meta.get("mimeType", "")
    if not _kind(name, mime):
        return f"'{name}' is not a supported type yet (docs, .md, .txt only — no PDF yet)."
    if mime == "application/vnd.google-apps.document":
        raw = service.files().export(fileId=file_id, mimeType="text/plain").execute()
    else:
        raw = service.files().get_media(fileId=file_id).execute()
    text = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
    if not text.strip():
        return f"'{name}' came back empty — nothing to store."
    knowledge.add_source(conn, name, text, origin="drive", path=f"drive:{file_id}")
    return f"Imported '{name}' ({len(text)} chars) into Knowledge."
