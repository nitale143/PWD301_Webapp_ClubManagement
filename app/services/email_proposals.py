"""Reviewable email drafts created from BDH requests, including saved templates."""

import json
import re
from datetime import timedelta

from flask import current_app

from app import db
from app.mail import send_email
from app.models import EmailProposal, EmailTemplate, User
from .access import approved, board_members as visible_members, is_board, require
from .assistant import AnthropicProvider, _normalize, get_provider
from .common import DomainError, audit
from .email_templates import find_email_template, render_template, system_template
from app.timeutil import utcnow


_EMAIL_COMMAND = re.compile(r"^\s*(?:gửi|gui)\s*(?:mail|email|thư|thu)\s+(?:cho\s+)?(.+)$", re.IGNORECASE)
_EMAIL_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\Z")
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAIL_SCHEMA = {"type": "object", "properties": {
    "subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"]}


def _split_command(message):
    """Tách lệnh "gửi mail cho X: nội dung" thành (người nhận, mục đích)."""
    match = _EMAIL_COMMAND.match(message)
    if not match:
        return None
    rest = match.group(1).strip()
    if ":" in rest:
        name, purpose = rest.split(":", 1)
    else:
        parts = re.split(r"\s+về\s+", rest, maxsplit=1, flags=re.IGNORECASE)
        name, purpose = (parts[0], parts[1]) if len(parts) == 2 else (rest, "")
    return name.strip(), purpose.strip()


def is_email_write_command(message):
    """True nếu câu chat là yêu cầu soạn email (lệnh trực tiếp hoặc dùng mail mẫu)."""
    parsed = _split_command(message)
    if parsed and parsed[1]:
        return True
    if not _is_template_request(message):
        return False
    try:
        return _mentioned_template(message) is not None
    except DomainError:
        return True


def _is_template_request(message):
    """Nhận diện câu dạng "gửi/soạn mail … mẫu …"."""
    text = _normalize(message).strip()
    text = re.sub(r"^ai oi[,!]*\s*", "", text)
    text = re.sub(r"^(?:(?:hay|nho|giup|lam on|toi muon|cho toi|ban)\s+)+", "", text)
    return bool(re.match(r"^(?:gui|soan|viet|dung|tao nhap)\b", text)
                and re.search(r"\b(?:mail|email|thu|mau)\b", text))


def _mentioned_template(message):
    """Mail mẫu đang bật được nhắc tên trong câu (nếu nhiều mẫu khớp thì yêu cầu chọn)."""
    text = " ".join(_normalize(message).split())
    matches = []
    for template in EmailTemplate.query.filter_by(active=True).all():
        hit = re.search(r"(?<!\w)" + re.escape(template.name_key) + r"(?!\w)", text)
        if hit:
            matches.append((template, hit.span()))
    # A specific name may contain a shorter name (e.g. "nhắc quỹ Fall").
    matches = [(template, span) for template, span in matches if not any(
        other.id != template.id and other_span[0] <= span[0] and span[1] <= other_span[1]
        and len(other.name_key) > len(template.name_key)
        for other, other_span in matches)]
    if len(matches) > 1:
        raise DomainError("Yêu cầu nhắc đến nhiều mail mẫu. Hãy chọn một mẫu cho mỗi email.")
    if matches:
        return matches[0][0]
    # Accept short, unmistakable phrasings like "nhắc quỹ" for a template
    # called "Nhắc đóng quỹ". Never guess when several templates qualify.
    words = set(re.findall(r"\w+", text))
    approximate = []
    for template in EmailTemplate.query.filter_by(active=True).all():
        name_words = set(re.findall(r"\w+", template.name_key))
        if len(name_words & words) >= 2 and len(name_words & words) / len(name_words) >= 0.6:
            approximate.append(template)
    if len(approximate) > 1:
        raise DomainError("Có nhiều mail mẫu phù hợp. Hãy nói rõ tên một mẫu.")
    return approximate[0] if approximate else None


def _mentioned_recipient(actor, message):
    """Xác định người nhận từ câu: một địa chỉ email ngoài CLB, hoặc một thành viên theo MSSV/họ
    tên.
    """
    addresses = list(dict.fromkeys(_EMAIL_IN_TEXT.findall(message)))
    if len(addresses) > 1:
        raise DomainError("Hãy chọn một địa chỉ nhận cho mỗi bản nháp email.")
    if addresses:
        text = _normalize(message)
        if any(re.search(r"(?<!\w)" + re.escape(member.mssv) + r"(?!\w)",
                         message, re.IGNORECASE)
               or re.search(r"(?<!\w)" + re.escape(_normalize(member.ho_ten)) + r"(?!\w)",
                            text)
               for member in visible_members(actor)):
            raise DomainError("Yêu cầu có cả email và tên thành viên. Hãy chọn một người nhận.")
        require(is_board(actor),
                "Chỉ BDH được tạo nháp gửi tới email ngoài CLB.")
        return None, _validate_address(addresses[0]), "bạn", addresses[0]

    members = visible_members(actor).all()
    codes = [member for member in members if re.search(
        r"(?<!\w)" + re.escape(member.mssv) + r"(?!\w)", message, re.IGNORECASE)]
    if len(codes) > 1:
        raise DomainError("Yêu cầu nhắc đến nhiều thành viên. Hãy chọn một người nhận.")
    if codes:
        member = codes[0]
    else:
        exact_text = message.casefold()
        exact = [member for member in members if re.search(
            r"(?<!\w)" + re.escape(member.ho_ten.casefold()) + r"(?!\w)", exact_text)]
        normalized_text = _normalize(message)
        normalized_matches = [member for member in members if re.search(
            r"(?<!\w)" + re.escape(_normalize(member.ho_ten)) + r"(?!\w)", normalized_text)]
        accented_exact = [member for member in exact
                          if _normalize(member.ho_ten) != member.ho_ten.casefold()]
        # Accentless spelling must not pick one of several names that normalize alike.
        matches = accented_exact or normalized_matches
        if len(matches) > 1:
            longest = max(len(member.ho_ten) for member in matches)
            longest_matches = [member for member in matches if len(member.ho_ten) == longest]
            if len(longest_matches) == 1 and all(
                _normalize(other.ho_ten) in _normalize(longest_matches[0].ho_ten)
                for other in matches):
                matches = longest_matches
        if len(matches) > 1:
            choices = ", ".join(f"{member.ho_ten} ({member.mssv})" for member in matches[:5])
            raise DomainError(f"Có nhiều người nhận phù hợp: {choices}. Hãy dùng MSSV.")
        member = matches[0] if matches else None
    if member is None:
        return None
    address = _validate_address(member.email)
    return member, address, member.ho_ten, f"{member.ho_ten} ({member.mssv})"


def _draft_from_template(actor, message):
    """Tạo nháp email từ mail mẫu cho người nhận được nhắc trong câu."""
    if not _is_template_request(message):
        return None
    template = _mentioned_template(message)
    if template is None:
        if "mau" not in _normalize(message):
            return None  # Existing free-form email requests still work.
        available = [row.name for row in EmailTemplate.query.filter_by(active=True)
                     .order_by(EmailTemplate.name).limit(20)]
        return {"intent": "email_template_needed", "preview": True, "provider": "rules",
                "answer": "Tôi chưa tạo nháp. Hãy nói tên mail mẫu muốn dùng. "
                + ("Mẫu đang có: " + ", ".join(available) if available else
                   "Chưa có mẫu email nào; Quản trị/Chủ nhiệm/Phó chủ nhiệm có thể thêm mẫu ở trang Kho mẫu.")}
    recipient = _mentioned_recipient(actor, message)
    if recipient is None:
        return {"intent": "email_needs_recipient", "preview": True, "provider": "rules",
                "answer": f"Đã chọn mẫu {template.name}, nhưng chưa xác định được người nhận. "
                "Hãy nói họ tên, MSSV hoặc email người nhận; chưa gửi thư."}
    member, address, display_name, target_label = recipient
    parsed = _split_command(message)
    purpose = parsed[1] if parsed and parsed[1] else ""
    subject, body = render_template(template, display_name,
                                    member.mssv if member else None, address, purpose)
    draft = EmailProposal(recipient_id=member.id if member else None, recipient_email=address,
                          request_text=message, subject=subject, body=body,
                          status="pending", created_by_id=actor.id)
    db.session.add(draft)
    db.session.flush()
    audit(actor, "email_draft_create", draft,
          {"recipient_id": member.id if member else None, "template_id": template.id})
    return {"intent": "email_draft", "preview": True, "provider": "template",
            "answer": f"Đã lấy mẫu '{template.name}' và tạo bản nháp email #{draft.id} cho "
            f"{target_label}. Chưa gửi. BDH hãy kiểm tra nội dung rồi bấm Duyệt và gửi."}


def _recipient(actor, name):
    """Tìm đúng một thành viên theo MSSV hoặc họ tên; trùng tên thì yêu cầu dùng MSSV."""
    members = visible_members(actor).all()
    code_matches = [member for member in members if member.mssv.casefold() == name.casefold()]
    normalized = [member for member in members if _normalize(member.ho_ten) == _normalize(name)]
    exact = [member for member in normalized if member.ho_ten.casefold() == name.casefold()]
    # An accentless name may refer to several distinct members; require MSSV.
    matches = (code_matches or (exact if _normalize(name) != name.casefold() else normalized)
               or normalized)
    if not matches:
        raise DomainError("Không tìm thấy thành viên được duyệt với tên hoặc MSSV này trong phạm vi bạn quản lý.")
    if len(matches) > 1:
        choices = ", ".join(f"{member.ho_ten} ({member.mssv})" for member in matches[:5])
        raise DomainError(f"Có nhiều thành viên trùng tên: {choices}. Hãy dùng MSSV để chọn đúng người.")
    member = matches[0]
    return member


def _validate_address(value):
    """Kiểm tra địa chỉ email người nhận."""
    address = str(value or "").strip()
    if len(address) > 120 or not _EMAIL_ADDRESS.fullmatch(address):
        raise DomainError("Địa chỉ email người nhận không hợp lệ.")
    return address


def _general_text(display_name, purpose):
    """Tiêu đề/nội dung từ mẫu chung `general` trong kho (điền tên + mục đích)."""
    subject, body = system_template("general")
    values = {"{{ten}}": display_name, "{{noi_dung}}": purpose}
    for marker, value in values.items():
        subject, body = subject.replace(marker, value), body.replace(marker, value)
    return subject, body


def _draft_text(display_name, purpose, provider=None, code=None, email=""):
    """Soạn tiêu đề/nội dung email theo thứ tự:

    1. Mẫu email trong kho có tên/từ khóa khớp với mục đích → điền biến, không gọi model.
    2. Không có mẫu phù hợp → lấy mẫu chung `general`; nếu có model AI thì nhờ model
       viết lại theo khung mẫu chung cho đúng mục đích, không có model thì điền thẳng.
    """
    template = find_email_template(purpose)
    if template is not None:
        try:
            subject, body = render_template(template, display_name, code, email, purpose)
            return subject, body, f"template:{template.name}"
        except DomainError:
            pass  # vd mẫu cần MSSV nhưng người nhận ngoài CLB → dùng mẫu chung
    subject, body = _general_text(display_name, purpose)
    general_subject, general_body = system_template("general")
    provider = provider if provider is not None else get_provider()
    if isinstance(provider, AnthropicProvider):
        try:
            response = provider.complete(
                "Bạn soạn bản nháp email tiếng Việt cho CLB. Chỉ dùng mục đích được cung cấp; "
                "không bịa số liệu, sự kiện hay hứa đã gửi thư. Viết theo khung của mẫu chung "
                "(general_template), thay phần {{noi_dung}} bằng nội dung phù hợp mục đích. "
                "Chỉ trả về một JSON object có hai chuỗi subject và body, không markdown.",
                json.dumps({"recipient_name": display_name, "purpose": purpose,
                            "general_template": {"subject": general_subject, "body": general_body}},
                           ensure_ascii=False),
                max_tokens=700,
                response_schema=_EMAIL_SCHEMA,
                purpose="email_draft",
            )
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.strip(), flags=re.IGNORECASE).strip()
            generated = json.loads(cleaned)
            if (isinstance(generated, dict) and isinstance(generated.get("subject"), str)
                    and isinstance(generated.get("body"), str)
                    and generated["subject"].strip() and generated["body"].strip()):
                subject = generated["subject"]
                body = generated["body"]
                provider_name = provider.provider_name
            else:
                provider_name = "rules"
        except (DomainError, ValueError, TypeError):
            provider_name = "rules"
    else:
        provider_name = "rules"
    subject = re.sub(r"[\r\n]+", " ", subject).strip()[:180]
    body = body.strip()[:3000]
    if not subject or not body:
        subject, body = _general_text(display_name, purpose)
    return subject, body, provider_name


def maybe_create_email_draft(actor, message, provider=None):
    """Nếu câu chat là yêu cầu email: tạo EmailProposal trạng thái pending (không gửi) và trả lời
    xem trước.
    """
    parsed = _split_command(message)
    if parsed is None and not _is_template_request(message):
        return None
    require(is_board(actor), "Chỉ BDH được tạo bản nháp email cho thành viên.")
    from_template = _draft_from_template(actor, message)
    if from_template is not None:
        return from_template
    if parsed is None:
        return {"intent": "email_needs_details", "preview": True, "provider": "rules",
                "answer": "Tôi chưa tạo nháp. Hãy cho biết người nhận và nội dung, hoặc nói tên mail mẫu cần dùng."}
    target, purpose = parsed
    if not target:
        raise DomainError("Hãy nhập tên hoặc MSSV của người nhận.")
    external = "@" in target
    if external:
        require(is_board(actor),
                "Chỉ BDH được tạo nháp gửi tới email ngoài CLB.")
        address = _validate_address(target)
        member = None
        display_name = "bạn"
        target_label = address
    else:
        member = _recipient(actor, target)
        address = _validate_address(member.email)
        display_name = member.ho_ten
        target_label = f"{member.ho_ten} ({member.mssv})"
    if not purpose:
        return {"intent": "email_needs_details", "preview": True, "provider": "rules",
                "answer": f"Đã chọn người nhận {target_label}. Tôi chưa gửi email. "
                f"Hãy nhập mục đích, ví dụ: Gửi mail {address}: liên hệ về hoạt động CLB."}
    if not 3 <= len(purpose) <= 1000:
        raise DomainError("Nội dung yêu cầu email phải từ 3 đến 1000 ký tự.")
    subject, body, provider = _draft_text(display_name, purpose, provider=provider,
                                          code=member.mssv if member else None, email=address)
    draft = EmailProposal(recipient_id=member.id if member else None, recipient_email=address,
                          request_text=purpose, subject=subject, body=body,
                          status="pending", created_by_id=actor.id)
    db.session.add(draft)
    db.session.flush()
    audit(actor, "email_draft_create", draft, {"recipient_id": member.id if member else None,
                                               "provider": provider})
    source = (f"theo mẫu '{provider.split(':', 1)[1]}' trong kho" if provider.startswith("template:")
              else "theo mẫu chung" + (" (AI viết lại cho đúng mục đích)" if provider != "rules" else ""))
    return {"intent": "email_draft", "preview": True,
            "provider": "template" if provider.startswith("template:") else provider,
            "answer": f"Đã tạo bản nháp email #{draft.id} cho {target_label} {source}. Chưa gửi. "
            "BDH cần xem nội dung bên dưới và bấm Duyệt và gửi."}


def can_review_email(actor, draft):
    """Người được sửa/duyệt/gửi nháp email (thành viên BĐH)."""
    return is_board(actor)


def edit_email_draft(actor, draft, subject, body):
    """Sửa tiêu đề/nội dung một nháp chưa gửi."""
    require(can_review_email(actor, draft))
    if draft.status != "pending":
        raise DomainError("Chỉ có thể sửa bản nháp chưa gửi.")
    subject = str(subject or "").strip()
    body = str(body or "").strip()
    if not 1 <= len(subject) <= 180 or "\r" in subject or "\n" in subject:
        raise DomainError("Tiêu đề email phải là một dòng, tối đa 180 ký tự.")
    if not 1 <= len(body) <= 3000:
        raise DomainError("Nội dung email phải từ 1 đến 3000 ký tự.")
    draft.subject = subject
    draft.body = body
    audit(actor, "email_draft_edited", draft)
    db.session.commit()


def reconcile_email_draft(actor, draft, outcome):
    """Manual recovery only; never replay an uncertain SMTP send automatically."""
    require(can_review_email(actor, draft))
    if draft.status != "sending" or outcome not in {"sent", "failed"}:
        raise DomainError("Bản nháp không ở trạng thái gửi dở.")
    if not draft.reviewed_at or utcnow() - draft.reviewed_at < timedelta(minutes=2):
        raise DomainError("Hãy đợi ít nhất 2 phút rồi kiểm tra hộp thư đã gửi.")
    claimed = db.session.execute(
        db.update(EmailProposal).where(EmailProposal.id == draft.id,
                                       EmailProposal.status == "sending")
        .values(status=outcome, sent_at=utcnow() if outcome == "sent" else None)
    )
    if claimed.rowcount != 1:
        raise DomainError("Bản nháp này đã được xử lý.")
    audit(actor, "email_draft_reconciled", draft, {"outcome": outcome})
    db.session.commit()


def review_email_draft(actor, draft, decision, confirm_retry=False):
    """Duyệt và gửi nháp (chống gửi trùng bằng UPDATE có điều kiện) hoặc từ chối; gửi lỗi thì cho
    phép thử lại.
    """
    require(can_review_email(actor, draft))
    if decision not in {"approved", "rejected"}:
        raise DomainError("Quyết định không hợp lệ.")
    if draft.status not in {"pending", "failed"}:
        raise DomainError("Bản nháp này đã được xử lý.")
    if draft.status == "failed" and decision == "approved" and not confirm_retry:
        raise DomainError("Hãy kiểm tra hộp thư đã gửi rồi xác nhận thử gửi lại để tránh email trùng.")
    if decision == "approved":
        if draft.recipient_id is not None:
            recipient = db.session.get(User, draft.recipient_id)
            if not approved(recipient) or recipient.email.strip() != draft.recipient_email:
                raise DomainError("Email hoặc trạng thái người nhận đã thay đổi; hãy tạo bản nháp mới.")
        _validate_address(draft.recipient_email)
        if draft.recipient_email.lower().endswith(("@example.com", "@example.test")):
            raise DomainError("Địa chỉ email người nhận đang là địa chỉ mẫu; hãy cập nhật hồ sơ trước.")
        if not current_app.config.get("MAIL_ENABLED"):
            raise DomainError("SMTP chưa bật; chưa gửi email.")
    claimed = db.session.execute(
        db.update(EmailProposal).where(EmailProposal.id == draft.id,
                                       EmailProposal.status == draft.status)
        .values(status="rejected" if decision == "rejected" else "sending",
                reviewed_by_id=actor.id, reviewed_at=utcnow())
    )
    if claimed.rowcount != 1:
        raise DomainError("Bản nháp này đã được xử lý.")
    if decision == "rejected":
        audit(actor, "email_draft_rejected", draft)
        db.session.commit()
        return False
    # Claim is committed before SMTP so two concurrent reviewers cannot send twice.
    db.session.commit()
    sent = send_email(draft.recipient_email, draft.subject, draft.body)
    draft.status = "sent" if sent else "failed"
    draft.sent_at = utcnow() if sent else None
    audit(actor, "email_draft_sent" if sent else "email_draft_failed", draft)
    db.session.commit()
    return sent
