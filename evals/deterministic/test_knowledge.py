"""DETERMINISTIC EVAL — Knowledge: sources in, sentences out, FTS in between.

Covers the store (chunking/search/reindex), the gate wiring, the tool, the
Drive import path (over fake googleapiclient, same approach as
test_gmail.py), the registry card and the dashboard actions. No network, so
`make gate` never depends on Drive or on any file outside tmp_path.
"""

from __future__ import annotations

import sys
from types import ModuleType

from evals.helpers import ScriptedProvider, make_pu
from pu.db import connect
from pu.memory import knowledge

# ---- chunking + search -----------------------------------------------------

def test_chunk_text_splits_at_paragraphs():
    paras = ["A" * 300, "B" * 300, "C" * 300, "D" * 300]
    chunks = knowledge.chunk_text("\n\n".join(paras))
    assert len(chunks) >= 2
    assert all(len(c) <= knowledge.CHUNK_LIMIT + 50 for c in chunks)
    assert chunks[0].startswith("A") and "B" in chunks[0]  # whole paragraphs kept


def test_add_search_exclude_disabled(tmp_path):
    conn = connect(tmp_path)
    knowledge.add_source(conn, "Playbook", "Gia tri ban hang la long tru long. " * 20)
    knowledge.add_source(conn, "Secrets", "mat khau noi bo xzenovia", enabled=0)
    hits = knowledge.search(conn, "ban hang")
    assert hits and hits[0]["title"] == "Playbook"
    assert knowledge.search(conn, "xzenovia") == []  # disabled source is invisible


def test_delete_removes_chunks(tmp_path):
    conn = connect(tmp_path)
    sid = knowledge.add_source(conn, "Temp", "noi dung xoa thu nghiem")
    knowledge.delete_source(conn, sid)
    assert knowledge.search(conn, "xoa thu nghiem") == []
    assert knowledge.list_sources(conn) == []


def test_set_group(tmp_path):
    conn = connect(tmp_path)
    sid = knowledge.add_source(conn, "Doc", "noi dung nhom")
    knowledge.set_group(conn, sid, "Sale")
    assert knowledge.list_sources(conn)[0]["group"] == "Sale"


# ---- folder re-index -------------------------------------------------------

def test_reindex_folder_adds_updates_and_removes(tmp_path):
    folder = tmp_path / "knowledge"
    folder.mkdir()
    (folder / "a.md").write_text("tai lieu dau tien ve ban hang", encoding="utf-8")
    conn = connect(tmp_path)
    out = knowledge.reindex_folder(conn, folder)
    assert "1" in out
    (folder / "a.md").write_text("tai lieu da cap nhat hoan toan", encoding="utf-8")
    (folder / "b.txt").write_text("ghi chu tu thu muc", encoding="utf-8")
    knowledge.reindex_folder(conn, folder)
    assert knowledge.search(conn, "ban hang") == []          # old content gone
    assert knowledge.search(conn, "cap nhat hoan toan")       # updated content in
    (folder / "b.txt").unlink()
    knowledge.reindex_folder(conn, folder)                    # removal syncs too
    assert knowledge.search(conn, "tu thu muc") == []
    titles = [r["title"] for r in knowledge.list_sources(conn)]
    assert titles == ["a"]                                    # only a.md survives


# ---- gate wiring + tool ----------------------------------------------------

def test_gate_retrieve_includes_knowledge(tmp_path, monkeypatch):
    from pu.config import Settings
    from pu.memory import Memory, retrieval_gate

    monkeypatch.setattr(retrieval_gate, "should_retrieve",
                        lambda *a, **k: (True, "ban hang", "test"))
    settings = Settings(home=tmp_path, api_key="offline")
    conn = connect(tmp_path)
    knowledge.add_source(conn, "Playbook", "quy trinh ban hang ba buoc")
    mem = Memory(conn, settings, ScriptedProvider([]))
    out = mem.gated_retrieve("cho hoi ve ban hang")
    assert "Playbook" in out and "ban hang ba buoc" in out


def test_knowledge_tool_registered_and_answers(tmp_path):
    from pu.tools import knowledge_search

    pu = make_pu(tmp_path, llm_provider=ScriptedProvider([]))  # no flags needed — core memory
    assert "search_knowledge" in pu.tools._tools
    conn = connect(tmp_path)
    knowledge.add_source(conn, "Playbook", "giam gia cuoi nam cho khvip")
    tool = knowledge_search.make_tool(conn)
    out = tool.fn(query="khvip")
    assert "Playbook" in out and "khvip" in out


# ---- Google Drive import (fake API) ----------------------------------------

def _drive_file(id="f1", name="Playbook", mime="application/vnd.google-apps.document"):
    return {"id": id, "name": name, "mimeType": mime}


def _install_fake_drive(monkeypatch, files, contents=None):
    captured = {}

    class Files:
        def list(self, **kwargs):
            captured["list"] = kwargs
            self._op = "list"
            return self

        def get(self, **kwargs):
            captured["get"] = kwargs
            self._op = "get"
            self._meta = next((f for f in files if f["id"] == kwargs.get("fileId")), files[0])
            return self

        def export(self, fileId, mimeType):
            captured["export"] = (fileId, mimeType)
            self._op = "export"
            return self

        def get_media(self, fileId):
            captured["media"] = fileId
            self._op = "media"
            return self

        def execute(self):
            if self._op == "list":
                return {"files": files}
            if self._op == "get":
                return dict(self._meta)
            return (contents or "noi dung tu drive").encode()

    class Service:
        def files(self):
            return Files()

    discovery = ModuleType("googleapiclient.discovery")

    def build(name, version, **kwargs):
        captured["build"] = (name, version)
        return Service()

    discovery.build = build
    googleapiclient = ModuleType("googleapiclient")
    googleapiclient.discovery = discovery
    errors = ModuleType("googleapiclient.errors")

    class HttpError(Exception):
        pass

    errors.HttpError = HttpError
    googleapiclient.errors = errors
    # google.auth bits so _load_credentials can read the cached token file
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
    for name, module in {
        "google": google, "google.auth": google_auth,
        "google.auth.transport": transport,
        "google.auth.transport.requests": requests_mod,
        "google.oauth2": oauth2, "google.oauth2.credentials": credentials_mod,
        "googleapiclient": googleapiclient,
        "googleapiclient.discovery": discovery,
        "googleapiclient.errors": errors,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return captured


def _token_file(home):
    import json

    (home / "drive-token.json").write_text(json.dumps(
        {"refresh_token": "r", "client_id": "c", "client_secret": "s",
         "token": "t", "scopes": ["s"]}))


def test_drive_list_filters_supported(tmp_path, monkeypatch):
    from pu.tools import google_drive

    _install_fake_drive(monkeypatch, [
        _drive_file("f1", "Playbook", "application/vnd.google-apps.document"),
        _drive_file("f2", "notes.txt", "text/plain"),
        _drive_file("f3", "movie.mp4", "video/mp4"),          # must be filtered out
    ])
    _token_file(tmp_path)
    files = google_drive.list_files(tmp_path)
    assert [f["id"] for f in files] == ["f1", "f2"]
    assert files[0]["kind"] == "doc" and files[1]["kind"] == "txt"


def test_drive_import_creates_source(tmp_path, monkeypatch):
    from pu.tools import google_drive

    _install_fake_drive(monkeypatch, [_drive_file()], contents="quy trinh tu drive")
    _token_file(tmp_path)
    conn = connect(tmp_path)
    out = google_drive.import_file(conn, tmp_path, "f1")
    assert "Playbook" in out
    hits = knowledge.search(conn, "quy trinh tu drive")
    assert hits and hits[0]["title"] == "Playbook"


# ---- registry card + dashboard actions -------------------------------------

def test_drive_card_sits_in_knowledge_group():
    from pu.integrations import list_integrations

    view = next(v for v in list_integrations() if v.key == "google_drive")
    assert view.group == "Knowledge"
    assert any(f.name == "PU_DRIVE" for f in view.fields)


def test_dashboard_knowledge_actions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pu.ops import dashboard

    out = dashboard.knowledge_action({"op": "add", "title": "T", "text": "noi dung them qua ui"})
    assert not out.get("error")
    hits = dashboard.knowledge_action({"op": "search", "query": "them qua ui"})
    assert hits["results"]
    dashboard.knowledge_action({"op": "toggle", "id": 1, "enabled": False})
    assert dashboard.knowledge_action({"op": "search", "query": "them qua ui"})["results"] == []
