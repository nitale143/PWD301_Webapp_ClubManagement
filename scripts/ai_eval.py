"""Đo độ đúng của Trợ lý AI bằng bộ câu hỏi mẫu (chỉ ĐỌC dữ liệu).

    python scripts/ai_eval.py                               # database test, chế độ luật (không gọi AI)
    python scripts/ai_eval.py --provider env                # dùng AI theo .env (Gemini/Claude) để so sánh
    python scripts/ai_eval.py --db instance/clb.db --as SE180001 -v

Mỗi câu có "intent mong đợi" (trợ lý phải chọn đúng loại tra cứu) và với một số câu,
con số đúng được tính bằng truy vấn ĐỘC LẬP ngay trong script để so với câu trả lời.
Tên ban / sự kiện / MSSV trong câu hỏi được lấy từ database đang dùng, nên script chạy
được trên cả dữ liệu test lẫn dữ liệu thật.

Lưu ý: với --provider env mỗi câu tốn 1–3 lượt gọi model (tính vào quota và AI_MAX_CALLS_PER_MINUTE);
script tự nghỉ giữa các câu (--pause) để không bị chặn vì gọi quá nhanh.
"""
import argparse
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app import create_app, db  # noqa: E402
from app.models import (  # noqa: E402
    Attendance, Ban, Event, EventRegistration, MemberRecord, SYSTEM_ADMIN_ROLE, User, UserBan,
)
from app.services.common import club_now  # noqa: E402


def _members_query():
    """Thành viên đang hoạt động — viết lại độc lập với services/access.py."""
    return (User.query.filter(User.status == "approved", User.chuc_vu != SYSTEM_ADMIN_ROLE)
            .outerjoin(MemberRecord, MemberRecord.user_id == User.id)
            .filter(MemberRecord.luu_tru_luc.is_(None)))


def _fixtures(actor):
    """Chọn ban / sự kiện / thành viên có trong database để điền vào câu hỏi."""
    now = club_now()
    live = Event.query.filter(Event.trang_thai.notin_(["cancelled", "draft"]))
    ban = Ban.query.order_by(Ban.id).first()
    checked = None
    for event in live.filter(Event.thoi_gian_ket_thuc <= now).order_by(Event.thoi_gian_ket_thuc.desc()).limit(20):
        if (db.session.query(Attendance.id).join(EventRegistration)
                .filter(EventRegistration.event_id == event.id, Attendance.trang_thai != "pending").first()):
            checked = event
            break
    upcoming = live.filter(Event.thoi_gian_bat_dau >= now).order_by(Event.thoi_gian_bat_dau).first()
    other = (_members_query().filter(User.id != actor.id).order_by(User.id).first())
    return {"ban": ban, "checked": checked, "upcoming": upcoming, "member": other}


def _count_in_ban(ban):
    return _members_query().join(UserBan, UserBan.user_id == User.id).filter(UserBan.ban_id == ban.id).distinct().count()


def _registered(event):
    return EventRegistration.query.filter(EventRegistration.event_id == event.id,
                                          EventRegistration.trang_thai != "cancelled").count()


def _absent(event):
    return (Attendance.query.join(EventRegistration)
            .filter(EventRegistration.event_id == event.id, Attendance.trang_thai == "absent").count())


# Quyền cần có theo tiền tố câu hỏi: members = MANAGE_MEMBERS, events = MANAGE_EVENTS hoặc BĐH,
# board = BĐH. Không đủ quyền thì câu trả lời đúng là từ chối.
REQUIRED = (
    ("Có bao nhiêu thành viên tích cực?", "members"), ("Liệt kê thành viên ít hoạt động", "members"),
    ("Có ai đang tạm nghỉ", "members"), ("Mấy bạn chăm", "members"),
    ("Thành viên không hoạt động", "members"), ("Có bao nhiêu thành viên trong sự kiện", "events"),
    ("Có bao nhiêu thành viên vắng", "events"), ("Sự kiện gần nhất có bao nhiêu", "events"),
    ("Sự kiện nào có nhiều người vắng", "events_only"), ("Báo cáo tháng", "board"),
    ("Ai vắng ở", "events"), ("Bao nhiêu người đăng ký ", "events"),
)
DENIED = {"permission_denied", "member_needs_details"}


def build_cases(fx):
    """30 câu hỏi mẫu: (câu hỏi, intent chấp nhận, chuỗi phải có trong câu trả lời hoặc None).

    Câu nằm trong PRIVILEGED cần quyền BĐH: khi hỏi bằng tài khoản thành viên thường,
    câu trả lời đúng là TỪ CHỐI (permission_denied) hoặc yêu cầu nói rõ người được hỏi.
    """
    ban, checked, upcoming, member = fx["ban"], fx["checked"], fx["upcoming"], fx["member"]
    cases = [
        ("Có bao nhiêu thành viên tích cực?", {"member_stats"}, None),
        ("Câu lạc bộ đang có bao nhiêu thành viên?", {"member_stats"}, f"{_members_query().count()}"),
        ("Liệt kê thành viên ít hoạt động", {"member_stats"}, None),
        ("Có ai đang tạm nghỉ không?", {"member_stats"}, None),
        ("Mấy bạn chăm đi sự kiện nhất là ai?", {"member_stats"}, None),
        ("Tôi có tích cực không?", {"member_activity"}, None),
        ("Thành viên không hoạt động", {"inactive_members", "member_stats"}, None),
        ("Có bao nhiêu thành viên trong sự kiện sắp diễn ra?", {"event_participation"},
         str(_registered(upcoming)) if upcoming else None),
        ("Có bao nhiêu thành viên vắng mặt trong sự kiện gần nhất?", {"event_participation"},
         str(_absent(checked)) if checked else None),
        ("Sự kiện gần nhất có bao nhiêu người có mặt?", {"event_participation"}, None),
        ("Bao nhiêu người đăng ký sự kiện?", {"data_needs_details"}, None),
        ("Liệt kê sự kiện sắp diễn ra", {"upcoming_events", "event_list"}, None),
        ("Sự kiện nào đang diễn ra?", {"event_list"}, None),
        ("Các sự kiện đã diễn ra gần đây", {"event_list"}, None),
        ("Sự kiện nào có nhiều người vắng nhất?", {"highest_absence"}, None),
        ("Ai chưa đóng quỹ?", {"unpaid_members"}, None),
        ("Tổng đã thu của câu lạc bộ", {"collected_total"}, None),
        ("Kiểm tra tình trạng quỹ", {"fund_overview", "period_ambiguous"}, None),
        ("Báo cáo tháng này", {"verified_report", "monthly_report"}, None),
        ("Xin chào", {"greeting"}, None),
        ("Có bao nhiêu thành viên tích cực tháng trước?", {"unsupported_query"}, None),
        ("Thời tiết hôm nay thế nào?", {"help", "unsupported_query"}, None),
    ]
    if ban is not None:
        cases += [
            (f"Ban {ban.ten_ban} có bao nhiêu thành viên?", {"member_stats"}, str(_count_in_ban(ban))),
            (f"Ban {ban.ten_ban} gồm những ai?", {"member_stats"}, None),
        ]
    if checked is not None:
        cases += [
            (f"Ai vắng ở {checked.ten_su_kien}?", {"event_participation"}, f"vắng: {_absent(checked)}"),
            (f"Bao nhiêu người đăng ký {checked.ma_su_kien}?", {"event_participation"},
             f"{_registered(checked)} người"),
        ]
    if upcoming is not None:
        cases.append((f"Tôi đã đăng ký {upcoming.ten_su_kien} chưa?", {"event_member_status"}, None))
    if member is not None:
        cases += [
            (f"{member.mssv} có tích cực không?", {"member_activity"}, member.ho_ten),
            (f"Hồ sơ của {member.mssv}", {"member_info"}, member.ho_ten),
            (f"Task của {member.mssv}", {"member_tasks"}, member.ho_ten),
        ]
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="instance/test_seed.db", help="file SQLite (mặc định database test)")
    parser.add_argument("--as", dest="mssv", default=None, help="hỏi với tư cách MSSV/tên đăng nhập này "
                        "(mặc định: Chủ nhiệm đầu tiên)")
    parser.add_argument("--provider", choices=["rules", "env"], default="rules",
                        help="rules = không gọi AI; env = dùng AI_PROVIDER trong .env")
    parser.add_argument("--pause", type=float, default=None,
                        help="nghỉ giữa các câu (giây); mặc định 0 với rules, 6 với env")
    parser.add_argument("-v", "--verbose", action="store_true", help="in đầy đủ câu trả lời")
    options = parser.parse_args()

    database = (ROOT / options.db).resolve() if not Path(options.db).is_absolute() else Path(options.db)
    if not database.exists():
        sys.exit(f"Không thấy database {database}")
    config = {"SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(database), "SCHEDULER_ENABLED": False,
              "MAIL_ENABLED": False, "AI_CHAT_MESSAGES_PER_MINUTE": 1000}
    if options.provider == "rules":
        config["AI_PROVIDER"] = "rules"
    app = create_app(config)
    pause = options.pause if options.pause is not None else (6.0 if options.provider == "env" else 0.0)

    from app.services.agent import respond_events
    from app.services.assistant import get_provider

    with app.test_request_context():
        query = User.query
        actor = (query.filter((User.mssv == options.mssv) | (User.mssv == (options.mssv or "").upper())).first()
                 if options.mssv else query.filter_by(chuc_vu="CN", status="approved").first())
        if actor is None:
            sys.exit("Không tìm thấy người hỏi (--as).")
        provider = get_provider()
        print(f"Database: {database.name} · người hỏi: {actor.ho_ten} ({actor.chuc_vu}) · "
              f"provider: {type(provider).__name__}\n")
        fx = _fixtures(actor)
        cases = build_cases(fx)
        passed = 0
        calls = {"n": 0}
        original = getattr(provider, "complete", None)

        def counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        from app.models import Permission
        from app.services.access import has_permission
        allowed = {
            "members": has_permission(actor, Permission.MANAGE_MEMBERS),
            "events": has_permission(actor, Permission.MANAGE_EVENTS) or has_permission(actor, Permission.BOARD),
            "events_only": has_permission(actor, Permission.MANAGE_EVENTS),
            "board": has_permission(actor, Permission.BOARD),
        }
        other = fx["member"]
        for index, (question, intents, must_contain) in enumerate(cases, 1):
            need = next((perm for prefix, perm in REQUIRED if question.startswith(prefix)), None)
            if question == "Bao nhiêu người đăng ký sự kiện?":
                need = None  # chưa nêu sự kiện → hỏi lại trước khi xét quyền
            if other is not None and other.mssv in question:
                need = "members_or_board" if question.startswith("Hồ sơ") or question.startswith("Task") else "members"
                allowed["members_or_board"] = allowed["members"] or allowed["board"]
            if need and not allowed[need]:
                intents, must_contain = DENIED, None
            calls["n"] = 0
            started = time.monotonic()
            # Đăng nhập giả cho quota AI theo người dùng.
            with patch("flask_login.utils._get_user", return_value=actor):
                if original is not None:
                    provider.complete = counting
                with patch("app.services.agent_orchestrator.get_provider", return_value=provider):
                    try:
                        events = list(respond_events(actor, question))
                        result = next(e["result"] for e in events if e.get("type") == "result")
                    except Exception as exc:  # noqa: BLE001 – báo lỗi thay vì dừng cả bộ
                        result = {"intent": f"ERROR {type(exc).__name__}", "answer": str(exc)}
            db.session.rollback()
            intent = str(result.get("intent"))
            answer = str(result.get("answer", ""))
            ok_intent = intent in intents
            ok_value = must_contain is None or must_contain in answer or must_contain in str(
                result.get("verified_answer", ""))
            ok = ok_intent and ok_value
            passed += ok
            mode = result.get("agent_mode", "-")
            extra = "" if ok_value else f" · thiếu '{must_contain}'"
            print(f"{'✔' if ok else '✘'} {index:2}. {question}\n"
                  f"      intent={intent} (mong đợi {'/'.join(sorted(intents))}) · {mode} · "
                  f"{calls['n']} lượt model · {time.monotonic() - started:.1f}s{extra}")
            if options.verbose or not ok:
                print("      " + answer.replace("\n", "\n      ")[:1500])
            if pause and index < len(cases):
                time.sleep(pause)
        print(f"\nĐúng {passed}/{len(cases)} câu ({passed * 100 // len(cases)}%).")
        return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
