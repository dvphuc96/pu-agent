"""Bounded text extraction for notebook documents; never executes embedded content."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree


def extract_attachment(filename: str, encoded: str) -> dict:
    filename = PurePath(filename).name
    if not filename or len(filename) > 200 or len(encoded) > 1_400_000:
        raise ValueError("Tài liệu tối đa 1 MB; tên file tối đa 200 ký tự.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Dữ liệu file không hợp lệ.") from exc
    if len(data) > 1_000_000:
        raise ValueError("Tài liệu vượt giới hạn 1 MB.")
    extension = PurePath(filename).suffix.lower()
    if extension in {".txt", ".md", ".markdown"}:
        text = data.decode("utf-8-sig")
    elif extension == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 10_000_000:
                raise ValueError("DOCX vượt giới hạn giải nén.")
            xml = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))
    elif extension == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError(
                "Đọc PDF cần cài extra pu-agent[sources]. Có thể dán nội dung text vào nguồn."
            ) from exc
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError("PDF mã hóa hoặc quá 100 trang chưa được hỗ trợ.")
        parts = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) > 100_000:
                raise ValueError("Nội dung tài liệu vượt 100.000 ký tự.")
        text = "\n\n".join(parts)
    else:
        raise ValueError("Hỗ trợ PDF, DOCX, TXT và Markdown.")
    if not text.strip():
        raise ValueError("Tài liệu chưa có text trích xuất được. PDF scan cần OCR trước.")
    if len(text) > 100_000:
        raise ValueError("Nội dung tài liệu vượt 100.000 ký tự.")
    return {
        "title": filename,
        "content": text,
        "source_type": "document",
        "metadata": {
            "filename": filename,
            "sha256": hashlib.sha256(data).hexdigest(),
            "untrusted_input": True,
        },
    }
