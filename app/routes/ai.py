from datetime import timedelta
import json
from uuid import uuid4

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   send_file, abort, Response, stream_with_context, jsonify, current_app)
from flask_login import login_required, current_user

from app import db
from app.models import (
    AIReportSnapshot, AIUsageLog, Ban, EmailProposal, EmailTemplate, Event, ProposalTemplate,
)
from app.models import ChatConversation, User
from app.models import Permission
from app.permissions import approved_required, bdh_required, permission_required
from app.security import require_csrf
from app.services.agent import check_chat_rate, respond, respond_events
from app.services.access import can_manage_events, is_board, actor_scope_stamp
from app.services.common import audit, DomainError
from app.services.chat_conversations import (append_exchange, checked_title,
    conversation_list, create_conversation, message_page, owned_conversation,
    select_conversation, begin_turn, finish_turn)
from app.services import event_drafts
from app.services.event_status import OPEN
from app.services.email_proposals import (can_review_email, edit_email_draft,
                                          reconcile_email_draft, review_email_draft)
from app.services.briefings import (can_access_report, daily_digest, report_data,
                                    report_email_draft, report_text, report_workbook,
                                    save_report)
from app.timeutil import utcnow

ai_bp = Blueprint("ai", __name__)


@ai_bp.post("/ai/stream")
@login_required
@approved_required
def ai_stream():
    """Actual operational progress; never a model's private reasoning trace."""
    require_csrf()
    actor = current_user._get_current_object()
    content = request.form.get("message", "").strip()
    try:
        if not content or len(content) > 2000:
            raise DomainError("Câu hỏi phải có từ 1 đến 2000 ký tự.")
        check_chat_rate(actor)
        if request.form.get("new_conversation") == "1":
            if "conversation_id" in request.form:
                raise DomainError("Chỉ chọn một cuộc trò chuyện.")
            conversation = create_conversation(actor)
        else:
            conversation = select_conversation(actor,
                request.form.get("conversation_id"), create=True, active=True)
        human = begin_turn(actor, conversation, content)
        permission_stamp = actor_scope_stamp(actor)
        conversation_id, user_message_id = conversation.id, human.id
        conversation_title = conversation.title
    except DomainError as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), exc.status
    run_id = uuid4().hex
    actor_id = actor.id

    def frame(event):
        return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    @stream_with_context
    def generate():
        identifiers = {"conversation_id": conversation_id,
                       "message_id": user_message_id, "run_id": run_id}
        saved = False
        # Generator chạy sau khi view return: session DB cũ đã đóng -> nạp lại object
        actor = db.session.get(User, actor_id)
        conversation = db.session.get(ChatConversation, conversation_id)
        try:
            yield frame({"type": "start", "conversation_title": conversation_title, **identifiers})
            result = None
            for event in respond_events(actor, content, conversation=conversation,
                                        message_id=user_message_id):
                if event.get("type") == "status":
                    yield frame(event)
                elif event.get("type") == "result":
                    result = event["result"]
                    break
            if result is None:
                raise DomainError("AI chưa hoàn tất câu trả lời. Hãy tải lại lịch sử trước khi gửi lại.", 503)
            answer = finish_turn(actor, conversation, result, user_message_id, run_id,
                                 permission_stamp=permission_stamp)
            saved = True
            # Transport chunks of the verified answer, not fabricated token streaming.
            for offset in range(0, len(result["answer"]), 240):
                yield frame({"type": "answer_delta", "text": result["answer"][offset:offset + 240]})
            yield frame({"type": "result", "result": result,
                         **identifiers, "message_id": answer.id})
        except GeneratorExit:
            db.session.rollback()
            raise
        except DomainError as exc:
            db.session.rollback()
            if not saved:
                try:
                    finish_turn(actor, conversation, {"answer": str(exc), "intent": "denied",
                        "provider": "error", "agent_mode": "rules_fallback"},
                        user_message_id, run_id)
                except DomainError:
                    db.session.rollback()
            yield frame({"type": "error", "message": str(exc), **identifiers})
        except Exception as exc:
            db.session.rollback()
            # Exception messages can contain upstream headers or credential-bearing URLs.
            current_app.logger.warning("Assistant stream failed (%s), run=%s", type(exc).__name__, run_id)
            yield frame({"type": "error", "message":
                "Kết nối xử lý bị gián đoạn. Hãy tải lại lịch sử và bản nháp trước khi gửi lại.",
                **identifiers})

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff"})


@ai_bp.route("/ai", methods=["GET", "POST"])
@login_required
@approved_required
def ai_chat():
    """Trang Trợ lý AI: lịch sử hội thoại, danh sách cuộc trò chuyện. BĐH (BOARD) thấy thêm tóm tắt
    ngày, báo cáo, nháp email chờ duyệt; người quản lý sự kiện (MANAGE_EVENTS) thấy form và danh
    sách bản nháp sự kiện đang dở.
    """
    if request.method == "POST":
        require_csrf()
        content = request.form.get("message", "").strip()
        conversation = None
        try:
            if not content or len(content) > 2000:
                raise DomainError("Câu hỏi phải có từ 1 đến 2000 ký tự.")
            check_chat_rate(current_user)
            if request.form.get("new_conversation") == "1":
                if "conversation_id" in request.form:
                    raise DomainError("Chỉ chọn một cuộc trò chuyện.")
                conversation = create_conversation(current_user)
            else:
                conversation = select_conversation(current_user,
                    request.form.get("conversation_id"), create=True, active=True)
            try:
                result = respond(current_user, content, conversation=conversation)
                reply = result["answer"]
                intent = result["intent"]
                provider = result.get("provider", "rules")
            except DomainError as exc:
                reply = str(exc)
                intent = "denied"
                provider = "error"
            append_exchange(current_user, conversation, content, reply)
            audit(current_user, "assistant_query", current_user, {"intent": intent, "provider": provider})
            db.session.commit()
        except DomainError as exc:
            db.session.rollback()
            if exc.status == 404:
                abort(404)
            flash(str(exc), "error")
        return redirect(url_for("ai.ai_chat", chat=conversation.id if conversation else None))

    try:
        conversation = select_conversation(current_user, request.args.get("chat"))
        history, older_before, newer_after = message_page(current_user, conversation,
            before=request.args.get("before"), after=request.args.get("after"))
        show_archived = request.args.get("archived") == "1"
        conversations, conversations_has_next, conversation_page = conversation_list(
            current_user, request.args.get("chats_page", 1), archived=show_archived)
    except DomainError as exc:
        abort(exc.status)
    drafts = []
    email_proposals = []
    usage_summary = None
    digest = None
    reports = []
    report_previews = {}
    board = is_board(current_user)
    event_manager = can_manage_events(current_user)
    if event_manager:
        drafts = event_drafts.visible_drafts(current_user).limit(30).all()
    if board:
        digest = daily_digest(current_user)
        reports = [row for row in (AIReportSnapshot.query.filter_by(user_id=current_user.id)
                   .order_by(AIReportSnapshot.id.desc()).limit(30).all())
                   if can_access_report(current_user, row)][:10]
        report_previews = {period: report_text(report_data(current_user, period))
                           for period in ("week", "month")}
        email_query = EmailProposal.query.filter(
            EmailProposal.status.in_(["pending", "failed", "sending"])
        ).order_by(EmailProposal.created_at.desc())
        email_proposals = [draft for draft in email_query.limit(30) if can_review_email(current_user, draft)]
        usage_query = AIUsageLog.query.filter(
            AIUsageLog.created_at >= utcnow() - timedelta(days=1))
        usage = usage_query.all()
        usage_summary = {"calls": len(usage),
                         "tokens": sum(row.total_tokens for row in usage),
                         "average_ms": round(sum(row.elapsed_ms for row in usage) / len(usage))
                         if usage else 0}
    return render_template("ai.html", history=history, drafts=drafts,
                           current_conversation=conversation, conversations=conversations,
                           conversations_has_next=conversations_has_next,
                           conversation_page=conversation_page, show_archived=show_archived,
                           older_before=older_before, newer_after=newer_after,
                           viewing_older=newer_after is not None,
                           email_proposals=email_proposals, usage_summary=usage_summary,
                           email_templates=(EmailTemplate.query.filter(
                                                EmailTemplate.system_key.is_(None),
                                                EmailTemplate.active.is_(True))
                                            .order_by(EmailTemplate.name).all()
                                            if board else []),
                           proposal_templates=(ProposalTemplate.query.filter_by(active=True)
                                               .order_by(ProposalTemplate.kind,
                                                         ProposalTemplate.name).all()
                                               if event_manager else []),
                           digest=digest, reports=reports, report_previews=report_previews,
                           report_text=report_text,
                           draft_summary=event_drafts.summary, draft_missing=event_drafts.missing,
                           events=Event.query.filter(Event.trang_thai.in_(OPEN)).order_by(Event.thoi_gian_bat_dau).all()
                           if event_manager else [],
                           is_board=board, can_manage_events=event_manager)


@ai_bp.post("/ai/conversations/new")
@login_required
@approved_required
def conversation_new():
    """Tạo cuộc trò chuyện AI mới cho người đang đăng nhập."""
    require_csrf()
    try:
        conversation = create_conversation(current_user, request.form.get("title"))
        db.session.commit()
        return redirect(url_for("ai.ai_chat", chat=conversation.id))
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("ai.ai_chat"))


def _change_conversation(conversation_id, action):
    """Đổi tên / lưu trữ / khôi phục một cuộc trò chuyện thuộc sở hữu của người dùng."""
    require_csrf()
    try:
        conversation = owned_conversation(current_user, conversation_id)
        if action == "rename":
            conversation.title = checked_title(request.form.get("title"))
        else:
            conversation.archived = action == "archive"
        conversation.updated_at = utcnow()
        db.session.commit()
        flash("Đã lưu thay đổi cuộc trò chuyện.", "success")
        return redirect(url_for("ai.ai_chat", chat=conversation.id,
                                archived="1" if conversation.archived else None))
    except DomainError as exc:
        db.session.rollback()
        if exc.status == 404:
            abort(404)
        flash(str(exc), "error")
        return redirect(url_for("ai.ai_chat", chat=conversation_id))


@ai_bp.post("/ai/conversations/<int:conversation_id>/rename")
@login_required
@approved_required
def conversation_rename(conversation_id):
    """Đổi tên cuộc trò chuyện."""
    return _change_conversation(conversation_id, "rename")


@ai_bp.post("/ai/conversations/<int:conversation_id>/archive")
@login_required
@approved_required
def conversation_archive(conversation_id):
    """Lưu trữ (ẩn) cuộc trò chuyện."""
    return _change_conversation(conversation_id, "archive")


@ai_bp.post("/ai/conversations/<int:conversation_id>/restore")
@login_required
@approved_required
def conversation_restore(conversation_id):
    """Khôi phục cuộc trò chuyện đã lưu trữ."""
    return _change_conversation(conversation_id, "restore")


@ai_bp.post("/ai/reports/<period>/save")
@login_required
@bdh_required
def report_save(period):
    """Lưu bản chụp báo cáo tuần/tháng (AIReportSnapshot) để tải Excel hoặc tạo nháp email."""
    require_csrf()
    try:
        saved = save_report(current_user, period)
        flash(f"Đã lưu báo cáo #{saved.id}. Có thể tải đúng bản này hoặc tạo nháp email.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ai.ai_chat"))


@ai_bp.get("/ai/reports/<int:report_id>.xlsx")
@login_required
@bdh_required
def report_export(report_id):
    """Tải file Excel của đúng bản báo cáo đã lưu."""
    snapshot = db.get_or_404(AIReportSnapshot, report_id)
    if not can_access_report(current_user, snapshot):
        abort(403)
    return send_file(report_workbook(snapshot),
                     download_name=f"bao-cao-clb-{snapshot.period}-{snapshot.id}.xlsx",
                     as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@ai_bp.post("/ai/reports/<int:report_id>/email-draft")
@login_required
@bdh_required
def report_draft_email(report_id):
    """Tạo nháp email chứa báo cáo gửi tới email hồ sơ của chính người dùng (chưa gửi)."""
    require_csrf()
    snapshot = db.get_or_404(AIReportSnapshot, report_id)
    if not can_access_report(current_user, snapshot):
        abort(403)
    try:
        draft = report_email_draft(current_user, snapshot)
        flash(f"Đã tạo nháp email #{draft.id} gửi tới email hồ sơ của bạn; chưa gửi.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ai.ai_chat"))


@ai_bp.route("/ai/email-proposals/<int:draft_id>/<decision>", methods=["POST"])
@login_required
@bdh_required
def email_proposal_review(draft_id, decision):
    """Duyệt và gửi (approved) hoặc từ chối (rejected) một nháp email do AI tạo."""
    require_csrf()
    draft = db.get_or_404(EmailProposal, draft_id)
    try:
        sent = review_email_draft(current_user, draft, decision,
                                  confirm_retry=request.form.get("confirm_retry") == "yes")
        if decision == "rejected":
            flash("Đã từ chối bản nháp email; chưa gửi thư.", "success")
        elif sent:
            flash("Máy chủ SMTP đã nhận email để gửi.", "success")
        else:
            flash("Email chưa gửi được. Kiểm tra SMTP rồi bấm Thử gửi lại.", "error")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ai.ai_chat"))


@ai_bp.route("/ai/email-proposals/<int:draft_id>/edit", methods=["POST"])
@login_required
@bdh_required
def email_proposal_edit(draft_id):
    """Sửa tiêu đề/nội dung nháp email trước khi gửi."""
    require_csrf()
    draft = db.get_or_404(EmailProposal, draft_id)
    try:
        edit_email_draft(current_user, draft, request.form.get("subject"),
                         request.form.get("body"))
        flash("Đã lưu bản nháp; vẫn chưa gửi email.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ai.ai_chat"))


@ai_bp.route("/ai/email-proposals/<int:draft_id>/reconcile/<outcome>", methods=["POST"])
@login_required
@bdh_required
def email_proposal_reconcile(draft_id, outcome):
    """Xác nhận thủ công kết quả một lần gửi email bị gián đoạn (sent/failed), không tự gửi lại."""
    require_csrf()
    draft = db.get_or_404(EmailProposal, draft_id)
    try:
        reconcile_email_draft(current_user, draft, outcome)
        flash("Đã ghi nhận kết quả sau khi đối soát hộp thư.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ai.ai_chat"))


@ai_bp.route("/ai/drafts/new", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def draft_new():
    """Lập bản nháp sự kiện (hoặc bản nháp chia task cho sự kiện đã có) từ mô tả trên form."""
    require_csrf()
    event_id = request.form.get("event_id", "")
    try:
        event = db.get_or_404(Event, int(event_id)) if event_id.isdigit() else None
        draft = event_drafts.create_draft(current_user, request.form.get("description"), event=event,
                                          template_id=request.form.get("template_id") or None)
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("ai.ai_chat"))
    return redirect(url_for("ai.draft_page", draft_id=draft.id))


def _draft_or_404(draft_id):
    try:
        return event_drafts.get_draft(current_user, draft_id)
    except DomainError as exc:
        abort(exc.status)


@ai_bp.route("/ai/drafts/<int:draft_id>", methods=["GET", "POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def draft_page(draft_id):
    """Xem / sửa bản nháp: thông tin sự kiện, từng task, người phụ trách."""
    draft = _draft_or_404(draft_id)
    if request.method == "POST":
        require_csrf()
        try:
            event_drafts.update_from_form(current_user, draft, request.form)
            flash("Đã lưu bản nháp.", "success")
        except DomainError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("ai.draft_page", draft_id=draft.id))
    bans = Ban.query.order_by(Ban.ten_ban).all()
    members = {ban.id: [link.user for link in ban.user_links if link.user.status == "approved"]
               for ban in bans}
    return render_template("draft.html", draft=draft, data=draft.data, bans=bans, members=members,
                           event=db.session.get(Event, draft.event_id) if draft.event_id else None,
                           missing=event_drafts.missing(draft), problems=event_drafts.problems(draft),
                           deadline=lambda task: event_drafts.task_deadline(draft, task),
                           users={user.id: user for user in User.query.filter(User.id.in_(
                               [task["assignee_id"] for task in draft.data.get("tasks", [])
                                if task.get("assignee_id")] or [-1]))})


@ai_bp.route("/ai/drafts/<int:draft_id>/<action>", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def draft_action(draft_id, action):
    """Gợi ý người phụ trách, duyệt (tạo sự kiện + task) hoặc bỏ bản nháp."""
    require_csrf()
    if action not in {"suggest", "approve", "discard"}:
        abort(404)
    draft = _draft_or_404(draft_id)
    try:
        if action == "suggest":
            count = event_drafts.suggest_assignees(draft)
            draft.updated_at = utcnow()
            audit(current_user, "event_draft_update", draft, {"changes": [f"gợi ý {count} task"]})
            db.session.commit()
            flash(f"AI đã gợi ý người cho {count} task." if count else
                  "Chưa gợi ý được ai: task chưa có ban hoặc ban đã kín việc.", "success" if count else "error")
        elif action == "approve":
            event = event_drafts.approve(current_user, draft)
            flash(f"Đã tạo sự kiện {event.ten_su_kien} và {len(draft.data['tasks'])} task.", "success")
            return redirect(url_for("event.event_detail", event_id=event.id))
        else:
            event_drafts.discard(current_user, draft)
            flash("Đã bỏ bản nháp.", "success")
            return redirect(url_for("ai.ai_chat"))
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("ai.draft_page", draft_id=draft.id))
