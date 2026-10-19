import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user

from app import db
from app.models import FundCollection, FundPayment, FundPeriod, FundTransaction, FundDue, User
from app.forms import FundPeriodForm, FundTransactionForm
from app.models import Permission
from app.permissions import approved_required, permission_required
from app.security import require_csrf
from app.mail import send_email
from app.services.email_templates import render_system_email
from app.services.access import active_members, approved, can_manage_funds
from app.services.common import DomainError, audit, club_now
from app.services.export_security import safe_excel_row
from app.services.funds import balance, create_collection, record_payment, review_payment, target_members

funds_bp = Blueprint("funds", __name__)


def _flash_form_errors(form):
    """Hiển thị mọi lỗi validate của form dưới dạng flash."""
    for errors in form.errors.values():
        for message in errors:
            flash(message, "error")


def _is_thu_quy():
    """Người đang đăng nhập có quyền quản lý quỹ (MANAGE_FUNDS)."""
    return can_manage_funds(current_user)


@funds_bp.route("/funds/period/new", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_FUNDS)
def period_new():
    """Mở kỳ quỹ mới (Spring/Summer/Fall…). Kỳ đang mở tự đóng lại – tại một
    thời điểm chỉ có đúng một kỳ hiện tại."""
    form = FundPeriodForm()
    if not form.validate_on_submit():
        _flash_form_errors(form)
        return redirect(url_for("funds.funds_home"))

    FundPeriod.query.filter_by(is_current=True).update({"is_current": False})
    period = FundPeriod(
        ten_ky=form.ten_ky.data,
        ngay_bat_dau=form.ngay_bat_dau.data,
        ngay_ket_thuc=form.ngay_ket_thuc.data,
        is_current=True,
        da_nhac_ky_moi=False,
    )
    db.session.add(period)
    db.session.flush()

    # Tạo sẵn bản ghi FundDue (chưa đóng) cho mọi thành viên đang hoạt động
    for member in active_members().all():
        if approved(member):
            db.session.add(FundDue(period_id=period.id, user_id=member.id, da_dong=False))

    audit(current_user, "legacy_period_create", period)
    db.session.commit()
    flash(f"Đã mở kỳ mới: {period.ten_ky}.", "success")
    return redirect(url_for("funds.funds_home"))


@funds_bp.route("/funds")
@login_required
@approved_required
def funds_home():
    """Trang Quỹ: kỳ hiện tại, sổ thu chi (người quản lý quỹ), tình trạng đóng quỹ, khoản thu và
    thanh toán chờ xác nhận.
    """
    period = FundPeriod.query.filter_by(is_current=True).first()
    transactions, dues, so_du, tong_thu, tong_chi = [], [], 0, 0, 0

    if period:
        if _is_thu_quy():
            transactions = (
                FundTransaction.query.filter_by(period_id=period.id)
                .order_by(FundTransaction.ngay.desc()).all()
            )
        tong_thu = sum(t.so_tien for t in transactions if t.so_tien > 0)
        tong_chi = sum(t.so_tien for t in transactions if t.so_tien < 0)
        so_du = tong_thu + tong_chi
        due_query = FundDue.query.filter_by(period_id=period.id)
        if not _is_thu_quy():
            due_query = due_query.filter_by(user_id=current_user.id)
        dues = due_query.all()

    collections = FundCollection.query.order_by(FundCollection.id.desc()).all()
    if not _is_thu_quy():
        collections = [fund for fund in collections if fund.trang_thai in {"open", "closed"}
                       and target_members(fund).filter(User.id == current_user.id).first()]
    collection_rows = [{"fund": fund, "balance": balance(fund, current_user)
                        if target_members(fund).filter(User.id == current_user.id).first() else None}
                       for fund in collections]
    pending_payments = (FundPayment.query.filter_by(trang_thai="pending")
                        .order_by(FundPayment.id.desc()).limit(50).all()) if _is_thu_quy() else []
    payment_members = {user.id: user for user in User.query.filter(
        User.id.in_([row.user_id for row in pending_payments])).all()} if pending_payments else {}
    collection_names = {fund.id: fund.ten_khoan_thu for fund in collections}

    return render_template(
        "funds.html",
        collection_rows=collection_rows,
        pending_payments=pending_payments,
        payment_members=payment_members,
        collection_names=collection_names,
        period=period,
        transactions=transactions,
        dues=dues,
        so_du=so_du,
        tong_thu=tong_thu,
        tong_chi=tong_chi,
        is_thu_quy=_is_thu_quy(),
        period_form=FundPeriodForm(),
        tx_form=FundTransactionForm(),
    )


@funds_bp.post("/funds/collections/new")
@login_required
@approved_required
def collection_new():
    """Mở khoản thu mới từ form trên trang Quỹ (MANAGE_FUNDS, kiểm tra trong service)."""
    require_csrf()
    try:
        create_collection(current_user, {
            "name": request.form.get("name"), "type": request.form.get("type"),
            "amount": request.form.get("amount"), "start_date": request.form.get("start_date"),
            "due_date": request.form.get("due_date"), "status": "open",
            "applies_to_all": True, "description": request.form.get("description", ""),
        })
        flash("Đã mở khoản thu.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("funds.funds_home"))


@funds_bp.post("/funds/collections/<int:fund_id>/payments")
@login_required
@approved_required
def collection_pay(fund_id):
    """Thành viên khai báo đã đóng tiền cho khoản thu (chờ xác nhận)."""
    require_csrf()
    fund = db.get_or_404(FundCollection, fund_id)
    try:
        record_payment(current_user, fund, current_user, request.form)
        flash("Đã gửi thông tin đóng quỹ, chờ thủ quỹ xác nhận.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("funds.funds_home"))


@funds_bp.post("/funds/payments/<int:payment_id>/review")
@login_required
@approved_required
def collection_payment_review(payment_id):
    """Người quản lý quỹ xác nhận/từ chối một lần thanh toán."""
    require_csrf()
    payment = db.get_or_404(FundPayment, payment_id)
    try:
        review_payment(current_user, payment, request.form.get("decision"), request.form.get("note", ""))
        flash("Đã xử lý giao dịch.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("funds.funds_home"))


@funds_bp.route("/funds/transaction/new", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_FUNDS)
def transaction_new():
    """Ghi một giao dịch vào sổ thu chi của kỳ đang mở (FundTransactionForm)."""
    period = FundPeriod.query.filter_by(is_current=True).first()
    if not period:
        flash("Chưa có kỳ quỹ nào đang mở.", "error")
        return redirect(url_for("funds.funds_home"))
    form = FundTransactionForm()
    if not form.validate_on_submit():
        _flash_form_errors(form)
        return redirect(url_for("funds.funds_home"))
    tx = FundTransaction(
        period_id=period.id,
        danh_muc=form.danh_muc.data,
        noi_dung=form.noi_dung.data or "",
        ngay=form.ngay.data,
        so_tien=form.amount,
        tao_boi_id=current_user.id,
    )
    db.session.add(tx)
    db.session.flush()
    audit(current_user, "legacy_fund_transaction", tx, {"amount": str(form.amount)})
    db.session.commit()
    flash("Đã ghi nhận giao dịch.", "success")
    return redirect(url_for("funds.funds_home"))


@funds_bp.route("/funds/due/<int:user_id>/toggle", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_FUNDS)
def toggle_due(user_id):
    """Đánh dấu đã đóng / chưa đóng quỹ kỳ hiện tại cho một thành viên."""
    period = FundPeriod.query.filter_by(is_current=True).first()
    if not period:
        abort(400)
    member = db.session.get(User, user_id)
    if not member or member.status != "approved":
        abort(404)

    due = FundDue.query.filter_by(period_id=period.id, user_id=user_id).first()
    if not due:
        due = FundDue(period_id=period.id, user_id=user_id, da_dong=False)
        db.session.add(due)

    due.da_dong = not due.da_dong
    due.ngay_dong = club_now().date() if due.da_dong else None
    db.session.flush()
    audit(current_user, "legacy_fund_due_toggle", due, {"paid": due.da_dong})
    db.session.commit()
    return redirect(url_for("funds.funds_home"))


@funds_bp.route("/funds/nhac-dong-quy", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_FUNDS)
def remind_unpaid():
    """Gửi mail nhắc các thành viên chưa đóng quỹ trong kỳ hiện tại."""
    period = FundPeriod.query.filter_by(is_current=True).first()
    if not period:
        flash("Chưa có kỳ quỹ nào đang mở.", "error")
        return redirect(url_for("funds.funds_home"))

    unpaid_dues = FundDue.query.filter_by(period_id=period.id, da_dong=False).all()
    sent = 0
    for due in unpaid_dues:
        sent += bool(send_email(due.user.email, *render_system_email(
            "fund_reminder", ten=due.user.ho_ten, ky=period.ten_ky)))
    audit(current_user, "legacy_fund_reminder", period, {"recipients": len(unpaid_dues), "sent": sent})
    db.session.commit()
    if sent == len(unpaid_dues):
        flash(f"Đã gửi nhắc nhở tới {sent} thành viên chưa đóng quỹ.", "success")
    else:
        flash(f"Đã gửi {sent}/{len(unpaid_dues)} email nhắc. Kiểm tra cấu hình SMTP cho các thư chưa gửi.", "error")
    return redirect(url_for("funds.funds_home"))


@funds_bp.route("/funds/export")
@login_required
@approved_required
def export_funds_excel():
    """Xuất Excel kỳ hiện tại: sổ thu chi (chỉ người quản lý quỹ) và tình trạng đóng quỹ (thành
    viên chỉ thấy dòng của mình).
    """
    from openpyxl import Workbook

    period = FundPeriod.query.filter_by(is_current=True).first()
    wb = Workbook()
    ws = wb.active
    ws.title = "Thu chi"
    ws.append(safe_excel_row(["Ngày", "Danh mục", "Nội dung", "Số tiền"]))
    if period and _is_thu_quy():
        for tx in FundTransaction.query.filter_by(period_id=period.id):
            ws.append(safe_excel_row([tx.ngay.isoformat(), tx.danh_muc, tx.noi_dung, tx.so_tien]))

    ws2 = wb.create_sheet("Đóng quỹ")
    ws2.append(safe_excel_row(["Họ tên", "MSSV", "Đã đóng"]))
    if period:
        due_query = FundDue.query.filter_by(period_id=period.id)
        if not _is_thu_quy():
            due_query = due_query.filter_by(user_id=current_user.id)
        for due in due_query:
            ws2.append(safe_excel_row([due.user.ho_ten, due.user.mssv, "Có" if due.da_dong else "Chưa"]))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="quy_clb.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
