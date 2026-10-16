"""Grounded, distinct content shared by offline inbox/worker integration tests."""
from __future__ import annotations

import json

PUBLIC_PROFILE = (
    "Acme là công ty công nghệ cung cấp dịch vụ AI và phần mềm OCR cho khách hàng doanh nghiệp Việt Nam. "
    "Theo hồ sơ năm 2026, Acme có 120 nhân viên và một trung tâm vận hành. "
    "Công ty phát triển nền tảng tra cứu dữ liệu và tự động hóa quy trình."
)


def grounded_report(prompt: str) -> dict:
    # Repair prompts append diagnostics after EVIDENCE, so decode just the
    # first JSON value rather than accidentally parsing diagnostic content.
    evidence, _ = json.JSONDecoder().raw_decode(prompt.split("EVIDENCE:\n", 1)[1])
    email = next(item for item in evidence if item["source_type"] == "email")
    web = next(item for item in evidence if item["source_type"] == "web")
    calendar = next((item for item in evidence if item["source_type"] == "calendar"), None)
    eid, wid = email["source_id"], web["source_id"]
    facts = {
        "sector": "Acme là công ty công nghệ.",
        "products": "Acme cung cấp dịch vụ AI và phần mềm OCR.",
        "scale": "Theo hồ sơ năm 2026, Acme có 120 nhân viên.",
        "markets": "Acme phục vụ khách hàng doanh nghiệp Việt Nam.",
        "digital_context": "Acme phát triển nền tảng tra cứu dữ liệu và tự động hóa quy trình.",
    }
    sections = {
        "company": f"Acme là công ty công nghệ [{wid}].",
        "contacts": f"Thông tin người gửi được ghi trong email; cần xác nhận quyền đại diện [{eid}].",
        "email_need": f"Nhu cầu AI có trong email: người gửi đề nghị hợp tác triển khai AI [{eid}].",
        "public_research": f"Sản phẩm được công bố gồm dịch vụ AI và phần mềm OCR [{wid}].",
        "company_scale": f"Theo hồ sơ năm 2026, Acme có 120 nhân viên và một trung tâm vận hành [{wid}].",
        "business_context": f"Nền tảng tra cứu dữ liệu và tự động hóa quy trình là bối cảnh công nghệ được công bố [{wid}].",
        "meeting_context": "Đề xuất trao đổi để xác định quy trình thử nghiệm và người phụ trách.",
        "meeting_preparation": "Đề xuất hỏi: Quy trình nào ưu tiên? Dữ liệu do ai quản lý? Đo hiệu quả thế nào? Thời hạn mong muốn? Ai duyệt pilot?",
        "risks_opportunities": "Giả thuyết: thử một quy trình hẹp trước khi đề xuất mở rộng; cần xác nhận phạm vi và điều kiện bảo mật.",
    }
    coverage = [
        {"field": field, "status": "supported", "source_ids": [wid], "summary": text}
        for field, text in facts.items()
    ]
    coverage.extend([
        {"field": "website", "status": "supported", "source_ids": [wid], "summary": "Acme cung cấp dịch vụ AI."},
        {"field": "contact", "status": "supported", "source_ids": [eid], "summary": "Người gửi cần hợp tác AI."},
        {"field": "request", "status": "supported", "source_ids": [eid], "summary": "Cần hợp tác AI."},
        {"field": "meeting_preparation", "status": "supported", "source_ids": [eid], "summary": "Cần hợp tác AI."},
        {"field": "calendar", "status": "supported" if calendar else "not_found",
         "source_ids": [calendar["source_id"]] if calendar else [],
         "summary": "Lịch trống đã kiểm tra." if calendar else "Chưa kiểm tra lịch."},
    ])
    return {
        "schema_version": 2, "title": "Chuẩn bị trao đổi với Acme",
        "executive_summary": f"Acme cung cấp dịch vụ AI và phần mềm OCR [{wid}]. Người gửi đề nghị hợp tác AI [{eid}]. Đề xuất làm rõ phạm vi thử nghiệm.",
        "sections": sections, "information_coverage": coverage,
        "claims": [
            {"text": text, "section": "company", "source_ids": [wid]}
            for text in facts.values()
        ] + [{"text": "Người gửi cần hợp tác AI.", "section": "email_need", "source_ids": [eid]}],
        "missing_information": ["Ngân sách và người quyết định pilot chưa được nêu."],
        "contradictions": [],
    }
