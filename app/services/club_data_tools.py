"""Công cụ đọc dữ liệu CÓ THAM SỐ cho trợ lý AI.

Nguyên tắc (giống verified_queries):
- Model chỉ CHỌN công cụ và gợi ý tham số; server tự trích tham số từ câu hỏi gốc,
  chỉ nhận gợi ý của model khi gợi ý đó bám vào câu hỏi (ban/sự kiện phải được nhắc
  trong câu) hoặc là giá trị trong danh sách cho phép (mức hoạt động, trạng thái…).
- Mọi số liệu do truy vấn ORM tính ra; model không viết SQL, không có quyền ghi.
- Quyền xem do server kiểm tra (access.py), không phụ thuộc vào model.

Ba công cụ:
- member_stats: đếm/liệt kê thành viên theo ban, mức hoạt động, trạng thái;
  hoặc mức hoạt động của MỘT thành viên.
- event_participation: đăng ký và điểm danh của MỘT sự kiện (sắp tới gần nhất,
  vừa diễn ra, đang diễn ra hoặc theo tên/mã).
- event_list: sự kiện sắp tới / đang diễn ra / đã diễn ra gần đây.
"""
import re

from app import db
from app.models import (
    Attendance, Ban, Event, EventRegistration, MemberRecord, User, UserBan,
)
from .access import (
    active_members, approved, can_manage_events, can_manage_members, directory_members, is_board,
    require, visible_members,
)
from .activities import DEFAULT_THRESHOLDS, activity_stats, setting
from .assistant import _normalize
from .common import club_now

TOOLS = ("member_stats", "event_participation", "event_list")

ACTIVITY_LEVELS = {
    "tich_cuc": "Tích cực",
    "binh_thuong": "Bình thường",
    "it_hoat_dong": "Ít hoạt động",
    "khong_hoat_dong": "Không hoạt động",
}
MEMBER_STATES = {"active": "đang sinh hoạt", "paused": "tạm nghỉ"}
EVENT_SELECTORS = ("next", "latest", "ongoing", "named")
EVENT_DETAILS = ("summary", "registered", "present", "absent")
EVENT_WINDOWS = ("upcoming", "ongoing", "past")
OUTPUTS = ("count", "list")

# Gợi ý tham số cho model (dùng trong JSON schema của bước lập kế hoạch).
ARGS_SCHEMA = {
    "type": "object",
    "properties": {
        "ban": {"type": "string", "description": "Tên ban đúng như câu hỏi; rỗng nếu không nhắc"},
        "activity": {"type": "string", "enum": ["", *ACTIVITY_LEVELS]},
        "state": {"type": "string", "enum": ["", *MEMBER_STATES]},
        "event": {"type": "string", "description": "Tên hoặc mã sự kiện đúng như câu hỏi"},
        "selector": {"type": "string", "enum": ["", *EVENT_SELECTORS]},
        "detail": {"type": "string", "enum": ["", *EVENT_DETAILS]},
        "window": {"type": "string", "enum": ["", *EVENT_WINDOWS]},
        "output": {"type": "string", "enum": ["", *OUTPUTS]},
    },
}

_LIST_LIMIT = 50
_ACTIVITY_PHRASES = (
    ("khong_hoat_dong", ("khong hoat dong", "khong con hoat dong", "khong tham gia")),
    ("it_hoat_dong", ("it hoat dong", "it tham gia", "hoat dong it", "hoat dong yeu")),
    ("binh_thuong", ("binh thuong", "trung binh")),
    ("tich_cuc", ("tich cuc", "nang dong", "cham chi", "hoat dong nhieu", "tham gia nhieu",
                  "hoat dong tot", "cham di", "hay di", "di deu", "nhiet tinh", "sieng")),
)
_COUNT_WORDS = re.compile(r"\b(?:bao nhieu|may|so luong|tong so|dem|co tong)\b")
_LIST_WORDS = re.compile(r"\b(?:liet ke|danh sach|nhung ai|la ai|gom ai|co ai|ai la|ke ten|ten cac|ten nhung"
                         r"|nhung (?:ban|nguoi|thanh vien) nao|ai (?:da|dang|chua|vang|co mat|di))\b"
                         r"|^ai\b")
_MEMBER_WORDS = re.compile(r"\b(?:thanh vien|nguoi|ban nao|sinh vien|members?|nhan su)\b")
_PARTICIPATION_WORDS = re.compile(
    r"\b(?:dang ky|tham gia|tham du|co mat|vang|diem danh|check ?in|den du|di muon|"
    r"bao nhieu nguoi|may nguoi|so nguoi|thanh vien|nguoi)\b")
HIGHEST_ABSENCE = re.compile(r"\b(?:cao nhat|nhieu nhat|vang nhat)\b")
_FUND_WORDS = re.compile(r"\b(?:quy|khoan thu|da thu|chua dong|dong thieu|thieu tien|"
                         r"thanh toan|dong tien|tien quy|qua han)\b")


def is_highest_absence(text):
    """Câu hỏi "sự kiện nào vắng nhiều nhất" (luật cũ trong RulesProvider xử lý)."""
    return bool(HIGHEST_ABSENCE.search(text) and _has("vang", text))


def _has(pattern, text):
    """Tìm cụm từ trọn vẹn (không khớp giữa chừng một từ khác)."""
    return re.search(r"(?<![a-z0-9])" + re.escape(pattern) + r"(?![a-z0-9])", text) is not None


def _empty_args():
    """Bộ tham số rỗng (mọi khóa đều có mặt)."""
    return {key: "" for key in ARGS_SCHEMA["properties"]}


# ---------------------------------------------------------------- trích tham số
def _bans_in(text):
    """Các ban có tên xuất hiện trong câu (đã chuẩn hóa)."""
    found = []
    for ban in Ban.query.order_by(Ban.id).all():
        name = _normalize(ban.ten_ban).strip()
        if name and _has(name, text):
            found.append(ban)
    return found


def _events_in(text):
    """Các sự kiện (không tính nháp) có mã hoặc tên xuất hiện trong câu.

    Bỏ khớp nằm gọn trong khớp dài hơn của sự kiện khác (vd. "CTF" trong "FIA CTF Fall").
    """
    matches = []
    for event in Event.query.filter(Event.trang_thai != "draft").order_by(Event.id).all():
        for value in (event.ten_su_kien, event.ma_su_kien):
            candidate = _normalize(value).strip()
            if len(candidate) >= 3 and _has(candidate, text):
                matches.append((candidate, event))
                break
    return [event for candidate, event in matches
            if not any(other != candidate and candidate in other for other, _e in matches)]


def parse_args(question):
    """Trích tham số từ câu hỏi gốc bằng luật (không dùng model)."""
    text = _normalize(question)
    args = _empty_args()
    for level, phrases in _ACTIVITY_PHRASES:
        if any(_has(phrase, text) for phrase in phrases):
            args["activity"] = level
            break
    if _has("tam nghi", text):
        args["state"] = "paused"
    elif _has("dang sinh hoat", text):
        args["state"] = "active"
    bans = _bans_in(text)
    if len(bans) == 1:
        args["ban"] = bans[0].ten_ban
    elif len(bans) > 1:
        args["ban"] = "|".join(ban.ten_ban for ban in bans)
    events = _events_in(text)
    if len(events) == 1:
        args["event"] = events[0].ma_su_kien
        args["selector"] = "named"
    elif len(events) > 1:
        args["event"] = "|".join(event.ma_su_kien for event in events)
        args["selector"] = "named"
    if not args["selector"]:
        if re.search(r"\b(?:sap dien ra|sap toi|sap to chuc|tiep theo|sap\b)", text):
            args["selector"] = "next"
        elif re.search(r"\b(?:dang dien ra|dang to chuc|hien tai|hom nay)\b", text):
            args["selector"] = "ongoing"
        elif re.search(r"\b(?:gan nhat|gan day nhat|vua roi|vua qua|vua dien ra|vua ket thuc|"
                       r"da dien ra|da ket thuc|truoc do|lan truoc|vua to chuc)\b", text):
            args["selector"] = "latest"
    if re.search(r"\bvang\b", text):
        args["detail"] = "absent"
    elif re.search(r"\b(?:co mat|den du|tham du|da den|check ?in)\b", text):
        args["detail"] = "present"
    elif re.search(r"\b(?:dang ky|ghi danh)\b", text):
        args["detail"] = "registered"
    if re.search(r"\b(?:dang dien ra|dang to chuc)\b", text):
        args["window"] = "ongoing"
    elif re.search(r"\b(?:da dien ra|da ket thuc|da qua|gan day|vua qua|truoc day)\b", text):
        args["window"] = "past"
    elif re.search(r"\b(?:sap|sap toi|sap dien ra|tiep theo)\b", text):
        args["window"] = "upcoming"
    if _COUNT_WORDS.search(text):
        args["output"] = "count"
    elif _LIST_WORDS.search(text):
        args["output"] = "list"
    return args


def merge_args(question, model_args):
    """Gộp tham số luật trích được với gợi ý của model.

    - Giá trị luật trích được từ chính câu chữ luôn thắng (không để model bỏ bộ lọc).
    - Giá trị liệt kê (enum) của model chỉ được dùng khi luật không thấy gì
      (model hiểu cách nói khác, vd. "chăm đi sự kiện" = tích cực).
    - Ban / sự kiện do model nêu chỉ được dùng nếu thực sự xuất hiện trong câu hỏi.
    """
    args = parse_args(question)
    if not isinstance(model_args, dict):
        return args
    allowed = {"activity": ACTIVITY_LEVELS, "state": MEMBER_STATES, "selector": EVENT_SELECTORS,
               "detail": EVENT_DETAILS, "window": EVENT_WINDOWS, "output": OUTPUTS}
    for key, values in allowed.items():
        value = model_args.get(key)
        if not args[key] and isinstance(value, str) and value in values:
            args[key] = value
    text = _normalize(question)
    for key in ("ban", "event"):
        value = model_args.get(key)
        if args[key] or not isinstance(value, str) or not value.strip():
            continue
        if key == "ban":
            matched = [ban for ban in _bans_in(_normalize(value)) if _has(_normalize(ban.ten_ban), text)]
            if len(matched) == 1:
                args["ban"] = matched[0].ten_ban
        else:
            matched = [event for event in _events_in(_normalize(value))
                       if any(_has(_normalize(v), text) for v in (event.ma_su_kien, event.ten_su_kien))]
            if len(matched) == 1:
                args["event"], args["selector"] = matched[0].ma_su_kien, "named"
    # Model chỉ được chọn selector "named" khi câu có tên sự kiện.
    if args["selector"] == "named" and not args["event"]:
        args["selector"] = ""
    return args


# ---------------------------------------------------------------- nhận diện (luật)
def _member_target(actor, question, scope):
    """Một thành viên được nhắc đích danh (MSSV/họ tên) hoặc chính người hỏi; None nếu không có."""
    text = _normalize(question)
    rows = scope.order_by(User.id).all()
    hits = []
    for row in rows:
        if _has(_normalize(row.mssv), text) or (len(row.ho_ten) >= 4 and _has(_normalize(row.ho_ten), text)):
            hits.append(row)
    if len(hits) == 1:
        return hits[0]
    if not hits and re.search(r"\b(?:toi|minh|tui|ban than toi)\b", text) and \
            not re.search(r"\b(?:cho toi|giup toi|cho minh|giup minh|toi muon|minh muon)\b", text):
        return next((row for row in rows if row.id == actor.id), None)
    return None


def detect_tool(question):
    """Đoán công cụ phù hợp chỉ bằng luật; None nếu không chắc (để luật cũ xử lý)."""
    text = _normalize(question)
    if _FUND_WORDS.search(text) or _has("bao cao", text):
        return None
    if re.search(r"\b(?:tao|chia|giao|phan cong|de xuat|soan|gui|sua|xoa|cap nhat)\b", text) and \
            not _COUNT_WORDS.search(text):
        return None
    named_event = bool(_events_in(text))
    has_event = _has("su kien", text) or named_event or bool(
        re.search(r"\b(?:workshop|ctf|talkshow|training|buoi)\b", text))
    if is_highest_absence(text):
        return None  # luật cũ: sự kiện vắng nhiều nhất
    activity = any(_has(phrase, text) for _level, phrases in _ACTIVITY_PHRASES for phrase in phrases)
    picks_event = named_event or re.search(
        r"\b(?:sap|dang dien ra|gan nhat|vua roi|vua qua|vua dien ra|hom nay)\b", text)
    if activity and not picks_event and not any(
            _has(phrase, text) for phrase in ("khong hoat dong", "khong tham gia")):
        return "member_stats"  # "mấy bạn chăm đi sự kiện là ai" hỏi về thành viên
    if has_event and _PARTICIPATION_WORDS.search(text):
        return "event_participation"
    if has_event:
        if re.search(r"\b(?:dang dien ra|dang to chuc|da dien ra|da ket thuc|da qua|gan day|vua qua)\b", text):
            return "event_list"
        return None
    if any(_has(phrase, text) for phrase in ("khong hoat dong", "khong tham gia", "60 ngay")):
        return None  # luật cũ: danh sách không hoạt động
    if _has("tam nghi", text):
        return "member_stats"
    if _bans_in(text) and (_COUNT_WORDS.search(text) or _LIST_WORDS.search(text)):
        return "member_stats"  # "Ban Kỹ thuật gồm những ai?"
    if _MEMBER_WORDS.search(text) and (activity or _COUNT_WORDS.search(text) or _LIST_WORDS.search(text)
                                       or _has("tam nghi", text)):
        return "member_stats"
    if activity and re.search(r"\b(?:co|la|duoc xep|xep loai|muc)\b", text):
        return "member_stats"  # "SE180011 có tích cực không"
    return None


def _result(intent, answer, sources=None, verification="database", facts=None):
    """Kết quả chuẩn cho chat/agent."""
    result = {"intent": intent, "provider": "database", "verification": verification,
              "answer": answer}
    if sources:
        result["sources"] = sources
    if facts:
        result["facts"] = facts
    return result


def _clarify(answer):
    """Câu hỏi cần nói rõ thêm (không đoán)."""
    return _result("data_needs_details", answer, verification="clarification")


def _unsupported(answer):
    """Bộ lọc chưa hỗ trợ: báo rõ thay vì trả dữ liệu rộng hơn câu hỏi."""
    return _result("unsupported_query", answer, verification="unsupported")


_TIME_FILTER = re.compile(r"\b20\d{2}\b|\b\d{1,2}[/-]\d{1,2}\b|\b(?:thang|tuan|ky|nam)\s+"
                          r"(?:nay|truoc|sau|\d{1,2})\b|\b(?:hom qua|ngay mai)\b")


# ---------------------------------------------------------------- member_stats
def _criteria_note():
    """Giải thích ngắn quy tắc xếp loại hoạt động hiện tại của CLB."""
    rules = setting("activity_thresholds", DEFAULT_THRESHOLDS)
    return (f"Quy tắc xếp loại: Tích cực ≥ {rules['active']}% sự kiện đủ điều kiện, "
            f"Bình thường ≥ {rules['normal']}%, thấp hơn là Ít hoạt động; "
            f"không có lần điểm danh nào trong {rules['inactive_days']} ngày là Không hoạt động.")


def member_stats(actor, args, question):
    """Đếm/liệt kê thành viên theo ban, mức hoạt động, trạng thái (trong phạm vi được xem)."""
    text = _normalize(question)
    if _TIME_FILTER.search(text):
        return _unsupported("Thống kê thành viên hiện chỉ tính theo dữ liệu hiện tại, chưa lọc theo "
                            "ngày/tháng/kỳ. Tôi chưa trả số liệu rộng hơn câu hỏi.")
    if "|" in args["ban"]:
        return _clarify("Câu hỏi nhắc nhiều ban. Hãy hỏi từng ban một để số liệu không bị gộp nhầm.")
    level = args["activity"]
    detailed = bool(level or args["state"])
    if detailed:
        # Mức hoạt động / tạm nghỉ là thông tin quản lý, không thuộc danh bạ cơ bản.
        member = _member_target(actor, question, visible_members(actor))
        if member is not None and (member.id == actor.id or can_manage_members(actor)):
            return _one_member_activity(member)
        require(can_manage_members(actor),
                "Mức hoạt động và trạng thái của thành viên khác chỉ dành cho người có quyền "
                "quản lý thành viên. Bạn có thể hỏi về chính mình.")
        query = visible_members(actor)
    else:
        require(approved(actor), "Tài khoản chưa được duyệt.")
        query = directory_members(actor)
    filters = []
    ban = None
    if args["ban"]:
        ban = Ban.query.filter_by(ten_ban=args["ban"]).first()
        if ban is None:
            return _clarify(f"Không tìm thấy ban \"{args['ban']}\". Hãy ghi đúng tên ban.")
        query = query.join(UserBan, UserBan.user_id == User.id).filter(UserBan.ban_id == ban.id)
        filters.append(f"ban {ban.ten_ban}")
    if args["state"] == "paused":
        query = query.filter(MemberRecord.tinh_trang == "paused")
        filters.append("đang tạm nghỉ")
    elif args["state"] == "active":
        query = query.filter(db.or_(MemberRecord.tinh_trang.is_(None),
                                    MemberRecord.tinh_trang != "paused"))
        filters.append("đang sinh hoạt")
    members = query.distinct().order_by(User.ho_ten, User.id).all()
    if level:
        label = ACTIVITY_LEVELS[level]
        members = [row for row in members if activity_stats(row)["classification"] == label]
        filters.append(f"mức {label}")
    scope = ", ".join(filters) or "toàn CLB"
    names = [f"- {row.ho_ten} ({row.mssv})" for row in members[:_LIST_LIMIT]]
    lines = [f"Số thành viên ({scope}): {len(members)}."]
    if names and (args["output"] != "count" or len(members) <= 15):
        lines.append("Danh sách:" if args["output"] == "list" or len(members) <= 15 else "Một số thành viên:")
        lines.extend(names)
        if len(members) > _LIST_LIMIT:
            lines.append(f"(Đang hiển thị {_LIST_LIMIT}/{len(members)} người; xem đầy đủ ở trang Thành viên.)")
    if level:
        lines.append(_criteria_note())
    lines.append("Nguồn: danh sách thành viên đang hoạt động trong database CLB"
                 + (" và thống kê điểm danh." if level else "."))
    return _result("member_stats", "\n".join(lines), {"user": [row.id for row in members[:300]]},
                   facts={"count": len(members), "filters": scope})


def _one_member_activity(member):
    """Mức hoạt động của một thành viên kèm số liệu để người đọc tự kiểm."""
    stats = activity_stats(member)
    latest = f"{stats['latest']:%d/%m/%Y}" if stats["latest"] else "chưa có"
    answer = (f"{member.ho_ten} ({member.mssv}) đang được xếp loại: {stats['classification']}.\n"
              f"- Có mặt {stats['present']}/{stats['eligible']} sự kiện đủ điều kiện ({stats['rate']}%), "
              f"vắng {stats['absent']}, vắng có phép {stats['excused']}, đi muộn {stats['late']}.\n"
              f"- Điểm hoạt động: {stats['points']}; lần điểm danh gần nhất: {latest}.\n"
              + _criteria_note())
    return _result("member_activity", answer, {"user": [member.id]},
                   facts={"classification": stats["classification"]})


# ---------------------------------------------------------------- sự kiện
def _timing_label(event, now):
    """Nhãn theo thời gian thực (cột trạng thái có thể chưa được cập nhật)."""
    if event.trang_thai == "cancelled":
        return "đã hủy"
    if event.thoi_gian_bat_dau > now:
        return "sắp diễn ra"
    if event.thoi_gian_ket_thuc < now:
        return "đã kết thúc"
    return "đang diễn ra"


def _live_events():
    """Sự kiện không phải nháp và chưa hủy."""
    return Event.query.filter(Event.trang_thai.notin_(["cancelled", "draft"]))


def _has_attendance(event):
    """Sự kiện đã có ít nhất một kết quả điểm danh (khác "pending")."""
    return db.session.query(Attendance.id).join(EventRegistration).filter(
        EventRegistration.event_id == event.id, Attendance.trang_thai != "pending").first() is not None


def _pick_event(args):
    """Chọn đúng MỘT sự kiện theo selector; trả (event, câu hỏi lại nếu chưa chọn được)."""
    now = club_now()
    selector = args["selector"]
    if selector == "named":
        codes = [code for code in args["event"].split("|") if code]
        if len(codes) != 1:
            return None, "Câu hỏi nhắc nhiều sự kiện. Hãy hỏi từng sự kiện một (ghi tên hoặc mã)."
        return Event.query.filter_by(ma_su_kien=codes[0]).first(), None
    if selector == "next":
        event = (_live_events().filter(Event.thoi_gian_bat_dau >= now)
                 .order_by(Event.thoi_gian_bat_dau, Event.id).first())
        return event, None if event else "Hiện chưa có sự kiện nào sắp diễn ra."
    if selector == "ongoing":
        events = (_live_events().filter(Event.thoi_gian_bat_dau <= now, Event.thoi_gian_ket_thuc >= now)
                  .order_by(Event.thoi_gian_bat_dau.desc()).all())
        if len(events) > 1:
            return None, ("Đang có nhiều sự kiện diễn ra: " + ", ".join(f"{e.ten_su_kien} ({e.ma_su_kien})" for e in events)
                          + ". Hãy ghi tên hoặc mã sự kiện.")
        return (events[0], None) if events else (None, "Hiện không có sự kiện nào đang diễn ra.")
    if selector == "latest":
        ended = (_live_events().filter(Event.thoi_gian_ket_thuc <= now)
                 .order_by(Event.thoi_gian_ket_thuc.desc(), Event.id.desc()))
        event = ended.first()
        if event is None:
            return None, "Chưa có sự kiện nào đã diễn ra."
        if args["detail"] in {"present", "absent"} and not _has_attendance(event):
            checked = next((row for row in ended.limit(20) if _has_attendance(row)), None)
            if checked is not None:
                args["note"] = (f"Sự kiện vừa kết thúc gần nhất là {event.ten_su_kien} ({event.ma_su_kien}) "
                                "nhưng chưa có điểm danh; dưới đây là sự kiện gần nhất đã điểm danh.")
                return checked, None
        return event, None
    candidates = (_live_events().filter(Event.thoi_gian_ket_thuc >= now)
                  .order_by(Event.thoi_gian_bat_dau).limit(5).all())
    names = ", ".join(f"{e.ten_su_kien} ({e.ma_su_kien})" for e in candidates)
    return None, ("Bạn hỏi sự kiện nào? Hãy ghi tên/mã sự kiện, hoặc nói \"sắp diễn ra\", "
                  "\"đang diễn ra\", \"vừa diễn ra\"." + (f" Sự kiện hiện có: {names}." if names else ""))


def event_participation(actor, args, question):
    """Đăng ký và điểm danh của một sự kiện (hoặc của một thành viên trong sự kiện đó)."""
    text = _normalize(question)
    if _TIME_FILTER.search(text) and args["selector"] != "named":
        return _unsupported("Tra cứu tham gia sự kiện chưa hỗ trợ chọn sự kiện theo ngày/tháng. "
                            "Hãy ghi tên hoặc mã sự kiện.")
    if args["selector"] == "named" and "|" in args["event"]:
        return _clarify("Câu hỏi nhắc nhiều sự kiện. Hãy hỏi từng sự kiện một (ghi tên hoặc mã).")
    if "|" in args["ban"]:
        return _clarify("Câu hỏi nhắc nhiều ban. Hãy hỏi từng ban một.")
    args = dict(args)
    if args["detail"] in {"present", "absent"} and not args["selector"]:
        args["selector"] = "latest"
    event, question_back = _pick_event(args)
    if event is None:
        return _clarify(question_back or "Không tìm thấy sự kiện được hỏi.")
    manager = can_manage_events(actor) or is_board(actor)
    member = _member_target(actor, question, active_members() if manager
                            else active_members().filter(User.id == actor.id))
    if member is None:
        require(manager,
                "Số liệu đăng ký/điểm danh của sự kiện chỉ dành cho Ban điều hành. "
                "Bạn có thể hỏi về chính mình, vd. \"tôi đã đăng ký sự kiện X chưa\".")
    rows = (db.session.query(EventRegistration, Attendance, User)
            .join(User, User.id == EventRegistration.user_id)
            .outerjoin(Attendance, Attendance.registration_id == EventRegistration.id)
            .filter(EventRegistration.event_id == event.id,
                    EventRegistration.trang_thai != "cancelled")
            .order_by(User.ho_ten, User.id))
    ban = None
    if args["ban"]:
        ban = Ban.query.filter_by(ten_ban=args["ban"]).first()
        if ban is not None:
            rows = rows.join(UserBan, UserBan.user_id == User.id).filter(UserBan.ban_id == ban.id)
    rows = rows.all()
    now = club_now()
    started = event.thoi_gian_bat_dau <= now
    header = (f"Sự kiện {event.ten_su_kien} ({event.ma_su_kien}), bắt đầu "
              f"{event.thoi_gian_bat_dau:%d/%m/%Y %H:%M}, {_timing_label(event, now)}"
              + (f" — chỉ tính ban {ban.ten_ban}" if ban else "") + ".")
    sources = {"event": [event.id]}
    if member is not None:
        mine = next(((reg, att) for reg, att, user in rows if user.id == member.id), None)
        if mine is None:
            status = "chưa đăng ký (hoặc đã hủy đăng ký)"
        elif mine[1] is None or mine[1].trang_thai == "pending":
            status = "đã đăng ký, chưa có kết quả điểm danh"
        else:
            status = "đã đăng ký, điểm danh: " + {"on_time": "có mặt đúng giờ", "late": "đi muộn",
                                                   "excused": "vắng có phép", "absent": "vắng"}.get(
                mine[1].trang_thai, mine[1].trang_thai)
        sources["user"] = [member.id]
        return _result("event_member_status", f"{header}\n{member.ho_ten} ({member.mssv}): {status}.",
                       sources)
    counts = {"on_time": 0, "late": 0, "excused": 0, "absent": 0, "pending": 0}
    for _reg, att, _user in rows:
        counts[att.trang_thai if att and att.trang_thai in counts else "pending"] += 1
    present = counts["on_time"] + counts["late"]
    checked = present + counts["excused"] + counts["absent"] > 0
    lines = ([args["note"]] if args.get("note") else []) + [
        header, f"- Đăng ký (chưa hủy): {len(rows)} người."]
    if not started:
        lines.append("- Sự kiện chưa diễn ra nên chưa có điểm danh.")
    elif not checked:
        lines.append("- Chưa có kết quả điểm danh.")
    else:
        lines.append(f"- Có mặt: {present} (đúng giờ {counts['on_time']}, đi muộn {counts['late']}); "
                     f"vắng: {counts['absent']}; vắng có phép: {counts['excused']}; "
                     f"chưa điểm danh: {counts['pending']}.")
    picks = {"absent": ("Người vắng", {"absent"}),
             "present": ("Người có mặt", {"on_time", "late"}),
             "registered": ("Người đã đăng ký", None)}
    if args["detail"] in picks and (args["output"] == "list" or args["detail"] != "registered"
                                    or len(rows) <= 15):
        title, states = picks[args["detail"]]
        chosen = [user for _reg, att, user in rows
                  if states is None or (att is not None and att.trang_thai in states)]
        if states is None or checked:
            lines.append(f"{title} ({len(chosen)}):")
            lines.extend(f"- {user.ho_ten} ({user.mssv})" for user in chosen[:_LIST_LIMIT])
            if not chosen:
                lines.append("- Không có.")
        sources["user"] = [user.id for user in chosen[:300]]
    lines.append("Nguồn: đăng ký và điểm danh trong database CLB.")
    return _result("event_participation", "\n".join(lines), sources,
                   facts={"registered": len(rows), "present": present, "absent": counts["absent"]})


def event_list(actor, args, question):
    """Sự kiện sắp tới / đang diễn ra / đã diễn ra gần đây (thông tin chung, ai cũng xem được)."""
    require(approved(actor), "Tài khoản chưa được duyệt.")
    now = club_now()
    window = args["window"] or "upcoming"
    if window == "ongoing":
        query = (_live_events().filter(Event.thoi_gian_bat_dau <= now, Event.thoi_gian_ket_thuc >= now)
                 .order_by(Event.thoi_gian_bat_dau))
        title = "Sự kiện đang diễn ra"
    elif window == "past":
        query = (_live_events().filter(Event.thoi_gian_ket_thuc < now)
                 .order_by(Event.thoi_gian_ket_thuc.desc()).limit(10))
        title = "Sự kiện đã diễn ra gần đây (tối đa 10)"
    else:
        query = (_live_events().filter(Event.thoi_gian_bat_dau >= now)
                 .order_by(Event.thoi_gian_bat_dau).limit(20))
        title = "Sự kiện sắp diễn ra"
    events = query.all()
    rows = [f"- {e.ten_su_kien} ({e.ma_su_kien}) - {e.thoi_gian_bat_dau:%d/%m/%Y %H:%M}" for e in events]
    return _result("event_list", f"{title}: {len(events)}\n" + ("\n".join(rows) or "- Không có."),
                   {"event": [e.id for e in events]})


def run_tool(actor, tool, question, model_args=None):
    """Chạy một công cụ có tham số; tham số được gộp và kiểm tra theo câu hỏi gốc."""
    args = merge_args(question, model_args)
    if tool == "member_stats":
        return member_stats(actor, args, question)
    if tool == "event_participation":
        return event_participation(actor, args, question)
    if tool == "event_list":
        return event_list(actor, args, question)
    return None


def answer_by_rules(actor, question):
    """Đường dự phòng không dùng model: nhận diện bằng luật rồi chạy công cụ."""
    tool = detect_tool(question)
    return run_tool(actor, tool, question) if tool else None
