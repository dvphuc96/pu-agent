"""Dependency-free Word export of an immutable report snapshot."""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from pu.reporting.citations import (
    build_citation_presentation,
    canonical_public_url,
    collect_citation_ids,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("w", W)
ET.register_namespace("r", R)


def render_docx(payload: dict) -> bytes:
    root = ET.Element(f"{{{W}}}document")
    body = ET.SubElement(root, f"{{{W}}}body")
    hyperlink_relationships: list[tuple[str, str]] = []
    legacy_schema = int(payload.get("schema_version", 1) or 1) < 2

    def paragraph(text: str, style: str = ""):
        p = ET.SubElement(body, f"{{{W}}}p")
        if style:
            props = ET.SubElement(p, f"{{{W}}}pPr")
            ET.SubElement(props, f"{{{W}}}pStyle", {f"{{{W}}}val": style})
        run = ET.SubElement(p, f"{{{W}}}r")
        node = ET.SubElement(
            run, f"{{{W}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}
        )
        node.text = text

    def hyperlink_paragraph(text: str, url: str):
        """Add a clickable external hyperlink using a standard OOXML rel."""
        rid = f"rId{len(hyperlink_relationships) + 2}"
        hyperlink_relationships.append((rid, url))
        p = ET.SubElement(body, f"{{{W}}}p")
        hyperlink = ET.SubElement(p, f"{{{W}}}hyperlink", {f"{{{R}}}id": rid})
        run = ET.SubElement(hyperlink, f"{{{W}}}r")
        props = ET.SubElement(run, f"{{{W}}}rPr")
        ET.SubElement(props, f"{{{W}}}color", {f"{{{W}}}val": "0563C1"})
        ET.SubElement(props, f"{{{W}}}u", {f"{{{W}}}val": "single"})
        node = ET.SubElement(
            run, f"{{{W}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"}
        )
        node.text = text

    def cited_paragraph(text: str, style: str = ""):
        """Write report prose with the same numbered/linkable citations."""
        presentation = citation_presentation
        p = ET.SubElement(body, f"{{{W}}}p")
        if style:
            props = ET.SubElement(p, f"{{{W}}}pPr")
            ET.SubElement(props, f"{{{W}}}pStyle", {f"{{{W}}}val": style})
        cursor = 0
        for match in re.finditer(r"\[([A-Za-z][A-Za-z0-9_-]*)\]", str(text or "")):
            if match.start() > cursor:
                run = ET.SubElement(p, f"{{{W}}}r")
                node = ET.SubElement(run, f"{{{W}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                node.text = str(text)[cursor:match.start()]
            source_id = match.group(1)
            item = presentation.get(source_id)
            if not item:
                label, url, anchor = "[Nguồn chưa xác minh]", None, None
            else:
                label = (
                    f"[{source_id}]" if legacy_schema
                    else (f"[{item.get('number')}]" if item.get("number") is not None else f"[{item.get('label') or 'Nguồn chưa xác minh'}]")
                )
                url = item.get("url") if item.get("clickable") else None
                anchor = item.get("anchor")
            if url and anchor:
                rid = f"rId{len(hyperlink_relationships) + 2}"
                hyperlink_relationships.append((rid, str(url)))
                hyperlink = ET.SubElement(p, f"{{{W}}}hyperlink", {f"{{{R}}}id": rid})
                run = ET.SubElement(hyperlink, f"{{{W}}}r")
                props = ET.SubElement(run, f"{{{W}}}rPr")
                ET.SubElement(props, f"{{{W}}}color", {f"{{{W}}}val": "0563C1"})
                ET.SubElement(props, f"{{{W}}}u", {f"{{{W}}}val": "single"})
                node = ET.SubElement(run, f"{{{W}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                node.text = label
            else:
                run = ET.SubElement(p, f"{{{W}}}r")
                node = ET.SubElement(run, f"{{{W}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
                node.text = label
            cursor = match.end()
        if cursor < len(str(text or "")):
            run = ET.SubElement(p, f"{{{W}}}r")
            node = ET.SubElement(run, f"{{{W}}}t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
            node.text = str(text)[cursor:]

    citation_presentation = build_citation_presentation(payload)
    cited_ids = collect_citation_ids(payload)
    paragraph(payload.get("title", "Báo cáo nghiên cứu"), "Title")
    paragraph("Tóm tắt", "Heading1")
    cited_paragraph(payload.get("executive_summary", ""))
    labels = {
        "company": "Giới thiệu doanh nghiệp",
        "contacts": "Người liên hệ",
        "email_need": "Nhu cầu từ email",
        "meeting_context": "Bối cảnh cuộc họp",
        "public_research": "Lĩnh vực và sản phẩm",
        "recent_updates": "Tin tức gần đây",
        "risks_opportunities": "Điểm cần lưu ý",
        "company_scale": "Quy mô và vận hành",
        "business_context": "Bối cảnh kinh doanh và công nghệ",
        "meeting_preparation": "Chuẩn bị cuộc gặp",
    }
    for key, text in payload.get("sections", {}).items():
        paragraph(labels.get(key, key), "Heading1")
        for line in str(text).splitlines():
            cited_paragraph(line)
    paragraph("Kết luận và dẫn chứng", "Heading1")
    for claim in payload.get("claims", []):
        cited_paragraph(claim.get("text", "") + " " + " ".join(f"[{s}]" for s in claim.get("source_ids", [])))
    if payload.get("missing_information"):
        paragraph("Thông tin còn thiếu", "Heading1")
        for item in payload["missing_information"]:
            cited_paragraph("• " + item)
    coverage = (payload.get("metadata") or {}).get("information_coverage", [])
    if coverage:
        paragraph("Mức độ bao phủ thông tin", "Heading1")
        status_labels = {
            "supported": "đã có nguồn",
            "not_found": "chưa tìm thấy",
            "conflicting": "nguồn mâu thuẫn",
            "not_applicable": "ngoài phạm vi",
        }
        field_labels = {
            "website": "Website", "sector": "Lĩnh vực", "products": "Sản phẩm/dịch vụ",
            "scale": "Quy mô", "markets": "Thị trường", "digital_context": "Bối cảnh số",
            "contact": "Người liên hệ", "request": "Nhu cầu từ email",
            "meeting_preparation": "Chuẩn bị cuộc họp", "calendar": "Lịch",
        }
        for item in coverage:
            if not isinstance(item, dict):
                continue
            status = status_labels.get(str(item.get("status")), "chưa đánh giá")
            detail = str(item.get("summary") or item.get("reason") or "")
            field = field_labels.get(str(item.get("field") or ""), str(item.get("field") or ""))
            cited_paragraph(f"• {field}: {status}" + (f" — {detail}" if detail else ""))
    metadata = payload.get("metadata") or {}
    availability = metadata.get("availability") if isinstance(metadata.get("availability"), dict) else {}
    slots = metadata.get("calendar_free_slots") or []
    if slots:
        checked_at = availability.get("checked_at") or metadata.get("calendar_checked_at") or "chưa lưu thời điểm kiểm tra"
        timezone = availability.get("timezone") or metadata.get("calendar_timezone") or "chưa xác định"
        scope = availability.get("scope") or metadata.get("calendar_scope") or "lịch local; chưa biết lịch đối tác"
        sources = availability.get("checked_sources") or metadata.get("calendar_checked_sources") or []
        source_note = ", ".join(str(value) for value in sources if value) or "chưa xác định nguồn lịch"
        paragraph("Lịch trống (ảnh chụp tại thời điểm báo cáo)", "Heading1")
        paragraph(f"Đã kiểm tra lúc {checked_at} · Múi giờ: {timezone} · Phạm vi: {scope} · Nguồn: {source_note}")
        for item in slots:
            if isinstance(item, dict) and item.get("windows"):
                label = item.get("label") or item.get("date") or ""
                paragraph(f"• {label}: {', '.join(str(window) for window in item['windows'])}")
    paragraph("Nguồn", "Heading1")
    public_count = 0
    seen_public: set[str] = set()
    for source in payload.get("sources", []):
        if source.get("derived"):
            continue
        source_id = str(source.get("id") or "")
        is_public = bool(source.get("url") and canonical_public_url(source.get("url")))
        canonical = canonical_public_url(source.get("url")) if is_public else None
        if canonical and canonical in seen_public:
            continue
        if is_public:
            public_count += 1
            if public_count > 8 and source_id not in cited_ids:
                continue
            if canonical:
                seen_public.add(canonical)
        elif source_id not in cited_ids:
            continue
        title = str(source.get("title") or source.get("url") or source.get("source_type", "Nguồn"))
        url = str(source.get("url") or "")
        item = citation_presentation.get(source_id) or {}
        if legacy_schema:
            prefix = f"[{source_id}] "
        else:
            prefix = f"[{item['number']}] " if item.get("number") is not None else f"[{item.get('label') or 'Tài liệu nội bộ'}] "
        if is_public and canonical:
            hyperlink_paragraph(prefix + title, url)
        else:
            paragraph(prefix + title)
    section = ET.SubElement(body, f"{{{W}}}sectPr")
    ET.SubElement(section, f"{{{W}}}pgSz", {f"{{{W}}}w": "11906", f"{{{W}}}h": "16838"})
    ET.SubElement(
        section,
        f"{{{W}}}pgMar",
        {f"{{{W}}}{k}": "1440" for k in ("top", "right", "bottom", "left")},
    )
    types = """<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
    hyperlink_xml = "".join(
        f'<Relationship Id="{xml_escape(rid)}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{xml_escape(url, {chr(34): "&quot;"})}" TargetMode="External"/>'
        for rid, url in hyperlink_relationships
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        f"{hyperlink_xml}</Relationships>"
    )
    styles = f'''<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160"/></w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:sz w:val="40"/><w:b/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:keepNext/><w:spacing w:before="240"/></w:pPr><w:rPr><w:sz w:val="28"/><w:b/></w:rPr></w:style></w:styles>'''
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr(
            "word/document.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True)
        )
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
    return stream.getvalue()
