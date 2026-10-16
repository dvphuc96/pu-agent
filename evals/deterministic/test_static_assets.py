"""Cheap contract checks for the no-build dashboard frontend."""

from __future__ import annotations

import re
from pathlib import Path

from pu.integrations import INTEGRATIONS

STATIC = Path(__file__).resolve().parents[2] / "pu" / "ops" / "static"
INDEX = (STATIC / "index.html").read_text()
JS_FILES = sorted((STATIC / "js").glob("*.js"))
JS_SRC = "\n".join(f.read_text() for f in JS_FILES)
CONNECTION_LOGOS = {f"{integration.key}.svg" for integration in INTEGRATIONS}
ALLOWED = {
    "if", "for", "while", "switch", "return", "typeof", "new", "await", "function",
    "Math", "JSON", "Date", "Number", "String", "Boolean", "Object", "Array",
    "parseInt", "parseFloat", "isNaN", "console", "setTimeout", "setInterval",
    "encodeURIComponent", "decodeURIComponent", "alert", "confirm", "prompt",
    "document", "window", "event", "fetch", "location",
}


def test_referenced_assets_exist():
    refs = re.findall(r'(?:src|href)="(/static/[^"]+)"', INDEX)
    assert refs
    for ref in refs:
        assert (STATIC / ref[len("/static/"):]).is_file(), ref


def test_connection_card_logos_match_retained_integrations_exactly():
    logo_dir = STATIC / "logos" / "connections"
    found = {path.name for path in logo_dir.glob("*.svg")}
    assert found == CONNECTION_LOGOS
    for name in found:
        svg = (logo_dir / name).read_text()
        assert svg.startswith("<svg ") and "<title>" in svg


def test_connection_display_groups_match_phase2_product():
    assert 'const CONNECTION_GROUPS = ["Email", "Productivity", "Knowledge", "Research", "Observability"]' in JS_SRC


def test_every_registry_group_has_a_display_name():
    mapped = set(re.findall(r'^\s*"([^"]+)":\s*"[^"]+",\s*$', JS_SRC, re.MULTILINE))
    groups = {i.group for i in INTEGRATIONS}
    assert not (groups - mapped)


def _defined_names() -> set[str]:
    names = set(re.findall(r'^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)', JS_SRC, re.MULTILINE))
    names |= set(re.findall(r'^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', JS_SRC, re.MULTILINE))
    return names


def _handler_calls(text: str) -> set[str]:
    called = set()
    for body in re.findall(r'\bon\w+="([^"]*)"', text) + re.findall(r"\bon\w+='([^']*)'", text):
        called |= set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', body))
    return called


def test_inline_handlers_are_defined():
    missing = {
        n for n in (_handler_calls(INDEX) | _handler_calls(JS_SRC))
        if n not in _defined_names() and n not in ALLOWED
    }
    assert not missing, sorted(missing)


def test_removed_frontend_surfaces_stay_removed():
    assert not (STATIC / "js" / "compare.js").exists()
    assert "/api/voice" not in JS_SRC
    assert "/api/compare" not in JS_SRC
    assert "Telegram" not in JS_SRC
    assert "Notion" not in JS_SRC
