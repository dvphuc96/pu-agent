"""Actual chat facade: fresh inbox → new partners → inline reports + this week."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from evals.report_fixtures import PUBLIC_PROFILE, grounded_report
from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.llm import LLMResponse, LLMUsage
from pu.orchestration.inbox_chat import is_inbox_request
from pu.reporting.workspace import WorkspaceRepository
from pu.tools import gmail


class ClassifierProvider:
    def __init__(self, *, known=False, invented_website=False):
        self.requests = []
        self.known = known
        self.invented_website = invented_website

    def complete(self, request, **kwargs):
        self.requests.append(request)
        if "website_candidates" in request.messages[0]["content"]:
            candidates = json.loads(request.messages[0]["content"])
            payload = {
                "partners": [
                    {
                        "candidate_id": c["candidate_id"],
                        "is_new_partner": not self.known,
                        "needs_research": True,
                        "company": c["emails"][0]["sender"],
                        "website": "https://invented.example"
                        if self.invented_website
                        else c["website_candidates"][0],
                        "reason": "Yêu cầu hợp tác",
                        "intent": "partnership",
                        "direct_request": True,
                        "request_evidence": {
                            "email_id": c["emails"][0]["id"],
                            "quote": c["emails"][0]["body"],
                        },
                        "email_decisions": [
                            {
                                "email_id": e["id"],
                                "status": "existing_partner" if self.known else "new_partner",
                                "reason": "Lịch sử kinh doanh"
                                if self.known
                                else "Yêu cầu trực tiếp",
                            }
                            for e in c["emails"]
                        ],
                    }
                    for c in candidates
                ]
            }
        else:
            payload = {
                "score": 9,
                "criteria": {"factual_support": 9, "relevance": 9, "meeting_readiness": 9, "nondup": 9, "completeness": 9},
                "reason": "Nguồn hỗ trợ nội dung",
            }
        return LLMResponse(text=json.dumps(payload), usage=LLMUsage(10, 10))


class ReportRuntime:
    def __init__(self, *, empty_first=False, always_empty=False):
        self.requests = []
        self.empty_first = empty_first
        self.always_empty = always_empty

    def run(self, request, observer=None):
        self.requests.append(request)
        if request.tool_context.agent_id != "report-generation-agent":
            return AgentResult(reply="Theo bằng chứng được cung cấp.")
        reports = [r for r in self.requests if r.tool_context.agent_id == "report-generation-agent"]
        if self.always_empty or self.empty_first and len(reports) == 1:
            return AgentResult(reply="(I ran out of output tokens before I could answer.)")
        return AgentResult(reply=json.dumps(grounded_report(request.messages[0]["content"])))


def build(tmp_path, monkeypatch, *, known=False, fresh=True, runtime=None, invented_website=False):
    provider = ClassifierProvider(known=known, invented_website=invented_website)
    app = create_application(
        settings=Settings(home=tmp_path, api_key="offline", graph_workflows=True),
        llm_provider=provider,
        agent_runtime=runtime or ReportRuntime(),
    )

    def sync(conn, home, *args, status=None, **kwargs):
        if status is not None:
            status.update(ok=fresh, limited=False)
        return "offline snapshot" if not fresh else "synced"

    monkeypatch.setattr(gmail, "sync", sync)
    collected = []

    def collect(repo, wid, *, app, calendar_snapshot, progress):
        collected.append(repo.require(wid))
        progress("website")
        repo.add_source(
            wid,
            title="Giới thiệu",
            source_type="web",
            url=repo.require(wid)["website"],
            content=PUBLIC_PROFILE,
        )
        repo.add_source(
            wid,
            title="Lịch tuần này",
            source_type="calendar",
            content=json.dumps(calendar_snapshot),
            metadata={"availability": calendar_snapshot},
        )
        return []

    monkeypatch.setattr("pu.reporting.collection.collect_partner_sources", collect)
    return app, provider, collected


def seed(app, domain="acme.example", name="Acme", *, old=False):
    current = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    stamp = current - timedelta(days=2) if old else current
    gmail.upsert_messages(
        app.conn,
        [
            {
                "gmail_id": domain + ("-old" if old else "-today"),
                "thread_id": "thread",
                "sender_name": name,
                "sender_email": "alice@" + domain,
                "subject": "Đề nghị trao đổi giải pháp AI",
                "body_text": f"{name} cần hợp tác triển khai AI. Website https://{domain}",
                "snippet": "Cần hợp tác AI",
                "internal_date": stamp.isoformat(),
                "unread": 1,
            }
        ],
    )


@pytest.mark.parametrize(
    "message",
    [
        "Hôm nay có email gì mới?",
        "em xem inbox giúp anh",
        "Kiểm tra email mới hôm nay",
        "Any new email today?",
    ],
)
def test_read_requests_route_to_inbox_pipeline(message):
    assert is_inbox_request(message)


@pytest.mark.parametrize(
    "message",
    ["Gửi email cho Acme hôm nay", "Hướng dẫn tạo prompt hỏi email mới", "hello", "Đừng xóa inbox"],
)
def test_other_requests_are_not_hijacked(message):
    assert not is_inbox_request(message)


def test_chat_returns_complete_report_and_current_week_calendar_without_manual_workspace(
    tmp_path, monkeypatch
):
    app, provider, collected = build(tmp_path, monkeypatch)
    seed(app)
    events = []
    result = app.respond(
        "Hôm nay có email gì mới?",
        observer=lambda kind, event: events.append((kind, event)),
        source="dashboard",
        stream=True,
    )
    assert "Nhu cầu AI có trong email" in result.reply
    assert "Lịch trống trong tuần của anh" in result.reply
    assert "chưa kiểm tra Google Calendar" in result.reply and "lịch của đối tác" in result.reply
    assert len(result.reports) == 1
    assert result.reports[0]["release_allowed"]
    assert len(collected) == 1
    assert all(not request.tools for request in provider.requests)
    assert any(
        kind == "inbox_progress" and event.get("stage") == "website" for kind, event in events
    )
    assert "Nhu cầu AI có trong email" in "".join(
        event.get("delta", "") for kind, event in events if kind == "text"
    )
    meta = json.loads(
        app.conn.execute(
            "SELECT meta FROM chat_log WHERE role='assistant' ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert meta["reports"] == result.reports
    assert app.conn.execute("SELECT COUNT(*) FROM workspace_deliveries").fetchone()[0] == 0
    assert (
        app.conn.execute("SELECT COUNT(*) FROM proposed_actions WHERE status='pending'").fetchone()[
            0
        ]
        == 1
    )


def test_repeating_question_reuses_report_even_when_background_workspace_exists(
    tmp_path, monkeypatch
):
    app, _, collected = build(tmp_path, monkeypatch)
    WorkspaceRepository(tmp_path).create("Existing workspace", "https://acme.example")
    seed(app)
    first = app.respond("Hôm nay có email gì mới?")
    second = app.respond("Hôm nay có email gì mới?")
    assert first.reports[0]["report_id"] == second.reports[0]["report_id"]
    assert len(collected) == 1
    assert len(WorkspaceRepository(tmp_path).list()) == 1
    assert app.conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0] == 1


def test_multiple_new_companies_are_all_reported_in_one_chat_turn(tmp_path, monkeypatch):
    app, _, collected = build(tmp_path, monkeypatch)
    seed(app)
    seed(app, "beta.example", "Beta")
    result = app.respond("Hôm nay có email gì mới?")
    assert len(result.reports) == 2
    assert {item["company"] for item in collected} == {"Acme", "Beta"}
    assert result.reply.count("## Lịch trống trong tuần của anh") == 1


def test_old_correspondence_supplies_context_but_does_not_discard_direct_meeting_request(
    tmp_path, monkeypatch
):
    app, provider, collected = build(tmp_path, monkeypatch, known=True)
    seed(app, old=True)
    seed(app)
    result = app.respond("Hôm nay có email gì mới?")
    classification = next(
        r for r in provider.requests if "website_candidates" in r.messages[0]["content"]
    )
    assert json.loads(classification.messages[0]["content"])[0]["older_correspondence"]
    assert len(collected) == len(result.reports) == 1
    assert result.inbox_validation[0]["relationship"] == "existing"
    assert "cuộc trao đổi kinh doanh cần anh chú ý" in result.reply


def test_model_cannot_invent_research_website(tmp_path, monkeypatch):
    app, _, collected = build(tmp_path, monkeypatch, invented_website=True)
    seed(app)
    app.respond("Hôm nay có email gì mới?")
    assert collected[0]["website"] == "https://acme.example"


def test_failed_sync_is_explicit_and_does_not_claim_fresh_inbox(tmp_path, monkeypatch):
    app, _, _ = build(tmp_path, monkeypatch, fresh=False)
    result = app.respond("Hôm nay có email gì mới?")
    assert "Chưa đồng bộ được Gmail" in result.reply
    assert "Inbox đã đồng bộ có" not in result.reply


def test_truncated_generation_is_repaired_with_larger_budget(tmp_path, monkeypatch):
    runtime = ReportRuntime(empty_first=True)
    app, _, _ = build(tmp_path, monkeypatch, runtime=runtime)
    seed(app)
    result = app.respond("Hôm nay có email gì mới?")
    requests = [r for r in runtime.requests if r.tool_context.agent_id == "report-generation-agent"]
    assert [r.max_tokens for r in requests] == [12000, 20000]
    assert len(result.reports) == 1 and "ran out" not in result.reply


def test_two_empty_generations_never_create_successful_empty_report(tmp_path, monkeypatch):
    app, _, _ = build(tmp_path, monkeypatch, runtime=ReportRuntime(always_empty=True))
    seed(app)
    result = app.respond("Hôm nay có email gì mới?")
    assert not result.reports
    assert "chưa hoàn tất báo cáo" in result.reply
    assert "ValueError" not in result.reply and "finalize" not in result.reply
    assert app.conn.execute("SELECT COUNT(*) FROM workspace_reports").fetchone()[0] == 0


def test_today_scan_uses_vietnam_midnight_and_exact_query_window(tmp_path, monkeypatch):
    from pu.db import connect

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 15, 3, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(gmail, "datetime", Fixed)
    conn = connect(tmp_path)
    seen = {}

    def sync(*args, status=None, **kwargs):
        seen.update(kwargs)
        status.update(ok=True)

    monkeypatch.setattr(gmail, "sync", sync)
    for mid, stamp in (
        ("before", "2026-10-14T16:59:59Z"),
        ("start", "2026-10-14T17:00:00Z"),
        ("end", "2026-10-15T17:00:00Z"),
    ):
        conn.execute("INSERT INTO crm_emails(gmail_id,internal_date) VALUES(?,?)", (mid, stamp))
    conn.commit()
    result = json.loads(gmail.make_today_tool(conn, tmp_path).fn())
    assert [e["gmail_id"] for e in result["messages"]] == ["start"]
    assert seen["start"].isoformat() == "2026-10-15T00:00:00+07:00"


def test_week_tool_stops_at_sunday_instead_of_rolling_seven_days(tmp_path, monkeypatch):
    from pu.reporting import availability

    class Friday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 16, 3, tzinfo=UTC).astimezone(tz)

    captured = {}
    monkeypatch.setattr(availability, "datetime", Friday)

    def read(*args, **kwargs):
        captured.update(kwargs)
        return {"slots": []}

    monkeypatch.setattr(availability, "read_availability", read)
    availability.make_week_tool(tmp_path).fn()
    assert captured["days"] == 3


@pytest.mark.parametrize("enclosed", [True, False])
def test_report_json_transport_escaping_preserves_vietnamese_and_embedded_quotes(enclosed):
    from pu.reporting.evidence import StructuredReportBuilder

    original = {
        "title": "Báo cáo đối tác",
        "sections": {"company": 'Tên công ty "Acme"\nDòng tiếp theo'},
        "claims": [],
    }
    encoded = json.dumps(json.dumps(original, ensure_ascii=False), ensure_ascii=False)
    raw = encoded if enclosed else encoded[1:-1]
    assert StructuredReportBuilder._parse_json(raw) == original


def test_malformed_escaped_report_is_not_silently_repaired_into_facts():
    from pu.reporting.evidence import StructuredReportBuilder

    assert StructuredReportBuilder._parse_json(r"{\"title\":\"Chưa xong") is None


@pytest.mark.parametrize(
    "subject",
    [
        "Welcome to MCP Market",
        "You're one step away from selling skills on MCP Market",
        "Weekly digest: partnership opportunities",
    ],
)
def test_onboarding_and_newsletters_never_call_classifier_or_research(
    tmp_path, monkeypatch, subject
):
    app, provider, collected = build(tmp_path, monkeypatch)
    seed(app)
    app.conn.execute("UPDATE crm_emails SET subject=?", (subject,))
    app.conn.commit()
    result = app.respond("Hôm nay có email gì mới?")
    assert not provider.requests and not collected and not result.reports
    assert "email quảng cáo, thông báo" in result.reply
    assert subject not in result.reply
    assert result.inbox_validation[0]["status"] == "excluded"
    meta = json.loads(
        app.conn.execute("SELECT meta FROM chat_log WHERE role='assistant'").fetchone()[0]
    )
    assert meta["inbox_validation"] == result.inbox_validation


@pytest.mark.parametrize(
    "mutation",
    ["missing_quote", "invented_quote", "subject_only", "invented_company", "no_direct_request"],
)
def test_model_positive_flag_without_verified_body_evidence_never_triggers_research(
    tmp_path, monkeypatch, mutation
):
    app, provider, collected = build(tmp_path, monkeypatch)
    seed(app)
    complete = provider.complete

    def ungrounded(request, **kwargs):
        response = complete(request, **kwargs)
        data = json.loads(response.text)
        item = data["partners"][0]
        if mutation == "missing_quote":
            item.pop("request_evidence")
        elif mutation == "invented_quote":
            item["request_evidence"]["quote"] = "Acme cần mua 100 tài khoản AI."
        elif mutation == "subject_only":
            item["request_evidence"]["quote"] = "Đề nghị trao đổi giải pháp AI"
        elif mutation == "invented_company":
            item["company"] = "Invented Co"
        else:
            item["direct_request"] = False
        return LLMResponse(text=json.dumps(data), usage=LLMUsage(10, 10))

    monkeypatch.setattr(provider, "complete", ungrounded)
    result = app.respond("Hôm nay có email gì mới?")
    assert not collected and not result.reports
    assert "email chưa đủ dữ kiện" in result.reply
    assert all(v["status"] == "uncertain" for v in result.inbox_validation)


def test_same_domain_unrelated_mail_is_not_in_report_sources(tmp_path, monkeypatch):
    app, _, collected = build(tmp_path, monkeypatch)
    seed(app)
    app.conn.execute(
        "INSERT INTO crm_emails (gmail_id, thread_id, sender_name, sender_email, subject, body_text, snippet, internal_date, unread) SELECT 'newsletter', thread_id, sender_name, sender_email, 'Weekly digest', 'Unrelated promotion', snippet, internal_date, unread FROM crm_emails"
    )
    app.conn.commit()
    result = app.respond("Hôm nay có email gì mới?")
    assert len(collected) == len(result.reports) == 1
    statuses = {v["email_id"]: v["status"] for v in result.inbox_validation}
    assert statuses == {"acme.example-today": "eligible", "newsletter": "excluded"}
    sources = WorkspaceRepository(tmp_path).sources(result.reports[0]["workspace_id"])
    assert all("Unrelated promotion" not in s["content"] for s in sources)


def test_neutral_subject_with_actual_business_request_can_still_qualify(tmp_path, monkeypatch):
    app, _, collected = build(tmp_path, monkeypatch)
    seed(app)
    app.conn.execute("UPDATE crm_emails SET subject='Xin chào anh Phúc'")
    app.conn.commit()
    result = app.respond("Hôm nay có email gì mới?")
    assert len(collected) == len(result.reports) == 1
    assert result.inbox_validation[0]["quote"]


def test_conversational_summary_has_up_to_eight_unique_real_source_links():
    from pu.orchestration.inbox_chat import inline_report

    sources = [
        {
            "id": f"W{i}",
            "title": f"Nguồn {i}",
            "source_type": "web",
            "url": f"https://acme.example/page-{i}",
            "excerpt": "Nguồn đã đọc",
            "derived": False,
        }
        for i in range(1, 11)
    ]
    sources += [
        {**sources[0], "id": "W11", "url": sources[0]["url"] + "/"},
        {**sources[0], "id": "S1", "derived": True},
    ]
    payload = {
        "title": "Báo cáo Acme",
        "metadata": {"company": "Acme"},
        "executive_summary": "Acme cung cấp giải pháp AI [W1].",
        "sections": {
            "public_research": "- Đào tạo AI [W1].\n- Xử lý tài liệu [W2].\n- Triển khai agent [W3].",
            "contacts": "PRIVATE DETAIL ONLY IN FULL REPORT",
        },
        "sources": sources,
        "evaluation": {"release_allowed": True},
    }
    text = inline_report(payload)
    links = text.split("**Nguồn tham khảo:**")[1]
    assert links.count("https://") == 8
    assert links.count("page-1)") == 1 and "page-9" not in links
    assert "**Acme**" in text and "Các điểm nổi bật" in text
    assert "PRIVATE DETAIL ONLY IN FULL REPORT" not in text
    assert "Import vào sổ tay" in text
    assert "[W1]" not in text


def test_summary_uses_only_available_sources_without_padding():
    from pu.orchestration.inbox_chat import inline_report

    payload = {
        "title": "Acme",
        "executive_summary": "Chưa đủ dữ kiện công khai.",
        "sections": {},
        "sources": [],
        "evaluation": {"release_allowed": False},
    }
    text = inline_report(payload)
    assert "Chưa đọc được nguồn công khai" in text
    assert "http" not in text and "Bản nháp cần xác minh" in text


def test_calendar_output_converts_utc_to_readable_vietnam_time():
    from pu.orchestration.inbox_chat import calendar_markdown

    text = calendar_markdown(
        {
            "status": "available",
            "slots": [],
            "checked_at": "2026-10-15T09:13:30.538553+00:00",
            "checked_sources": ["Lịch local"],
        }
    )
    assert "16:13, 15/10/2026" in text
    assert "+00:00" not in text and "chưa kiểm tra Google Calendar" in text


def test_ordinary_reply_hides_newsletter_titles_but_retains_auditable_decisions(
    tmp_path, monkeypatch
):
    app, _, _ = build(tmp_path, monkeypatch)
    seed(app)
    app.conn.execute("UPDATE crm_emails SET subject='Weekly digest — internal unique phrase'")
    app.conn.commit()
    result = app.respond("Hôm nay có email gì mới?")
    assert "internal unique phrase" not in result.reply
    assert result.inbox_validation[0]["subject"] == "Weekly digest — internal unique phrase"


def test_line_wrapped_meeting_email_reaches_research_with_original_evidence(tmp_path, monkeypatch):
    app, provider, collected = build(tmp_path, monkeypatch)
    seed(app)
    body = "Acme mong muốn sắp xếp một buổi giới thiệu giải pháp trực tiếp với đội\nngũ phía anh vào tuần tới."
    app.conn.execute(
        "UPDATE crm_emails SET subject=?,body_text=?",
        ("Hẹn buổi giới thiệu giải pháp hợp tác", body),
    )
    app.conn.commit()
    original = provider.complete

    def classify(request, **kwargs):
        response = original(request, **kwargs)
        payload = json.loads(response.text)
        if "partners" in payload:
            payload["partners"][0]["request_evidence"]["quote"] = " ".join(body.split())
            response = LLMResponse(text=json.dumps(payload), usage=LLMUsage(10, 10))
        return response

    monkeypatch.setattr(provider, "complete", classify)
    result = app.respond("Hôm nay có email gì mới?")
    assert len(collected) == len(result.reports) == 1
    assert result.inbox_validation[0]["quote"] == body
    assert result.inbox_validation[0]["status"] == "eligible"


def test_classifier_selects_original_passage_without_retyping_quote(tmp_path, monkeypatch):
    app, provider, collected = build(tmp_path, monkeypatch)
    seed(app)
    complete = provider.complete

    def classify(request, **kwargs):
        response = complete(request, **kwargs)
        data = json.loads(response.text)
        if "partners" in data:
            candidate = json.loads(request.messages[0]["content"])[0]
            passage = candidate["emails"][0]["request_passages"][0]
            data["partners"][0]["request_evidence"] = {
                "email_id": candidate["emails"][0]["id"],
                "passage_id": passage["id"],
            }
            response = LLMResponse(text=json.dumps(data), usage=LLMUsage(10, 10))
        return response

    monkeypatch.setattr(provider, "complete", classify)
    result = app.respond("Hôm nay có email gì mới?")
    assert len(collected) == len(result.reports) == 1
    assert result.inbox_validation[0]["quote"].startswith("Acme cần hợp tác")


def test_workspace_stages_cannot_research_unrelated_company_with_tools(tmp_path, monkeypatch):
    runtime = ReportRuntime()
    app, _, _ = build(tmp_path, monkeypatch, runtime=runtime)
    seed(app)
    result = app.respond("Hôm nay có email gì mới?")
    assert result.reports
    assert all(not request.tools.definitions() for request in runtime.requests)
    assert all(not request.tool_context.capabilities for request in runtime.requests)


def test_conflicting_company_identity_always_keeps_report_as_draft(tmp_path, monkeypatch):
    app, _, _ = build(tmp_path, monkeypatch)
    seed(app)
    body = "Tôi là Alice, Quản lý Đối tác tại Acme.\n\nAcme cần hợp tác triển khai AI.\n\nTrân trọng,\nAlice\nQuản lý | Beta"
    app.conn.execute("UPDATE crm_emails SET body_text=?", (body,))
    app.conn.commit()
    result = app.respond("Hôm nay có email gì mới?")
    assert result.reports and not result.reports[0]["release_allowed"]
    assert result.inbox_validation[0]["identity_conflicts"]
    assert app.conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0] == 0


def test_same_sender_two_companies_are_classified_independently(tmp_path, monkeypatch):
    app, provider, collected = build(tmp_path, monkeypatch)
    seed(app)
    original = dict(app.conn.execute("SELECT * FROM crm_emails").fetchone())
    first = {
        **original,
        "gmail_id": "alpha",
        "sender_email": "phuc@agency.example",
        "sender_name": "Phuc",
        "subject": "Hẹn buổi giới thiệu giải pháp hợp tác",
        "body_text": "Tôi là Phuc, Quản lý Đối tác tại Alpha.\n\nAlpha muốn trao đổi hợp tác AI.\n\nWebsite công ty: https://alpha.example/\n\nTrân trọng,\nPhuc\nQuản lý | Alpha",
    }
    second = {
        **original,
        "gmail_id": "milk",
        "sender_email": "phuc@agency.example",
        "sender_name": "Phuc",
        "subject": "Vinamilk – Đề nghị trao đổi hợp tác triển khai AI Agent",
        "body_text": "Tôi là Minh Anh, Quản lý Đối tác tại Vinamilk.\n\nChúng tôi mong muốn trao đổi hợp tác triển khai AI Agent.\n\nWebsite công ty: https://www.vinamilk.com.vn/\n\nTrân trọng,\nMinh Anh\nQuản lý | Vinamilk",
    }
    app.conn.execute("DELETE FROM crm_emails")
    gmail.upsert_messages(app.conn, [first, second])
    complete = provider.complete

    def classify(request, **kwargs):
        response = complete(request, **kwargs)
        data = json.loads(response.text)
        if "partners" in data:
            candidates = json.loads(request.messages[0]["content"])
            assert all(len(c["emails"]) == 1 for c in candidates)
            for decision, candidate in zip(data["partners"], candidates, strict=True):
                email = candidate["emails"][0]
                decision["company"] = "Vinamilk" if email["id"] == "milk" else "Alpha"
                decision["request_evidence"] = {"email_id": email["id"], "passage_id": "p1"}
            return LLMResponse(text=json.dumps(data), usage=LLMUsage(10, 10))
        return response

    monkeypatch.setattr(provider, "complete", classify)
    result = app.respond("Hôm nay có email gì mới?")
    assert {v["email_id"]: v["status"] for v in result.inbox_validation} == {
        "alpha": "eligible",
        "milk": "eligible",
    }
    assert {w["website"] for w in collected} == {
        "https://alpha.example",
        "https://www.vinamilk.com.vn",
    }
    assert {w["company"] for w in collected} == {"Alpha", "Vinamilk"}
    assert len(result.reports) == 2
    milk = next(v for v in result.inbox_validation if v["email_id"] == "milk")
    assert milk["affiliation_warnings"]


def test_one_invalid_evidence_does_not_mark_other_company_uncertain(tmp_path, monkeypatch):
    app, provider, collected = build(tmp_path, monkeypatch)
    seed(app)
    seed(app, "beta.example", "Beta")
    app.conn.execute("UPDATE crm_emails SET sender_email='phuc@agency.example'")
    # Explicit websites ensure company targets remain distinct from the sender.
    app.conn.execute(
        "UPDATE crm_emails SET body_text=body_text || char(10) || 'Website công ty: https://' || replace(gmail_id,'-today','')"
    )
    app.conn.commit()
    complete = provider.complete

    def classify(request, **kwargs):
        response = complete(request, **kwargs)
        data = json.loads(response.text)
        if "partners" in data:
            for decision in data["partners"]:
                if decision["company"] == "Acme":
                    decision["request_evidence"]["quote"] = (
                        "Câu model tự viết lại không có trong email gốc."
                    )
            return LLMResponse(text=json.dumps(data), usage=LLMUsage(10, 10))
        return response

    monkeypatch.setattr(provider, "complete", classify)
    result = app.respond("Hôm nay có email gì mới?")
    statuses = {v["email_id"]: v["status"] for v in result.inbox_validation}
    assert statuses == {"acme.example-today": "uncertain", "beta.example-today": "eligible"}
    assert len(collected) == len(result.reports) == 1
