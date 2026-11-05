"""Bản nháp sự kiện: trợ lý AI lập sự kiện + danh sách task từ một câu mô tả.

Luồng:
  1. Người quản lý sự kiện (MANAGE_EVENTS) mô tả sự kiện trong chat hoặc form. AI tạo NGAY một bản
     nháp, kể cả khi còn thiếu: lấy task từ Kho mẫu (→ sự kiện tương tự → mẫu chung), điền những gì
     có trong câu (ngày, giờ, địa điểm, ban, quy mô).
  2. Phần còn thiếu được bổ sung sau, qua chat ("ngày 12/12 ở Lab 304", "task 2 giao cho SE180012",
     "gợi ý người", "thêm task âm thanh", "bỏ task 3") hoặc form ở trang bản nháp.
  3. Chỉ duyệt được khi đủ thông tin bắt buộc (`missing`). Duyệt tạo sự kiện và toàn bộ task trong
     một giao dịch; trước đó không ghi gì vào bảng event/task.

Sự kiện tính theo ngày: chưa nêu giờ thì sự kiện chiếm cả ngày (00:00–23:59). Hạn task tính theo
ngày sự kiện ("trước N ngày", mặc định 23:59); task diễn ra tại sự kiện có hạn là giờ kết thúc.
Mọi ngày giờ, mã, MSSV đều do server đọc từ câu hoặc database, không lấy từ câu trả lời của model.
"""
import copy
import re
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm.attributes import flag_modified

from app import db
from app.models import Ban, Event, EventDetail, EventDraft, EventTargetBan, MemberPlanningProfile, Task, User, UserBan
from .access import approved, can_manage_events, require
from .assistant import _normalize
from .common import DomainError, audit, club_now
from .event_status import OPEN, initial_status, is_terminal
from .natural_datetime import extract_schedule
from .proposal_templates import find_proposal_template
from app.timeutil import utcnow

MAX_TASKS = 15
DEFAULT_DUE = "23:59"
DEFAULT_DAYS_BEFORE = 2
ACTIVE_STATES = ("dang_lam", "cho_duyet", "lam_lai")
DRAFT_TTL = timedelta(days=14)          # bản nháp không sửa quá hạn này thì không còn "đang dở"
_AT_EVENT = re.compile(r"\b(?:tai su kien|trong giai|ho tro|dieu phoi|tong ket|bao cao|check ?in|truc)\b")
_QUESTION = re.compile(r"\?|\b(?:ai|bao nhieu|nhung ai|gi|nao|co khong|duoc khong|the nao|khi nao|o dau|xem|liet ke)\b")


# ---------------------------------------------------------------- đọc / quyền
def visible_drafts(actor):
    """Bản nháp đang dở mà người quản lý sự kiện được xem (mọi người quản lý sự kiện cùng làm)."""
    require(can_manage_events(actor), "Chỉ người có quyền quản lý sự kiện được xem bản nháp.")
    return (EventDraft.query.filter(EventDraft.status == "draft",
                                    EventDraft.updated_at >= utcnow() - DRAFT_TTL)
            .order_by(EventDraft.updated_at.desc()))


def get_draft(actor, draft_id):
    """Bản nháp đang dở theo id (404 nếu không có / đã xử lý)."""
    draft = visible_drafts(actor).filter(EventDraft.id == int(draft_id)).first()
    if draft is None:
        raise DomainError("Không tìm thấy bản nháp đang dở này.", 404)
    return draft


def active_draft(actor, conversation=None):
    """Bản nháp đang được bàn trong cuộc trò chuyện hiện tại (mới sửa gần nhất)."""
    if not can_manage_events(actor):
        return None
    query = EventDraft.query.filter(EventDraft.status == "draft", EventDraft.created_by_id == actor.id,
                                    EventDraft.updated_at >= utcnow() - DRAFT_TTL)
    query = (query.filter(EventDraft.conversation_id == conversation.id) if conversation is not None
             else query.filter(EventDraft.conversation_id.is_(None)))
    return query.order_by(EventDraft.updated_at.desc()).first()


def _event_of(draft):
    return db.session.get(Event, draft.event_id) if draft.event_id else None


# ---------------------------------------------------------------- ngày giờ của bản nháp
def _clock(value, fallback):
    hour, minute = (value or fallback).split(":")
    return time(int(hour), int(minute))


def event_window(draft):
    """(bắt đầu, kết thúc) của sự kiện, hoặc None khi chưa có ngày."""
    event = _event_of(draft)
    if event is not None:
        return event.thoi_gian_bat_dau, event.thoi_gian_ket_thuc
    data = draft.data
    if not data.get("date"):
        return None
    day = date.fromisoformat(data["date"])
    start = datetime.combine(day, _clock(data.get("start"), "00:00"))
    day_end = datetime.combine(day, time(23, 59))
    if data.get("end"):
        return start, datetime.combine(day, _clock(data["end"], "23:59"))
    if data.get("start") and data.get("duration_hours"):      # chỉ nêu giờ bắt đầu: lấy thời lượng của mẫu
        return start, min(start + timedelta(hours=float(data["duration_hours"])), day_end)
    return start, day_end


def task_deadline(draft, task):
    """Hạn của một task: ngày sự kiện − N ngày, lúc `due`; không sau giờ kết thúc sự kiện."""
    window = event_window(draft)
    if window is None:
        return None
    start, end = window
    if task.get("days_before", DEFAULT_DAYS_BEFORE) <= 0:
        return end
    due_day = start.date() - timedelta(days=task["days_before"])
    return min(datetime.combine(due_day, _clock(task.get("due"), DEFAULT_DUE)), end)


# ---------------------------------------------------------------- còn thiếu gì
def missing(draft):
    """[(mã, mô tả)] thông tin bắt buộc còn thiếu, theo thứ tự nên hỏi."""
    data, items = draft.data, []
    if not draft.event_id:
        if not data.get("date"):
            items.append(("date", "ngày tổ chức"))
        if not data.get("name"):
            items.append(("name", "tên sự kiện"))
    tasks = data.get("tasks", [])
    if not tasks:
        items.append(("tasks", "ít nhất một task"))
    no_ban = [index for index, task in enumerate(tasks, 1) if not task.get("ban_id")]
    if no_ban:
        items.append(("task_ban", f"ban phụ trách cho {len(no_ban)} task"))
    no_owner = [index for index, task in enumerate(tasks, 1) if not task.get("assignee_id")]
    if no_owner:
        items.append(("assignee", f"người phụ trách cho {len(no_owner)} task"))
    return items


def problems(draft, now=None):
    """Lỗi làm bản nháp không duyệt được dù đã đủ thông tin (giờ sai, hạn đã qua…)."""
    now = now or club_now()
    errors = []
    event = _event_of(draft)
    if draft.event_id and (event is None or is_terminal(event)):
        return ["Sự kiện của bản nháp không còn mở."]
    window = event_window(draft)
    if window:
        start, end = window
        if end <= start:
            errors.append("Giờ kết thúc phải sau giờ bắt đầu.")
        if not draft.event_id and end <= now:
            errors.append("Ngày tổ chức đã qua.")
        for index, task in enumerate(draft.data.get("tasks", []), 1):
            deadline = task_deadline(draft, task)
            if deadline and deadline <= now:
                errors.append(f"Hạn task {index} ({deadline:%H:%M %d/%m}) đã qua; hãy dời hạn.")
    return errors


# ---------------------------------------------------------------- chọn người phụ trách
def _capacity(profile):
    return profile.max_active_tasks if profile else 3


def _ban_members(ban_id):
    members = (User.query.join(UserBan, UserBan.user_id == User.id)
               .filter(UserBan.ban_id == ban_id, User.status == "approved").order_by(User.id).all())
    return [member for member in members if approved(member)]


def _workload(member_ids):
    """{user_id: số task đang làm} và {user_id: hồ sơ nhận task}."""
    load = dict(db.session.query(Task.assignee_id, db.func.count(Task.id))
                .filter(Task.assignee_id.in_(member_ids or [-1]), Task.trang_thai.in_(ACTIVE_STATES))
                .group_by(Task.assignee_id).all())
    profiles = {row.user_id: row for row in MemberPlanningProfile.query.filter(
        MemberPlanningProfile.user_id.in_(member_ids or [-1]))}
    return load, profiles


def _free(member, deadline, load, profiles):
    profile = profiles.get(member.id)
    if profile and profile.unavailable_until and deadline and profile.unavailable_until >= deadline.date():
        return False
    return load.get(member.id, 0) < _capacity(profile)


def suggest_assignees(draft):
    """Gợi ý người cho các task chưa có ai: đúng ban, còn sức nhận việc, ưu tiên kỹ năng khớp tên task,
    rồi ít việc nhất. Trả về số task đã gợi ý (chưa commit)."""
    data = copy.deepcopy(draft.data)
    tasks = data.get("tasks", [])
    planned = {}
    for task in tasks:
        if task.get("assignee_id"):
            planned[task["assignee_id"]] = planned.get(task["assignee_id"], 0) + 1
    count = 0
    for task in tasks:
        if task.get("assignee_id") or not task.get("ban_id"):
            continue
        members = _ban_members(task["ban_id"])
        load, profiles = _workload([member.id for member in members])
        projected = {member.id: load.get(member.id, 0) + planned.get(member.id, 0) for member in members}
        deadline = task_deadline(draft, task)
        candidates = [member for member in members if _free(member, deadline, projected, profiles)]
        if not candidates:
            continue
        words = set(_normalize(task["name"]).split())

        def score(member):
            skills = {_normalize(skill) for skill in (profiles[member.id].skills if member.id in profiles else [])}
            matched = sum(1 for skill in skills if skill and (skill in words or skill in _normalize(task["name"])))
            return (-matched, projected[member.id] / _capacity(profiles.get(member.id)), member.id)

        chosen = min(candidates, key=score)
        task["assignee_id"], task["assignee_by"] = chosen.id, "ai"
        planned[chosen.id] = planned.get(chosen.id, 0) + 1
        count += 1
    _store(draft, data)
    return count


# ---------------------------------------------------------------- đọc câu chat
def _bans_in(text):
    found = [ban for ban in Ban.query.order_by(Ban.id).all()
             if re.search(r"\bban\s+" + re.escape(_normalize(ban.ten_ban)) + r"\b", text)]
    return found


def _member_in(message):
    """Đúng một thành viên được nhắc (MSSV hoặc họ tên đầy đủ); nhiều người → hỏi lại."""
    members = [member for member in User.query.filter(User.status == "approved", User.chuc_vu != "ADMIN")
               if approved(member)]
    by_code = [member for member in members
               if re.search(r"(?<!\w)" + re.escape(member.mssv) + r"(?!\w)", message, re.I)]
    if not by_code:
        normalized = _normalize(message)
        by_code = [member for member in members if len(member.ho_ten) >= 4 and re.search(
            r"(?<!\w)" + re.escape(_normalize(member.ho_ten)) + r"(?!\w)", normalized)]
        exact = [member for member in by_code if member.ho_ten.casefold() in message.casefold()]
        by_code = exact or by_code
    if len(by_code) > 1:
        raise DomainError("Câu có nhiều người phù hợp; bạn ghi MSSV giúp mình nhé.")
    return by_code[0] if by_code else None


def _task_in(draft, text):
    """Đúng một task được nhắc: "task 2" / "việc 2" hoặc một phần tên task."""
    tasks = draft.data.get("tasks", [])
    number = re.search(r"\b(?:task|viec)\s*(?:so\s*)?(\d{1,2})\b", text)
    if number:
        index = int(number.group(1))
        if not 1 <= index <= len(tasks):
            raise DomainError(f"Bản nháp chỉ có {len(tasks)} task.")
        return tasks[index - 1]
    matches = [task for task in tasks if len(_normalize(task["name"])) >= 3
               and _normalize(task["name"]) in text]
    if not matches:
        matches = [task for task in tasks
                   if any(len(word) >= 4 and re.search(r"\b" + re.escape(word) + r"\b", text)
                          for word in _normalize(task["name"]).split())]
    if len(matches) > 1:
        raise DomainError("Câu khớp với nhiều task; bạn ghi số thứ tự task giúp mình (vd: task 2).")
    return matches[0] if matches else None


def _strip(message, spans):
    for start, end in sorted(spans, reverse=True):
        message = message[:start] + " " + message[end:]
    return message


_LOCATION = re.compile(r"(?i)(?:^|[\s,])(?:ở|tại|địa điểm(?:\s+là)?)\s+([^,;\n]+)")
_LOCATION_CUT = re.compile(r"(?i)\s+(?:lúc|vào|từ|ngày|cho ban|cho|khoảng|với|nhé|nha)\b.*$")
_RENAME = re.compile(r"(?i)\b(?:đổi tên(?:\s+sự kiện)?|tên sự kiện|đặt tên)(?:\s+(?:là|thành))?\s*:?\s*(.+)$")
_ADD_TASK = re.compile(r"(?i)\bthêm\s+(?:task|việc)\s+(.+)$")
_SIZE = re.compile(r"\b(?:khoang|tam|du kien)?\s*(\d{1,4})\s*(?:nguoi|ban tham gia|thanh vien)\b")


def _clauses(message):
    return [part.strip() for part in re.split(r"[;\n]+|,\s+(?=(?:task|việc)\b)", message) if part.strip()]


def parse_edits(draft, message):
    """Các thay đổi mà câu chat yêu cầu trên bản nháp (chưa áp dụng). [] nếu câu không phải chỉnh sửa
    (vd. một câu hỏi tra cứu) – khi đó trợ lý trả lời như bình thường."""
    text = _normalize(message)
    if re.search(r"\b(?:huy|bo|xoa)\s+(?:ban\s+)?nhap\b", text):
        return [("discard", None)]
    asking = bool(_QUESTION.search(text)) and not re.search(r"\b(?:giao|them|bo|xoa|doi|dat|goi y)\b", text)
    if asking:
        return []
    ops = []
    if re.search(r"\b(?:goi y|tu chon|tu phan cong|chon giup|phan cong giup|de (?:ai|ban|minh) chon)\b", text):
        ops.append(("suggest", None))
    for clause in _clauses(message):
        ops.extend(_parse_clause(draft, clause))
    return ops


def _parse_clause(draft, clause):
    text = _normalize(clause)
    ops = []
    added = _ADD_TASK.search(clause)
    if added:
        name = _LOCATION_CUT.sub("", re.sub(r"(?i)\s+cho\s+ban\s+.+$", "", added.group(1))).strip(" .")
        bans = _bans_in(text)
        if name:
            ops.append(("add_task", {"name": name[:150], "ban_id": bans[0].id if len(bans) == 1 else None}))
        return ops
    removing = re.search(r"\b(?:bo|xoa)\s+(?:task|viec)\b", text)
    task = _task_in(draft, text) if re.search(r"\b(?:task|viec)\b", text) else None
    if removing:
        if task is None:
            raise DomainError("Mình chưa rõ bạn muốn bỏ task nào; ghi số thứ tự giúp mình nhé.")
        return [("remove_task", task["key"])]
    renamed = _RENAME.search(clause)
    if renamed and not draft.event_id:
        return [("name", renamed.group(1).strip(" .")[:150])]

    member = _member_in(clause) if re.search(r"\b(?:giao|cho|do|phu trach|lam)\b", text) else None
    if member is not None:
        if task is None:
            raise DomainError(f"Bạn muốn giao task nào cho {member.ho_ten}? Ghi số thứ tự task giúp mình nhé.")
        ops.append(("assign", (task["key"], member.id)))
    bans = _bans_in(text)
    if len(bans) > 1:
        raise DomainError("Câu nhắc nhiều ban; mỗi task chỉ thuộc một ban, bạn nói từng task giúp mình nhé.")
    schedule = extract_schedule(clause)
    if len(set(schedule["dates"])) > 1:
        raise DomainError("Câu có hai ngày khác nhau; bạn nói rõ giúp mình ngày nào nhé.")
    if task is not None and member is None:
        if bans:
            ops.append(("task_ban", (task["key"], bans[0].id)))
        before = re.search(r"\btruoc\s+(\d{1,2})\s+ngay\b", text)
        if before:
            ops.append(("task_days", (task["key"], int(before.group(1)))))
        elif schedule["dates"]:
            ops.append(("task_due_date", (task["key"], schedule["dates"][0],
                                          schedule["times"][0] if schedule["times"] else None)))
        return ops
    if member is not None:
        return ops
    if bans:
        ops.append(("ban", bans[0].id))
    if not draft.event_id:
        if schedule["dates"]:
            ops.append(("date", schedule["dates"][0]))
        if schedule["times"]:
            ops.append(("times", schedule["times"][:2]))
        rest = _strip(clause, schedule["spans"])
        location = _LOCATION.search(rest)
        if location:
            place = _LOCATION_CUT.sub("", location.group(1)).strip(" .")
            if place:
                ops.append(("location", place[:300]))
        size = _SIZE.search(text)
        if size:
            ops.append(("size", int(size.group(1))))
    return ops


# ---------------------------------------------------------------- áp dụng thay đổi
def _store(draft, data):
    """Ghi lại JSON của bản nháp (cột JSON không tự nhận thay đổi bên trong dict)."""
    draft.data = data
    flag_modified(draft, "data")
    draft.updated_at = utcnow()


def _task_by_key(data, key):
    return next(task for task in data["tasks"] if task["key"] == key)


def apply_ops(actor, draft, ops):
    """Áp các thay đổi lên bản nháp (chưa commit). Trả về câu mô tả ngắn từng thay đổi."""
    data = copy.deepcopy(draft.data)
    draft.data = data          # các bước sau (hạn task theo ngày, gợi ý người) đọc dữ liệu đang sửa
    done = []
    for kind, value in ops:
        if kind == "name":
            data["name"] = value
            _confirm(data, "name")
            done.append(f"tên \"{value}\"")
        elif kind == "date":
            data["date"] = value.isoformat()
            done.append(f"ngày {value:%d/%m/%Y}")
        elif kind == "times":
            data["start"] = value[0].strftime("%H:%M")
            if len(value) > 1:
                data["end"] = value[1].strftime("%H:%M")
            done.append("giờ " + " – ".join(item.strftime("%H:%M") for item in value))
        elif kind == "location":
            data["location"] = value
            _confirm(data, "location")
            done.append(f"địa điểm {value}")
        elif kind == "size":
            data["size"] = value
            done.append(f"quy mô khoảng {value} người")
        elif kind == "ban":
            ban = db.session.get(Ban, value)
            data["ban_id"] = value
            for task in data["tasks"]:
                task.setdefault("ban_id", None)
                if not task["ban_id"]:
                    task["ban_id"] = value
            done.append(f"ban {ban.ten_ban}")
        elif kind == "add_task":
            if len(data["tasks"]) >= MAX_TASKS:
                raise DomainError(f"Một bản nháp tối đa {MAX_TASKS} task.")
            key = max((task["key"] for task in data["tasks"]), default=0) + 1
            data["tasks"].append(_new_task(key, value["name"], "", value["ban_id"] or data.get("ban_id")))
            done.append(f"thêm task \"{value['name']}\"")
        elif kind == "remove_task":
            task = _task_by_key(data, value)
            data["tasks"] = [row for row in data["tasks"] if row["key"] != value]
            done.append(f"bỏ task \"{task['name']}\"")
        elif kind == "assign":
            key, member_id = value
            task, member = _task_by_key(data, key), db.session.get(User, member_id)
            member_bans = {link.ban_id for link in member.ban_links}
            if task.get("ban_id") and task["ban_id"] not in member_bans:
                ban = db.session.get(Ban, task["ban_id"])
                raise DomainError(f"{member.ho_ten} không thuộc ban {ban.ten_ban} của task \"{task['name']}\".")
            if not task.get("ban_id"):
                if len(member_bans) != 1:
                    raise DomainError(f"Task \"{task['name']}\" chưa có ban; bạn chọn ban trước giúp mình nhé.")
                task["ban_id"] = next(iter(member_bans))
            task["assignee_id"], task["assignee_by"] = member.id, "user"
            done.append(f"\"{task['name']}\" giao cho {member.ho_ten}")
        elif kind == "task_ban":
            key, ban_id = value
            task = _task_by_key(data, key)
            if task.get("ban_id") != ban_id:
                task["ban_id"], task["assignee_id"], task["assignee_by"] = ban_id, None, None
            done.append(f"\"{task['name']}\" thuộc ban {db.session.get(Ban, ban_id).ten_ban}")
        elif kind == "task_days":
            key, days = value
            task = _task_by_key(data, key)
            task["days_before"] = max(0, days)
            done.append(f"hạn \"{task['name']}\" trước sự kiện {days} ngày")
        elif kind == "task_due_date":
            key, day, clock = value
            task, window = _task_by_key(data, key), event_window(draft)
            if window is None:
                raise DomainError("Bản nháp chưa có ngày tổ chức nên chưa đặt được hạn task theo ngày.")
            days = (window[0].date() - day).days
            if days < 0:
                raise DomainError("Hạn task không được sau ngày tổ chức sự kiện.")
            task["days_before"] = days
            if clock:
                task["due"] = clock.strftime("%H:%M")
            done.append(f"hạn \"{task['name']}\" {day:%d/%m}")
        elif kind == "suggest":
            count = suggest_assignees(draft)
            data = draft.data
            done.append(f"gợi ý người cho {count} task" if count else "chưa gợi ý được ai (thiếu ban hoặc ban đã kín việc)")
    _store(draft, data)
    audit(actor, "event_draft_update", draft, {"changes": done})
    return done


def _confirm(data, field):
    data["guessed"] = [item for item in data.get("guessed", []) if item != field]


def _new_task(key, name, description, ban_id):
    days = 0 if _AT_EVENT.search(_normalize(name)) else DEFAULT_DAYS_BEFORE
    return {"key": key, "name": name[:150], "description": (description or "")[:2000], "ban_id": ban_id,
            "days_before": days, "due": DEFAULT_DUE, "assignee_id": None, "assignee_by": None}


# ---------------------------------------------------------------- tạo bản nháp
_EVENT_NOUN = r"(?:su kien|workshop|hoi thao|ctf|talkshow|training|cuoc thi|buoi|ngay hoi|seminar)"
_NEW_EVENT = re.compile(r"\b(?:tao|lap|de xuat|to chuc|len ke hoach|nhap|chuan bi)\s+(?:(?:mot|1|ban nhap|cho)\s+)*"
                        + _EVENT_NOUN + r"\b")
_CREATE_EVENT = re.compile(r"\b(?:tao|lap|de xuat|to chuc|len ke hoach|lam|nhap|chuan bi)\b.*\b" + _EVENT_NOUN + r"\b")
_SPLIT_TASKS = re.compile(r"\b(?:chia|giao|phan cong|tao|lap|de xuat|len)\b.*\b(?:task|cong viec|viec)\b")
_LEAD = re.compile(r"(?i)^\s*(?:(?:ai|bạn|cậu)\s+ơi[,!]?\s*)?(?:(?:hãy|giúp\s+(?:mình|tôi|tớ)|mình muốn|tôi muốn|"
                   r"tớ muốn|cho\s+(?:mình|tôi|tớ))\s+)*(?:tháng sau|tuần sau|tháng tới|tuần tới|sắp tới)?\s*"
                   r"(?:tạo|lập|đề xuất|tổ chức|lên kế hoạch|làm|nháp|chuẩn bị)\s+(?:(?:một|1)\s+)?"
                   r"(?:bản nháp\s+)?(?:(?:cho\s+)?sự kiện\b\s*:?\s*)?")
_NOISE = re.compile(r"(?i)\b(?:tháng sau|tuần sau|tháng tới|tuần tới|sắp tới|nhé|nha|giúp mình|giúp tớ)\b|"
                    r"\b(?:khoảng|tầm|dự kiến)?\s*\d{1,4}\s*(?:người|bạn tham gia|thành viên)\b")


def draft_intent(message):
    """"event" nếu câu yêu cầu lập sự kiện mới, "tasks" nếu yêu cầu chia task cho sự kiện có sẵn."""
    text = _normalize(message)
    if re.search(r"\b(?:co|nhung|cac)\s+su kien\b.*\b(?:nao|gi)\b|\bsu kien nao\b", text):
        return None
    if _NEW_EVENT.search(text) or (_CREATE_EVENT.search(text) and not _SPLIT_TASKS.search(text)):
        return "event"
    if _SPLIT_TASKS.search(text):
        return "tasks"
    return None


_SCHEDULE_GLUE = re.compile(r"(?i)(?:\b(?:từ|đến|tới|lúc|vào|ngày|hôm)\s*|[-–]\s*)*\x00")


def _guess_name(message, spans):
    for start, end in sorted(spans, reverse=True):      # bỏ ngày giờ cùng chữ nối đi kèm ("từ … đến …")
        message = message[:start] + "\x00" + message[end:]
    text = _SCHEDULE_GLUE.sub(" ", message).replace("\x00", " ")
    text = _LOCATION.sub(" ", text)
    text = _LEAD.sub("", text.strip())
    text = _NOISE.sub(" ", text)
    for ban in Ban.query.all():       # "cho ban Kỹ thuật" là đối tượng, không phải tên sự kiện
        text = re.sub(r"(?i)\b(?:dành cho|cho|của)?\s*ban\s+" + re.escape(ban.ten_ban) + r"\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    return (text[:1].upper() + text[1:])[:150] if len(text) >= 3 else ""


def _open_event_in(message):
    text = _normalize(message)
    events = [event for event in Event.query.filter(Event.trang_thai.in_(OPEN))
              if re.search(r"(?<!\w)" + re.escape(event.ma_su_kien.lower()) + r"(?!\w)", message.lower())
              or (len(event.ten_su_kien) >= 4 and _normalize(event.ten_su_kien) in text)]
    if len(events) > 1:
        raise DomainError("Câu khớp với nhiều sự kiện; bạn ghi mã sự kiện giúp mình nhé.")
    return events[0] if events else None


def create_draft(actor, message, conversation=None, template_id=None, event=None):
    """Lập bản nháp từ câu mô tả (chưa đủ thông tin vẫn lập). Commit; trả về bản nháp."""
    require(can_manage_events(actor), "Chỉ người có quyền quản lý sự kiện được lập bản nháp sự kiện.")
    message = str(message or "").strip()
    if not 3 <= len(message) <= 2000:
        raise DomainError("Mô tả sự kiện cần từ 3 đến 2000 ký tự.")
    task_frame = find_proposal_template("task", message, event=event, template_id=template_id)
    data = {"tasks": [], "guessed": [], "template": task_frame["label"]}
    if event is None:
        frame = find_proposal_template("event", message)
        content = frame["content"]
        schedule = extract_schedule(message)
        data.update(name=_guess_name(message, schedule["spans"]), type=content.get("type") or "other",
                    content=content.get("content") or "", location=content.get("location") or "",
                    ban_id=None, date=None, start=None, end=None)
        data["guessed"] = [field for field in ("type", "location") if data[field]]
        if content.get("duration_hours"):
            data["duration_hours"] = content["duration_hours"]
    for key, row in enumerate(task_frame["content"].get("tasks", [])[:MAX_TASKS], 1):
        data["tasks"].append(_new_task(key, row["name"], row.get("description", ""), None))
    draft = EventDraft(created_by_id=actor.id, conversation_id=conversation.id if conversation else None,
                       event_id=event.id if event else None, request_text=message, data=data)
    db.session.add(draft)
    db.session.flush()
    try:      # câu mô tả đầu tiên chỉ điền thông tin sự kiện; giao người / bỏ task làm ở bước sau
        ops = [op for op in _parse_clause(draft, message) if op[0] not in {"assign", "remove_task", "name"}]
    except DomainError:
        ops = []  # câu còn mơ hồ (vd. hai ngày khác nhau): để trống, câu hỏi tiếp theo sẽ hỏi lại
    apply_ops(actor, draft, ops)
    audit(actor, "event_draft_create", draft, {"event_id": draft.event_id, "template": data["template"]})
    db.session.commit()
    return draft


def update_from_form(actor, draft, form):
    """Lưu form ở trang bản nháp (thông tin sự kiện + từng task). Commit."""
    data = copy.deepcopy(draft.data)
    if not draft.event_id:
        data["name"] = str(form.get("name", "")).strip()[:150]
        data["location"] = str(form.get("location", "")).strip()[:300]
        data["content"] = str(form.get("content", "")).strip()[:3000]
        data["date"] = _form_date(form.get("date"))
        data["start"] = _form_clock(form.get("start"), "Giờ bắt đầu")
        data["end"] = _form_clock(form.get("end"), "Giờ kết thúc")
        data["ban_id"] = _form_ban(form.get("ban_id"))
        data["guessed"] = []
    tasks = []
    for task in data.get("tasks", []):
        prefix = f"task_{task['key']}_"
        if form.get(prefix + "remove"):
            continue
        row = dict(task)
        row["name"] = str(form.get(prefix + "name", task["name"])).strip()[:150] or task["name"]
        row["ban_id"] = _form_ban(form.get(prefix + "ban_id"))
        row["days_before"] = _form_int(form.get(prefix + "days_before"), "Số ngày trước sự kiện", 0, 365)
        assignee = form.get(prefix + "assignee_id")
        new_assignee = int(assignee) if str(assignee or "").isdigit() else None
        if new_assignee != task.get("assignee_id"):
            row["assignee_by"] = "user" if new_assignee else None
        row["assignee_id"] = new_assignee
        if row["assignee_id"]:
            member = db.session.get(User, row["assignee_id"])
            if not approved(member) or not row["ban_id"] or row["ban_id"] not in {link.ban_id for link in member.ban_links}:
                raise DomainError(f"Người phụ trách task \"{row['name']}\" phải là thành viên của ban đã chọn.")
        tasks.append(row)
    new_name = str(form.get("new_task", "")).strip()
    if new_name:
        tasks.append(_new_task(max((task["key"] for task in tasks), default=0) + 1, new_name,
                               "", data.get("ban_id")))
    if len(tasks) > MAX_TASKS:
        raise DomainError(f"Một bản nháp tối đa {MAX_TASKS} task.")
    data["tasks"] = tasks
    _store(draft, data)
    audit(actor, "event_draft_update", draft, {"changes": ["form"]})
    db.session.commit()
    return draft


def _form_date(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise DomainError("Ngày tổ chức không hợp lệ.") from None


def _form_clock(value, label):
    value = str(value or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise DomainError(f"{label} không hợp lệ (HH:MM).")
    return value


def _form_ban(value):
    if not str(value or "").isdigit():
        return None
    if db.session.get(Ban, int(value)) is None:
        raise DomainError("Ban không tồn tại.")
    return int(value)


def _form_int(value, label, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise DomainError(f"{label} không hợp lệ.") from None
    if not low <= number <= high:
        raise DomainError(f"{label} phải từ {low} đến {high}.")
    return number


# ---------------------------------------------------------------- duyệt / hủy
def _claim(draft, status):
    """Chuyển trạng thái bằng UPDATE có điều kiện (hai người bấm cùng lúc chỉ một lượt thành công)."""
    result = db.session.execute(db.update(EventDraft).where(
        EventDraft.id == draft.id, EventDraft.status == "draft").values(status=status))
    if result.rowcount != 1:
        raise DomainError("Bản nháp đã được xử lý.", 409)
    draft.status = status


def approve(actor, draft):
    """Duyệt: tạo sự kiện (nếu là bản nháp sự kiện mới) và toàn bộ task trong một giao dịch."""
    require(can_manage_events(actor), "Chỉ người có quyền quản lý sự kiện được duyệt bản nháp.")
    gaps, errors = missing(draft), problems(draft)
    if gaps or errors:
        raise DomainError("Chưa duyệt được: " + "; ".join([label for _code, label in gaps] + errors) + ".")
    _claim(draft, "approved")
    data = draft.data
    event = _event_of(draft)
    if event is None:
        start, end = event_window(draft)
        event = Event(ten_su_kien=data["name"], ma_su_kien=_event_code(data["name"], start),
                      thoi_gian_bat_dau=start, thoi_gian_ket_thuc=end,
                      trang_thai=initial_status(start), tao_boi_id=actor.id)
        db.session.add(event)
        db.session.flush()
        content = data.get("content") or ""
        if data.get("size"):
            content = (content + f"\nQuy mô dự kiến: {data['size']} người").strip()
        db.session.add(EventDetail(event_id=event.id, loai=data.get("type") or "other", noi_dung=content,
                                   dia_diem=data.get("location") or "", phu_trach_id=actor.id,
                                   tat_ca_thanh_vien=not data.get("ban_id")))
        if data.get("ban_id"):
            db.session.add(EventTargetBan(event_id=event.id, ban_id=data["ban_id"]))
    load, profiles = _workload([task["assignee_id"] for task in data["tasks"]])
    for task in data["tasks"]:
        member = db.session.get(User, task["assignee_id"])
        deadline = task_deadline(draft, task)
        if not approved(member) or task["ban_id"] not in {link.ban_id for link in member.ban_links}:
            raise DomainError(f"{member.ho_ten} không còn thuộc ban của task \"{task['name']}\".")
        if not _free(member, deadline, load, profiles):
            raise DomainError(f"{member.ho_ten} đã kín việc hoặc báo bận tới hạn task \"{task['name']}\"; "
                              "hãy đổi người rồi duyệt lại.")
        load[member.id] = load.get(member.id, 0) + 1
        db.session.add(Task(event_id=event.id, ten_task=task["name"], mo_ta=task.get("description") or "",
                            deadline=deadline, assignee_id=member.id, ban_id=task["ban_id"],
                            trang_thai="dang_lam"))
    draft.reviewed_by_id, draft.reviewed_at, draft.result_event_id = actor.id, utcnow(), event.id
    audit(actor, "event_draft_approve", draft, {"event_id": event.id, "tasks": len(data["tasks"])})
    db.session.commit()
    return event


def discard(actor, draft):
    """Bỏ bản nháp (không ảnh hưởng dữ liệu thật). Commit."""
    require(can_manage_events(actor))
    _claim(draft, "discarded")
    draft.reviewed_by_id, draft.reviewed_at = actor.id, utcnow()
    audit(actor, "event_draft_discard", draft)
    db.session.commit()


def _event_code(name, start):
    letters = "".join(word[0] for word in _normalize(name).upper().split() if word[:1].isalnum())[:6] or "EV"
    base = f"{letters}{start:%y%m%d}"
    code, suffix = base, 2
    while Event.query.filter_by(ma_su_kien=code).first():
        code, suffix = f"{base}-{suffix}", suffix + 1
    return code[:30]


# ---------------------------------------------------------------- trả lời trong chat
def _member_name(member_id):
    member = db.session.get(User, member_id) if member_id else None
    return member.ho_ten if member else None


def summary(draft):
    """Tóm tắt bản nháp dạng văn bản (cho trang AI, API và làm dữ kiện khi model viết câu trả lời)."""
    data, event = draft.data, _event_of(draft)
    window = event_window(draft)
    lines = [f"Bản nháp #{draft.id}: " + (f"thêm task cho sự kiện {event.ten_su_kien} ({event.ma_su_kien})"
                                         if event else (data.get("name") or "(chưa có tên)"))]
    if not event:
        when = (f"{window[0]:%d/%m/%Y}" + ("" if not (data.get("start") or data.get("end")) else
                f", {window[0]:%H:%M}–{window[1]:%H:%M}")) if window else "chưa có ngày"
        ban = db.session.get(Ban, data["ban_id"]).ten_ban if data.get("ban_id") else "tất cả thành viên"
        lines.append(f"Thời gian: {when}. Địa điểm: {data.get('location') or 'chưa có'}. Dành cho: {ban}.")
    for index, task in enumerate(data.get("tasks", []), 1):
        ban = db.session.get(Ban, task["ban_id"]).ten_ban if task.get("ban_id") else "chưa có ban"
        owner = _member_name(task.get("assignee_id")) or "chưa có người"
        if task.get("assignee_by") == "ai":
            owner += " (AI gợi ý)"
        deadline = task_deadline(draft, task)
        due = f"hạn {deadline:%H:%M %d/%m}" if deadline else (
            "tại sự kiện" if task.get("days_before", 0) <= 0 else f"trước sự kiện {task['days_before']} ngày")
        lines.append(f"{index}. {task['name']} – ban {ban} – {owner} – {due}")
    return "\n".join(lines)


def _ask(draft):
    """Một câu hỏi tiếp theo (tối đa 2 ý quan trọng nhất), hoặc lời nhắc duyệt khi đã đủ."""
    gaps = missing(draft)
    codes = [code for code, _label in gaps]
    errors = problems(draft)
    if not gaps and not errors:
        return f"Đủ thông tin rồi. Bạn xem lại rồi bấm Duyệt ở bản nháp #{draft.id} để tạo sự kiện và task nhé."
    questions = []
    if "date" in codes:
        questions.append("bạn định tổ chức ngày nào? Có ngày là mình tính được hạn cho từng việc")
    if "name" in codes:
        questions.append("sự kiện tên là gì?")
    if "task_ban" in codes and len(questions) < 2:
        questions.append("các việc này giao cho ban nào?")
    if "assignee" in codes and len(questions) < 2:
        label = dict(gaps)["assignee"]
        questions.append(f"còn thiếu {label}; bạn tự chọn hay để mình gợi ý theo kỹ năng và số việc đang làm?")
    if not questions and errors:
        questions.append(errors[0][:1].lower() + errors[0][1:])
    return ("Còn một chút nữa: " + "; ".join(questions)).rstrip("?") + "?"


def reply(draft, changes=None, created=False):
    """Kết quả trả về cho chat: câu trả lời tự nhiên + dữ kiện để kiểm chứng."""
    data, event = draft.data, _event_of(draft)
    names = [task["name"] for task in data.get("tasks", [])]
    if created:
        head = (f"Mình nháp sẵn {len(names)} việc cho sự kiện {event.ten_su_kien}"
                if event else f"Mình nháp sẵn sự kiện \"{data.get('name') or 'chưa đặt tên'}\"")
        head += f" theo khung \"{data.get('template')}\": " + ", ".join(names[:6])
        head += (f" và {len(names) - 6} việc khác." if len(names) > 6 else ".")
        if changes:
            head += " Đã ghi nhận " + ", ".join(changes) + "."
    else:
        head = ("Đã cập nhật " + ", ".join(changes) + ".") if changes else "Bản nháp chưa thay đổi."
    text = f"{head} {_ask(draft)}"
    return {"intent": "event_draft", "preview": True, "provider": "database", "verification": "draft",
            "draft_id": draft.id, "answer": text, "facts": summary(draft)}


def is_draft_message(actor, message, conversation=None):
    """True nếu câu chat là yêu cầu lập bản nháp hoặc chỉnh bản nháp đang bàn (không ghi gì)."""
    if not can_manage_events(actor):
        return bool(draft_intent(message))
    if draft_intent(message) == "event":
        return True
    draft = active_draft(actor, conversation)
    if draft is not None:
        try:
            if parse_edits(draft, message):
                return True
        except DomainError:
            return True
    return draft_intent(message) == "tasks"


def maybe_handle_draft(actor, message, conversation=None):
    """Điểm vào từ chat: lập bản nháp mới, hoặc chỉnh bản nháp đang bàn. None nếu câu không liên quan."""
    intent = draft_intent(message)
    if intent and not can_manage_events(actor):
        require(False, "Chỉ người có quyền quản lý sự kiện được lập bản nháp sự kiện.")
    if intent == "event":
        draft = create_draft(actor, message, conversation)
        return reply(draft, created=True)
    draft = active_draft(actor, conversation)
    if draft is not None:
        try:
            ops = parse_edits(draft, message)
        except DomainError as exc:
            return {**reply(draft), "answer": f"{exc} {_ask(draft)}", "verification": "clarification"}
        if ops:
            if ops[0][0] == "discard":
                discard(actor, draft)
                return {"intent": "event_draft_discarded", "preview": True, "provider": "database",
                        "verification": "draft", "answer": f"Đã bỏ bản nháp #{draft.id}; chưa có gì được tạo."}
            try:
                changes = apply_ops(actor, draft, ops)
            except DomainError as exc:
                db.session.rollback()
                return {**reply(draft), "answer": f"{exc} {_ask(draft)}", "verification": "clarification"}
            db.session.commit()
            return reply(draft, changes)
    if intent == "tasks":
        event = _open_event_in(message)
        if event is None:
            return {"intent": "event_draft_needs_event", "preview": True, "provider": "database",
                    "verification": "clarification",
                    "answer": "Bạn muốn chia task cho sự kiện nào? Ghi tên hoặc mã sự kiện đang mở giúp mình, "
                              "hoặc mô tả sự kiện mới để mình nháp cả sự kiện lẫn task."}
        draft = create_draft(actor, message, conversation, event=event)
        return reply(draft, created=True)
    return None
