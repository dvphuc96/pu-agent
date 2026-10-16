"""Customer notebooks: durable sources, immutable reports and grounded conversations."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from pu.db import connect
from pu.persistence.database import SQLiteDatabase


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def content_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def public_link(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("URL phải dùng http/https và không chứa credential.")
    return value


class WorkspaceRepository:
    def __init__(self, home: Path):
        self.home = home
        connect(home).close()
        self.database = SQLiteDatabase.from_home(home)

    def require(self, workspace_id: str) -> dict:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM research_workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Không tìm thấy workspace.")
        return dict(row)

    def create(self, company: str, website: str = "", contact: str = "") -> dict:
        company = company.strip()
        if not company or len(company) > 200 or len(contact) > 300:
            raise ValueError("Tên khách hàng phải có 1–200 ký tự; liên hệ tối đa 300 ký tự.")
        website = public_link(website)
        if len(website) > 2000:
            raise ValueError("URL quá dài.")
        workspace_id = "ws-" + uuid.uuid4().hex
        stamp = now()
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO research_workspaces(id,company,website,contact,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (workspace_id, company, website, contact.strip(), stamp, stamp),
            )
        return self.require(workspace_id)

    def list(self) -> list[dict]:
        with self.database.connection() as conn:
            rows = conn.execute("""SELECT w.*,
                (SELECT COUNT(*) FROM workspace_sources s WHERE s.workspace_id=w.id) AS source_count,
                (SELECT COUNT(*) FROM workspace_reports r WHERE r.workspace_id=w.id) AS report_count
                FROM research_workspaces w ORDER BY updated_at DESC LIMIT 200""").fetchall()
        return [dict(row) for row in rows]

    def sources(self, workspace_id: str) -> list[dict]:
        self.require(workspace_id)
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_sources WHERE workspace_id=? ORDER BY created_at,id",
                (workspace_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            item["metadata"] = json.loads(item.pop("metadata_json"))
            items.append(item)
        return items

    def add_source(
        self,
        workspace_id: str,
        *,
        title: str,
        content: str,
        source_type: str = "document",
        url: str = "",
        published_at: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        self.require(workspace_id)
        title, content = title.strip(), content.strip()
        if not title or len(title) > 300 or not content or len(content) > 100_000:
            raise ValueError("Nguồn cần tiêu đề và nội dung (tối đa 100.000 ký tự).")
        if source_type not in {
            "email",
            "web",
            "news",
            "document",
            "memory",
            "calendar",
            "internal_knowledge",
            "tool",
        }:
            raise ValueError("Loại nguồn không hợp lệ.")
        url = public_link(url)
        if published_at:
            datetime.fromisoformat(published_at)
        fingerprint = content_hash({"content": content, "url": url})
        source_id = "src-" + uuid.uuid4().hex
        with self.database.connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO workspace_sources(
                id,workspace_id,title,source_type,content,url,published_at,fingerprint,metadata_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    source_id,
                    workspace_id,
                    title,
                    source_type,
                    content,
                    url,
                    published_at,
                    fingerprint,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now(),
                ),
            )
            row = conn.execute(
                "SELECT id FROM workspace_sources WHERE workspace_id=? AND fingerprint=?",
                (workspace_id, fingerprint),
            ).fetchone()
            conn.execute(
                "UPDATE research_workspaces SET updated_at=? WHERE id=?", (now(), workspace_id)
            )
        return next(s for s in self.sources(workspace_id) if s["id"] == row["id"])

    def select_source(self, workspace_id: str, source_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled phải là boolean.")
        with self.database.connection() as conn:
            cur = conn.execute(
                "UPDATE workspace_sources SET enabled=? WHERE workspace_id=? AND id=?",
                (int(enabled), workspace_id, source_id),
            )
            if cur.rowcount != 1:
                raise KeyError("Nguồn không thuộc workspace này.")

    def selected(self, workspace_id: str, ids: list[str] | None = None) -> list[dict]:
        sources = self.sources(workspace_id)
        if ids is not None:
            if not isinstance(ids, list) or any(not isinstance(s, str) for s in ids):
                raise ValueError("source_ids phải là danh sách ID.")
            if set(ids) - {s["id"] for s in sources}:
                raise ValueError("Nguồn không thuộc workspace này.")
        result = [s for s in sources if (s["id"] in ids if ids is not None else s["enabled"])]
        # Do not silently drop a selected source from the model context.
        if len(result) > 30 or sum(len(s["content"]) for s in result) > 120_000:
            raise ValueError("Chọn tối đa 30 nguồn, tổng nội dung tối đa 120.000 ký tự.")
        return result

    def attach_report(self, workspace_id: str, payload: dict) -> dict:
        self.require(workspace_id)
        report_id = payload.get("report_id", "")
        if not re.fullmatch(r"report-[A-Za-z0-9_-]+", report_id):
            raise ValueError("Report ID không hợp lệ.")
        with self.database.connection() as conn:
            existing = conn.execute(
                "SELECT content_hash,workspace_id FROM workspace_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
        if existing:
            if existing["workspace_id"] != workspace_id or existing["content_hash"] != content_hash(
                payload
            ):
                raise ValueError("Báo cáo đã được lưu; cần tạo phiên bản mới để chỉnh sửa.")
            return self.require_report(workspace_id, report_id)
        source_map = {}
        for s in payload.get("sources", []):
            if (
                s.get("derived")
                or s.get("metadata", {}).get("tool_ok") is False
                or not s.get("excerpt")
            ):
                continue
            source = self.add_source(
                workspace_id,
                title=s.get("title") or s["id"],
                content=s["excerpt"],
                source_type=s.get("source_type", "tool"),
                url=s.get("url") or "",
                published_at=s.get("published_at"),
                metadata={"report_id": report_id, "source_id": s["id"]},
            )
            source_map[s["id"]] = source["id"]
        stamp = now()
        with self.database.connection() as conn:
            existing = conn.execute(
                "SELECT content_hash,workspace_id FROM workspace_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if existing and (
                existing["workspace_id"] != workspace_id
                or existing["content_hash"] != content_hash(payload)
            ):
                raise ValueError("Báo cáo đã được lưu; cần tạo phiên bản mới để chỉnh sửa.")
            conn.execute(
                """INSERT OR IGNORE INTO workspace_reports
                (report_id,workspace_id,content_hash,payload_json,source_map_json,created_at) VALUES(?,?,?,?,?,?)""",
                (
                    report_id,
                    workspace_id,
                    content_hash(payload),
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(source_map),
                    stamp,
                ),
            )
            conn.execute(
                "UPDATE research_workspaces SET active_report_id=?,updated_at=? WHERE id=?",
                (report_id, stamp, workspace_id),
            )
        return self.report(workspace_id, report_id)

    def report(self, workspace_id: str, report_id: str | None = None) -> dict | None:
        workspace = self.require(workspace_id)
        report_id = report_id or workspace["active_report_id"]
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM workspace_reports WHERE workspace_id=? AND report_id=?",
                (workspace_id, report_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "payload": json.loads(row["payload_json"]),
            "content_hash": row["content_hash"],
            "source_map": json.loads(row["source_map_json"]),
        }

    def require_report(
        self, workspace_id: str, report_id: str, expected_hash: str | None = None
    ) -> dict:
        report = self.report(workspace_id, report_id)
        if report is None:
            raise KeyError("Báo cáo không thuộc workspace này.")
        if expected_hash is not None and report["content_hash"] != expected_hash:
            raise ValueError("Nội dung báo cáo đã thay đổi; cần phê duyệt lại.")
        return report

    def detail(self, workspace_id: str, report_id: str | None = None) -> dict:
        workspace = self.require(workspace_id)
        with self.database.connection() as conn:
            versions = [
                dict(r)
                for r in conn.execute(
                    "SELECT report_id,content_hash,created_at FROM workspace_reports WHERE workspace_id=? ORDER BY created_at DESC",
                    (workspace_id,),
                )
            ]
            messages = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM (SELECT * FROM workspace_messages WHERE workspace_id=? ORDER BY created_at DESC LIMIT 50) ORDER BY created_at",
                    (workspace_id,),
                )
            ]
            deliveries = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM workspace_deliveries WHERE workspace_id=? ORDER BY created_at DESC LIMIT 30",
                    (workspace_id,),
                )
            ]
        for m in messages:
            m["source_ids"] = json.loads(m.pop("source_ids_json"))
        for d in deliveries:
            d["result"] = json.loads(d.pop("result_json"))
        report = self.report(workspace_id, report_id)
        exports = ["md", "docx", "json"]
        if report:
            rid = report["payload"]["report_id"]
            exports += [
                ext for ext in ("html", "pdf") if (self.home / "reports" / f"{rid}.{ext}").is_file()
            ]
        return {
            "workspace": workspace,
            "sources": self.sources(workspace_id),
            "report": report,
            "export_formats": exports,
            "versions": versions,
            "messages": messages,
            "deliveries": deliveries,
        }

    def add_message(
        self, workspace_id: str, question: str, answer: str, source_ids: list[str]
    ) -> None:
        self.require(workspace_id)
        with self.database.connection() as conn:
            conn.execute(
                "INSERT INTO workspace_messages VALUES(?,?,?,?,?,?)",
                (
                    "msg-" + uuid.uuid4().hex,
                    workspace_id,
                    question,
                    answer,
                    json.dumps(source_ids),
                    now(),
                ),
            )
            conn.execute("UPDATE research_workspaces SET updated_at=? WHERE id=?", (now(), workspace_id))

    def record_delivery(
        self, workspace_id: str, report_id: str, action_hash: str, destination: str, result: dict
    ) -> None:
        with self.database.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO workspace_deliveries VALUES(?,?,?,?,?,?)",
                (action_hash, workspace_id, report_id, destination, json.dumps(result), now()),
            )

    def import_existing(self) -> None:
        """Expose existing report artifacts without requiring an LLM or rerunning research."""
        with self.database.connection() as conn:
            known = {r[0] for r in conn.execute("SELECT report_id FROM workspace_reports")}
        for path in sorted(
            (self.home / "reports").glob("report-*.json"), key=lambda p: p.stat().st_mtime
        ):
            if path.stem in known:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("report_id") != path.stem:
                    continue
                company = (
                    payload.get("metadata", {}).get("company")
                    or payload.get("title")
                    or "Nghiên cứu khách hàng"
                )
                workspace = next(
                    (w for w in self.list() if w["company"] == company), None
                ) or self.create(company[:200])
                self.attach_report(workspace["id"], payload)
            except (ValueError, KeyError, TypeError, OSError):
                continue
