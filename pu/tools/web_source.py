"""Bounded public-page reader for notebook sources, with DNS/redirect checks."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title, self.links = [], [], []
        self.published_at = None
        self.og_type = None
        self.schema_types = set()
        self.hidden = 0
        self.in_title = False
        self.in_jsonld = False
        self.jsonld_parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        normalized = {str(key).casefold(): value for key, value in attrs}
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if tag == "meta":
            key = str(normalized.get("property") or normalized.get("name") or "").casefold()
            value = normalized.get("content")
            if key in {"article:published_time", "datepublished", "pubdate", "date"} and value:
                self.published_at = value
            if key == "og:type" and value:
                self.og_type = value
        if tag == "script" and str(normalized.get("type") or "").casefold() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld_parts = []
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self.in_jsonld:
            self._read_jsonld("".join(self.jsonld_parts))
            self.in_jsonld = False
            self.jsonld_parts = []
        if tag in {"script", "style", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld_parts.append(data)
        text = data.strip()
        if text and not self.hidden:
            self.parts.append(text)
            if self.in_title:
                self.title.append(text)

    def _read_jsonld(self, raw):
        try:
            value = json.loads(raw.strip())
        except (TypeError, ValueError):
            return
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict):
                continue
            types = item.get("@type") or ()
            if isinstance(types, str):
                types = [types]
            self.schema_types.update(str(kind) for kind in types)
            self.published_at = self.published_at or item.get("datePublished") or item.get("dateCreated")


def public_target(url: str) -> tuple:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Chỉ đọc URL http/https công khai, không chứa credential.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise ValueError("Chỉ hỗ trợ cổng web 80/443.")
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Chặn địa chỉ local/private hoặc DNS trỏ vào mạng nội bộ.")
    return parsed, port, addresses[0][4][0]


def fetch_source(url: str) -> dict:
    for _ in range(5):
        parsed, port, address = public_target(url)
        cls = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        conn = cls(parsed.hostname, port, timeout=15)
        # Pin the checked IP; HTTPS still verifies the original hostname/SNI.
        conn._create_connection = (
            lambda _target, timeout=None, source_address=None, checked_address=address, checked_port=port: (
                socket.create_connection(
                    (checked_address, checked_port), timeout=timeout, source_address=source_address
                )
            )
        )
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn.request(
                "GET",
                path,
                headers={
                    "User-Agent": "PU-Agent/0.1 (research source)",
                    "Accept-Encoding": "identity",
                },
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Redirect thiếu URL đích.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Website trả HTTP {response.status}.")
            kind = response.getheader("Content-Type", "").lower()
            if not ("text/html" in kind or "text/plain" in kind):
                raise ValueError("Nguồn website phải là HTML hoặc plain text.")
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError("Trang vượt giới hạn 2 MB.")
            text = body.decode("utf-8", errors="replace")
            title = parsed.hostname
            links, published_at = [], None
            if "html" in kind:
                parser = PageText()
                parser.feed(text)
                title = " ".join(parser.title) or title
                text = "\n".join(parser.parts)
                links = list(dict.fromkeys(urljoin(url, link).split("#")[0] for link in parser.links))[:200]
                published_at = parser.published_at
            if not text.strip():
                raise ValueError(
                    "Không trích xuất được nội dung. Có thể dán nội dung trang vào nguồn."
                )
            page = {
                "title": title[:300],
                "content": text[:100_000],
                "url": url,
                "source_type": "web",
                "published_at": published_at,
                "metadata": {
                    "truncated": len(text) > 100_000,
                    "untrusted_input": True,
                    "links": links,
                    "page_fetched": True,
                    "og_type": parser.og_type if "html" in kind else None,
                    "schema_types": sorted(parser.schema_types) if "html" in kind else [],
                },
            }
            from pu.reporting.source_contract import classify_public_source

            return classify_public_source(page)
        finally:
            conn.close()
    raise ValueError("Website redirect quá nhiều lần.")


def make_tool():
    import json

    from pu.core.interfaces.tools import ToolResult
    from pu.tools.registry import Tool

    def read_web_page(url: str):
        try:
            page = fetch_source(url)
            # Keep the bounded full page for collector-side targeted excerpts;
            # report prompt payloads apply their own smaller excerpt budget.
            page["content"] = page["content"][:100_000]
            return ToolResult(ok=True, output=json.dumps(page, ensure_ascii=False))
        except Exception:
            return ToolResult(ok=False, output="Không đọc được website công khai.", error="web_read_failed")

    return Tool(
        name="read_web_page", description="Read a public website page in full for evidence. Use after search to verify company identity, products and dated news.",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        fn=read_web_page, capability="web:read", required_scope="web:read", side_effect=False, external=True,
    )
