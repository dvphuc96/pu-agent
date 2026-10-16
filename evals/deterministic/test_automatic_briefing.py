"""Inbox → actual workflow → immutable report → pending action, without network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from evals.report_fixtures import PUBLIC_PROFILE, grounded_report
from pu.config import Settings
from pu.core.container import create_application
from pu.core.interfaces.agents import AgentResult
from pu.core.interfaces.llm import LLMResponse, LLMUsage
from pu.ops.briefing import BriefingService, sender_domain
from pu.reporting.availability import partner_availability
from pu.reporting.evidence import EvidenceNormalizer
from pu.reporting.workspace import now
from pu.tools.gmail import upsert_messages


def seed(service, ids=("m1", "m2"), sender="alice@acme.example"):
    with service.repo.database.connection() as conn:
        upsert_messages(
            conn,
            [
                {
                    "gmail_id": mid,
                    "thread_id": "t",
                    "sender_name": "Alice",
                    "sender_email": sender,
                    "subject": "Trao đổi hợp tác AI",
                    "snippet": "Cần AI",
                    "body_text": "Acme cần AI cho tài liệu.",
                    "internal_date": now(),
                    "unread": 1,
                }
                for mid in ids
            ],
        )


def first_job(service):
    with service.repo.database.connection() as conn:
        return dict(
            conn.execute("SELECT * FROM briefing_jobs ORDER BY created_at LIMIT 1").fetchone()
        )


class Provider:
    def __init__(self, support=9):
        self.support = support
        self.calls = []

    def complete(self, request, **kwargs):
        self.calls.append(request)
        if request.system and "needs_research" in request.system:
            emails = json.loads(request.messages[0]["content"])
            return LLMResponse(
                text=json.dumps(
                    {
                        "needs_research": True,
                        "reason": "Hợp tác AI",
                        "requests": [
                            {
                                "intent": "project",
                                "direct_request": True,
                                "company": "Acme",
                                "request_evidence": {"email_id": e["id"], "quote": e["body"]},
                            }
                            for e in emails
                        ],
                    }
                ),
                usage=LLMUsage(10, 10),
            )
        return LLMResponse(
            text=json.dumps(
                {
                    "score": self.support,
                    "criteria": {"factual_support": self.support, "relevance": 9, "meeting_readiness": 9, "nondup": 9, "completeness": 9},
                    "reason": "Evidence matches",
                }
            ),
            usage=LLMUsage(10, 10),
        )


class ResearchRuntime:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def run(self, request, observer=None):
        agent = request.tool_context.agent_id
        self.calls.append(agent)
        if self.fail and agent == "report-generation-agent":
            raise RuntimeError("offline report provider interruption")
        if agent == "report-generation-agent":
            return AgentResult(reply=json.dumps(grounded_report(request.messages[0]["content"])))
        return AgentResult(reply="Acme: theo nguồn đã cung cấp.")


def app_for(service, support=9, runtime=None):
    return create_application(
        settings=Settings(home=service.home, api_key="offline", graph_workflows=False),
        llm_provider=Provider(support),
        agent_runtime=runtime or ResearchRuntime(),
    )


def stub_collection(monkeypatch):
    def collect(repo, wid, progress, app=None):
        progress("calendar")
        repo.add_source(
            wid,
            title="Website Acme",
            source_type="web",
            url=repo.require(wid)["website"].rstrip("/") + "/about",
            content=PUBLIC_PROFILE,
        )
        return []

    monkeypatch.setattr("pu.reporting.collection.collect_partner_sources", collect)


def test_discovery_groups_company_and_survives_restart_without_duplicate(tmp_path):
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    restarted = BriefingService(tmp_path)
    restarted.discover()
    assert len(restarted.snapshot()["jobs"]) == 1
    assert len(restarted.repo.list()) == 1
    assert set(json.loads(first_job(restarted)["email_ids_json"])) == {"m1", "m2"}
    seed(service, ids=("m3",))
    restarted.discover()
    assert len(restarted.snapshot()["jobs"]) == 2
    assert len(restarted.repo.list()) == 1


@pytest.mark.parametrize("email", ["a@gmail.com", "no-reply@corp.example", "broken", "a@127.0.0.1"])
def test_personal_automated_invalid_senders_are_not_guessed_as_companies(email):
    domain, reason = sender_domain(email)
    assert not domain and reason


def test_ignored_domain_never_creates_workspace(tmp_path):
    service = BriefingService(tmp_path)
    service.set_state("ignored_domains", "acme.example")
    seed(service)
    service.discover()
    assert service.repo.list() == []
    assert service.snapshot()["skipped_emails"] == 2


def test_lease_excludes_other_process_and_recovers_after_expiry(tmp_path):
    one, two = BriefingService(tmp_path), BriefingService(tmp_path)
    assert one.lease()
    assert not two.lease()
    one.set_state("lease_until", "0")
    assert two.lease()
    assert not one.lease()


def test_end_to_end_prepares_report_and_pending_approval_without_writing_knowledge(
    tmp_path, monkeypatch
):
    stub_collection(monkeypatch)
    service = BriefingService(tmp_path)
    seed(service, ids=("m1",))
    service.discover()
    app = app_for(service)
    service.run_job(first_job(service), app)
    job = first_job(service)
    assert job["status"] == "completed", job["error"]
    report = service.repo.require_report(job["workspace_id"], job["report_id"])["payload"]
    assert report["evaluation"]["release_allowed"]
    assert report["metadata"]["require_semantic_review"]
    with service.repo.database.connection() as conn:
        actions = conn.execute("SELECT * FROM proposed_actions").fetchall()
        assert len(actions) == 1 and actions[0]["status"] == "pending"
        assert conn.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM workspace_deliveries").fetchone()[0] == 0
    # Retry a saved job after a process crash must reuse artifact and pending proposal.
    previous_calls = len(app.llm_provider.calls)
    service.run_job(job, app)
    assert len(app.llm_provider.calls) == previous_calls
    with service.repo.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0] == 1


def test_semantic_review_failure_keeps_draft_without_distribution_proposals(tmp_path, monkeypatch):
    stub_collection(monkeypatch)
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    service.run_job(first_job(service), app_for(service, support=3))
    job = first_job(service)
    report = service.repo.require_report(job["workspace_id"], job["report_id"])["payload"]
    assert job["status"] == "completed"
    assert not report["evaluation"]["release_allowed"]
    with service.repo.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM proposed_actions").fetchone()[0] == 0


def test_interrupted_job_resumes_completed_stages(tmp_path, monkeypatch):
    stub_collection(monkeypatch)
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    runtime = ResearchRuntime(fail=True)
    app = app_for(service, runtime=runtime)
    service.run_job(first_job(service), app)
    job = first_job(service)
    assert job["status"] == "retrying" and job["workflow_id"]
    runtime.fail = False
    service.run_job(job, app)
    assert first_job(service)["status"] == "completed"
    assert runtime.calls.count("email-agent") == 0
    assert runtime.calls.count("company-info-agent") == 0
    assert runtime.calls.count("report-generation-agent") == 2
    stages = app.workflow_repository.checkpoints(job["workflow_id"])
    assert all(stages[name].status == "completed" for name in ("email", "company", "web", "calendar", "memory"))


def test_read_page_evidence_keeps_exact_content_date_and_url():
    output = json.dumps(
        {
            "url": "https://acme.example/news",
            "content": "Acme launch",
            "title": "Launch",
            "published_at": "2026-10-14",
        }
    )
    bundle = EvidenceNormalizer().normalize(
        {
            "web": AgentResult(
                reply="", tool_calls=[{"tool": "read_web_page", "ok": True, "output": output}]
            )
        }
    )
    assert len(bundle.sources) == 1
    assert bundle.sources[0].excerpt == "Acme launch"
    assert bundle.sources[0].published_at == "2026-10-14"


def test_search_results_do_not_attribute_another_result_to_each_url():
    output = json.dumps(
        {
            "results": [
                {"url": "https://a.example", "title": "A", "content": "Alpha"},
                {"url": "https://b.example", "title": "B", "content": "Beta"},
            ]
        }
    )
    sources = (
        EvidenceNormalizer()
        .normalize(
            {"web": AgentResult(reply="", tool_calls=[{"tool": "search_web", "output": output}])}
        )
        .sources
    )
    assert [(s.url, s.excerpt) for s in sources] == [
        ("https://a.example", "Alpha"),
        ("https://b.example", "Beta"),
    ]


def test_partner_calendar_filters_meetings_but_all_events_still_block_slots(tmp_path):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")
    day = datetime.now(UTC) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    day = day.date().isoformat()
    with service.repo.database.connection() as conn:
        conn.execute(
            'INSERT INTO calendar_events(title,start,"end",attendees) VALUES(?,?,?,?)',
            ("Private", day + "T09:00:00+07:00", day + "T12:00:00+07:00", "else@other.example"),
        )
        conn.execute(
            'INSERT INTO calendar_events(title,start,"end",attendees) VALUES(?,?,?,?)',
            (
                "Acme meeting",
                day + "T14:00:00+07:00",
                day + "T15:00:00+07:00",
                "alice@acme.example",
            ),
        )
    result = partner_availability(tmp_path, workspace, start_date=day, days=1, duration=45)
    assert [ev["title"] for ev in result["events"]] == ["Acme meeting"]
    assert result["slots"][0]["window"] == "12:00–12:45"
    for slot in result["slots"]:
        assert datetime.fromisoformat(slot["end"]) - datetime.fromisoformat(
            slot["start"]
        ) == timedelta(minutes=45)


def test_disabled_cycle_does_not_fetch_or_construct_application(tmp_path):
    def unexpected():
        raise AssertionError("must not create app")

    service = BriefingService(tmp_path, app_factory=unexpected)
    service.set_state("enabled", "0")
    service.cycle()
    assert service.snapshot()["last_scan"] == ""


def test_irrelevant_corporate_mail_is_skipped_without_public_research(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    app = app_for(service)
    monkeypatch.setattr(
        app.llm_provider,
        "complete",
        lambda request: LLMResponse(
            text='{"needs_research":false,"reason":"Marketing"}', usage=LLMUsage(10, 10)
        ),
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("must not research irrelevant mail")

    monkeypatch.setattr("pu.reporting.collection.collect_partner_sources", unexpected)
    service.run_job(first_job(service), app)
    assert first_job(service)["status"] == "skipped"
    assert service.snapshot()["skipped_emails"] == 2
    from pu.ops.workspace_api import workspace_list

    assert workspace_list(tmp_path)["items"] == []


def test_collection_follows_real_official_links_and_reads_dated_news_through_harness(
    tmp_path, monkeypatch
):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")
    fetched, queries = [], []

    def fetch(url):
        fetched.append(url)
        return {
            "title": "Acme",
            "content": "Official products and services. https://acme.example",
            "url": url,
            "source_type": "web",
            "published_at": "2026-10-14" if "article" in url else None,
            "metadata": {
                "links": [
                    "https://acme.example/about",
                    "https://acme.example/products",
                    "http://127.0.0.1/about",
                ]
                if url == workspace["website"]
                else []
            },
        }

    def search(query, max_results):
        queries.append(query)
        return [("Acme release", "Short snippet", "https://news.example/article")]

    monkeypatch.setattr("pu.tools.web_source.fetch_source", fetch)
    monkeypatch.setattr("pu.tools.search._duckduckgo", search)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("PU_SEARCH_API_KEY", raising=False)
    from pu.reporting.collection import collect_partner_sources

    warnings = collect_partner_sources(service.repo, workspace["id"], app=app_for(service))
    assert not warnings
    assert set(fetched) == {
        "https://acme.example",
        "https://acme.example/about",
        "https://acme.example/products",
        "https://news.example/article",
    }
    assert queries and "alice" not in queries[0] and "Cần AI" not in queries[0]
    sources = service.repo.sources(workspace["id"])
    assert (
        next(s for s in sources if s["url"] == "https://news.example/article")["published_at"]
        == "2026-10-14"
    )
    with service.repo.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tool_audit_events").fetchone()[0] >= 5


def test_missing_semantic_judge_result_never_releases_auto_report(tmp_path, monkeypatch):
    stub_collection(monkeypatch)
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    app = app_for(service)
    app.evaluation_harness.judge = None
    service.run_job(first_job(service), app)
    job = first_job(service)
    report = service.repo.require_report(job["workspace_id"], job["report_id"])["payload"]
    assert report["evaluation"]["release_allowed"] is False


def test_network_failure_retries_are_bounded_and_enqueue_deduplicates_waiting_retry(
    tmp_path, monkeypatch
):
    stub_collection(monkeypatch)
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    app = app_for(service, runtime=ResearchRuntime(fail=True))
    for attempt in range(1, 4):
        service.run_job(first_job(service), app)
        job = first_job(service)
        assert job["attempts"] == attempt
        assert job["status"] == ("retrying" if attempt < 3 else "failed")
        if attempt < 3:
            assert service.enqueue(job["workspace_id"]) == job["id"]


def test_morning_refresh_for_upcoming_partner_meeting_needs_no_new_email(tmp_path, monkeypatch):
    import pu.ops.briefing as module

    class Morning(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 15, 3, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(module, "datetime", Morning)
    monkeypatch.setattr(
        "pu.reporting.availability.read_availability",
        lambda *args, **kwargs: {
            "status": "available",
            "warnings": [],
            "events": [{"title": "Partner discussion", "attendees": "alice@acme.example"}],
        },
    )
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")
    service.schedule_meeting_briefs()
    service.schedule_meeting_briefs()
    job = first_job(service)
    assert job["workspace_id"] == workspace["id"]
    assert json.loads(job["email_ids_json"]) == []
    assert len(service.snapshot()["jobs"]) == 1


def test_calendar_matching_rejects_lookalike_attendee_domains():
    from pu.reporting.availability import filter_partner_events

    result = filter_partner_events(
        {
            "events": [
                {"title": "Other meeting", "attendees": "alice@acme.example.attacker.com"},
                {"title": "Real meeting", "attendees": "Alice <alice@acme.example>"},
            ]
        },
        {"company": "Acme", "website": "https://acme.example", "contact": "alice@acme.example"},
    )
    assert [ev["title"] for ev in result["events"]] == ["Real meeting"]


def test_missing_model_keeps_worker_alive_and_does_not_fetch_mail(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    (tmp_path / "gmail-token.json").write_text("{}")
    monkeypatch.setattr(service, "model_configured", lambda: False)

    def unexpected(*args, **kwargs):
        raise AssertionError("must not fetch mail before setup")

    monkeypatch.setattr("pu.tools.gmail.sync", unexpected)
    service.cycle()
    assert "model" in service.state("scan_message")
    assert not service.state("last_scan")


def test_worker_owns_separate_application_and_rebuilds_on_model_change(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pu.ops import browser_agent

    service = BriefingService(tmp_path)
    settings = Settings(home=tmp_path, api_key="offline", model="one")
    monkeypatch.setattr("pu.config.load_settings", lambda: settings)
    built, closed = [], []

    def create(**kwargs):
        built.append(kwargs)
        return SimpleNamespace(conn=SimpleNamespace(close=lambda: closed.append(True)))

    monkeypatch.setattr("pu.core.container.create_application", create)
    browser_app = browser_agent._agent
    first = service.worker_application()
    assert service.worker_application() is first
    assert built[0]["check_same_thread"] is False
    settings.model = "two"
    assert service.worker_application() is not first
    assert len(closed) == 1
    assert browser_agent._agent is browser_app


def test_triage_cannot_discard_relevant_mail_beyond_first_ten_messages(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    app = app_for(service)
    calls = []

    def complete(request):
        emails = json.loads(request.messages[0]["content"])
        calls.append(emails)
        return LLMResponse(
            text=json.dumps(
                {
                    "needs_research": len(calls) == 2,
                    "requests": [
                        {
                            "intent": "project",
                            "direct_request": True,
                            "company": "Acme",
                            "request_evidence": {"email_id": e["id"], "quote": e["body"]},
                        }
                        for e in emails
                    ],
                }
            ),
            usage=LLMUsage(10, 10),
        )

    monkeypatch.setattr(
        app.llm_provider,
        "complete",
        complete,
    )
    emails = [
        {
            "gmail_id": str(i),
            "sender_email": "alice@acme.example",
            "sender_name": "Alice",
            "subject": "Trao đổi",
            "body_text": "Acme cần AI cho tài liệu.",
            "snippet": "",
        }
        for i in range(11)
    ]
    assert service.triage(emails, app) is True
    assert len(calls) == 2


def test_worker_rejects_onboarding_before_creating_research_job(tmp_path):
    service = BriefingService(tmp_path)
    seed(service)
    with service.repo.database.connection() as conn:
        conn.execute("UPDATE crm_emails SET subject='Welcome to MCP Market'")
    service.discover()
    assert not service.snapshot()["jobs"]
    assert service.snapshot()["skipped_emails"] == 2


def test_worker_positive_flag_without_body_evidence_does_not_research(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    app = app_for(service)
    monkeypatch.setattr(
        app.llm_provider,
        "complete",
        lambda request: LLMResponse(
            text='{"needs_research":true,"reason":"Business keywords"}', usage=LLMUsage(10, 10)
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Unverified input must not reach public research")

    monkeypatch.setattr("pu.reporting.collection.collect_partner_sources", forbidden)
    service.run_job(first_job(service), app)
    assert first_job(service)["status"] == "skipped"


def test_worker_revalidates_onboarding_when_resuming_old_job(tmp_path):
    service = BriefingService(tmp_path)
    seed(service)
    service.discover()
    job = first_job(service)
    service.update(job["id"], workflow_id="legacy-newsletter-workflow")
    with service.repo.database.connection() as conn:
        conn.execute("UPDATE crm_emails SET subject='Welcome to MCP Market'")
    app = app_for(service)
    service.run_job(first_job(service), app)
    assert first_job(service)["status"] == "skipped"
    assert not app.llm_provider.calls


def test_discovery_uses_declared_company_site_not_shared_sender_domain(tmp_path):
    service = BriefingService(tmp_path)
    seed(service, ids=("a", "b"), sender="phuc@agency.example")
    with service.repo.database.connection() as conn:
        conn.execute(
            "UPDATE crm_emails SET body_text='Alpha cần hợp tác AI.' || char(10) || 'Website công ty: https://alpha.example' WHERE gmail_id='a'"
        )
        conn.execute(
            "UPDATE crm_emails SET body_text='Milk cần hợp tác AI.' || char(10) || 'Website công ty: https://milk.example' WHERE gmail_id='b'"
        )
    service.discover()
    jobs = service.snapshot()["jobs"]
    assert len(jobs) == 2
    assert {j["website"] for j in jobs} == {"https://alpha.example", "https://milk.example"}


def test_legacy_job_moves_to_correct_company_website_before_research(tmp_path, monkeypatch):
    stub_collection(monkeypatch)
    service = BriefingService(tmp_path)
    seed(service, ids=("m1",))
    service.discover()
    with service.repo.database.connection() as conn:
        conn.execute(
            "UPDATE crm_emails SET body_text='Tôi là Alice, Quản lý Đối tác tại Acme.' || char(10) || 'Acme cần AI cho tài liệu.' || char(10) || 'Website công ty: https://new-acme.example'"
        )
    app = app_for(service)
    service.run_job(first_job(service), app)
    job = first_job(service)
    assert job["status"] == "completed", job["error"]
    assert service.repo.require(job["workspace_id"])["website"] == "https://new-acme.example"
