"""Board-only delivery history and explicit, guarded retry actions."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, fresh_login_required, login_required

from app import db
from app.mail_models import EmailDelivery
from app.models import Permission
from app.permissions import permission_required
from app.services.mail_delivery import (
    DeliveryRetryError,
    mark_stale_deliveries_unknown,
    purge_expired_delivery_bodies,
    retry_email_delivery,
)


mail_delivery_bp = Blueprint("mail_delivery", __name__, url_prefix="/mail-deliveries")
PAGE_SIZE = 30


@mail_delivery_bp.route("/")
@login_required
@permission_required(Permission.BOARD)
def index():
    purge_expired_delivery_bodies()
    mark_stale_deliveries_unknown()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    query = EmailDelivery.query.order_by(EmailDelivery.created_at.desc(), EmailDelivery.id.desc())
    total = query.count()
    deliveries = query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    return render_template(
        "mail_delivery.html",
        deliveries=deliveries,
        page=page,
        page_count=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
    )


@mail_delivery_bp.route("/<int:delivery_id>/retry", methods=["POST"])
@login_required
@fresh_login_required
@permission_required(Permission.BOARD)
def retry(delivery_id):
    db.get_or_404(EmailDelivery, delivery_id)
    confirm_unknown = request.form.get("confirm_unknown") == "yes"
    try:
        sent = retry_email_delivery(
            delivery_id, current_user.id, confirm_unknown=confirm_unknown
        )
    except DeliveryRetryError as exc:
        flash(str(exc), "error")
    else:
        flash(
            "Email đã gửi thành công." if sent else
            "Lần thử gửi đã được ghi nhận nhưng chưa gửi thành công.",
            "success" if sent else "error",
        )
    return redirect(url_for("mail_delivery.index"))
