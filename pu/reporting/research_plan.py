"""Small, deterministic plans for bounded public company research.

The planner deliberately turns an opportunity into a handful of generic public
topics.  It must never put an email body, a contact address, or an internal
identifier into a search query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ResearchTopic:
    key: str
    query_suffix: str
    keywords: tuple[str, ...]


DEFAULT_TOPICS = (
    ResearchTopic(
        "profile",
        "official company profile about giới thiệu công ty",
        ("about", "company", "profile", "giới", "thiệu"),
    ),
    ResearchTopic(
        "products_markets",
        "products services customers markets sản phẩm dịch vụ thị trường",
        ("product", "service", "customer", "market", "solution", "sản", "phẩm", "dịch", "vụ", "thị", "trường"),
    ),
    ResearchTopic(
        "scale_operations",
        "annual report scale operations locations distribution quy mô doanh thu nhà máy phân phối",
        ("annual", "report", "operation", "location", "distribution", "factory", "quy", "mô", "doanh", "thu", "nhà", "máy", "phân", "phối", "vận", "hành"),
    ),
    ResearchTopic(
        "technology_context",
        "digital transformation technology AI data automation chuyển đổi số công nghệ dữ liệu",
        ("digital", "technology", "ai", "data", "automation", "software", "chuyển", "đổi", "số", "công", "nghệ", "dữ", "liệu", "tự", "động"),
    ),
    ResearchTopic(
        "recent_relevant_news",
        "recent news company update tin tức gần đây cập nhật",
        ("news", "update", "recent", "launch", "investment", "tin", "tức", "gần", "đây", "cập", "nhật"),
    ),
)

# These are broad, public opportunity labels.  A raw email body is never
# copied into a query; only these labels can be selected from optional context.
_PUBLIC_CONTEXT_TERMS = {
    "ai", "ocr", "nlp", "api", "data", "digital", "automation", "software",
    "cloud", "integration", "erp", "crm", "analytics", "agent", "training",
    "pilot", "demo", "quotation", "quote", "partnership", "cooperation",
    "manufacturing", "supply", "chain", "retail", "distribution", "customer",
    "sản", "xuất", "chuỗi", "cung", "ứng", "bán", "lẻ", "đào", "tạo",
    "thử", "nghiệm", "hợp", "tác", "giải", "pháp", "công", "nghệ",
}

# Extract meaningful opportunity labels rather than isolated Vietnamese word
# fragments such as ``công`` or ``nghệ``.  The fragments remain in the fixed
# topic queries, but are too broad to establish article relevance.
_PUBLIC_CONTEXT_PHRASES = (
    "chuyển đổi số", "chuỗi cung ứng", "tự động hóa", "giải pháp",
    "công nghệ", "sản xuất", "bán lẻ", "đào tạo", "thử nghiệm", "hợp tác",
    "manufacturing", "supply chain", "retail", "distribution", "customer",
    "partnership", "cooperation", "quotation", "pilot", "training",
    "automation", "software", "digital", "analytics", "integration",
    "cloud", "agent", "ocr", "nlp", "api", "ai", "data", "erp", "crm",
    "demo", "quote",
)


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized = " ".join(str(text).casefold().split())
    normalized_phrase = " ".join(str(phrase).casefold().split())
    if not normalized_phrase:
        return False
    pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    return re.search(rf"(?<!\w){pattern}(?!\w)", normalized, re.UNICODE) is not None


def _safe_context_terms(context: object) -> tuple[str, ...]:
    """Extract a tiny allow-listed vocabulary from public opportunity context."""

    if isinstance(context, dict):
        context = " ".join(str(context.get(key, "")) for key in ("need", "topics", "opportunity"))
    if not isinstance(context, str):
        return ()
    # Remove addresses and URLs before tokenization. This also prevents a
    # caller accidentally passing the full email text from leaking it to a
    # search provider.
    context = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", " ", context)
    context = re.sub(r"https?://\S+", " ", context)
    selected = []
    for phrase in _PUBLIC_CONTEXT_PHRASES:
        if _contains_phrase(context, phrase) and phrase not in selected:
            selected.append(phrase)
        if len(selected) >= 8:
            break
    return tuple(selected)


def _safe_company(company: str, domain: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", " ", str(company or ""))
    value = re.sub(r"https?://\S+", " ", value)
    tokens = [token for token in re.findall(r"[\wÀ-ỹ-]+", value, re.UNICODE) if len(token) <= 60]
    # Always anchor on the verified workspace domain. The bounded name helps
    # with Vietnamese brands while an untrusted long company field cannot turn
    # into a raw email/body search.
    name = " ".join(tokens[:12])[:100].strip()
    return " ".join(part for part in (domain, name) if part).strip()


def public_context_from_sources(sources: object) -> str:
    """Return only allow-listed opportunity labels from already stored emails."""

    if not isinstance(sources, (list, tuple)):
        return ""
    selected = []
    for source in sources:
        if not isinstance(source, dict) or source.get("source_type") != "email":
            continue
        for token in _safe_context_terms(source.get("content", "")):
            if token not in selected:
                selected.append(token)
            if len(selected) >= 8:
                return " ".join(selected)
    return " ".join(selected)


def allowlisted_opportunity_terms(context: object) -> tuple[str, ...]:
    """Expose the same safe vocabulary used to build public search queries."""

    return _safe_context_terms(context)


def build_research_plan(
    company: str,
    domain: str = "",
    *,
    context: object = None,
    topics: tuple[ResearchTopic, ...] = DEFAULT_TOPICS,
) -> tuple[tuple[ResearchTopic, str], ...]:
    """Return bounded ``(topic, query)`` pairs for public web search."""

    safe_domain = (urlparse("https://" + domain).hostname or "").lower()
    anchor = _safe_company(company, safe_domain)
    context_terms = _safe_context_terms(context)
    pairs = []
    for topic in topics:
        suffix = topic.query_suffix
        if topic.key in {"recent_relevant_news", "technology_context"} and context_terms:
            suffix += " " + " ".join(context_terms[:4])
        query = f'"{anchor}" {suffix}'.strip()
        pairs.append((topic, query))
    return tuple(pairs)


def topic_for_path(path: str) -> tuple[str, ...]:
    """Best-effort topic labels for official links discovered on a root page."""

    value = (path or "").casefold()
    matches = []
    for topic in DEFAULT_TOPICS:
        if any(keyword in value for keyword in topic.keywords):
            matches.append(topic.key)
    return tuple(matches or ("profile",))

