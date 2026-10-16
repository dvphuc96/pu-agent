"""Report distribution bound to immutable report content and human approval."""

from __future__ import annotations

import base64
import json
import re
from email.message import EmailMessage
from urllib.parse import urlparse

from pu.core.interfaces.tools import ToolDefinition, ToolResult
from pu.reporting.identity import RESEARCH_POLICY_VERSION
from pu.reporting.models import REPORT_QUALITY_POLICY_VERSION
from pu.reporting.policy import CURRENT_RUBRIC_VERSION
from pu.reporting.workspace import WorkspaceRepository, now
from pu.tools.registry import Tool

SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def report_can_release(payload):
    evaluation = payload.get("evaluation") or {}
    if not evaluation.get("release_allowed"):
        return False
    metadata = payload.get("metadata") or {}
    is_v2 = metadata.get("quality_policy_version") == REPORT_QUALITY_POLICY_VERSION or payload.get("schema_version", 1) == 2
    if is_v2 and (
        evaluation.get("quality_policy_version") != REPORT_QUALITY_POLICY_VERSION
        or evaluation.get("rubric_version") != CURRENT_RUBRIC_VERSION
        or (evaluation.get("status_details") or {}).get("semantic_review") != "passed"
        or evaluation.get("evaluated") is not True
    ):
        return False
    if (
        metadata.get("require_semantic_review")
        and (
            metadata.get("evidence_policy_version") != RESEARCH_POLICY_VERSION
            or metadata.get("quality_policy_version") != "report-quality-v2"
            or payload.get("evaluation", {}).get("quality_policy_version") != "report-quality-v2"
        )
    ):
        return False
    return (metadata.get("partner_identity") or {}).get("status") != "conflicting"


def public_research_sources(payload):
    sources = {}
    for source in payload.get("sources", []):
        url = source.get("url") or ""
        if (
            source.get("derived")
            or source.get("source_type") not in {"web", "news"}
            or urlparse(url).scheme not in {"http", "https"}
        ):
            continue
        key = url.rstrip("/")
        if key not in sources or len(source.get("excerpt", "")) > len(
            sources[key].get("excerpt", "")
        ):
            sources[key] = source
    return list(sources.values())


def send_credentials(home):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = home / "gmail-send-token.json"
    if not path.exists():
        raise ValueError("Chưa cấp quyền gửi Gmail. Chọn Kết nối Gmail gửi email trước.")
    credentials = Credentials.from_authorized_user_file(str(path))
    if not credentials.has_scopes([SEND_SCOPE]):
        raise ValueError("Gmail token thiếu quyền gửi; cần kết nối lại.")
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        path.write_text(credentials.to_json(), encoding="utf-8")
        path.chmod(0o600)
    if not credentials.valid:
        raise ValueError("Gmail token không hợp lệ; cần kết nối lại.")
    return credentials


def connect_send(home) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    from pu.tools.gmail import BUNDLED_CLIENT_CONFIG

    own = home / "credentials.json"
    if own.exists():
        flow = InstalledAppFlow.from_client_secrets_file(str(own), scopes=[SEND_SCOPE])
    else:
        if not BUNDLED_CLIENT_CONFIG["installed"].get("client_id"):
            raise ValueError("Cần Desktop OAuth client tại PU_HOME/credentials.json.")
        flow = InstalledAppFlow.from_client_config(BUNDLED_CLIENT_CONFIG, scopes=[SEND_SCOPE])
    credentials = flow.run_local_server(port=0)
    path = home / "gmail-send-token.json"
    path.write_text(credentials.to_json(), encoding="utf-8")
    path.chmod(0o600)
    return "Đã kết nối Gmail gửi email. Mọi lần gửi vẫn cần phê duyệt."


def make_tools(home) -> list[Tool]:
    base = {
        "workspace_id": {"type": "string"},
        "report_id": {"type": "string"},
        "content_hash": {"type": "string", "minLength": 64, "maxLength": 64},
    }

    def checked(workspace_id, report_id, content_hash):
        repo = WorkspaceRepository(home)
        stored = repo.require_report(workspace_id, report_id, content_hash)
        if not report_can_release(stored["payload"]):
            raise ValueError("Báo cáo chưa đạt quality gate; hãy bổ sung nguồn và nghiên cứu lại.")
        return repo, stored["payload"]

    def save_report_to_knowledge(
        workspace_id: str, report_id: str, content_hash: str, _context=None
    ):
        repo, payload = checked(workspace_id, report_id, content_hash)
        from pu.memory.knowledge import _fingerprint, chunk_text

        digest = _context.metadata["approval_action_hash"]
        text = payload.get("markdown") or ""
        if not text:
            return ToolResult("Báo cáo không có nội dung để lưu.", ok=False, error="empty_report")
        with repo.database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT result_json FROM workspace_deliveries WHERE action_hash=?", (digest,)
            ).fetchone()
            if row:
                result = json.loads(row[0])
                return ToolResult(f"Đã lưu trước đó: nguồn knowledge #{result['source_id']}.")
            workspace = repo.require(workspace_id)
            cur = conn.execute(
                """INSERT INTO knowledge_sources(title,"group",origin,path,fingerprint,enabled,chars)
                VALUES(?,?,?,?,?,1,?)""",
                (
                    payload["title"],
                    workspace["company"],
                    "research_report",
                    report_id,
                    _fingerprint(text),
                    len(text),
                ),
            )
            source_id = cur.lastrowid
            for chunk in chunk_text(text):
                conn.execute(
                    "INSERT INTO knowledge_chunks(source_id,content) VALUES(?,?)",
                    (source_id, chunk),
                )
            conn.execute(
                "INSERT INTO workspace_deliveries VALUES(?,?,?,?,?,?)",
                (
                    digest,
                    workspace_id,
                    report_id,
                    "knowledge",
                    json.dumps({"status": "saved", "source_id": source_id}),
                    now(),
                ),
            )
        return ToolResult(
            f"Đã lưu báo cáo vào knowledge, nguồn #{source_id}.", metadata={"source_id": source_id}
        )

    def forward_report_email(
        workspace_id: str,
        report_id: str,
        content_hash: str,
        recipient: str,
        subject: str,
        intro: str = "",
        _context=None,
    ):
        if (
            not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", recipient)
            or "\r" in subject
            or "\n" in subject
        ):
            raise ValueError("Địa chỉ người nhận hoặc tiêu đề không hợp lệ.")
        repo, payload = checked(workspace_id, report_id, content_hash)
        from pu.harness.security import SecretEgressPolicy

        # Approval is bound to immutable content. Never silently replace times
        # after approval; an expired snapshot needs a new report and review.
        from pu.reporting.availability import snapshot_is_current

        availability = payload.get("metadata", {}).get("availability")
        if (
            payload.get("metadata", {}).get("quality_policy_version") == "report-quality-v2"
            and availability and availability.get("slots")
            and not snapshot_is_current(availability)
        ):
            return ToolResult(
                "Lịch trong báo cáo đã cũ hoặc có giờ đã qua. Hãy cập nhật báo cáo và duyệt lại trước khi gửi.",
                ok=False, error="stale_availability",
            )

        decision = SecretEgressPolicy().evaluate(
            ToolDefinition(
                name="report_content",
                description="",
                input_schema={},
                capability="email:send",
                required_scope="email:send",
                side_effect=True,
                external=True,
            ),
            {"report": payload.get("markdown", ""), "intro": intro, "subject": subject},
            _context,
        )
        if not decision.allowed:
            return ToolResult(
                "Báo cáo hoặc lời mở đầu chứa dữ liệu giống secret; chưa gửi email.",
                ok=False,
                error="secret_egress",
            )
        credentials = send_credentials(home)
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        digest = _context.metadata["approval_action_hash"]
        with repo.database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT result_json FROM workspace_deliveries WHERE action_hash=?", (digest,)
            ).fetchone()
            if previous:
                result = json.loads(previous[0])
                return ToolResult(
                    f"Trạng thái lần gửi trước: {result['status']}. Không gửi lặp.",
                    ok=result["status"] == "sent",
                    error=None if result["status"] == "sent" else "delivery_uncertain",
                )
            conn.execute(
                "INSERT INTO workspace_deliveries VALUES(?,?,?,?,?,?)",
                (
                    digest,
                    workspace_id,
                    report_id,
                    recipient,
                    json.dumps({"status": "sending"}),
                    now(),
                ),
            )
        message = EmailMessage()
        message["To"], message["Subject"] = recipient, subject
        message["Message-ID"] = f"<pu-report-{digest}@pu-agent.local>"
        message.set_content((intro.strip() + "\n\n" if intro.strip() else "") + payload["markdown"])
        try:
            result = (
                service.users()
                .messages()
                .send(
                    userId="me",
                    body={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")},
                )
                .execute(num_retries=0)
            )
            if not result.get("id"):
                raise ValueError("Gmail không trả message ID.")
            receipt = {"status": "sent", "message_id": result["id"]}
        except Exception:
            receipt = {
                "status": "uncertain",
                "error": "Chưa xác định kết quả gửi. Kiểm tra Gmail Sent trước khi thử lại.",
            }
        with repo.database.connection() as conn:
            conn.execute(
                "UPDATE workspace_deliveries SET result_json=? WHERE action_hash=?",
                (json.dumps(receipt), digest),
            )
        return ToolResult(
            f"Đã gửi báo cáo tới {recipient}. Gmail ID: {receipt.get('message_id')}"
            if receipt["status"] == "sent"
            else receipt["error"],
            ok=receipt["status"] == "sent",
            error=None if receipt["status"] == "sent" else "delivery_uncertain",
            metadata=receipt,
        )

    return [
        Tool(
            name="save_report_to_knowledge",
            description="Save the exact approved report to knowledge.",
            input_schema={"type": "object", "properties": base, "required": list(base)},
            fn=save_report_to_knowledge,
            capability="knowledge:write",
            side_effect=True,
            wants_context=True,
        ),
        Tool(
            name="forward_report_email",
            description="Send the exact approved report using Gmail.",
            input_schema={
                "type": "object",
                "properties": {
                    **base,
                    "recipient": {"type": "string", "maxLength": 300},
                    "subject": {"type": "string", "minLength": 1, "maxLength": 200},
                    "intro": {"type": "string", "maxLength": 3000},
                },
                "required": [*base, "recipient", "subject"],
            },
            fn=forward_report_email,
            capability="email:send",
            side_effect=True,
            external=True,
            wants_context=True,
        ),
    ]
