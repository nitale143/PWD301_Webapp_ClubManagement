import io
from datetime import timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user

from app import db
from app.models import FundCollection, FundPayment, FundPeriod, FundTransaction, User
from app.forms import FundPeriodForm, FundTransactionForm
from app.models import Permission
from app.permissions import approved_required, permission_required
from app.security import require_csrf
from app.mail import send_email
from app.services.email_templates import render_system_email
from app.services.access import can_manage_funds
from app.services.common import DomainError, audit
from app.services.export_security import safe_excel_row
from app.services.funds import (
    PERIOD_FUND, applies_to_member, balance, create_collection, fund_member_rows, ledger_totals,
    owing_by_member, period_collections, period_fund, record_confirmed_payment, record_payment,
    review_payment, target_members,
)

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
    """Mở kỳ quỹ mới (Spring/Summer/Fall…). Kỳ đang mở tự đóng lại – tại một thời điểm chỉ có đúng
    một kỳ hiện tại. Nhập số tiền quỹ kỳ thì tạo luôn khoản thu "Quỹ <tên kỳ>" cho mọi thành viên."""
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

    audit(current_user, "fund_period_create", period)
    fund_amount = getattr(form, "fund_amount", None)   # None khi để trống ô quỹ kỳ
    if fund_amount:
        due = form.han_dong_quy.data or period.ngay_ket_thuc or (period.ngay_bat_dau + timedelta(days=30))
        try:
            create_collection(current_user, {
                "name": f"Quỹ {period.ten_ky}", "type": PERIOD_FUND, "amount": str(fund_amount),
                "start_date": period.ngay_bat_dau.isoformat(), "due_date": due.isoformat(),
                "status": "open", "applies_to_all": True, "period_id": period.id, "commit": False,
                "description": f"Quỹ kỳ {period.ten_ky}.",
            })
        except DomainError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("funds.funds_home"))
    db.session.commit()
    flash(f"Đã mở kỳ mới: {period.ten_ky}" + (" và quỹ kỳ." if fund_amount else "."), "success")
    return redirect(url_for("funds.funds_home"))


@funds_bp.route("/funds")
@login_required
@approved_required
def funds_home():
    """Trang Quỹ: kỳ hiện tại, sổ thu chi (người quản lý quỹ), tình trạng đóng quỹ, khoản thu và
    thanh toán chờ xác nhận.
    """
    period = FundPeriod.query.filter_by(is_current=True).first()
    transactions, tong_thu, tong_chi, so_du = [], 0, 0, 0
    if period and _is_thu_quy():
        transactions = (FundTransaction.query.filter_by(period_id=period.id)
                        .order_by(FundTransaction.ngay.desc(), FundTransaction.id.desc()).all())
        tong_thu, tong_chi, so_du = ledger_totals(period)
    # Quỹ kỳ: thủ quỹ thấy mọi thành viên, thành viên thấy dòng của mình
    quy_ky = period_fund(period)
    quy_ky_rows = []
    if quy_ky:
        quy_ky_rows = (fund_member_rows(quy_ky) if _is_thu_quy() else
                       [(current_user, balance(quy_ky, current_user))]
                       if applies_to_member(quy_ky, current_user) else [])

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
        quy_ky=quy_ky,
        quy_ky_rows=quy_ky_rows,
        owing_count=len(owing_by_member([fund for fund in collections if fund.trang_thai == "open"]))
        if _is_thu_quy() else 0,
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


@funds_bp.post("/funds/collections/<int:fund_id>/members/<int:user_id>/paid")
@login_required
@permission_required(Permission.MANAGE_FUNDS)
def collection_record_paid(fund_id, user_id):
    """Thủ quỹ ghi nhận một thành viên đã đóng đủ (vd. tiền mặt): tạo thanh toán đã xác nhận và
    tự ghi vào sổ thu chi. Thay cho ô bật/tắt "đã đóng" trước đây."""
    require_csrf()
    fund = db.get_or_404(FundCollection, fund_id)
    member = db.get_or_404(User, user_id)
    try:
        record_confirmed_payment(current_user, fund, member, request.form.get("method", "cash"))
        flash(f"Đã ghi nhận {member.ho_ten} đóng {fund.ten_khoan_thu}.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("funds.funds_home", tab="dues"))


@funds_bp.route("/funds/nhac-dong-quy", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_FUNDS)
def remind_unpaid():
    """Gửi mail nhắc những thành viên còn phải đóng (chưa đóng, đóng thiếu, quá hạn) ở các khoản thu
    đang mở; mỗi người một mail liệt kê các khoản."""
    period = FundPeriod.query.filter_by(is_current=True).first()
    owing = owing_by_member(FundCollection.query.filter_by(trang_thai="open")
                            .order_by(FundCollection.han_dong, FundCollection.id).all())
    sent = 0
    for member, items in owing.items():
        lines = "\n".join(f"- {fund.ten_khoan_thu}: còn {item['remaining']:,.0f} đ, hạn "
                          f"{fund.han_dong:%d/%m/%Y}".replace(",", ".") for fund, item in items)
        sent += bool(send_email(member.email, *render_system_email(
            "fund_reminder", ten=member.ho_ten, ky=period.ten_ky if period else "hiện tại",
            danh_sach=lines)))
    audit(current_user, "fund_reminder", period or current_user, {"recipients": len(owing), "sent": sent})
    db.session.commit()
    if not owing:
        flash("Không có thành viên nào còn phải đóng ở các khoản thu đang mở.", "success")
    elif sent == len(owing):
        flash(f"Đã gửi nhắc nhở tới {sent} thành viên còn phải đóng.", "success")
    else:
        flash(f"Đã gửi {sent}/{len(owing)} email nhắc. Kiểm tra cấu hình SMTP cho các thư chưa gửi.", "error")
    return redirect(url_for("funds.funds_home"))


@funds_bp.route("/funds/export")
@login_required
@approved_required
def export_funds_excel():
    """Xuất Excel kỳ hiện tại: sổ thu chi (chỉ người quản lý quỹ) và tình trạng đóng từng khoản thu
    của kỳ (thành viên chỉ thấy dòng của mình).
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
    ws2.append(safe_excel_row(["Họ tên", "MSSV", "Khoản thu", "Phải đóng", "Đã đóng", "Còn thiếu",
                               "Trạng thái"]))
    labels = {"paid": "Đã đóng", "partial": "Đóng một phần", "unpaid": "Chưa đóng", "pending": "Chờ xác nhận",
              "overdue": "Quá hạn", "exempt": "Được miễn"}
    member_ids = None if _is_thu_quy() else [current_user.id]
    for fund in period_collections(period):
        for member, item in fund_member_rows(fund, member_ids):
            ws2.append(safe_excel_row([member.ho_ten, member.mssv, fund.ten_khoan_thu, item["required"],
                                       item["paid"], item["remaining"], labels[item["status"]]]))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="quy_clb.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
