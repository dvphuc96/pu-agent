"""Offline checks for the bounded, topic-oriented public research collector."""

from __future__ import annotations

from pu.evaluation import DeterministicReportEvaluator
from pu.ops.briefing import BriefingService
from pu.reporting.collection import CollectionConfig, canonical_url, collect_partner_sources
from pu.reporting.models import ReportClaim, ReportSource, StructuredReport
from pu.reporting.research_plan import build_research_plan
from pu.reporting.source_contract import classify_public_source


def test_research_plan_only_uses_allowlisted_public_context_terms():
    plan = build_research_plan(
        "Acme",
        "acme.example",
        context="Need AI/OCR pilot; contact alice@example.com; internal-id=secret-42",
    )
    queries = "\n".join(query for _, query in plan)
    assert "alice@example.com" not in queries
    assert "secret-42" not in queries
    assert "AI".casefold() in queries.casefold()
    assert {topic.key for topic, _ in plan} == {
        "profile",
        "products_markets",
        "scale_operations",
        "technology_context",
        "recent_relevant_news",
    }


def test_canonical_url_removes_tracking_and_fragment():
    assert canonical_url("HTTPS://Acme.Example:443/news/?utm_source=x&b=2#read") == (
        "https://acme.example/news?b=2"
    )
    assert canonical_url("https://acme.example/news/") == "https://acme.example/news"


def test_collection_deduplicates_urls_and_keeps_topics_dates_and_targeted_excerpt(
    tmp_path, monkeypatch
):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")

    def fake_search(query, max_results):
        return [
            {
                "title": "Acme update",
                "content": "Search snippet",
                "url": "https://acme.example/news/?utm_source=research",
                "source_type": "web",
                "metadata": {"snippet_only": True},
            }
        ]

    def fake_fetch(url):
        return {
            "title": "Acme update",
            "content": (
                "Introductory material. " * 1000
                + "The company reports a new digital transformation and AI automation program."
            ),
            "url": url,
            "source_type": "web",
            "published_at": "2026-10-14",
            "metadata": {"links": ["https://acme.example/news"]},
        }

    monkeypatch.setattr("pu.reporting.collection.search_results", fake_search)
    monkeypatch.setattr("pu.reporting.collection.fetch_source", fake_fetch)
    warnings = collect_partner_sources(
        service.repo,
        workspace["id"],
        config=CollectionConfig(max_search_queries=5, max_detail_pages=0, max_excerpt_chars=900),
    )
    assert not [warning for warning in warnings if "chưa chứng minh" in warning]
    web_sources = [
        source for source in service.repo.sources(workspace["id"]) if source["source_type"] == "web"
    ]
    news = next(source for source in web_sources if "/news" in source["url"])
    assert news["url"] == "https://acme.example/news"
    assert news["published_at"] == "2026-10-14"
    assert set(news["metadata"]["research_topics"]) >= {
        "profile",
        "products_markets",
        "scale_operations",
        "technology_context",
        "recent_relevant_news",
    }
    assert news["metadata"]["snippet_only"] is False
    assert news["metadata"]["excerpt_strategy"] == "targeted"
    assert len(news["content"]) <= 900
    assert "digital transformation" in news["content"]
    assert len({source["url"] for source in web_sources}) == len(web_sources)
    calendar = next(
        source for source in service.repo.sources(workspace["id"]) if source["source_type"] == "calendar"
    )
    performance = calendar["metadata"]["collection_performance"]
    assert performance["scope"] == "source_collection"
    assert performance["search_attempts"] == 5
    assert performance["read_attempts"] == 2  # homepage plus one deduplicated news URL
    assert performance["successful_reads"] == 2
    assert performance["duration_seconds"] >= 0


def test_collection_continues_after_one_search_topic_fails_and_derives_safe_email_context(
    tmp_path, monkeypatch
):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")
    service.repo.add_source(
        workspace["id"],
        title="Business request",
        source_type="email",
        content="We are exploring AI and OCR automation. Contact alice@example.com; confidential plan.",
    )
    queries = []

    def fake_search(query, max_results):
        queries.append(query)
        if "official company" in query:
            raise RuntimeError("one topic unavailable")
        return []

    monkeypatch.setattr("pu.reporting.collection.search_results", fake_search)
    monkeypatch.setattr(
        "pu.reporting.collection.fetch_source",
        lambda url: {
            "title": "Acme",
            "content": "Acme company profile",
            "url": url,
            "source_type": "web",
            "metadata": {"links": []},
        },
    )
    warnings = collect_partner_sources(service.repo, workspace["id"])
    assert len(queries) == 5
    assert any("nhóm profile" in warning for warning in warnings)
    assert all("alice@example.com" not in query for query in queries)
    assert all("confidential" not in query for query in queries)
    assert any("AI".casefold() in query.casefold() for query in queries)
    calendar = next(
        source
        for source in service.repo.sources(workspace["id"])
        if source["source_type"] == "calendar"
    )
    assert calendar["metadata"]["collection_performance"]["search_attempts"] == 5


def test_read_attempt_budget_counts_failed_fetches(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")
    attempted = []

    def fail_fetch(url):
        attempted.append(url)
        raise OSError("offline fixture")

    def search(query, max_results):
        suffix = str(len(attempted))
        return [
            {
                "title": "External result",
                "content": "snippet",
                "url": f"https://outside.example/article-{suffix}",
                "source_type": "web",
                "metadata": {"snippet_only": True},
            }
        ]

    monkeypatch.setattr("pu.reporting.collection.fetch_source", fail_fetch)
    monkeypatch.setattr("pu.reporting.collection.search_results", search)
    collect_partner_sources(
        service.repo,
        workspace["id"],
        config=CollectionConfig(max_read_attempts=2, max_duration_seconds=30),
    )
    calendar = next(
        source
        for source in service.repo.sources(workspace["id"])
        if source["source_type"] == "calendar"
    )
    performance = calendar["metadata"]["collection_performance"]
    assert performance["read_attempts"] == 2
    assert len(attempted) == 2


def test_detail_page_budget_prioritizes_research_topics(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")
    fetched = []
    links = [
        *[f"https://acme.example/product-{index}" for index in range(5)],
        "https://acme.example/annual-report",
        "https://acme.example/digital-technology",
    ]

    def fetch(url):
        fetched.append(url)
        return {
            "title": "Acme",
            "content": "Acme public information",
            "url": url,
            "source_type": "web",
            "metadata": {"links": links} if url == workspace["website"] else {"links": []},
        }

    monkeypatch.setattr("pu.reporting.collection.fetch_source", fetch)
    monkeypatch.setattr("pu.reporting.collection.search_results", lambda query, max_results: [])
    collect_partner_sources(
        service.repo,
        workspace["id"],
        config=CollectionConfig(max_search_queries=0, max_detail_pages=3),
    )
    assert fetched == [
        "https://acme.example",
        "https://acme.example/product-0",
        "https://acme.example/annual-report",
        "https://acme.example/digital-technology",
    ]


def test_redirected_external_page_without_identity_relation_is_not_saved(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")

    def fetch(url):
        if url == workspace["website"]:
            return {
                "title": "Acme",
                "content": "Acme home",
                "url": url,
                "source_type": "web",
                "metadata": {"links": ["https://acme.example/about"]},
            }
        return {
            "title": "Unrelated",
            "content": "An unrelated website",
            "url": "https://outside.example/about",
            "source_type": "web",
            "metadata": {"links": []},
        }

    monkeypatch.setattr("pu.reporting.collection.fetch_source", fetch)
    monkeypatch.setattr("pu.reporting.collection.search_results", lambda query, max_results: [])
    collect_partner_sources(service.repo, workspace["id"], config=CollectionConfig(max_search_queries=0))
    assert all(
        source["url"] != "https://outside.example/about"
        for source in service.repo.sources(workspace["id"])
    )


def test_collection_stops_between_calls_when_time_budget_is_exhausted(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")

    def fetch(url):
        return {
            "title": "Acme",
            "content": "Acme public information",
            "url": url,
            "source_type": "web",
            "metadata": {"links": []},
        }

    monkeypatch.setattr("pu.reporting.collection.fetch_source", fetch)
    monkeypatch.setattr("pu.reporting.collection.search_results", lambda query, max_results: [])
    warnings = collect_partner_sources(
        service.repo,
        workspace["id"],
        config=CollectionConfig(max_search_queries=5, max_duration_seconds=0.000001),
    )
    calendar = next(
        source
        for source in service.repo.sources(workspace["id"])
        if source["source_type"] == "calendar"
    )
    performance = calendar["metadata"]["collection_performance"]
    assert performance["timed_out"] is True
    assert performance["search_attempts"] == 0
    assert any("hết ngân sách thời gian" in warning for warning in warnings)


def test_public_source_contract_rejects_snippets_email_and_future_news():
    snippet = classify_public_source(
        {
            "title": "AI news",
            "content": "AI announcement",
            "url": "https://acme.example/news/ai",
            "source_type": "web",
            "published_at": "2026-10-14",
            "metadata": {"snippet_only": True},
        },
        opportunity_terms=("ai",),
    )
    assert snippet["metadata"]["is_news"] is False
    assert snippet["metadata"]["relevant_to_opportunity"] is None

    article = classify_public_source(
        {
            "title": "Acme launches AI automation",
            "content": "Published company news about an AI automation launch.",
            "url": "https://acme.example/news/ai",
            "source_type": "web",
            "published_at": "2026-10-14",
            "metadata": {
                "page_fetched": True,
                "schema_types": ["NewsArticle"],
            },
        },
        opportunity_terms=("ai",),
    )
    assert article["metadata"]["is_news"] is True
    assert article["metadata"]["content_kind"] == "news"
    assert article["metadata"]["relevant_to_opportunity"] is True

    email = classify_public_source(
        {
            "title": "AI news",
            "content": "Published AI news",
            "source_type": "email",
            "published_at": "2026-10-14",
            "metadata": {"page_fetched": True, "schema_types": ["NewsArticle"]},
        },
        opportunity_terms=("ai",),
    )
    assert email["metadata"]["is_news"] is False

    future = classify_public_source(
        {
            "title": "Future AI news",
            "content": "Published AI news",
            "url": "https://acme.example/news/future",
            "source_type": "web",
            "published_at": "2099-01-01",
            "metadata": {"page_fetched": True, "schema_types": ["NewsArticle"]},
        },
        opportunity_terms=("ai",),
    )
    assert future["metadata"]["future_published_at"] is True
    assert future["metadata"]["is_news"] is False


def test_source_contract_matches_whole_opportunity_terms_and_real_news_type():
    chairman = classify_public_source(
        {
            "title": "Chairman update",
            "content": "Published company news about a chairman appointment.",
            "url": "https://acme.example/news/chairman",
            "source_type": "web",
            "published_at": "2026-10-01T00:00:00+00:00",
            "metadata": {"page_fetched": True},
        },
        opportunity_terms=("ai",),
    )
    assert chairman["metadata"]["relevant_to_opportunity"] is False
    assert chairman["metadata"]["opportunity_terms_matched"] == []

    ai_article = classify_public_source(
        {
            "title": "AI Agent launch",
            "content": "Published company news about an AI Agent launch.",
            "url": "https://acme.example/news/ai-agent",
            "source_type": "web",
            "published_at": "2026-10-01T00:00:00+00:00",
            "metadata": {"page_fetched": True},
        },
        opportunity_terms=("ai",),
    )
    assert ai_article["metadata"]["relevant_to_opportunity"] is True

    typed_news = classify_public_source(
        {
            "title": "Company update",
            "content": "A public page returned by a trusted news source.",
            "url": "https://acme.example/company-update",
            "source_type": "news",
            "metadata": {"page_fetched": True},
        },
        opportunity_terms=("ai",),
    )
    assert typed_news["metadata"]["content_kind"] == "news"
    assert typed_news["metadata"]["is_news"] is True


def test_redirected_search_result_merges_against_final_url_across_topics(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url == workspace["website"]:
            return {
                "title": "Acme",
                "content": "Acme home",
                "url": url,
                "source_type": "web",
                "metadata": {"links": []},
            }
        return {
            "title": "Acme AI article",
            "content": "Published news about https://acme.example AI automation.",
            "url": "https://news.example/story/amp",
            "source_type": "web",
            "published_at": "2026-10-14",
            "metadata": {"page_fetched": True, "schema_types": ["NewsArticle"], "links": []},
        }

    monkeypatch.setattr("pu.reporting.collection.fetch_source", fetch)
    monkeypatch.setattr(
        "pu.reporting.collection.search_results",
        lambda query, max_results: [
            {
                "title": "Acme article",
                "content": "snippet",
                "url": "https://news.example/story",
                "source_type": "web",
                "metadata": {"snippet_only": True},
            }
        ],
    )
    warnings = collect_partner_sources(
        service.repo,
        workspace["id"],
        config=CollectionConfig(max_detail_pages=0),
    )
    assert not any("KeyError" in warning for warning in warnings)
    articles = [
        source
        for source in service.repo.sources(workspace["id"])
        if source["url"] == "https://news.example/story/amp"
    ]
    assert len(articles) == 1
    assert set(articles[0]["metadata"]["research_topics"]) == {
        "profile",
        "products_markets",
        "scale_operations",
        "technology_context",
        "recent_relevant_news",
    }
    assert fetched.count("https://news.example/story") == 1


def test_collector_article_metadata_survives_into_evaluator_news_metrics(tmp_path, monkeypatch):
    service = BriefingService(tmp_path)
    workspace = service.repo.create("Acme", "https://acme.example", "alice@acme.example")

    def fetch(url):
        if url == workspace["website"]:
            return {
                "title": "Acme",
                "content": "Acme home",
                "url": url,
                "source_type": "web",
                "metadata": {"links": []},
            }
        return {
            "title": "Acme AI automation news",
            "content": "Published news: Acme launched an AI automation program.",
            "url": url,
            "source_type": "web",
            "published_at": "2026-10-14",
            "metadata": {
                "page_fetched": True,
                "schema_types": ["NewsArticle"],
                "links": [],
            },
        }

    monkeypatch.setattr("pu.reporting.collection.fetch_source", fetch)
    monkeypatch.setattr(
        "pu.reporting.collection.search_results",
        lambda query, max_results: [
            {
                "title": "Acme AI news",
                "content": "snippet",
                "url": "https://acme.example/news/ai",
                "source_type": "web",
                "metadata": {"snippet_only": True},
            }
        ],
    )
    collect_partner_sources(
        service.repo,
        workspace["id"],
        config=CollectionConfig(max_search_queries=1, max_detail_pages=0),
        research_context="AI automation",
    )
    stored = next(
        source
        for source in service.repo.sources(workspace["id"])
        if source["url"] == "https://acme.example/news/ai"
    )
    assert stored["metadata"]["content_kind"] == "news"
    report_source = ReportSource(
        id="W1",
        stage="workspace",
        source_type=stored["source_type"],
        title=stored["title"],
        excerpt=stored["content"],
        url=stored["url"],
        domain="acme.example",
        published_at=stored["published_at"],
        external=True,
        metadata=stored["metadata"],
    )
    report = StructuredReport(
        report_id="report-contract",
        workflow_id="workflow-contract",
        title="Acme",
        executive_summary="Acme launched an AI automation program.",
        sections={
            "company": "Acme",
            "contacts": "Chưa xác định",
            "email_need": "AI automation",
            "meeting_context": "Prepare a discussion",
            "public_research": "AI automation",
            "risks_opportunities": "Validate scope",
        },
        claims=[
            ReportClaim(
                id="C1",
                text="Acme launched an AI automation program.",
                section="recent_updates",
                source_ids=("W1",),
            )
        ],
        sources=[report_source],
        generated_at="2026-10-16T00:00:00+00:00",
        schema_version=2,
        metadata={"opportunity_topics": ["recent_relevant_news"]},
    )
    evaluation = DeterministicReportEvaluator().evaluate(report)
    assert evaluation.metrics.public_news_known_sources == 1
    assert evaluation.metrics.public_news_relevant_sources == 1

