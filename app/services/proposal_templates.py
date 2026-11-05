"""Kho mẫu sự kiện / mẫu chia task cho bản nháp sự kiện của AI.

Thứ tự AI chọn mẫu khi lập bản nháp (`find_proposal_template`):

1. Mẫu người dùng chọn trên form (nếu có).
2. Mẫu trong kho khớp nhất với mô tả: tên mẫu, từ khóa, loại sự kiện.
3. Sự kiện tương tự đã có trong hệ thống (cùng loại hoặc tên gần giống):
   lấy nội dung sự kiện / danh sách task của nó làm khung.
4. Mẫu chung (`is_default`) trong kho.
5. Mẫu chung viết sẵn trong code (`BUILTIN`), khi kho còn trống.

Bản nháp sự kiện (services/event_drafts.py) lấy danh sách task từ khung này, rồi điền thêm
những gì người dùng nêu trong câu mô tả.
"""

from app import db
from app.models import Event, EventDetail, Permission, ProposalTemplate, Task
from .access import has_permission, require
from .assistant import _normalize
from .common import DomainError, audit

KINDS = {"event": "Mẫu sự kiện", "task": "Mẫu chia task"}

BUILTIN = {
    "event": {"name": "Sự kiện chung", "keywords": "", "event_type": "", "content": {
        "name": "", "type": "other", "location": "", "duration_hours": 3,
        "content": ("Mục tiêu: …\nĐối tượng tham gia: …\nChương trình:\n"
                    "- Setup, check-in\n- Nội dung chính\n- Tổng kết, chụp ảnh\n"
                    "- Báo cáo sau sự kiện")}},
    "task": {"name": "Chuẩn bị sự kiện chung", "keywords": "", "event_type": "", "content": {
        "tasks": [
            {"name": "Lên kế hoạch và timeline", "description": "Mục tiêu, chương trình, phân công, mốc thời gian."},
            {"name": "Truyền thông sự kiện", "description": "Poster, bài đăng, nhắc đăng ký."},
            {"name": "Chuẩn bị hậu cần", "description": "Địa điểm, thiết bị, nước uống, check-in."},
            {"name": "Hỗ trợ tại sự kiện", "description": "Điều phối, check-in, xử lý phát sinh."},
            {"name": "Tổng kết và báo cáo", "description": "Ảnh, số người tham gia, phản hồi, chi phí."},
        ]}},
}

DEFAULT_TEMPLATES = [
    {"kind": "event", "is_default": True, **BUILTIN["event"]},
    {"kind": "event", "name": "Workshop", "keywords": "workshop, training, hội thảo, chia sẻ",
     "event_type": "workshop", "content": {
         "name": "Workshop …", "type": "workshop", "location": "Phòng Lab", "duration_hours": 3,
         "content": ("Chủ đề: …\nDiễn giả: …\nChương trình:\n- Setup, check-in (30 phút)\n"
                     "- Phần lý thuyết\n- Thực hành / lab\n- Hỏi đáp, tổng kết\n- Báo cáo sau sự kiện")}},
    {"kind": "event", "name": "Cuộc thi CTF", "keywords": "ctf, capture the flag, cuộc thi",
     "event_type": "ctf", "content": {
         "name": "FIA CTF …", "type": "ctf", "location": "Online + Phòng Lab", "duration_hours": 8,
         "content": ("Thể thức: Jeopardy, đội 1–4 người\nMảng: Web, Crypto, Forensics, Pwn, Misc\n"
                     "Lịch: setup hạ tầng → thi đấu → công bố kết quả → writeup và báo cáo")}},
    {"kind": "task", "is_default": True, **BUILTIN["task"]},
    {"kind": "task", "name": "Task cho workshop", "keywords": "workshop, training, hội thảo",
     "event_type": "workshop", "content": {"tasks": [
         {"name": "Chuẩn bị nội dung và slide", "description": "Slide, tài liệu, bài lab."},
         {"name": "Liên hệ diễn giả", "description": "Mời, chốt lịch, gửi thông tin."},
         {"name": "Truyền thông workshop", "description": "Poster, bài đăng, form đăng ký."},
         {"name": "Chuẩn bị phòng và thiết bị", "description": "Phòng, máy chiếu, mạng, máy lab."},
     ]}},
    {"kind": "task", "name": "Task cho CTF", "keywords": "ctf, capture the flag, cuộc thi",
     "event_type": "ctf", "content": {"tasks": [
         {"name": "Ra đề các mảng", "description": "Web, Crypto, Forensics, Pwn; kèm lời giải."},
         {"name": "Dựng hạ tầng CTFd", "description": "Server, domain, kiểm thử tải."},
         {"name": "Truyền thông và mở đăng ký đội", "description": "Thể lệ, poster, form đăng ký."},
         {"name": "Hỗ trợ kỹ thuật trong giải", "description": "Trực kênh hỗ trợ, xử lý sự cố."},
     ]}},
]


def _key(text):
    """Chuẩn hóa chuỗi để so khớp (bỏ dấu, chữ thường, gộp khoảng trắng)."""
    return " ".join(_normalize(text).split())


def _keywords(value):
    """Danh sách từ khóa (ngăn bằng dấu phẩy), giữ nguyên chữ người dùng nhập, bỏ trùng."""
    items, seen = [], set()
    for item in str(value or "").split(","):
        item = " ".join(item.split())
        if item and _key(item) not in seen:
            seen.add(_key(item))
            items.append(item)
    if len(items) > 15 or any(len(item) > 40 for item in items):
        raise DomainError("Tối đa 15 từ khóa, mỗi từ khóa không quá 40 ký tự.")
    return ", ".join(items)


def keyword_hits(keywords, text):
    """Số từ khóa (đã bỏ dấu) xuất hiện trong `text` (đã chuẩn hóa)."""
    return sum(1 for item in str(keywords or "").split(",") if _key(item) and _key(item) in text)


def parse_task_lines(text):
    """Đọc danh sách task từ textarea: mỗi dòng "Tên task | mô tả" (mô tả có thể bỏ trống)."""
    tasks = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        name, _, description = line.partition("|")
        name, description = name.strip(), description.strip()
        if not 1 <= len(name) <= 150 or len(description) > 2000:
            raise DomainError("Mỗi task cần tên 1–150 ký tự, mô tả tối đa 2000 ký tự.")
        tasks.append({"name": name, "description": description})
    if not 1 <= len(tasks) <= 10:
        raise DomainError("Mẫu chia task cần từ 1 đến 10 task (mỗi dòng một task).")
    return tasks


def task_lines(content):
    """Ngược lại của parse_task_lines: hiển thị danh sách task trong ô sửa."""
    return "\n".join(f"{row['name']} | {row.get('description', '')}".rstrip(" |")
                     for row in (content or {}).get("tasks", []))


def save_proposal_template(actor, kind, form, template=None):
    """Tạo/sửa mẫu sự kiện hoặc mẫu chia task (quyền MANAGE_EVENTS)."""
    require(has_permission(actor, Permission.MANAGE_EVENTS),
            "Chỉ người có quyền Sự kiện & task được quản lý mẫu sự kiện/task.")
    if kind not in KINDS:
        raise DomainError("Loại mẫu không hợp lệ.")
    name = str(form.get("name") or "").strip()
    if not 2 <= len(name) <= 100 or "\n" in name:
        raise DomainError("Tên mẫu phải từ 2 đến 100 ký tự, trên một dòng.")
    name_key = _key(name)
    existing = ProposalTemplate.query.filter_by(kind=kind, name_key=name_key).first()
    if existing and (template is None or existing.id != template.id):
        raise DomainError("Tên mẫu đã tồn tại.")
    event_type = str(form.get("event_type") or "").strip()[:100]
    if kind == "event":
        try:
            duration = float(form.get("duration_hours") or 3)
        except (TypeError, ValueError):
            raise DomainError("Thời lượng (giờ) không hợp lệ.") from None
        if not 0.5 <= duration <= 240:
            raise DomainError("Thời lượng phải từ 0,5 đến 240 giờ.")
        body = str(form.get("content") or "").strip()
        if len(body) > 3000:
            raise DomainError("Nội dung mẫu tối đa 3000 ký tự.")
        content = {"name": str(form.get("default_name") or "").strip()[:150],
                   "type": event_type or "event", "content": body,
                   "location": str(form.get("location") or "").strip()[:300],
                   "duration_hours": duration}
    else:
        content = {"tasks": parse_task_lines(form.get("tasks"))}
    if template is None:
        template = ProposalTemplate(kind=kind, created_by_id=actor.id, active=True)
        db.session.add(template)
    template.name, template.name_key = name, name_key
    template.keywords = _keywords(form.get("keywords"))
    template.event_type = event_type
    template.content = content
    # Form gửi hidden is_default=0 kèm checkbox is_default=1 → xét cả danh sách giá trị
    values = form.getlist("is_default") if hasattr(form, "getlist") else [form.get("is_default")]
    values = [value for value in values if value is not None]
    if any(value in {"1", "on", "yes", True} for value in values):
        db.session.flush()
        ProposalTemplate.query.filter(ProposalTemplate.kind == kind,
                                      ProposalTemplate.id != template.id).update(
            {"is_default": False})
        template.is_default = True
    elif values:
        template.is_default = False
    db.session.flush()
    audit(actor, "proposal_template_saved", template, {"kind": kind})
    db.session.commit()
    return template


def set_proposal_template_active(actor, template, active):
    """Bật/tắt mẫu sự kiện/task."""
    require(has_permission(actor, Permission.MANAGE_EVENTS),
            "Chỉ người có quyền Sự kiện & task được quản lý mẫu sự kiện/task.")
    template.active = bool(active)
    audit(actor, "proposal_template_toggle", template, {"active": template.active})
    db.session.commit()


def _from_row(row, source, text_match=False):
    """Kết quả chọn mẫu. `text_match`: mẫu được chọn tay hoặc khớp tên/từ khóa trong mô tả
    (không chỉ khớp loại sự kiện) – chế độ không có model AI chỉ áp mẫu khi True.
    """
    return {"source": source, "template_id": row.id, "label": row.name,
            "content": row.content or {}, "event_type": row.event_type, "text_match": text_match}


def _similar_event(kind, brief, event=None):
    """Sự kiện cũ tương tự (cùng loại, hoặc có từ ≥4 ký tự trùng tên với mô tả)."""
    words = {word for word in _key(brief).split() if len(word) >= 4}
    target_type = event.detail.loai if event is not None and event.detail else None
    best, best_score = None, 0
    query = Event.query.filter(Event.trang_thai != "cancelled")
    if event is not None:
        query = query.filter(Event.id != event.id)
    for candidate in query.order_by(Event.thoi_gian_bat_dau.desc()).limit(100):
        detail = db.session.get(EventDetail, candidate.id)
        if kind == "task" and not candidate.tasks:
            continue
        score = 0
        if target_type and detail and detail.loai == target_type and target_type != "other":
            score += 2
        name_words = set(_key(candidate.ten_su_kien).split())
        score += len(words & name_words)
        if detail and detail.loai and _key(detail.loai) in words:
            score += 1
        if score > best_score:
            best, best_score = candidate, score
    if best is None:
        return None
    detail = db.session.get(EventDetail, best.id)
    if kind == "task":
        seen, tasks = set(), []
        for task in Task.query.filter_by(event_id=best.id).order_by(Task.id):
            if task.trang_thai == "huy" or _key(task.ten_task) in seen:
                continue
            seen.add(_key(task.ten_task))
            tasks.append({"name": task.ten_task, "description": (task.mo_ta or "")[:500]})
        if not tasks:
            return None
        content = {"tasks": tasks[:10]}
    else:
        hours = (best.thoi_gian_ket_thuc - best.thoi_gian_bat_dau).total_seconds() / 3600
        content = {"name": best.ten_su_kien, "type": detail.loai if detail else "other",
                   "content": (detail.noi_dung if detail else "")[:3000],
                   "location": detail.dia_diem if detail else "",
                   "duration_hours": round(hours, 1)}
    return {"source": "similar_event", "template_id": None,
            "label": f"Sự kiện tương tự: {best.ten_su_kien}", "content": content,
            "event_type": content.get("type", "")}


def find_proposal_template(kind, brief, event=None, template_id=None):
    """Chọn khung cho bản nháp theo thứ tự: mẫu được chọn → mẫu trong kho → sự kiện tương tự →
    mẫu chung trong kho → mẫu chung viết sẵn.
    """
    if template_id:
        row = db.session.get(ProposalTemplate, int(template_id))
        if row is None or row.kind != kind or not row.active:
            raise DomainError("Mẫu đã chọn không tồn tại hoặc đã tắt.")
        return _from_row(row, "store", text_match=True)
    text = _key(brief)
    event_type = _key(event.detail.loai) if event is not None and event.detail else ""
    best, best_score, best_text = None, 0, False
    for row in ProposalTemplate.query.filter_by(kind=kind, active=True, is_default=False):
        text_score = 3 if row.name_key in text else 0
        text_score += keyword_hits(row.keywords, text)
        score = text_score + (2 if row.event_type and event_type and _key(row.event_type) == event_type else 0)
        if score > best_score:
            best, best_score, best_text = row, score, text_score > 0
    if best is not None:
        return _from_row(best, "store", text_match=best_text)
    similar = _similar_event(kind, brief, event)
    if similar is not None:
        return similar
    default = ProposalTemplate.query.filter_by(kind=kind, active=True, is_default=True).first()
    if default is not None:
        return _from_row(default, "default")
    builtin = BUILTIN[kind]
    return {"source": "builtin", "template_id": None, "label": builtin["name"],
            "content": builtin["content"], "event_type": builtin["event_type"]}


def seed_proposal_templates():
    """Nạp các mẫu sự kiện/task mặc định nếu kho chưa có mẫu nào của loại đó."""
    created = 0
    for spec in DEFAULT_TEMPLATES:
        if ProposalTemplate.query.filter_by(kind=spec["kind"], name_key=_key(spec["name"])).first():
            continue
        if spec.get("is_default") and ProposalTemplate.query.filter_by(
                kind=spec["kind"], is_default=True).first():
            continue
        db.session.add(ProposalTemplate(kind=spec["kind"], name=spec["name"], name_key=_key(spec["name"]),
                                        keywords=spec["keywords"], event_type=spec["event_type"],
                                        content=spec["content"], is_default=bool(spec.get("is_default")),
                                        active=True))
        created += 1
    db.session.commit()
    return created
