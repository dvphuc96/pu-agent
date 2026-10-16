"""Input policy is checked locally before model selection can trigger research."""

import pytest

from pu.orchestration.partner_validation import precheck, subject_signal, validate_request


def email(
    subject="Đề nghị hợp tác AI",
    body="Acme cần hợp tác triển khai AI cho dự án tài liệu.",
    **kwargs,
):
    return {
        "gmail_id": "m1",
        "sender_email": "alice@acme.example",
        "sender_name": "Alice — Acme",
        "subject": subject,
        "body_text": body,
        "snippet": "",
        **kwargs,
    }


@pytest.mark.parametrize(
    "subject",
    [
        "Welcome to MCP Market",
        "You're one step away from selling skills on MCP Market",
        "Re: Fwd: Partnership weekly digest",
        "Bản tin đối tác tuần này",
        "Top 10 AI tools",
        "Weekly updates: AI",
        "Ưu đãi cho đối tác mới",
        "Webinar: cơ hội hợp tác AI",
        "Your invoice from Acme",
        "OTP verification code",
        "Complete your profile",
        "Chào mừng bạn đến Acme",
    ],
)
def test_bulk_and_transactional_subjects_are_excluded_even_with_business_keywords(subject):
    assert precheck(email(subject))[0] == "excluded"


@pytest.mark.parametrize(
    "subject,signal",
    [
        ("Đề nghị hợp tác triển khai AI", "business"),
        ("Request for quotation", "business"),
        ("RE: Đề xuất dự án", "business"),
        ("Xin chào anh Phúc", "neutral"),
        ("Trao đổi tuần sau", "neutral"),
        ("", "neutral"),
    ],
)
def test_positive_or_neutral_subject_is_never_sufficient_proof(subject, signal):
    assert subject_signal(subject)[0] == signal
    assert precheck(email(subject))[0] == "candidate"
    assert validate_request({"company": "Acme"}, [email(subject)])[0] is None


@pytest.mark.parametrize(
    "sender", ["newsletter@acme.example", "no-reply@acme.example", "notifications+ai@acme.example"]
)
def test_automated_sender_cannot_be_promoted_by_subject(sender):
    assert precheck(email(sender_email=sender))[0] == "excluded"


def test_newsletter_body_is_excluded_but_unsubscribe_alone_is_not_proof():
    assert (
        precheck(email(body="Acme please book a demo. View in browser. Unsubscribe."))[0]
        == "excluded"
    )
    assert precheck(email(body="Acme cần hợp tác triển khai AI. Unsubscribe."))[0] == "candidate"


def valid_decision(mail):
    return {
        "company": "Acme",
        "intent": "project",
        "direct_request": True,
        "request_evidence": {"email_id": "m1", "quote": mail["body_text"]},
    }


def test_direct_request_must_be_traceable_to_body_and_company():
    mail = email()
    verified, reason = validate_request(valid_decision(mail), [mail])
    assert not reason and verified["email"] == mail
    assert verified["quote"] == mail["body_text"]


@pytest.mark.parametrize(
    "change",
    [
        {
            "request_evidence": {
                "email_id": "invented",
                "quote": "Acme cần hợp tác triển khai AI cho dự án tài liệu.",
            }
        },
        {"request_evidence": {"email_id": "m1", "quote": "Đề nghị hợp tác AI"}},
        {
            "request_evidence": {
                "email_id": "m1",
                "quote": "Acme cần triển khai chatbot cho 100 người.",
            }
        },
        {"company": "Invented Legal Entity"},
        {"direct_request": False},
        {"intent": "newsletter"},
    ],
)
def test_fabricated_or_insufficient_evidence_is_deferred(change):
    mail = email()
    assert validate_request({**valid_decision(mail), **change}, [mail])[0] is None


def test_company_introduction_without_request_does_not_qualify():
    mail = email(body="Acme cung cấp giải pháp AI cho tài liệu.")
    assert validate_request(valid_decision(mail), [mail])[0] is None


@pytest.mark.parametrize(
    "body,quote",
    [
        (
            "Acme mong muốn sắp xếp một buổi giới thiệu trực tiếp với đội\nngũ phía anh vào tuần tới.",
            "Acme mong muốn sắp xếp một buổi giới thiệu trực tiếp với đội ngũ phía anh vào tuần tới.",
        ),
        ("Acme cần\r\n  hợp tác triển khai AI.", "Acme cần hợp tác triển khai AI."),
        ("Acme cần\u00a0\u00a0hợp tác triển khai AI.", "Acme cần hợp tác triển khai AI."),
    ],
)
def test_layout_changes_do_not_reject_a_real_direct_request(body, quote):
    mail = email(body=body)
    decision = valid_decision(mail)
    decision["request_evidence"]["quote"] = quote
    verified, reason = validate_request(decision, [mail])
    assert not reason
    assert verified["quote"] == body


def test_unicode_composition_difference_preserves_vietnamese_evidence():
    import unicodedata

    body = "Acme cần hợp tác triển khai AI."
    mail = email(body=unicodedata.normalize("NFD", body))
    decision = valid_decision(mail)
    decision["request_evidence"]["quote"] = body
    verified, reason = validate_request(decision, [mail])
    assert not reason and verified["quote"] == body


@pytest.mark.parametrize(
    "quote",
    [
        "Acme cần hợp tác triển khai AI cho 100 nhân viên.",
        "Acme can hop tac trien khai AI.",
        "Acme cần hợp tác. triển khai AI.",
    ],
)
def test_whitespace_tolerance_never_allows_changed_facts_or_removed_accents(quote):
    mail = email(body="Acme cần hợp tác triển khai AI.")
    decision = valid_decision(mail)
    decision["request_evidence"]["quote"] = quote
    assert validate_request(decision, [mail])[0] is None


def test_selecting_source_paragraph_avoids_model_quotation_errors():
    from pu.orchestration.partner_validation import request_passages

    mail = email(
        body="Acme cung cấp giải pháp AI.\n\nChúng tôi mong muốn sắp xếp buổi giới thiệu trực tiếp với đội\nngũ phía anh vào tuần tới."
    )
    passages = request_passages(mail)
    decision = valid_decision(mail)
    decision["request_evidence"] = {"email_id": "m1", "passage_id": passages[1]["id"]}
    verified, reason = validate_request(decision, [mail])
    assert not reason and verified["quote"] == passages[1]["text"]
    assert "\n" in verified["quote"]


def test_invalid_source_paragraph_and_non_request_paragraph_are_rejected():
    mail = email(body="Acme cung cấp giải pháp AI.\n\nChúng tôi cần hợp tác triển khai dự án.")
    for pid in ("p999",):
        decision = valid_decision(mail)
        decision["request_evidence"] = {"email_id": "m1", "passage_id": pid}
        assert validate_request(decision, [mail])[0] is None


def test_context_paragraph_can_resolve_to_actual_request_in_same_email():
    mail = email(
        body="Acme cung cấp giải pháp AI.\n\nChúng tôi mong muốn sắp xếp một buổi trao đổi hợp tác."
    )
    decision = valid_decision(mail)
    decision["request_evidence"] = {"email_id": "m1", "passage_id": "p0"}
    verified, reason = validate_request(decision, [mail])
    assert (
        not reason and verified["quote"] == "Chúng tôi mong muốn sắp xếp một buổi trao đổi hợp tác."
    )
