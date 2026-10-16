"""Company identity and source affinity must be checked before report generation."""

import pytest

from pu.reporting.identity import partner_identity, scoped_sources, source_relation
from pu.tools.report_delivery import report_can_release


def test_sender_self_introduction_wins_over_conflicting_signature():
    identity = partner_identity(
        {
            "sender_email": "alice@alpha.example",
            "body_text": "Tôi là Alice, Quản lý Phát triển Đối tác tại Alpha. Chúng tôi cung cấp AI.\n\nChúng tôi mong muốn trao đổi hợp tác.\n\nTrân trọng,\nAlice\nQuản lý Đối tác | Beta",
        },
        "Beta",
        "https://alpha.example",
    )
    assert identity["name"] == "Alpha"
    assert identity["status"] == "conflicting" and identity["conflicts"]


@pytest.mark.parametrize(
    "url,content,expected",
    [
        ("https://alpha.example/products", "AI", "sender_domain"),
        ("https://www.alpha.example", "AI", "sender_domain"),
        (
            "https://news.example/alpha",
            "Visit https://alpha.example for details",
            "mentions_sender_domain",
        ),
        (
            "https://alphatechnology.example",
            "Alpha Technology builds software",
            "unverified_entity",
        ),
        ("https://alpha.example.evil.test", "Alpha", "unverified_entity"),
        ("https://news.example", "The domain not-alpha.example is unrelated", "unverified_entity"),
    ],
)
def test_similar_names_and_domains_cannot_join_companies(url, content, expected):
    assert source_relation({"url": url, "content": content}, "alpha.example") == expected


def test_independent_article_requires_explicit_domain_connection():
    page = {
        "url": "https://news.example/article",
        "metadata": {"links": ["https://alpha.example/about"]},
    }
    assert source_relation(page, "alpha.example") == "links_to_sender_domain"


def test_scoped_evidence_excludes_unrelated_company_without_discarding_email():
    sources = [
        {"id": "e", "source_type": "email", "content": "Alpha contact"},
        {"id": "a", "source_type": "web", "url": "https://alpha.example", "content": "AI"},
        {"id": "b", "source_type": "web", "url": "https://beta.example", "content": "Beta"},
    ]
    accepted, rejected = scoped_sources(sources, "https://alpha.example")
    assert [s["id"] for s in accepted] == ["e", "a"]
    assert [s["id"] for s in rejected] == ["b"]


def test_legacy_auto_report_and_conflicting_identity_cannot_be_distributed():
    old = {"evaluation": {"release_allowed": True}, "metadata": {"require_semantic_review": True}}
    assert not report_can_release(old)
    old["metadata"]["evidence_policy_version"] = "company-boundary-v1"
    assert not report_can_release(old)
    old["metadata"]["quality_policy_version"] = "report-quality-v2"
    old["evaluation"]["quality_policy_version"] = "report-quality-v2"
    assert not report_can_release(old)
    old["evaluation"].update(rubric_version="report-quality-rubric-v3", evaluated=True,
                             status_details={"semantic_review": "passed"})
    assert report_can_release(old)
    old["metadata"]["partner_identity"] = {"status": "conflicting"}
    assert not report_can_release(old)


def test_explicit_company_website_is_distinct_from_sender_domain():
    from pu.reporting.identity import candidate_website

    email = {
        "sender_email": "phuc@agency.example",
        "body_text": "Tôi là Minh Anh, Quản lý Đối tác tại Vinamilk.\n\nWebsite công ty: https://www.vinamilk.com.vn/\n\nTrân trọng,\nMinh Anh\nQuản lý | Vinamilk",
    }
    assert candidate_website(email) == "https://www.vinamilk.com.vn"
    identity = partner_identity(email, "Vinamilk")
    assert identity["research_domain"] == "vinamilk.com.vn"
    assert identity["sender_domain"] == "agency.example"
    assert identity["status"] == "candidate" and identity["affiliation_warnings"]
