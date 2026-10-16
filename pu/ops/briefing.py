"""Durable inbox-to-brief worker. Drafts automatically; side effects require approval.

The dashboard owns the worker lifecycle. A renewable SQLite lease prevents two
dashboard processes researching the same inbox concurrently. Failed jobs remain
visible and are retried explicitly, never in an unbounded paid-model loop.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from pu.harness.audit import redact
from pu.orchestration.partner_validation import (
    POLICY_VERSION,
    REQUEST_RULES,
    precheck,
    request_passages,
    validate_request,
)
from pu.reporting.identity import candidate_website, domain_of
from pu.reporting.workspace import WorkspaceRepository, now

PERSONAL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
    "live.com",
}
STAGES = {
    "queued": "Đang chờ",
    "inbox": "Đọc email",
    "website": "Đọc website và sản phẩm",
    "triage": "Nhận diện nhu cầu hợp tác",
    "skipped": "Đã bỏ qua email không cần nghiên cứu",
    "retrying": "Đang đợi tự thử lại",
    "news": "Tìm và đối chiếu tin tức",
    "knowledge": "Tra cứu tri thức nội bộ",
    "calendar": "Kiểm tra cuộc họp và lịch trống",
    "email": "Phân tích nhu cầu",
    "company": "Đối chiếu công ty",
    "web": "Xác minh nguồn công khai",
    "memory": "Tra lịch sử",
    "report": "Viết báo cáo",
    "approval": "Chuẩn bị phê duyệt",
    "completed": "Sẵn sàng đọc",
    "failed": "Cần kiểm tra",
}


def sender_domain(address):
    email = parseaddr(address)[1].lower()
    if not re.fullmatch(r"[^\s@]+@(?:[a-z0-9-]+\.)+[a-z]{2,63}", email):
        return "", "Địa chỉ gửi không hợp lệ"
    local, domain = email.rsplit("@", 1)
    if domain in PERSONAL_DOMAINS:
        return "", "Email cá nhân: chưa đủ dữ kiện xác định doanh nghiệp"
    if any(
        word in local
        for word in ("noreply", "no-reply", "notification", "newsletter", "mailer-daemon")
    ):
        return "", "Email thông báo tự động"
    return domain, ""


class BriefingService:
    def __init__(self, home, app_factory=None):
        self.repo = WorkspaceRepository(home)
        self.home = home
        self.app_factory = app_factory
        self.owner = uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.application = None
        self.application_signature = None

    def state(self, key, default=""):
        with self.repo.database.connection() as conn:
            row = conn.execute("SELECT value FROM briefing_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key, value):
        with self.repo.database.connection() as conn:
            conn.execute(
                "INSERT INTO briefing_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def snapshot(self):
        with self.repo.database.connection() as conn:
            jobs = [
                dict(r)
                for r in conn.execute(
                    "SELECT j.*,w.company,w.website FROM briefing_jobs j JOIN research_workspaces w ON w.id=j.workspace_id ORDER BY j.created_at DESC LIMIT 100"
                )
            ]
            skipped = conn.execute(
                "SELECT COUNT(*) FROM briefing_seen WHERE reason!=''"
            ).fetchone()[0]
        return {
            "enabled": self.state("enabled", "1") == "1",
            "interval_minutes": 15,
            "model_configured": self.model_configured(),
            "last_scan": self.state("last_scan"),
            "scan_message": self.state("scan_message"),
            "recipient": self.state("recipient"),
            "ignored_domains": self.state("ignored_domains"),
            "meeting_scan_message": self.state("meeting_scan_message"),
            "worker_active": self.state("lease_until", "0").isdigit()
            and int(self.state("lease_until", "0")) > time.time(),
            "jobs": [
                {**job, "stage_label": STAGES.get(job["stage"], job["stage"])} for job in jobs
            ],
            "skipped_emails": skipped,
            "gmail_connected": (self.home / "gmail-token.json").is_file(),
            "send_connected": (self.home / "gmail-send-token.json").is_file(),
        }

    def model_configured(self):
        import os

        from pu.config import load_settings
        from pu.providers.llm.catalog import resolve_provider_settings

        try:
            settings = load_settings()
            spec = resolve_provider_settings(settings)
            return bool((settings.api_key or os.getenv(spec.key_env, "")).strip())
        except ValueError:
            return False

    def discover(self):
        """Atomically mark messages and enqueue one company per unseen email batch."""
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        with self.repo.database.connection() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT e.* FROM crm_emails e LEFT JOIN briefing_seen s ON s.gmail_id=e.gmail_id WHERE s.gmail_id IS NULL AND e.internal_date>=? ORDER BY e.internal_date LIMIT 100",
                    (cutoff,),
                )
            ]
        groups, skipped = {}, []
        ignored = set(self.state("ignored_domains").lower().replace(",", " ").split())
        for row in rows:
            domain, reason = sender_domain(row["sender_email"])
            status, validation_reason = precheck(row)
            target_domain = domain_of(candidate_website(row))
            if status == "candidate" and target_domain and target_domain not in PERSONAL_DOMAINS:
                domain, reason = target_domain, ""
            if status != "candidate":
                reason, domain = validation_reason, ""
            if domain in ignored:
                reason, domain = "Domain đã loại trừ", ""
            if reason:
                skipped.append((row["gmail_id"], "", reason, now()))
            else:
                groups.setdefault(domain, []).append(row)
        # SQLite serializes discoveries across API calls and processes.
        with self.repo.database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("INSERT OR IGNORE INTO briefing_seen VALUES(?,?,?,?)", skipped)
            for domain, emails in groups.items():
                ids = [e["gmail_id"] for e in emails]
                # Another discover may have committed after our initial read.
                unseen = [
                    i
                    for i in ids
                    if not conn.execute(
                        "SELECT 1 FROM briefing_seen WHERE gmail_id=?", (i,)
                    ).fetchone()
                ]
                if not unseen:
                    continue
                workspace = conn.execute(
                    "SELECT id FROM research_workspaces WHERE lower(website) IN (?,?) LIMIT 1",
                    (f"https://{domain}", f"https://{domain}/"),
                ).fetchone()
                wid = workspace[0] if workspace else "ws-" + uuid.uuid4().hex
                if not workspace:
                    conn.execute(
                        "INSERT INTO research_workspaces(id,company,website,contact,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        (
                            wid,
                            domain,
                            f"https://{domain}",
                            parseaddr(emails[0]["sender_email"])[1],
                            now(),
                            now(),
                        ),
                    )
                fingerprint = hashlib.sha256(json.dumps(sorted(unseen)).encode()).hexdigest()
                conn.execute(
                    "INSERT OR IGNORE INTO briefing_jobs(id,fingerprint,workspace_id,email_ids_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (
                        "brief-" + uuid.uuid4().hex,
                        fingerprint,
                        wid,
                        json.dumps(unseen),
                        now(),
                        now(),
                    ),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO briefing_seen VALUES(?,?,?,?)",
                    [(i, domain, "", now()) for i in unseen],
                )

    def enqueue(self, workspace_id):
        self.repo.require(workspace_id)
        with self.repo.database.connection() as conn:
            active = conn.execute(
                "SELECT id FROM briefing_jobs WHERE workspace_id=? AND status IN ('queued','running','retrying')",
                (workspace_id,),
            ).fetchone()
            if active:
                return active[0]
            jid = "brief-" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO briefing_jobs(id,fingerprint,workspace_id,email_ids_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (jid, uuid.uuid4().hex, workspace_id, "[]", now(), now()),
            )
        self.wake.set()
        return jid

    def schedule_meeting_briefs(self):
        """Refresh known partners once in the morning when a meeting is near."""
        current = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        day = current.date().isoformat()
        if current.hour < 7 or self.state("meeting_scan_day") == day:
            return
        from pu.reporting.availability import filter_partner_events, read_availability

        availability = read_availability(self.home, days=2)
        count = 0
        for workspace in self.repo.list():
            if not filter_partner_events(availability, workspace)["events"]:
                continue
            with self.repo.database.connection() as conn:
                if conn.execute(
                    "SELECT 1 FROM briefing_jobs WHERE workspace_id=? AND status IN ('queued','running','retrying')",
                    (workspace["id"],),
                ).fetchone():
                    continue
                fingerprint = hashlib.sha256(
                    f"meeting:{workspace['id']}:{day}".encode()
                ).hexdigest()
                cur = conn.execute(
                    "INSERT OR IGNORE INTO briefing_jobs(id,fingerprint,workspace_id,email_ids_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    ("brief-" + uuid.uuid4().hex, fingerprint, workspace["id"], "[]", now(), now()),
                )
                count += cur.rowcount
        if availability["status"] != "unknown":
            self.set_state("meeting_scan_day", day)
        self.set_state(
            "meeting_scan_message",
            f"Đã lên lịch {count} lượt cập nhật cho đối tác có cuộc hẹn trong 2 ngày tới. "
            + " ".join(availability.get("warnings", [])),
        )

    def update(self, jid, **values):
        assert set(values) <= {
            "status",
            "stage",
            "workflow_id",
            "report_id",
            "error",
            "attempts",
            "next_attempt",
            "workspace_id",
        }
        with self.repo.database.connection() as conn:
            conn.execute(
                "UPDATE briefing_jobs SET "
                + ",".join(f"{key}=?" for key in values)
                + ",updated_at=? WHERE id=?",
                (*values.values(), now(), jid),
            )

    def lease(self):
        with self.repo.database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute(
                "SELECT value FROM briefing_state WHERE key='lease_owner'"
            ).fetchone()
            expires = conn.execute(
                "SELECT value FROM briefing_state WHERE key='lease_until'"
            ).fetchone()
            if owner and owner[0] != self.owner and expires and float(expires[0]) > time.time():
                return False
            for key, value in (
                ("lease_owner", self.owner),
                ("lease_until", str(int(time.time() + 120))),
            ):
                conn.execute(
                    "INSERT INTO briefing_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            return True

    def run_job(self, job, app):
        jid, wid = job["id"], job["workspace_id"]
        attempts = job.get("attempts", 0) + 1
        self.update(jid, status="running", stage="inbox", error="", attempts=attempts)
        try:
            # Reuse an artifact if the process stopped after save but before job completion.
            with self.repo.database.connection() as conn:
                saved = (
                    conn.execute(
                        "SELECT payload_json FROM workspace_reports WHERE workspace_id=? AND json_extract(payload_json,'$.workflow_id')=? LIMIT 1",
                        (wid, job["workflow_id"]),
                    ).fetchone()
                    if job["workflow_id"]
                    else None
                )
                emails = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM crm_emails WHERE gmail_id IN (SELECT value FROM json_each(?))",
                        (job["email_ids_json"],),
                    )
                ]
            if saved:
                from pu.reporting.policy import CURRENT_RUBRIC_VERSION

                cached_payload = json.loads(saved["payload_json"])
                cached_metadata = cached_payload.get("metadata", {})
                if (cached_metadata.get("quality_policy_version") != "report-quality-v2"
                    or cached_payload.get("evaluation", {}).get("rubric_version") != CURRENT_RUBRIC_VERSION):
                    saved = None
                    job = {**job, "workflow_id": ""}
                    self.update(jid, workflow_id="", report_id="")
            # Also validate persisted/retrying jobs created under an older policy.
            # A cached workflow is not evidence that its input was a partner request.
            if emails:
                self.update(jid, stage="triage")
                selected = self.validated_job_emails(jid, emails, app)
                if not selected:
                    with self.repo.database.connection() as conn:
                        conn.execute(
                            "UPDATE briefing_seen SET reason='Không có nhu cầu hợp tác/chuẩn bị cuộc gặp' WHERE gmail_id IN (SELECT value FROM json_each(?))",
                            (job["email_ids_json"],),
                        )
                    self.update(jid, status="skipped", stage="skipped")
                    return
                emails = selected
                targets = {domain_of(candidate_website(email)) for email in emails}
                if len(targets) > 1:
                    # Old jobs may have bundled several clients under one sender.
                    # Rediscover by each explicit company website before research.
                    with self.repo.database.connection() as conn:
                        conn.execute(
                            "DELETE FROM briefing_seen WHERE gmail_id IN (SELECT value FROM json_each(?))",
                            (json.dumps([e["gmail_id"] for e in emails]),),
                        )
                    self.update(
                        jid,
                        status="skipped",
                        stage="skipped",
                        error="Đã tách email theo công ty được đề cập.",
                    )
                    self.discover()
                    return
            from pu.reporting.identity import partner_identity

            current_workspace = self.repo.require(wid)
            if emails:
                website = candidate_website(emails[0])
                if domain_of(website) != domain_of(current_workspace["website"]):
                    identity_hint = partner_identity(emails[0], "", website)
                    destination = next(
                        (
                            w
                            for w in self.repo.list()
                            if domain_of(w["website"]) == domain_of(website)
                        ),
                        None,
                    )
                    destination = destination or self.repo.create(
                        identity_hint["name"], website, emails[0]["sender_email"]
                    )
                    wid = destination["id"]
                    self.update(jid, workspace_id=wid, workflow_id="", report_id="")
                    job = {**job, "workspace_id": wid, "workflow_id": ""}
                    saved = None
                    current_workspace = destination
            identity = (
                partner_identity(
                    emails[0], current_workspace["company"], current_workspace["website"]
                )
                if emails
                else {}
            )
            if identity and identity["name"] != current_workspace["company"]:
                with self.repo.database.connection() as conn:
                    conn.execute(
                        "UPDATE research_workspaces SET company=? WHERE id=?",
                        (identity["name"], wid),
                    )
            for email in sorted(emails, key=lambda item: item["internal_date"]):
                self.repo.add_source(
                    wid,
                    title=email["subject"][:300],
                    source_type="email",
                    content=f"Người gửi: {email['sender_name']} <{email['sender_email']}>\nNgày: {email['internal_date']}\n{email['body_text'] or email['snippet']}",
                    published_at=email["internal_date"],
                    metadata={
                        "gmail_id": email["gmail_id"],
                        "untrusted_input": True,
                        "partner_identity": identity,
                    },
                )

            def observer(kind, event):
                if kind == "workflow_started":
                    self.update(jid, workflow_id=event["workflow_id"])
                if kind == "workflow_stage_started":
                    self.update(jid, stage=event["stage"])

            if not saved and not job["workflow_id"]:
                from pu.reporting.collection import collect_partner_sources

                warnings = collect_partner_sources(
                    self.repo, wid, progress=lambda stage: self.update(jid, stage=stage), app=app
                )
                workspace = self.repo.require(wid)
                prompt = (
                    "Chuẩn bị bản tóm tắt 2 phút trước cuộc gặp, phân biệt dữ kiện có nguồn, suy luận và điều cần hỏi. "
                    "Website công ty được nêu trong email chỉ là ứng viên; tách quyền đại diện của người gửi khỏi hồ sơ doanh nghiệp. "
                    "Không gọi tin không có ngày là tin mới. Tóm tắt nhu cầu, sản phẩm, tin 30 ngày gần đây nếu có, "
                    "câu hỏi cho cuộc gặp, rủi ro và bước tiếp theo. Chỉ dùng lịch của bạn để gợi ý; chưa biết lịch đối tác. "
                    + " ".join(warnings)
                )
                sources, size = [], 0
                for source in reversed(self.repo.sources(wid)):
                    if (
                        source["enabled"]
                        and len(sources) < 30
                        and size + len(source["content"]) <= 110000
                    ):
                        sources.append(source)
                        size += len(source["content"])
                prompt += " Nguồn tự động ưu tiên dữ liệu mới nhất trong giới hạn ngữ cảnh; không coi đây là toàn bộ lịch sử."
                result = app.run_research_workflow(
                    prompt,
                    company=workspace["company"],
                    workspace_id=wid,
                    evidence_sources=list(reversed(sources)),
                    observer=observer,
                    require_semantic_review=True,
                    partner_identity=identity,
                )
            elif not saved:
                result = app.resume_research_workflow(job["workflow_id"], observer=observer)
            payload = (
                json.loads(saved[0])
                if saved
                else {
                    **result.structured_report.to_dict(),
                    "evaluation": result.evaluation.to_dict(),
                }
            )
            self.update(jid, report_id=payload["report_id"], stage="approval")
            if payload.get("evaluation", {}).get("release_allowed"):
                with self.repo.database.connection() as conn:
                    obsolete = conn.execute(
                        "SELECT id FROM proposed_actions WHERE status IN ('pending','approved') AND json_extract(arguments_json,'$.workspace_id')=? AND json_extract(arguments_json,'$.report_id')!=?",
                        (wid, payload["report_id"]),
                    ).fetchall()
                for row in obsolete:
                    app.approval_service.reject(
                        row[0],
                        note="Đã có báo cáo mới; vui lòng đọc và duyệt phiên bản mới.",
                        decided_by="automatic-briefing",
                    )
                self.prepare_approvals(wid, payload, app)
            self.update(jid, status="completed", stage="completed")
        except Exception as exc:
            retry = attempts < 3
            self.update(
                jid,
                status="retrying" if retry else "failed",
                stage="retrying" if retry else "failed",
                error=redact(str(exc))[:600],
                next_attempt=(datetime.now(UTC) + timedelta(minutes=2**attempts)).isoformat(),
            )

    def triage(self, emails, app):
        return bool(self.select_business_emails(emails, app))

    def validated_job_emails(self, jid, emails, app):
        # Resume without another paid call only when both input and policy match.
        fingerprint = hashlib.sha256(
            json.dumps(
                [POLICY_VERSION, sorted(emails, key=lambda email: email["gmail_id"])],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        key = "input_validation:" + jid
        try:
            cached = json.loads(self.state(key, "{}"))
        except (ValueError, TypeError):
            cached = {}
        if isinstance(cached, dict) and cached.get("fingerprint") == fingerprint:
            return [
                email for email in emails if email["gmail_id"] in cached.get("selected_ids", [])
            ]
        selected = self.select_business_emails(emails, app)
        self.set_state(
            key,
            json.dumps(
                {
                    "fingerprint": fingerprint,
                    "selected_ids": [email["gmail_id"] for email in selected],
                    "policy_version": POLICY_VERSION,
                }
            ),
        )
        return selected

    def select_business_emails(self, emails, app):
        emails = [email for email in emails if precheck(email)[0] == "candidate"]
        selected = []
        for offset in range(0, len(emails), 10):
            selected.extend(self._triage_batch(emails[offset : offset + 10], app))
        return selected

    def _triage_batch(self, emails, app):
        from pu.core.interfaces.llm import LLMRequest
        from pu.reporting.evidence import StructuredReportBuilder

        response = app.llm_provider.complete(
            LLMRequest(
                model=app.settings.small_model or app.settings.model,
                max_tokens=2000,
                system=REQUEST_RULES
                + 'Trả duy nhất JSON {"needs_research":true,"requests":[{"intent":"partnership|project|quotation|demo|business_meeting","direct_request":true,"company":"tên trong email","request_evidence":{"email_id":"id","passage_id":"p0"}}],"reason":"lý do ngắn"}. '
                "requests phải liệt kê từng email đủ bằng chứng, để trống nếu không rõ. "
                "Luồng này có thể chuẩn bị cuộc gặp với đối tác cũ; không khẳng định đây là đối tác mới.",
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            [
                                {
                                    "id": email["gmail_id"],
                                    "sender": email["sender_name"],
                                    "subject": email["subject"],
                                    "body": (email["body_text"] or email["snippet"])[:3000],
                                    "request_passages": request_passages(email),
                                }
                                for email in emails[:10]
                            ],
                            ensure_ascii=False,
                        ),
                    }
                ],
            )
        )
        app.event_publisher.publish(
            "llm",
            {
                "agent_id": "email-triage-agent",
                "model": app.settings.small_model or app.settings.model,
                "usage": {"in": response.usage.input_tokens, "out": response.usage.output_tokens},
            },
        )
        data = StructuredReportBuilder._parse_json(response.text)
        if not data or not isinstance(data.get("needs_research"), bool):
            raise ValueError("Chưa phân loại được nhu cầu email; chưa tự suy đoán hoặc bỏ qua.")
        if not data["needs_research"]:
            return []
        requests = data.get("requests")
        if not isinstance(requests, list):
            return []  # No evidence means no automatic research.
        selected = {}
        for decision in requests:
            if isinstance(decision, dict):
                verified, _reason = validate_request(decision, emails)
                if verified:
                    selected[verified["email"]["gmail_id"]] = verified["email"]
        return list(selected.values())

    def prepare_approvals(self, wid, payload, app):
        from pu.ops.workspace_api import workspace_action

        stored = self.repo.require_report(wid, payload["report_id"])
        tools = ["save_report_to_knowledge"]
        if self.state("recipient") and (self.home / "gmail-send-token.json").is_file():
            tools.append("forward_report_email")
        for tool in tools:
            with self.repo.database.connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM proposed_actions WHERE tool_name=? AND json_extract(arguments_json,'$.report_id')=? AND (status IN ('executing','executed','failed','rejected') OR (status IN ('pending','approved') AND expires_at>?))",
                    (tool, payload["report_id"], now()),
                ).fetchone()
            if not existing:
                workspace_action(
                    self.home,
                    {
                        "op": "propose",
                        "workspace_id": wid,
                        "report_id": payload["report_id"],
                        "content_hash": stored["content_hash"],
                        "tool": tool,
                        "recipient": self.state("recipient"),
                        "subject": payload["title"][:200].replace("\n", " ").replace("\r", " "),
                        "intro": "Báo cáo chuẩn bị cuộc gặp, kèm nguồn để đối chiếu.",
                    },
                    app_factory=lambda: app,
                )

    def cycle(self):
        if self.state("enabled", "1") != "1" or not self.lease():
            return
        if not self.app_factory and not self.model_configured():
            self.set_state(
                "scan_message",
                "Kết nối model một lần trong Kết nối tài khoản để PU bắt đầu nghiên cứu tự động.",
            )
            return
        if (
            time.time() - float(self.state("last_scan_epoch", "0")) >= 900
            or self.state("scan_requested") == "1"
        ):
            self.set_state("scan_requested", "0")
            message = "Đã đọc email local. Kết nối Gmail một lần để tự đồng bộ inbox."
            if (self.home / "gmail-token.json").is_file():
                from pu.tools.gmail import sync

                with self.repo.database.connection() as conn:
                    message = sync(conn, self.home, max_messages=500, recent_days=7, only_new=True)
            self.discover()
            self.schedule_meeting_briefs()
            self.set_state("scan_message", redact(message))
            self.set_state("last_scan", now())
            self.set_state("last_scan_epoch", time.time())
        with self.repo.database.connection() as conn:
            # Only a lease owner can reclaim an interrupted job; no active owner exists then.
            job = conn.execute(
                "SELECT * FROM briefing_jobs WHERE status IN ('queued','running') OR (status='retrying' AND next_attempt<=?) ORDER BY created_at LIMIT 1",
                (now(),),
            ).fetchone()
        if job:
            # A separate connection/runtime keeps long research from blocking
            # the user's approval or chat behind the browser's agent lock.
            app = self.app_factory() if self.app_factory else self.worker_application()
            self.run_job(dict(job), app)

    def worker_application(self):
        from pu.config import load_settings
        from pu.core.container import create_application
        from pu.providers.llm.catalog import resolve_provider_settings

        settings = load_settings()
        settings.home = self.home
        resolve_provider_settings(settings)
        signature = (
            settings.provider,
            settings.model,
            settings.small_model,
            settings.base_url,
            hashlib.sha256(settings.api_key.encode()).hexdigest(),
        )
        if self.application is None or signature != self.application_signature:
            if self.application is not None:
                self.application.conn.close()
            self.application = create_application(settings=settings, check_same_thread=False)
            self.application_signature = signature
        return self.application

    def start(self):
        if self.thread and self.thread.is_alive():
            return

        def heartbeat():
            while not self.stop_event.wait(30):
                with self.repo.database.connection() as conn:
                    conn.execute(
                        "UPDATE briefing_state SET value=? WHERE key='lease_until' AND EXISTS(SELECT 1 FROM briefing_state WHERE key='lease_owner' AND value=?)",
                        (str(int(time.time() + 120)), self.owner),
                    )

        def loop():
            while not self.stop_event.is_set():
                try:
                    self.cycle()
                except (Exception, SystemExit) as exc:
                    self.set_state(
                        "scan_message", "Tác vụ tự động gặp lỗi: " + redact(str(exc))[:400]
                    )
                self.wake.wait(15)
                self.wake.clear()

        threading.Thread(target=heartbeat, daemon=True, name="brief-heartbeat").start()
        self.thread = threading.Thread(target=loop, daemon=True, name="partner-briefing")
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake.set()
        # Keep lease until expiry if a network/model call is still finishing.


_services = {}
_services_lock = threading.Lock()


def briefing_service(home, *, start=False):
    key = str(home.resolve())
    with _services_lock:
        service = _services.setdefault(key, None)
        if service is None:
            service = _services[key] = BriefingService(home)
        if start:
            service.start()
        return service


def briefing_action(home, payload):
    service = briefing_service(home, start=True)
    op = payload.get("op")
    if op == "scan":
        service.set_state("scan_requested", "1")
    elif op == "settings":
        if "recipient" in payload:
            recipient = str(payload["recipient"]).strip()
            if recipient and not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", recipient):
                raise ValueError("Người nhận cần là một địa chỉ email hợp lệ.")
            service.set_state("recipient", recipient)
        if "ignored_domains" in payload:
            domains = str(payload["ignored_domains"]).lower().replace(",", " ").split()
            if len(domains) > 100 or any(
                not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,63}", d) for d in domains
            ):
                raise ValueError("Nhập domain cần bỏ qua, cách nhau bằng dấu phẩy.")
            service.set_state("ignored_domains", " ".join(domains))
        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise ValueError("enabled phải là boolean.")
            service.set_state("enabled", "1" if payload["enabled"] else "0")
    elif op == "enqueue":
        service.enqueue(payload.get("workspace_id", ""))
    elif op == "prepare_review":
        from pu.ops.browser_agent import agent_lock, get_agent

        wid = payload.get("workspace_id", "")
        report = service.repo.require_report(wid, payload.get("report_id", ""))["payload"]
        if report.get("evaluation", {}).get("release_allowed"):
            if not service.model_configured():
                raise ValueError(
                    "Kết nối model trong Kết nối tài khoản để chuẩn bị hành động phê duyệt."
                )
            with agent_lock:
                service.prepare_approvals(wid, report, get_agent())
    elif op == "retry":
        with service.repo.database.connection() as conn:
            conn.execute(
                "UPDATE briefing_jobs SET status='queued',stage='queued',error='',attempts=0,updated_at=? WHERE id=? AND status='failed'",
                (now(), payload.get("id", "")),
            )
    else:
        raise ValueError("Thao tác tự động không hợp lệ.")
    service.wake.set()
    return service.snapshot()


def main():
    """Run without opening a browser: `pu worker`. Keep this process running."""
    from pu.config import load_settings

    settings = load_settings()
    settings.ensure_home()
    service = briefing_service(settings.home, start=True)
    print("PU tự chuẩn bị báo cáo từ inbox mỗi 15 phút. Ctrl-C để dừng.")
    try:
        service.stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
