"""Bounded automatic evidence collection for one sender-domain workspace."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

from pu.reporting.availability import partner_availability
from pu.reporting.identity import source_relation
from pu.reporting.research_plan import (
    DEFAULT_TOPICS,
    allowlisted_opportunity_terms,
    build_research_plan,
    public_context_from_sources,
    topic_for_path,
)
from pu.reporting.source_contract import classify_public_source
from pu.tools.search import search_results
from pu.tools.web_source import fetch_source


@dataclass(frozen=True)
class CollectionConfig:
    """Hard bounds for one public research pass.

    Values are intentionally conservative because this function runs from the
    automatic inbox worker. Callers can supply a config for offline fixtures or
    a more constrained deployment without changing the old function contract.
    ``max_duration_seconds`` is checked between external calls; it cannot
    interrupt a web provider call that is already in flight.
    """

    max_search_queries: int = 5
    max_results_per_query: int = 4
    max_detail_pages: int = 6
    max_web_pages: int = 12
    max_excerpt_chars: int = 16000
    recent_window_days: int = 30
    max_read_attempts: int = 12
    max_duration_seconds: float = 45.0

    def __post_init__(self):
        for name in ("max_search_queries", "max_detail_pages"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} không được âm.")
        for name in (
            "max_results_per_query",
            "max_web_pages",
            "max_read_attempts",
            "max_excerpt_chars",
            "recent_window_days",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} phải lớn hơn 0.")
        if self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds phải lớn hơn 0.")


_TRACKING_PARAMS = {"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "source"}


def canonical_url(value: str) -> str:
    """Normalize a public URL for source deduplication without fetching it."""

    try:
        parsed = urlsplit((value or "").strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        netloc = host
        if port and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
            netloc += f":{port}"
        path = re.sub(r"/{2,}", "/", parsed.path or "/")
        if path != "/":
            path = path.rstrip("/")
        query = urlencode(
            sorted(
                (key, val)
                for key, val in parse_qsl(parsed.query)
                if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
            )
        )
        return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))
    except ValueError:
        return ""


def _targeted_excerpt(content: str, keywords: tuple[str, ...], limit: int) -> tuple[str, bool]:
    """Keep relevant windows from long pages instead of only their header."""

    text = (content or "").strip()
    if len(text) <= limit:
        return text, False
    folded = text.casefold()
    points = []
    for keyword in keywords:
        start = 0
        while len(points) < 12:
            index = folded.find(keyword.casefold(), start)
            if index < 0:
                break
            points.append(index)
            start = index + max(1, len(keyword))
    points = sorted(set(points))
    if not points:
        return text[:limit], True
    window = max(500, limit // max(1, min(6, len(points))))
    chunks = []
    used = 0
    for point in points:
        left = max(0, point - window // 3)
        right = min(len(text), left + window)
        chunk = text[left:right].strip()
        if chunk and chunk not in chunks:
            chunks.append(chunk)
            used += len(chunk) + 2
        if used >= limit:
            break
    excerpt = "\n…\n".join(chunks)
    return excerpt[:limit], True


def _published_at(item: dict) -> str | None:
    value = item.get("published_at")
    if not value:
        value = (item.get("metadata") or {}).get("published_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def collect_partner_sources(
    repo,
    workspace_id,
    *,
    progress=lambda stage: None,
    app=None,
    calendar_snapshot=None,
    config: CollectionConfig | None = None,
    research_context: object = None,
):
    config = config or CollectionConfig()
    workspace = repo.require(workspace_id)
    domain = (urlparse(workspace["website"]).hostname or "").lower().removeprefix("www.")
    context = research_context
    if context is None:
        context = public_context_from_sources(repo.sources(workspace_id))
    opportunity_terms = allowlisted_opportunity_terms(context)
    warnings = []
    collected: dict[str, dict] = {}
    fetched: set[str] = set()
    resolved_urls: dict[str, str] = {}
    accepted_urls: set[str] = set()
    rejected_urls: set[str] = set()
    detail_pages = 0
    web_pages = 0
    observed_at = datetime.now(UTC).isoformat()
    started_perf = time.perf_counter()
    search_attempts = 0
    read_attempts = 0
    successful_reads = 0
    deadline = started_perf + config.max_duration_seconds
    timed_out = False

    def budget_exhausted() -> bool:
        nonlocal timed_out
        if time.perf_counter() < deadline:
            return False
        timed_out = True
        return True

    def execute(tool, arguments):
        from pu.core.interfaces.tools import ToolContext

        result = app.tool_harness.execute(
            tool,
            arguments,
            ToolContext(
                principal="local-user",
                actor="automatic-briefing",
                agent_id="web-research-agent",
                scopes=frozenset({"web:read"}),
                capabilities=frozenset({"web:read"}),
                observer=app.event_publisher.publish,
                metadata={"workspace_id": workspace_id, "untrusted_input": True},
            ),
        )
        if not result.ok:
            raise ValueError("Nguồn web không khả dụng qua tool harness.")
        return json.loads(result.output)

    def read_page(url):
        return execute("read_web_page", {"url": url}) if app else fetch_source(url)

    def search_pages(query):
        if not app:
            return search_results(query, config.max_results_per_query)
        return [
            {
                "title": item.get("title", "Kết quả tìm kiếm"),
                "content": item.get("content", ""),
                "url": item.get("url", ""),
                "source_type": item.get("source_type", "web"),
                "published_at": item.get("published_at"),
                "metadata": {
                    **(item.get("metadata") or {}),
                    "snippet_only": True,
                    "untrusted_input": True,
                },
            }
            for item in execute(
                "search_web", {"query": query, "max_results": config.max_results_per_query}
            )["results"]
        ]

    def add_candidate(item, topics, *, snippet_only=False, relation=None):
        nonlocal web_pages
        item = classify_public_source(item, opportunity_terms=opportunity_terms)
        url = canonical_url(item.get("url", ""))
        content = item.get("content") or item.get("excerpt") or ""
        if not url or not content:
            return
        metadata = dict(item.get("metadata") or {})
        metadata.update(
            {
                "research_topics": sorted(
                    set(metadata.get("research_topics", [])) | set(topics)
                ),
                "snippet_only": bool(snippet_only),
                "observed_at": observed_at,
                "untrusted_input": True,
            }
        )
        if relation:
            metadata["identity_relation"] = relation
        keywords = tuple(
            keyword
            for topic in topics
            for item_topic in DEFAULT_TOPICS
            if item_topic.key == topic
            for keyword in item_topic.keywords
        )
        excerpt, truncated = _targeted_excerpt(content, keywords, config.max_excerpt_chars)
        metadata["excerpt_strategy"] = "targeted" if truncated else "full"
        metadata["content_truncated"] = truncated
        if url in collected:
            existing = collected[url]
            all_topics = set(existing["metadata"].get("research_topics", [])) | set(topics)
            existing["metadata"]["research_topics"] = sorted(all_topics)
            full_content = existing.get("_full_content", "")
            if full_content and not snippet_only:
                all_keywords = tuple(
                    keyword
                    for item_topic in DEFAULT_TOPICS
                    if item_topic.key in all_topics
                    for keyword in item_topic.keywords
                )
                existing["content"], existing["metadata"]["content_truncated"] = _targeted_excerpt(
                    full_content, all_keywords, config.max_excerpt_chars
                )
                existing["metadata"]["excerpt_strategy"] = (
                    "targeted" if existing["metadata"]["content_truncated"] else "full"
                )
            if existing["metadata"].get("snippet_only") and not snippet_only:
                collected[url] = {
                    **item,
                    "url": url,
                    "content": excerpt,
                    "_full_content": content,
                    "published_at": _published_at(item) or existing.get("published_at"),
                    "metadata": {**existing["metadata"], **metadata, "snippet_only": False},
                    "source_type": item.get("source_type", existing.get("source_type", "web")),
                }
            return
        collected[url] = {
            **item,
            "url": url,
            "content": excerpt,
            "_full_content": content,
            "published_at": _published_at(item),
            "metadata": metadata,
            "source_type": item.get("source_type", "web"),
        }
        web_pages += 1

    def merge_topics(source_key, topics):
        source = collected.get(source_key)
        if not source:
            return
        all_topics = set(source["metadata"].get("research_topics", [])) | set(topics)
        source["metadata"]["research_topics"] = sorted(all_topics)
        full_content = source.get("_full_content", "")
        if not full_content:
            return
        keywords = tuple(
            keyword
            for item_topic in DEFAULT_TOPICS
            if item_topic.key in all_topics
            for keyword in item_topic.keywords
        )
        source["content"], source["metadata"]["content_truncated"] = _targeted_excerpt(
            full_content, keywords, config.max_excerpt_chars
        )
        source["metadata"]["excerpt_strategy"] = (
            "targeted" if source["metadata"]["content_truncated"] else "full"
        )

    def read_public_page(url, topics, *, fallback=None, topic_budget=None, topic_budgets=None):
        nonlocal detail_pages, read_attempts, successful_reads
        key = canonical_url(url)
        if not key:
            return
        if key in collected:
            fetched.add(key)
            resolved_urls[key] = key
            merge_topics(key, topics)
            return collected[key]
        if key in fetched:
            final_key = resolved_urls.get(key, key)
            return collected.get(final_key)
        if (
            web_pages >= config.max_web_pages
            or read_attempts >= config.max_read_attempts
            or (
                topic_budget is not None
                and (topic_budgets or {}).get(topic_budget, 0) <= 0
            )
            or budget_exhausted()
        ):
            return
        fetched.add(key)
        read_attempts += 1
        if topic_budget is not None and topic_budgets is not None:
            topic_budgets[topic_budget] = max(0, topic_budgets.get(topic_budget, 0) - 1)
        try:
            page = read_page(url)
            page["url"] = page.get("url") or url
            if not _published_at(page) and fallback and _published_at(fallback):
                page["published_at"] = _published_at(fallback)
            final_key = canonical_url(page["url"]) or key
            resolved_urls[key] = final_key
            relation = source_relation(page, domain)
            if relation == "unverified_entity":
                return None
            add_candidate(page, topics, snippet_only=False, relation=relation)
            accepted_urls.add(key)
            accepted_urls.add(final_key)
            detail_pages += 1
            successful_reads += 1
            return page
        except Exception:
            if fallback and fallback.get("content") and source_relation(fallback, domain) != "unverified_entity":
                add_candidate(fallback, topics, snippet_only=True, relation=source_relation(fallback, domain))
                accepted_urls.add(key)
                resolved_urls[key] = canonical_url(fallback.get("url")) or key
                accepted_urls.add(resolved_urls[key])
            return None

    progress("website")
    if workspace["website"]:
        try:
            page = read_public_page(workspace["website"], ("profile",))
            if page is None:
                raise ValueError("website unavailable")
            if not isinstance(page.get("metadata"), dict):
                page["metadata"] = {}
            page["metadata"]["identity"] = "Website ứng viên của công ty được nêu trong email; không xác nhận quyền đại diện của người gửi."
            root_key = canonical_url(page.get("url", workspace["website"]))
            if root_key in collected:
                collected[root_key]["metadata"]["identity"] = page["metadata"]["identity"]
            # Follow actual, same-domain links, rather than guessing about/product URLs.
            links = [
                (canonical_url(link), topic_for_path(urlparse(link).path))
                for link in page["metadata"].get("links", [])
                if (urlparse(link).hostname or "").lower().removeprefix("www.") == domain
                and any(word in urlparse(link).path.lower() for word in (
                    "about", "gioi-thieu", "product", "service", "san-pham", "dich-vu",
                    "company", "market", "investor", "annual", "technology", "digital",
                    "operation", "factory", "news", "press", "location",
                ))
                and canonical_url(link)
            ]
            # Round-robin topic labels so a homepage with many product links
            # cannot consume the whole detail-page budget before scale or
            # technology pages are considered.
            ordered_links = []
            seen_links = set()
            for topic in (item.key for item in DEFAULT_TOPICS):
                candidate = next(
                    (
                        (link, topics)
                        for link, topics in links
                        if link not in seen_links and topic in topics
                    ),
                    None,
                )
                if candidate:
                    ordered_links.append(candidate)
                    seen_links.add(candidate[0])
            ordered_links.extend((link, topics) for link, topics in links if link not in seen_links)
            for link, topics in ordered_links[: config.max_detail_pages]:
                if detail_pages >= config.max_web_pages:
                    break
                if budget_exhausted():
                    break
                detail = read_public_page(link, topics)
                if detail is None:
                    warnings.append("Không đọc được một trang công ty có liên quan.")
        except Exception:
            warnings.append(
                "Chưa đọc được website ứng viên của công ty; thông tin công khai còn thiếu bằng chứng."
            )
    progress("news")
    # Do not send private email contents/contact addresses to search providers.
    unrelated = 0
    plan = list(build_research_plan(workspace["company"], domain, context=context))
    priority = {
        "recent_relevant_news": 0,
        "technology_context": 1,
        "scale_operations": 2,
        "products_markets": 3,
        "profile": 4,
    }
    plan.sort(key=lambda pair: priority.get(pair[0].key, 99))
    plan = plan[: config.max_search_queries]
    available_reads = min(max(0, config.max_read_attempts - read_attempts),
                          max(0, config.max_web_pages - web_pages))
    topic_budgets = {topic.key: 0 for topic, _ in plan}
    # Give each selected topic one chance first, with recent news and digital
    # context at the front of the queue. Any spare capacity follows that order.
    for topic, _query in plan:
        if available_reads <= 0:
            break
        topic_budgets[topic.key] += 1
        available_reads -= 1
    index = 0
    while available_reads > 0 and plan:
        topic = plan[index % len(plan)][0]
        topic_budgets[topic.key] += 1
        available_reads -= 1
        index += 1
    all_results = []
    for topic, query in plan:
        if topic_budgets.get(topic.key, 0) <= 0:
            continue
        if read_attempts >= config.max_read_attempts or web_pages >= config.max_web_pages:
            break
        if budget_exhausted():
            break
        search_attempts += 1
        if topic.key == "recent_relevant_news":
            query += f" last {config.recent_window_days} days"
        try:
            results = search_pages(query)
        except Exception:
            warnings.append(f"Tìm kiếm nhóm {topic.key} chưa khả dụng; tiếp tục các nhóm còn lại.")
            continue
        if not isinstance(results, list):
            results = []
        all_results.extend((topic, item) for item in results if isinstance(item, dict))
        for item in results:
            if budget_exhausted():
                break
            if not isinstance(item, dict):
                continue
            item["metadata"] = {**(item.get("metadata") or {}), "research_topics": [topic.key]}
            if not item.get("url"):
                continue
            item_key = canonical_url(item["url"])
            if not item_key or item_key in rejected_urls:
                continue
            relation_hint = source_relation(item, domain)
            page = read_public_page(
                item["url"],
                (topic.key,),
                fallback=item,
                topic_budget=topic.key,
                topic_budgets=topic_budgets,
            )
            if page is not None:
                relation = source_relation(page, domain)
                if relation == "unverified_entity":
                    unrelated += 1
                    rejected_urls.add(item_key)
                    collected.pop(item_key, None)
                else:
                    final_key = resolved_urls.get(item_key, canonical_url(page.get("url")) or item_key)
                    if final_key in collected:
                        collected[final_key]["metadata"]["identity_relation"] = relation
                        merge_topics(final_key, (topic.key,))
            elif relation_hint == "unverified_entity":
                # A previously fetched URL may be returned for several
                # topics; its first relation decision is authoritative.
                if item_key in accepted_urls:
                    final_key = resolved_urls.get(item_key, item_key)
                    if final_key in collected:
                        merge_topics(final_key, (topic.key,))
                elif item_key not in rejected_urls:
                    unrelated += 1
                    rejected_urls.add(item_key)
                    collected.pop(item_key, None)
            elif item_key in collected:
                merge_topics(item_key, (topic.key,))
        if budget_exhausted():
            break
    if unrelated:
        warnings.append(f"Đã loại {unrelated} kết quả chưa chứng minh liên hệ với miền {domain}; không gộp công ty chỉ vì tên giống nhau.")
    if not all_results and plan and search_attempts == 0 and not timed_out:
        warnings.append("Không còn ngân sách đọc trang để bắt đầu tìm kiếm; giữ lại các nguồn đã đọc.")
    elif not all_results:
        warnings.append("Không tìm thấy kết quả tin tức hoặc nghiên cứu; không suy ra rằng công ty không có cập nhật.")
    if timed_out:
        warnings.append("Đã dừng research khi hết ngân sách thời gian; các nhóm còn lại cần bổ sung ở lần chạy sau.")
    for source in collected.values():
        source.pop("_full_content", None)
        repo.add_source(workspace_id, **source)
    progress("knowledge")
    from pu.memory.knowledge import search

    with repo.database.connection() as conn:
        hits = search(conn, domain or workspace["company"], top_k=3)
    for hit in hits:
        repo.add_source(
            workspace_id,
            title=hit["title"][:300],
            content=hit["content"][:8000],
            source_type="internal_knowledge",
            metadata={"automatic": True},
        )
    progress("calendar")
    if calendar_snapshot is not None:
        from pu.reporting.availability import filter_partner_events
        availability = filter_partner_events(calendar_snapshot, workspace)
    else:
        availability = partner_availability(repo.home, workspace)
    repo.add_source(
        workspace_id,
        title="Lịch họp liên quan và giờ trống đã kiểm tra",
        source_type="calendar",
        content=json.dumps(availability, ensure_ascii=False),
        metadata={
            "automatic": True,
            "availability": availability,
            "collection_performance": {
                "scope": "source_collection",
                "duration_seconds": round(time.perf_counter() - started_perf, 3),
                "search_attempts": search_attempts,
                "read_attempts": read_attempts,
                "successful_reads": successful_reads,
                "unique_web_sources": len(collected),
                "max_duration_seconds": config.max_duration_seconds,
                "max_read_attempts": config.max_read_attempts,
                "timed_out": timed_out,
            },
        },
    )
    return warnings
