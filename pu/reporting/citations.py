"""One deterministic citation presentation contract for every report export."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_CITATION_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9_-]*)\]")
_TRACKING_PREFIXES = ("utm_", "gclid", "fbclid")


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _validated_public_url(url: Any) -> str | None:
    """Return the original safe URL for use as an external hyperlink.

    Canonicalisation is deliberately separate: callers need to retain the
    fetched resource URL (including its query order and non-default port),
    while still being able to deduplicate equivalent aliases.
    """

    if not isinstance(url, str) or not url.strip() or any(char.isspace() for char in url.strip()):
        return None
    try:
        original = url.strip()
        parsed = urlsplit(original)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        # Accessing ``port`` validates malformed and out-of-range ports.
        _ = parsed.port
        return original
    except (TypeError, ValueError):
        return None


def canonical_public_url(url: Any) -> str | None:
    """Return a stable http(s) key for URL deduplication only.

    The returned value must not be used as the hyperlink target: it removes
    tracking/fragments and normalises aliases.  Renderers should use the
    validated original URL stored in the citation presentation instead.
    """

    original = _validated_public_url(url)
    if original is None:
        return None
    try:
        parsed = urlsplit(original)
        host = parsed.hostname.casefold().removeprefix("www.")
        port = parsed.port
        if port and not ((parsed.scheme.casefold() == "http" and port == 80) or
                         (parsed.scheme.casefold() == "https" and port == 443)):
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            host += f":{port}"
        path = parsed.path.rstrip("/") or "/"
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith(_TRACKING_PREFIXES)
        ]
        query.sort(key=lambda pair: (pair[0].casefold(), pair[0], pair[1]))
        return urlunsplit((parsed.scheme.casefold(), host, path, urlencode(query), ""))
    except (TypeError, ValueError):
        return None


def _sources(report_or_payload: Any) -> list[Any]:
    sources = _value(report_or_payload, "sources", [])
    return list(sources) if isinstance(sources, (list, tuple)) else []


def collect_citation_ids(report_or_payload: Any) -> set[str]:
    """Collect refs from every human-readable and structured report field."""

    ids: set[str] = set()
    text_parts = [str(_value(report_or_payload, "executive_summary", "") or "")]
    sections = _value(report_or_payload, "sections", {})
    if isinstance(sections, dict):
        text_parts.extend(str(value or "") for value in sections.values())
    missing = _value(report_or_payload, "missing_information", [])
    text_parts.extend(str(value or "") for value in missing if value)
    contradictions = _value(report_or_payload, "contradictions", [])
    for item in contradictions or []:
        text_parts.append(str(_value(item, "description", item) or ""))
        ids.update(str(value) for value in (_value(item, "source_ids", []) or []) if value)
    metadata = _value(report_or_payload, "metadata", {})
    coverage = metadata.get("information_coverage", []) if isinstance(metadata, dict) else []
    for item in coverage or []:
        text_parts.append(str(_value(item, "summary", "") or ""))
        ids.update(str(value) for value in (_value(item, "source_ids", []) or []) if value)
    for text in text_parts:
        ids.update(match.group(1) for match in _CITATION_RE.finditer(text))
    for claim in _value(report_or_payload, "claims", []) or []:
        ids.update(str(value) for value in (_value(claim, "source_ids", []) or []) if value)
        ids.update(str(value) for value in (_value(claim, "invalid_source_ids", []) or []) if value)
        ids.update(match.group(1) for match in _CITATION_RE.finditer(str(_value(claim, "text", "") or "")))
    return ids


def build_citation_presentation(report_or_payload: Any) -> dict[str, dict[str, Any]]:
    """Build serializable source-id → reader citation metadata.

    Public URLs are numbered in first-seen order after canonical URL
    deduplication. Email/calendar/internal sources stay readable by name and
    have no public URL number. Derived or invalid sources never receive a
    clickable target.
    """

    result: dict[str, dict[str, Any]] = {}
    canonical_to_number: dict[str, int] = {}
    canonical_to_anchor: dict[str, str] = {}
    canonical_to_href: dict[str, str] = {}
    next_number = 1
    for source in _sources(report_or_payload):
        source_id = str(_value(source, "id", "") or "")
        if not source_id:
            continue
        derived = bool(_value(source, "derived", False))
        source_type = str(_value(source, "source_type", "") or "").casefold()
        title = str(_value(source, "title", "") or "").strip()
        raw_url = _value(source, "url")
        href = _validated_public_url(raw_url) if not derived else None
        canonical = canonical_public_url(href) if href else None
        if canonical:
            if canonical not in canonical_to_number:
                canonical_to_number[canonical] = next_number
                canonical_to_anchor[canonical] = source_id
                canonical_to_href[canonical] = href
                next_number += 1
            result[source_id] = {
                "number": canonical_to_number[canonical],
                # All aliases point to the first validated resource URL so
                # deduplication never silently rewrites the fetched target.
                "url": canonical_to_href[canonical],
                "label": title or href,
                "anchor": f"src-{canonical_to_anchor[canonical]}",
                "public": True,
                "clickable": True,
            }
            continue
        labels = {
            "email": "Email", "calendar": "Lịch", "memory": "Bộ nhớ",
            "internal_knowledge": "Kiến thức nội bộ",
        }
        invalid_url = bool(raw_url and not href)
        result[source_id] = {
            "number": None,
            "url": None,
            "label": "Nguồn chưa xác minh" if derived or invalid_url else labels.get(source_type, "Tài liệu nội bộ"),
            "anchor": None if derived or invalid_url else f"src-{source_id}",
            "public": False,
            "clickable": not derived and not invalid_url,
        }
    return result

