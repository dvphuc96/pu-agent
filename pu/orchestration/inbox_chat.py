"""Chat is the delivery surface for inbox → new partner → grounded report."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime
from email.utils import parseaddr
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.llm import LLMRequest
from pu.core.interfaces.tools import ToolContext
from pu.harness.audit import redact
from pu.ops.briefing import PERSONAL_DOMAINS
from pu.orchestration.partner_validation import (
    POLICY_VERSION,
    REQUEST_RULES,
    precheck,
    request_passages,
    subject_signal,
    validate_request,
)
from pu.reporting.evidence import StructuredReportBuilder
from pu.reporting.identity import RESEARCH_POLICY_VERSION, candidate_website, domain_of
from pu.reporting.workspace import WorkspaceRepository
from pu.tools.report_delivery import public_research_sources


def is_inbox_request(message):
    text = unicodedata.normalize("NFD", message.lower().replace("đ", "d"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    if re.search(
        r"\b(gui|send|forward|xoa|delete|code|prompt)\b|huong dan|vi du|khong (can|duoc) (doc|kiem tra)",
        text,
    ):
        return False
    return bool(
        re.search(r"\b(email|mail|inbox|hop thu)\b", text)
        and re.search(r"hom nay|moi|inbox|today|new|hop thu", text)
    )


def calendar_markdown(snapshot):
    lines = ["### Lịch trống trong tuần của anh", ""]
    if not snapshot or snapshot.get("status") == "unknown":
        return "\n".join(lines + ["Em chưa đọc đủ dữ liệu lịch để gợi ý giờ hẹn đáng tin cậy."])
    sources = snapshot.get("checked_sources", [])
    if not any("Google" in source for source in sources):
        lines += [
            "Gợi ý theo lịch đã lưu trong PU; **chưa kiểm tra Google Calendar** và lịch của đối tác.",
            "",
        ]
    else:
        lines += ["Đã đối chiếu lịch của anh; chưa biết lịch của đối tác.", ""]
    slots = snapshot.get("slots", [])
    for slot in slots[:8]:
        lines.append(f"- **{slot['label']}, {slot['window']}** · {slot['duration_minutes']} phút")
    if not slots:
        lines.append("Chưa tìm được khoảng trống đủ 45 phút trong giờ làm việc còn lại của tuần.")
    if len(slots) > 8:
        lines.append(f"Còn {len(slots) - 8} khoảng trống khác trong lịch đã kiểm tra.")
    try:
        checked = datetime.fromisoformat(snapshot["checked_at"])
        if checked.tzinfo is not None:
            checked = checked.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
            lines += ["", f"Cập nhật lúc {checked:%H:%M, %d/%m/%Y} · giờ Việt Nam."]
    except (KeyError, ValueError, TypeError):
        pass
    return "\n".join(lines)


def inline_report(payload):
    """A source-backed conversational introduction; the full report stays attached."""
    sources = {s["id"]: s for s in payload.get("sources", []) if not s.get("derived")}
    public_sources = public_research_sources(payload)[:8]
    numbers = {source["url"].rstrip("/"): index for index, source in enumerate(public_sources, 1)}

    def link(url):
        return quote(url, safe=":/?#[]@!$&'*+,;=%")

    def cite(text):
        def replace(match):
            sid = match.group(1)
            if sid not in sources:
                return ""
            url = sources[sid].get("url") or ""
            number = numbers.get(url.rstrip("/"))
            return f"[{number}]({link(url)})" if number else ""

        return re.sub(r"\[(S\d+|W\d+)\]", replace, text or "")

    def excerpt(text, limit=450):
        text = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", text.strip(), flags=re.MULTILINE)
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        end = max(text.rfind(". ", 0, limit), text.rfind("; ", 0, limit))
        end = end + 1 if end >= limit // 2 else text.rfind(" ", 0, limit)
        result = text[:end].rstrip() + ("" if text[:end].endswith(".") else "…")
        refs = list(dict.fromkeys(re.findall(r"\[(?:S|W)\d+\]", text)))
        return result + " " + "".join(ref for ref in refs if ref not in result)

    company = payload.get("metadata", {}).get("company") or payload["title"]
    lines = [
        f"Dưới đây là những thông tin em tìm được về **{company}**:",
        "",
        cite(payload.get("executive_summary")),
        "",
    ]
    sections = payload.get("sections", {})
    public = sections.get("public_research", "")
    bullets = re.findall(r"^\s*(?:[-*]|\d+\.)\s+(.+)$", public, re.MULTILINE)
    if len(bullets) >= 3:
        highlights = [excerpt(bullet) for bullet in bullets[:3]]
    else:
        highlights = [
            f"**{label}:** {excerpt(sections[key])}"
            for key, label in (
                ("public_research", "Lĩnh vực và giải pháp"),
                ("email_need", "Nội dung cần trao đổi"),
                ("meeting_context", "Chuẩn bị cuộc gặp"),
            )
            if sections.get(key)
        ]
    if highlights:
        lines += (
            ["**Các điểm nổi bật:**", ""]
            + [f"{i}. {cite(item)}" for i, item in enumerate(highlights, 1)]
            + [""]
        )
    gaps = payload.get("missing_information") or []
    if gaps:
        lines += ["**Cần xác minh thêm:** " + excerpt("; ".join(gaps[:2]), 260), ""]
    affiliation_warnings = (
        payload.get("metadata", {}).get("partner_identity", {}).get("affiliation_warnings", [])
    )
    if affiliation_warnings:
        lines += ["**Lưu ý về người liên hệ:** " + " ".join(affiliation_warnings), ""]
    if payload.get("contradictions"):
        lines += [
            "**Lưu ý:** các nguồn có thông tin mâu thuẫn; em đã ghi rõ trong báo cáo chi tiết.",
            "",
        ]
    if payload.get("evaluation", {}).get("release_allowed"):
        lines.append(
            "Báo cáo chi tiết được đính kèm bên dưới. Anh có thể mở đọc và chọn **Import vào sổ tay** để lưu báo cáo."
        )
    else:
        lines.append(
            "**Bản nháp cần xác minh thêm.** Anh có thể đọc báo cáo đính kèm và đối chiếu các nguồn bên dưới; hiện chưa thể duyệt lưu hoặc gửi."
        )
    if public_sources:
        lines += ["", "**Nguồn tham khảo:**", ""]
        for index, source in enumerate(public_sources, 1):
            title = re.sub(
                r"[\r\n\[\]<>]", " ", source.get("title") or urlparse(source["url"]).hostname
            )
            lines.append(f"{index}. [{title}]({link(source['url'])})")
    else:
        lines += [
            "",
            "Chưa đọc được nguồn công khai đủ dữ kiện; nội dung hiện dựa trên email và các tài liệu được cung cấp.",
        ]
    return "\n".join(lines)


class InboxChatBriefing:
    def __init__(self, app):
        self.app = app
        self.repo = WorkspaceRepository(app.settings.home)
        self.calls = []
        self.validation = {}

    def record_validation(self, email, status, reason, **evidence):
        self.validation[email["gmail_id"]] = {
            "email_id": email["gmail_id"],
            "subject": email["subject"],
            "status": status,
            "reason": reason,
            "policy_version": POLICY_VERSION,
            **evidence,
        }

    def tool(self, name, notify):
        result = self.app.tool_harness.execute(
            name,
            {},
            ToolContext(
                principal="local-user",
                actor="chat-inbox",
                agent_id="inbox-briefing-agent",
                request_id=str(uuid.uuid4()),
                scopes=self.app.tools.scopes(),
                capabilities=self.app.tools.agent_capabilities(),
                observer=notify,
            ),
        )
        self.calls.append({"tool": name, "args": {}, "output": result.output, "ok": result.ok})
        if not result.ok:
            raise ValueError(result.output)
        return json.loads(result.output)

    def candidates(self, scan):
        groups, excluded = {}, 0
        for email in scan["messages"]:
            status, reason = precheck(email)
            if status != "candidate":
                self.record_validation(email, status, reason)
                excluded += 1
                continue
            self.record_validation(email, "uncertain", "Chưa hoàn tất kiểm tra nội dung và lịch sử")
            address = parseaddr(email["sender_email"])[1].lower()
            _, _, sender_domain = address.rpartition("@")
            website = candidate_website(email)
            domain = domain_of(website) or sender_domain
            urls = re.findall(r"https?://[^\s<>\"()]+", email["body_text"] or "")
            urls = [url.rstrip(".,;") for url in urls if urlparse(url).hostname]
            if domain in PERSONAL_DOMAINS:
                urls = [
                    url
                    for url in urls
                    if urlparse(url).hostname not in PERSONAL_DOMAINS
                    and not any(
                        site in (urlparse(url).hostname or "")
                        for site in ("google.", "facebook.", "linkedin.", "youtube.")
                    )
                ]
                if not urls:
                    self.record_validation(
                        email, "uncertain", "Email cá nhân chưa có website nhận diện công ty"
                    )
                    excluded += 1
                    continue
                domain = (urlparse(urls[0]).hostname or domain).removeprefix("www.")
                website = "https://" + domain
            group = groups.setdefault(
                email["gmail_id"],
                {
                    "candidate_id": "email:" + email["gmail_id"],
                    "domain": domain,
                    "sender_domain": sender_domain,
                    "website": website,
                    "urls": set(),
                    "emails": [],
                },
            )
            group["urls"].update(urls)
            group["emails"].append(email)
        for group in groups.values():
            with self.repo.database.connection() as conn:
                rows = conn.execute(
                    "SELECT subject,body_text FROM crm_emails WHERE lower(sender_email) LIKE ? AND julianday(internal_date)<julianday(?) ORDER BY internal_date DESC LIMIT 3",
                    ("%@" + group["domain"], scan["start"]),
                ).fetchall()
                knowledge = conn.execute(
                    "SELECT c.content FROM knowledge_chunks c JOIN knowledge_sources s ON s.id=c.source_id WHERE s.enabled=1 AND c.content LIKE ? LIMIT 2",
                    ("%" + group["domain"] + "%",),
                ).fetchall()
            group["history"] = [
                {"subject": row["subject"], "body": row["body_text"][:1500]} for row in rows
            ]
            group["knowledge"] = [row[0][:1500] for row in knowledge]
        return list(groups.values()), excluded

    def classify(self, candidates, notify):
        decisions = []
        # Negative decisions must cover every message, including later pages.
        paged = []
        for group in candidates:
            for offset in range(0, len(group["emails"]), 10):
                paged.append(
                    {
                        **group,
                        "candidate_id": group["candidate_id"] + f"#{offset // 10}",
                        "emails": group["emails"][offset : offset + 10],
                        "original": group,
                    }
                )
        candidates = paged
        for offset in range(0, len(candidates), 5):
            batch = candidates[offset : offset + 5]
            content = [
                {
                    "candidate_id": item["candidate_id"],
                    "sender_domain": item["sender_domain"],
                    "emails": [
                        {
                            "id": e["gmail_id"],
                            "sender": e["sender_name"],
                            "subject": e["subject"],
                            "subject_signal": subject_signal(e["subject"])[0],
                            "body": (e["body_text"] or e["snippet"])[:3000],
                            "request_passages": request_passages(e),
                        }
                        for e in item["emails"]
                    ],
                    "older_correspondence": item["history"],
                    "internal_knowledge": item["knowledge"],
                    "website_candidates": sorted(item["urls"] | {item["website"]}),
                }
                for item in batch
            ]
            response = self.app.llm_provider.complete(
                LLMRequest(
                    model=self.app.settings.small_model or self.app.settings.model,
                    max_tokens=6000,
                    system="Nhận diện email kinh doanh cần chuẩn bị báo cáo cho người dùng hôm nay. "
                    + REQUEST_RULES
                    + "Mỗi ứng viên chỉ có một email. Không dùng bằng chứng của email khác, kể cả cùng người gửi hoặc cùng miền. Miền người gửi không quyết định công ty được nghiên cứu; dùng tên tự giới thiệu và website công ty ghi trong đúng email. "
                    "Phân loại từng email trong email_decisions bằng email_id, status (new_partner/existing_partner/not_partner/uncertain), reason. "
                    "Nếu email có đề nghị trực tiếp, tên tự giới thiệu và website công ty rõ ràng, miền gửi khác website chỉ là cảnh báo quyền đại diện chưa xác minh; không chọn needs_research=false chỉ vì khác miền. "
                    "needs_research=true cho MỌI yêu cầu kinh doanh trực tiếp đang cần xử lý, kể cả đã từng trao đổi hoặc là đối tác cũ. Lịch sử để bổ sung ngữ cảnh, không phải lý do bỏ qua email hẹn gặp. "
                    "Trao đổi trước về cùng cơ hội chưa chứng minh đã là đối tác cũ; chỉ kết luận có quan hệ cũ khi có bằng chứng dự án/hợp đồng/giao dịch trong lịch sử. Newsletter và báo cáo AI không chứng minh quan hệ đối tác. "
                    "Không thấy bằng chứng quan hệ cũ trong dữ liệu chỉ có nghĩa chưa tìm thấy, không khẳng định tuyệt đối chưa từng làm việc. "
                    "Workspace hoặc báo cáo nháp có sẵn không chứng minh đó là đối tác cũ. "
                    "Company phải lấy từ email, không bịa pháp nhân. Website phải thuộc website_candidates. "
                    'Trả duy nhất JSON {"partners":[{"candidate_id":"domain#0","is_new_partner":true,"needs_research":true,"company":"tên công ty có trong email","website":"URL đã cho","intent":"partnership|project|quotation|demo|business_meeting","direct_request":true,"request_evidence":{"email_id":"id","passage_id":"p0"},"reason":"nhu cầu và kết quả kiểm tra lịch sử","email_decisions":[{"email_id":"id","status":"new_partner","reason":"lý do"}]}]}, đúng một quyết định cho MỖI candidate_id và MỖI email. passage_id phải trỏ vào đoạn có đề nghị kinh doanh. Thiếu bằng chứng yêu cầu thì needs_research=false. Đối tác cũ có nhu cầu trực tiếp thì is_new_partner=false nhưng needs_research=true.',
                    messages=[{"role": "user", "content": json.dumps(content, ensure_ascii=False)}],
                )
            )
            notify(
                "llm",
                {
                    "agent_id": "email-partner-classifier",
                    "model": self.app.settings.small_model or self.app.settings.model,
                    "usage": {
                        "in": response.usage.input_tokens,
                        "out": response.usage.output_tokens,
                    },
                },
            )
            parsed = StructuredReportBuilder._parse_json(response.text)
            items = parsed.get("partners") if parsed else None
            if (
                not isinstance(items, list)
                or len(items) != len(batch)
                or {item.get("candidate_id") for item in items if isinstance(item, dict)}
                != {item["candidate_id"] for item in batch}
            ):
                raise ValueError(
                    "Model chưa trả đủ quyết định phân loại đối tác; chưa tự bỏ qua các email chưa phân loại."
                )
            for item in items:
                if not isinstance(item.get("is_new_partner"), bool) or not isinstance(
                    item.get("needs_research"), bool
                ):
                    raise TypeError("Kết quả phân loại đối tác không hợp lệ.")
                group = next(
                    group for group in batch if group["candidate_id"] == item["candidate_id"]
                )
                # Only verified evidence below may promote a message to eligible.
                email_decisions = item.get("email_decisions")
                if (
                    not isinstance(email_decisions, list)
                    or len(email_decisions) != len(group["emails"])
                    or {d.get("email_id") for d in email_decisions if isinstance(d, dict)}
                    != {e["gmail_id"] for e in group["emails"]}
                    or any(
                        not isinstance(d, dict)
                        or d.get("status")
                        not in {"new_partner", "existing_partner", "not_partner", "uncertain"}
                        for d in email_decisions
                    )
                ):
                    raise ValueError("Chưa có quyết định hợp lệ cho từng email; chưa tự research.")
                if isinstance(email_decisions, list):
                    for decision in email_decisions:
                        if not isinstance(decision, dict):
                            continue
                        email = next(
                            (
                                e
                                for e in group["emails"]
                                if e["gmail_id"] == decision.get("email_id")
                            ),
                            None,
                        )
                        status = {"existing_partner": "existing", "not_partner": "excluded"}.get(
                            decision.get("status"), "uncertain"
                        )
                        if email:
                            self.record_validation(
                                email,
                                status,
                                "Chưa đối chiếu được trích dẫn yêu cầu cho email này"
                                if decision.get("status") == "new_partner"
                                else str(decision.get("reason") or "Chưa đủ bằng chứng"),
                            )
                if item["needs_research"]:
                    verified, reason = validate_request(item, group["emails"])
                    if not verified:
                        attempted = item.get("request_evidence")
                        attempted = attempted if isinstance(attempted, dict) else {}
                        for email in group["emails"]:
                            if self.validation[email["gmail_id"]]["status"] == "uncertain" or email[
                                "gmail_id"
                            ] == attempted.get("email_id"):
                                self.record_validation(
                                    email,
                                    "uncertain",
                                    reason,
                                    attempted_evidence={
                                        "email_id": str(attempted.get("email_id") or "")[:200],
                                        "quote": str(attempted.get("quote") or "")[:3000],
                                        "passage_id": str(attempted.get("passage_id") or "")[:100],
                                        "company": str(item.get("company") or "")[:200],
                                        "intent": str(item.get("intent") or "")[:100],
                                    },
                                )
                        continue
                    verdict = next(
                        d for d in email_decisions if d["email_id"] == verified["email"]["gmail_id"]
                    )
                    if verdict["status"] not in {"new_partner", "existing_partner"}:
                        continue
                    self.record_validation(
                        verified["email"],
                        "eligible",
                        "Có yêu cầu kinh doanh trực tiếp cần chuẩn bị báo cáo",
                        relationship="new" if item["is_new_partner"] else "existing",
                        quote=verified["quote"],
                        intent=item["intent"],
                        company=verified["company"],
                        identity_conflicts=verified["identity"]["conflicts"],
                        affiliation_warnings=verified["identity"].get("affiliation_warnings", []),
                    )
                    company = verified["company"]
                    website = group["website"]
                    identity = {
                        **verified["identity"],
                        "website": website,
                        "sender_domain": group["sender_domain"],
                        "research_domain": group["domain"],
                    }
                    # Consolidate only after validating each individual message.
                    existing = next(
                        (
                            d
                            for d in decisions
                            if d["domain"] == group["domain"]
                            and d["company"].casefold() == company.casefold()
                        ),
                        None,
                    )
                    if existing:
                        existing["emails"].append(verified["email"])
                        existing["identity"]["conflicts"] = list(
                            dict.fromkeys(existing["identity"]["conflicts"] + identity["conflicts"])
                        )
                        if existing["identity"]["conflicts"]:
                            existing["identity"]["status"] = "conflicting"
                        existing["identity"]["affiliation_warnings"] = list(
                            dict.fromkeys(
                                existing["identity"].get("affiliation_warnings", [])
                                + identity.get("affiliation_warnings", [])
                            )
                        )
                    else:
                        decisions.append(
                            {
                                **group["original"],
                                "emails": [verified["email"]],
                                "company": company,
                                "website": website,
                                "is_new_partner": item["is_new_partner"],
                                "identity": identity,
                            }
                        )
        return decisions

    def partner_report(self, group, scan, calendar, notify):
        from pu.reporting.cache import company_evidence_is_current, reusable_company_sources
        from pu.reporting.policy import CURRENT_RUBRIC_VERSION
        from pu.tools.report_delivery import report_can_release

        research_domain = domain_of(group["website"])
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    POLICY_VERSION,
                    "report-quality-v2",
                    CURRENT_RUBRIC_VERSION,
                    RESEARCH_POLICY_VERSION,
                    scan["start"],
                    research_domain,
                    group["company"],
                    sorted((e["gmail_id"], e.get("body_text") or e.get("snippet") or "") for e in group["emails"]),
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        with self.repo.database.connection() as conn:
            cached = conn.execute(
                "SELECT workspace_id,payload_json FROM workspace_reports WHERE json_extract(payload_json,'$.metadata.inbox_fingerprint')=? ORDER BY created_at DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
        cached_company_sources = []
        company_checked_at = ""
        if cached:
            payload = json.loads(cached["payload_json"])
            if (
                payload.get("claims")
                and payload.get("sections")
                and not payload.get("metadata", {}).get("parse_fallback")
                and payload.get("metadata", {}).get("evidence_policy_version")
                == RESEARCH_POLICY_VERSION
                and payload.get("metadata", {}).get("quality_policy_version") == "report-quality-v2"
            ):
                from pu.reporting.availability import snapshot_is_current

                if company_evidence_is_current(payload):
                    enabled_ids = {s["id"] for s in self.repo.sources(cached["workspace_id"]) if s["enabled"]}
                    cached_company_sources = reusable_company_sources(payload, enabled_ids)
                    company_checked_at = payload["metadata"]["company_evidence_checked_at"]
                    expected_ids = {
                        s.get("metadata", {}).get("workspace_source_id") for s in payload.get("sources", [])
                        if not s.get("derived") and s.get("source_type") not in {"calendar"}
                    }
                    if (
                        expected_ids <= enabled_ids and report_can_release(payload)
                        and snapshot_is_current(payload.get("metadata", {}).get("availability", {}))
                        and all(payload["metadata"]["availability"].get(key) == calendar.get(key)
                                for key in ("slots", "status", "checked_sources", "timezone"))
                    ):
                        notify("inbox_progress", {"stage": "report_cache", "company": group["company"]})
                        return cached["workspace_id"], payload
        workspace = next(
            (
                w
                for w in self.repo.list()
                if domain_of(w["website"]) == research_domain
            ),
            None,
        )
        workspace = workspace or self.repo.create(
            group["company"], group["website"], group["emails"][0]["sender_email"]
        )
        wid = workspace["id"]
        if cached and wid != cached["workspace_id"]:
            cached_company_sources = []
            company_checked_at = ""
        if workspace["company"] != group["company"]:
            with self.repo.database.connection() as conn:
                conn.execute(
                    "UPDATE research_workspaces SET company=? WHERE id=?", (group["company"], wid)
                )
        current_emails = []
        for email in sorted(group["emails"], key=lambda email: email["internal_date"]):
            current_emails.append(self.repo.add_source(
                wid,
                title=email["subject"][:300],
                source_type="email",
                content=f"Người gửi: {email['sender_name']} <{email['sender_email']}>\nNgày nhận: {email['internal_date']}\n{email['body_text'] or email['snippet']}",
                published_at=email["internal_date"],
                metadata={
                    "gmail_id": email["gmail_id"],
                    "untrusted_input": True,
                    "partner_identity": group.get("identity", {}),
                },
            ))
        from pu.reporting.collection import collect_partner_sources

        if cached_company_sources:
            from pu.reporting.availability import filter_partner_events

            snapshot = filter_partner_events(calendar, workspace)
            calendar_source = self.repo.add_source(
                wid, title="Lịch họp liên quan và giờ trống đã kiểm tra", source_type="calendar",
                content=json.dumps(snapshot, ensure_ascii=False),
                metadata={"automatic": True, "availability": snapshot},
            )
            sources = [*current_emails, *cached_company_sources, calendar_source]
            warnings = []
            notify("inbox_progress", {"stage": "calendar_refresh", "company": group["company"]})
        else:
            warnings = collect_partner_sources(
                self.repo,
                wid,
                app=self.app,
                calendar_snapshot=calendar,
                progress=lambda stage: notify(
                    "inbox_progress", {"stage": stage, "company": group["company"]}
                ),
            )
            from datetime import UTC, datetime

            company_checked_at = datetime.now(UTC).isoformat()
            sources, size = [], 0
            for source in reversed(self.repo.sources(wid)):
                if source["enabled"] and len(sources) < 30 and size + len(source["content"]) <= 110000:
                    sources.append(source)
                    size += len(source["content"])
            sources.reverse()
        result = self.app.run_research_workflow(
            "Viết báo cáo công ty để chuẩn bị trao đổi kinh doanh. Chỉ nghiên cứu đúng đối tác trong email đã cung cấp. "
            "Lịch sử liên hệ chỉ là ngữ cảnh; không suy đoán đây là đối tác cũ hoặc đã có hợp đồng. "
            "Bao gồm lĩnh vực hoạt động, sản phẩm/dịch vụ, nhu cầu email, tin có ngày xuất bản, người liên hệ, câu hỏi và lưu ý trước cuộc gặp. "
            "Không thay đối tác bằng công ty khác trong inbox. Chỉ dùng lịch trống còn lại trong TUẦN HIỆN TẠI đã cung cấp. "
            + " ".join(warnings),
            company=group["company"],
            workspace_id=wid,
            evidence_sources=sources,
            evidence_cache={"reused": bool(cached_company_sources), "checked_at": company_checked_at},
            partner_identity=group.get("identity", {}),
            require_semantic_review=True,
            inbox_fingerprint=fingerprint,
            observer=notify,
        )
        return wid, {
            **result.structured_report.to_dict(),
            "evaluation": result.evaluation.to_dict(),
        }

    def run(self, message, *, notify):
        parts, reports = [], []

        def append(text):
            parts.append(text)
            notify("text", {"delta": text + "\n\n"})

        notify("inbox_progress", {"stage": "inbox"})
        try:
            scan = self.tool("scan_inbox_today", notify)
        except Exception as exc:
            append(
                "Chưa đọc được inbox hôm nay: "
                + redact(str(exc))
                + ". Em chưa thể xác nhận email mới hoặc tự nghiên cứu đối tác."
            )
            return AgentResult(reply="\n\n".join(parts), tool_calls=self.calls)
        append(
            f"{'Hôm nay anh nhận được' if scan['fresh'] else 'Dữ liệu đang lưu có'} **{len(scan['messages'])} email**."
        )
        if not scan["fresh"]:
            append(
                "**Chưa đồng bộ được Gmail. Đây chỉ là dữ liệu đã lưu, không phải xác nhận inbox mới nhất.**\n"
                + scan.get("warning", "")
            )
        if scan.get("limited"):
            append(
                "Inbox vượt giới hạn 500 email/lượt; kết quả có thể chưa bao gồm toàn bộ email hôm nay."
            )
        calendar = None
        try:
            notify("inbox_progress", {"stage": "calendar"})
            calendar = self.tool("read_week_availability", notify)
        except Exception:
            calendar = {
                "status": "unknown",
                "slots": [],
                "events": [],
                "checked_sources": [],
                "timezone": "Asia/Ho_Chi_Minh",
                "warnings": ["Chưa đọc được lịch tuần này; không xác nhận giờ trống."],
            }
        try:
            notify("inbox_progress", {"stage": "triage"})
            candidates, _excluded = self.candidates(scan)
            partners = self.classify(candidates, notify) if candidates else []
            if partners:
                append(
                    f"Có **{len(partners)} cuộc trao đổi kinh doanh cần anh chú ý**. Em đã kiểm tra nội dung và đang chuẩn bị thông tin công ty để anh xem."
                )
            else:
                append(
                    "Em chưa tìm thấy yêu cầu kinh doanh trực tiếp nào đủ thông tin để chuẩn bị báo cáo."
                )
            excluded = sum(v["status"] == "excluded" for v in self.validation.values())
            uncertain = sum(v["status"] == "uncertain" for v in self.validation.values())
            if excluded:
                append(
                    f"Đã gộp **{excluded} email quảng cáo, thông báo hoặc nội dung không cần xử lý**."
                )
            if uncertain:
                append(
                    f"Có **{uncertain} email chưa đủ dữ kiện**; anh có thể xem trong chi tiết lọc email."
                )
        except Exception as exc:
            append(
                "Chưa hoàn tất phân loại đối tác: "
                + redact(str(exc))
                + ". Không kết luận rằng inbox không có đối tác mới."
            )
            partners = []
            for item in self.validation.values():
                if item["status"] == "eligible":
                    item.update(
                        status="uncertain",
                        reason="Chưa hoàn tất phân loại toàn bộ; chưa tự research",
                    )
        for group in partners:
            notify("inbox_progress", {"stage": "research", "company": group["company"]})
            try:
                wid, payload = self.partner_report(group, scan, calendar, notify)
                append(inline_report(payload))
                stored = self.repo.require_report(wid, payload["report_id"])
                reports.append(
                    {
                        "workspace_id": wid,
                        "report_id": payload["report_id"],
                        "title": payload["title"],
                        "company": group["company"],
                        "content_hash": stored["content_hash"],
                        "evidence_policy_version": payload.get("metadata", {}).get(
                            "evidence_policy_version"
                        ),
                        "quality_policy_version": payload.get("metadata", {}).get("quality_policy_version"),
                        "rubric_version": payload.get("evaluation", {}).get("rubric_version"),
                        "release_allowed": payload.get("evaluation", {}).get(
                            "release_allowed", False
                        ),
                    }
                )
                if payload.get("evaluation", {}).get("release_allowed"):
                    from pu.ops.briefing import BriefingService

                    try:
                        BriefingService(self.app.settings.home).prepare_approvals(
                            wid, payload, self.app
                        )
                    except Exception as exc:
                        append(
                            "Báo cáo đã tạo, nhưng chưa chuẩn bị được hành động chờ duyệt: "
                            + redact(str(exc))
                        )
            except Exception as exc:
                notify(
                    "inbox_report_failed", {"company": group["company"], "error": redact(str(exc))}
                )
                append(
                    f"**{group['company']}:** em đã nhận diện được yêu cầu, nhưng chưa hoàn tất báo cáo. Anh có thể yêu cầu thử lại; hiện chưa có báo cáo để duyệt."
                )
        append(calendar_markdown(calendar))
        if any(report["release_allowed"] for report in reports):
            append(
                "Anh có thể đọc báo cáo và chọn **Duyệt lưu tri thức** hoặc duyệt gửi email khi đã cấu hình người nhận. Em chưa thực hiện hành động phân phối nào."
            )
        notify("inbox_progress", {"stage": "completed"})
        return AgentResult(
            reply="\n\n".join(parts),
            tool_calls=self.calls,
            reports=reports,
            iterations=1,
            inbox_validation=list(self.validation.values()),
        )
