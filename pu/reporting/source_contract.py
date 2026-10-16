"""Shared contract for classifying public evidence.

Search snippets are leads only. A source becomes a news article only after a
real page read provides article metadata or several independent article cues.
This module is deliberately deterministic and does not fetch or inspect
private input.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urlparse

_ARTICLE_PATH = re.compile(
    r"/(?:news|press|article|blog|insights?|media|stories?)(?:/|$)", re.IGNORECASE
)
_ARTICLE_WORDS = re.compile(
    r"\b(?:published|publication|press release|news|article|blog|tin tức|thông cáo|cập nhật)\b",
    re.IGNORECASE,
)
_SCHEMA_ARTICLE_TYPES = {
    "article", "newsarticle", "blogposting", "pressrelease", "reportage",
}
_NON_PUBLIC_TYPES = {"email", "calendar", "memory", "internal_knowledge"}


def _date_value(item: dict) -> datetime | None:
    value = item.get("published_at") or (item.get("metadata") or {}).get("published_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _allowlisted_terms(terms) -> tuple[str, ...]:
    if isinstance(terms, str):
        terms = re.findall(r"[\wÀ-ỹ-]+", terms.casefold(), re.UNICODE)
    if not isinstance(terms, (list, tuple, set)):
        return ()
    result = []
    for term in terms:
        value = str(term).strip().casefold()
        if (
            value
            and len(value) <= 80
            and re.fullmatch(r"[\wÀ-ỹ -]+", value, re.UNICODE)
            and value not in result
        ):
            result.append(value)
    return tuple(result[:16])


def _term_matches(text: str, term: str) -> bool:
    """Match a whole word or phrase, never an accidental substring."""

    normalized_text = " ".join(str(text).casefold().split())
    normalized_term = " ".join(str(term).casefold().split())
    if not normalized_term:
        return False
    pattern = re.escape(normalized_term).replace(r"\ ", r"\s+")
    return re.search(rf"(?<!\w){pattern}(?!\w)", normalized_text, re.UNICODE) is not None


def classify_public_source(item: dict, *, opportunity_terms=()) -> dict:
    """Annotate a source with conservative content and relevance metadata.

    The returned value is a shallow copy. ``relevant_to_opportunity`` is
    ``True`` only when an allow-listed opportunity term occurs in an identified
    article; it is ``None`` for unknown relevance and for non-news pages.
    """

    result = dict(item or {})
    metadata = dict(result.get("metadata") or {})
    source_type = str(result.get("source_type") or "web").casefold()
    content = " ".join(
        str(result.get(key) or "") for key in ("title", "content", "excerpt")
    )
    content_folded = content.casefold()
    url = str(result.get("url") or "")
    parsed = urlparse(url)
    is_snippet = bool(metadata.get("snippet_only"))
    is_real_page = bool(metadata.get("page_fetched")) or not is_snippet
    schema_types = {
        str(value).casefold().replace(" ", "")
        for value in (metadata.get("schema_types") or ())
    }
    og_type = str(metadata.get("og_type") or "").casefold()
    published = _date_value(result)
    article_evidence = {str(value) for value in (metadata.get("article_evidence") or ())}
    has_date = published is not None
    has_schema = bool(schema_types & _SCHEMA_ARTICLE_TYPES)
    has_og = og_type in _SCHEMA_ARTICLE_TYPES or og_type in {"news", "article"}
    has_explicit_news_type = source_type == "news" and bool(metadata.get("page_fetched"))
    has_path = bool(_ARTICLE_PATH.search(parsed.path or ""))
    has_content = bool(_ARTICLE_WORDS.search(content))
    future_published = bool(published and published > datetime.now(UTC))
    article_cues = int(has_date) + int(has_schema) + int(has_og) + int(has_path) + int(has_content)
    # A fetched page needs at least two corroborating cues, or authoritative
    # structured metadata. A search snippet alone can never become news.
    identified_article = bool(
        source_type not in _NON_PUBLIC_TYPES
        and is_real_page
        and not future_published
        and (has_explicit_news_type or has_schema or has_og or (has_date and article_cues >= 2))
        and (not is_snippet)
    )
    metadata["article_evidence"] = sorted(article_evidence | {
        name for name, present in (
            ("date", has_date), ("schema", has_schema), ("og_type", has_og),
            ("path", has_path), ("content", has_content),
            ("source_type", has_explicit_news_type),
        ) if present
    })
    metadata["content_kind"] = "news" if identified_article else (
        source_type if source_type in _NON_PUBLIC_TYPES else "webpage"
    )
    metadata["is_news"] = identified_article
    terms = _allowlisted_terms(opportunity_terms or metadata.get("opportunity_terms") or ())
    if identified_article and terms:
        matched = tuple(term for term in terms if _term_matches(content_folded, term))
        metadata["relevant_to_opportunity"] = bool(matched)
        metadata["opportunity_terms_matched"] = list(matched)
    else:
        metadata["relevant_to_opportunity"] = None
        metadata["opportunity_terms_matched"] = []
    metadata["opportunity_terms"] = list(terms)
    metadata["news_relevance"] = (
        "relevant" if metadata["relevant_to_opportunity"] is True
        else "irrelevant" if metadata["relevant_to_opportunity"] is False
        else "unknown"
    )
    metadata["relevance"] = metadata["news_relevance"]
    metadata["future_published_at"] = future_published
    result["metadata"] = metadata
    return result


def annotate_public_source(item: dict, *, opportunity_terms=()) -> dict:
    """Compatibility alias for callers that prefer an imperative name."""

    return classify_public_source(item, opportunity_terms=opportunity_terms)

