"""Kho mẫu email (plain text). Thay biến bằng str.replace, không bao giờ chạy code trong mẫu.

Có 2 loại mẫu, cùng lưu ở bảng `email_template`:

1. **Mẫu hệ thống** (`system_key` có giá trị): nội dung các email tự động (xác minh
   email, đặt lại mật khẩu, duyệt thành viên, giao/duyệt task, nhắc deadline, nhắc
   kỳ quỹ, nhắc đóng quỹ) và mẫu chung `general` cho AI. Người có quyền
   MANAGE_TEMPLATES sửa được tiêu đề/nội dung, không xóa được. Nếu dòng trong DB
   thiếu hoặc bị tắt, code dùng nội dung mặc định trong `SYSTEM_EMAILS`.
2. **Mẫu của CLB** (`system_key` rỗng): BĐH tự tạo. AI tìm mẫu theo tên hoặc từ khóa
   trước khi soạn nháp; không có mẫu phù hợp thì dùng mẫu chung `general`.
"""

import re

from app import db
from app.models import EmailTemplate, Permission
from .access import has_permission, require
from .assistant import _normalize
from .common import DomainError, audit
from .proposal_templates import _keywords, keyword_hits  # cùng cách lưu / so khớp từ khóa

# Biến dùng được trong mẫu của CLB
PLACEHOLDERS = {"ten", "mssv", "email", "noi_dung"}

# Mẫu email hệ thống: tên hiển thị, mô tả, biến cho phép, nội dung mặc định
SYSTEM_EMAILS = {
    "general": {
        "name": "Mẫu chung", "description": "AI dùng khi không có mẫu phù hợp trong kho.",
        "placeholders": ["ten", "noi_dung"],
        "subject": "[CLB] Thông báo từ Ban điều hành",
        "body": "Chào {{ten}},\n\n{{noi_dung}}\n\nTrân trọng,\nBan điều hành CLB",
    },
    "confirm_email": {
        "name": "Xác minh email", "description": "Gửi khi đăng ký hoặc bấm gửi lại email xác minh.",
        "placeholders": ["ten", "link", "so_gio"],
        "subject": "[CLB] Xác minh địa chỉ email",
        "body": ("Chào {{ten}},\n\nVui lòng mở liên kết sau để xác minh email đăng ký CLB "
                 "(hiệu lực {{so_gio}} giờ):\n{{link}}\n\n"
                 "Nếu bạn không đăng ký, hãy bỏ qua email này.\n\n-- CLB"),
    },
    "reset_password": {
        "name": "Đặt lại mật khẩu", "description": "Gửi khi thành viên bấm Quên mật khẩu.",
        "placeholders": ["ten", "mssv", "link"],
        "subject": "[CLB] Đặt lại mật khẩu",
        "body": ("Chào {{ten}},\n\nCó yêu cầu đặt lại mật khẩu cho tài khoản MSSV {{mssv}}. "
                 "Mở liên kết sau trong vòng 1 giờ để đặt mật khẩu mới:\n{{link}}\n\n"
                 "Nếu bạn không yêu cầu, hãy bỏ qua email này; mật khẩu hiện tại vẫn giữ nguyên."
                 "\n\n-- CLB"),
    },
    "member_approved": {
        "name": "Duyệt thành viên", "description": "Gửi khi đơn đăng ký được duyệt.",
        "placeholders": ["ten", "mssv"],
        "subject": "[CLB] Bạn đã trở thành thành viên chính thức",
        "body": ("Chào {{ten}},\n\nBĐH đã duyệt hồ sơ của bạn (MSSV: {{mssv}}). "
                 "Bạn có thể đăng nhập và sử dụng không gian làm việc của CLB.\n\n-- CLB"),
    },
    "member_rejected": {
        "name": "Từ chối đăng ký", "description": "Gửi khi đơn đăng ký bị từ chối.",
        "placeholders": ["ten", "mssv"],
        "subject": "[CLB] Hồ sơ đăng ký chưa được chấp nhận",
        "body": ("Chào {{ten}},\n\nRất tiếc, BĐH chưa thể xác nhận thông tin đăng ký của bạn "
                 "(MSSV: {{mssv}}). Vui lòng liên hệ BĐH để biết thêm chi tiết.\n\n-- CLB"),
    },
    "task_assigned": {
        "name": "Giao task mới", "description": "Gửi cho người phụ trách khi BĐH tạo task.",
        "placeholders": ["ten", "task", "su_kien", "han"],
        "subject": "[CLB] Bạn được giao task mới: {{task}}",
        "body": ("Chào {{ten}},\n\nBạn được giao task \"{{task}}\" thuộc sự kiện \"{{su_kien}}\", "
                 "hạn hoàn thành: {{han}}.\n\n-- CLB"),
    },
    "task_reassigned": {
        "name": "Chỉ định lại task", "description": "Gửi cho người phụ trách mới.",
        "placeholders": ["ten", "task", "han"],
        "subject": "[CLB] Bạn được chỉ định task: {{task}}",
        "body": ("Chào {{ten}},\n\nBạn đã được chỉ định phụ trách task \"{{task}}\", "
                 "hạn hoàn thành: {{han}}.\n\n-- CLB"),
    },
    "task_submitted": {
        "name": "Có sản phẩm chờ duyệt", "description": "Gửi cho người tạo sự kiện khi thành viên nộp sản phẩm.",
        "placeholders": ["ten", "nguoi_nop", "task", "su_kien"],
        "subject": "[CLB] Có sản phẩm cho task \"{{task}}\" chờ duyệt",
        "body": ("Chào {{ten}},\n\n{{nguoi_nop}} vừa nộp sản phẩm cho task \"{{task}}\" "
                 "(sự kiện \"{{su_kien}}\"), đang chờ BĐH duyệt.\n\n-- CLB"),
    },
    "task_reviewed": {
        "name": "Kết quả duyệt task", "description": "Gửi khi BĐH chọn Hoàn thành / Làm lại / Hủy.",
        "placeholders": ["ten", "task", "su_kien", "trang_thai", "nhan_xet"],
        "subject": "[CLB] Cập nhật task: {{task}}",
        "body": ("Chào {{ten}},\n\nTask \"{{task}}\" (sự kiện \"{{su_kien}}\") vừa được BĐH cập nhật "
                 "trạng thái: {{trang_thai}}.\n\nNhận xét của BĐH: {{nhan_xet}}\n\n-- CLB"),
    },
    "deadline_reminder": {
        "name": "Nhắc deadline task", "description": "Job 07:00 mỗi ngày, gửi trước hạn task N ngày.",
        "placeholders": ["ten", "task", "su_kien", "han", "so_ngay"],
        "subject": "[CLB] Còn {{so_ngay}} ngày nữa đến hạn: {{task}}",
        "body": ("Chào {{ten}},\n\nTask \"{{task}}\" thuộc sự kiện \"{{su_kien}}\" sẽ đến hạn vào lúc "
                 "{{han}} (còn {{so_ngay}} ngày nữa).\n\n"
                 "Vui lòng hoàn thành và nộp sản phẩm đúng hạn.\n\n-- CLB"),
    },
    "period_reminder": {
        "name": "Nhắc chuẩn bị kỳ quỹ mới", "description": "Job 07:05 mỗi ngày, gửi người quản lý quỹ.",
        "placeholders": ["ky", "ngay_ket_thuc", "so_ngay"],
        "subject": "[CLB] Nhắc chuẩn bị kỳ mới",
        "body": ("Kỳ \"{{ky}}\" sẽ kết thúc vào {{ngay_ket_thuc}} (còn {{so_ngay}} ngày). "
                 "Vui lòng chuẩn bị nhập thông tin cho kỳ kế tiếp.\n\n-- CLB"),
    },
    "fund_reminder": {
        "name": "Nhắc đóng quỹ kỳ", "description": "Gửi khi thủ quỹ bấm Nhắc đóng quỹ.",
        "placeholders": ["ten", "ky"],
        "subject": "[CLB] Nhắc đóng quỹ kỳ {{ky}}",
        "body": ("Chào {{ten}},\n\nBạn chưa đóng quỹ CLB cho kỳ \"{{ky}}\". "
                 "Vui lòng liên hệ thủ quỹ để hoàn tất.\n\n-- CLB"),
    },
}

_MARKER = re.compile(r"\{\{([^{}]*)\}\}")


def _require_manager(actor):
    """Chỉ người có MANAGE_TEMPLATES (Quản trị hệ thống, CN, PCN) được sửa mẫu email."""
    require(has_permission(actor, Permission.MANAGE_TEMPLATES),
            "Chỉ Quản trị hệ thống, Chủ nhiệm hoặc Phó chủ nhiệm được quản lý mẫu email.")


def allowed_placeholders(template):
    """Biến được phép trong một mẫu (mẫu hệ thống có bộ biến riêng)."""
    if template is not None and template.system_key:
        return set(SYSTEM_EMAILS.get(template.system_key, {}).get("placeholders", []))
    return set(PLACEHOLDERS)


def _validate_text(subject, body, allowed=PLACEHOLDERS):
    """Kiểm tra tiêu đề (1 dòng ≤180) / nội dung (≤3000) và chỉ cho phép các biến trong `allowed`."""
    subject = str(subject or "").strip()
    body = str(body or "").strip()
    if not 1 <= len(subject) <= 180 or "\n" in subject or "\r" in subject:
        raise DomainError("Tiêu đề mẫu phải là một dòng, tối đa 180 ký tự.")
    if not 1 <= len(body) <= 3000:
        raise DomainError("Nội dung mẫu phải từ 1 đến 3000 ký tự.")
    names = ", ".join("{{" + item + "}}" for item in sorted(allowed))
    for value in (subject, body):
        if any(item not in allowed for item in _MARKER.findall(value)):
            raise DomainError(f"Chỉ dùng các biến {names} trong mẫu này.")
        if re.search(r"\{\{|\}\}", _MARKER.sub("", value)):
            raise DomainError("Biến trong mẫu không hợp lệ.")
    return subject, body


def save_template(actor, name, subject, body, template=None, keywords="", description=""):
    """Tạo mới hoặc cập nhật mẫu email (MANAGE_TEMPLATES). Mẫu hệ thống chỉ sửa tiêu đề/nội dung."""
    _require_manager(actor)
    if template is not None and template.system_key:
        template.subject, template.body = _validate_text(subject, body, allowed_placeholders(template))
        db.session.flush()
        audit(actor, "email_template_saved", template, {"system_key": template.system_key})
        db.session.commit()
        return template
    name = str(name or "").strip()
    if not 2 <= len(name) <= 100 or "\n" in name or "\r" in name:
        raise DomainError("Tên mẫu phải từ 2 đến 100 ký tự, trên một dòng.")
    name_key = " ".join(_normalize(name).split())
    if not name_key:
        raise DomainError("Tên mẫu không hợp lệ.")
    subject, body = _validate_text(subject, body)
    existing = EmailTemplate.query.filter_by(name_key=name_key).first()
    if existing and (template is None or existing.id != template.id):
        raise DomainError("Tên mẫu email đã tồn tại.")
    if template is None:
        template = EmailTemplate(created_by_id=actor.id, active=True)
        db.session.add(template)
    template.name = name
    template.name_key = name_key
    template.keywords = _keywords(keywords)
    template.description = str(description or "").strip()[:300]
    template.subject = subject
    template.body = body
    db.session.flush()
    audit(actor, "email_template_saved", template)
    db.session.commit()
    return template


def set_template_active(actor, template, active):
    """Bật/tắt mẫu email của CLB (mẫu hệ thống luôn bật)."""
    _require_manager(actor)
    if template.system_key:
        raise DomainError("Mẫu email hệ thống luôn được dùng; chỉ có thể sửa nội dung.")
    template.active = bool(active)
    audit(actor, "email_template_activated" if active else "email_template_disabled", template)
    db.session.commit()


def reset_system_template(actor, template):
    """Khôi phục nội dung mặc định cho một mẫu email hệ thống."""
    _require_manager(actor)
    default = SYSTEM_EMAILS.get(template.system_key or "")
    if not default:
        raise DomainError("Chỉ khôi phục được mẫu email hệ thống.")
    template.subject, template.body = default["subject"], default["body"]
    audit(actor, "email_template_reset", template, {"system_key": template.system_key})
    db.session.commit()


def _fill(text, values):
    """Thay {{bien}} bằng giá trị (chuỗi); biến không có giá trị → chuỗi rỗng."""
    return _MARKER.sub(lambda match: str(values.get(match.group(1), "")), text)


def render_template(template, display_name, code, email, purpose=""):
    """Điền thông tin người nhận vào mẫu email của CLB."""
    if not template.active:
        raise DomainError("Mẫu email này đã ngừng sử dụng.")
    if not code and any("{{mssv}}" in value for value in (template.subject, template.body)):
        raise DomainError("Mẫu email có biến MSSV nên chỉ dùng được cho thành viên CLB.")
    values = {"ten": display_name, "mssv": code or "", "email": email,
              "noi_dung": purpose or ""}
    subject = re.sub(r"[\r\n]+", " ", _fill(template.subject, values)).strip()[:180]
    body = _fill(template.body, values).strip()[:5000]
    return subject, body


def system_template(key):
    """(subject, body) của mẫu hệ thống: lấy trong kho; thiếu/không hợp lệ thì dùng mặc định."""
    default = SYSTEM_EMAILS[key]
    row = EmailTemplate.query.filter_by(system_key=key, active=True).first()
    if row is not None:
        try:
            return _validate_text(row.subject, row.body, set(default["placeholders"]))
        except DomainError:
            pass
    return default["subject"], default["body"]


def render_system_email(key, **values):
    """Nội dung email tự động đã điền biến, ví dụ:
    ``send_email(user.email, *render_system_email("member_approved", ten=..., mssv=...))``.
    """
    subject, body = system_template(key)
    subject = re.sub(r"[\r\n]+", " ", _fill(subject, values)).strip()[:250]
    return subject, _fill(body, values).strip()


def find_email_template(text):
    """Tìm mẫu email của CLB phù hợp nhất với câu yêu cầu: tên mẫu hoặc từ khóa xuất hiện trong câu.

    Trả về EmailTemplate hoặc None (khi đó AI dùng mẫu chung `general`).
    """
    normalized = " ".join(_normalize(text).split())
    best, best_score = None, 0
    for template in EmailTemplate.query.filter(EmailTemplate.system_key.is_(None),
                                               EmailTemplate.active.is_(True)):
        score = 3 if template.name_key and template.name_key in normalized else 0
        score += keyword_hits(template.keywords, normalized)
        if score > best_score:
            best, best_score = template, score
    return best


def seed_system_templates():
    """Nạp các mẫu email hệ thống còn thiếu vào kho (không ghi đè mẫu đã sửa)."""
    created = 0
    for key, spec in SYSTEM_EMAILS.items():
        if EmailTemplate.query.filter_by(system_key=key).first():
            continue
        name_key = " ".join(_normalize("he thong " + spec["name"]).split())
        db.session.add(EmailTemplate(system_key=key, name=spec["name"], name_key=name_key,
                                     description=spec["description"], subject=spec["subject"],
                                     body=spec["body"], active=True))
        created += 1
    db.session.commit()
    return created
