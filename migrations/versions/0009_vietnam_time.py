"""Mọi thời gian lưu theo giờ Việt Nam.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-06

Trước đây các mốc do hệ thống tự ghi (ngày đăng ký, tin nhắn, thanh toán, điểm danh, audit…)
lưu theo UTC, còn giờ sự kiện / hạn task lưu theo giờ CLB. Migration này đổi các cột UTC sang giờ
CLB (`APP_TIMEZONE`, mặc định Asia/Ho_Chi_Minh) để toàn hệ thống dùng một múi giờ.

Không đổi: giờ sự kiện, hạn task, hạn đăng ký, thời điểm chuyển trạng thái tay (vốn đã là giờ CLB).
Báo cáo AI đã lưu: khóa `confirmed_at_utc` của thanh toán đổi thành `confirmed_at` theo giờ CLB.
Hạ cấp đổi ngược lại về UTC.
"""
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

UTC_COLUMNS = {
    "user": ["ngay_tao", "email_confirmed_at", "password_changed_at", "khoa_dang_nhap_den"],
    "task": ["lan_nop_gan_nhat"],
    "member_activity": ["cap_nhat_luc"],
    "member_record": ["luu_tru_luc"],
    "chat_message": ["thoi_gian"],
    "chat_conversation": ["created_at", "updated_at"],
    "notification": ["thoi_gian"],
    "fund_collection": ["tao_luc"],
    "fund_adjustment": ["tao_luc"],
    "fund_payment": ["ngay_dong", "xac_nhan_luc"],
    "payment_evidence": ["tai_len_luc"],
    "event_registration": ["dang_ky_luc", "huy_luc"],
    "attendance": ["checkin_luc"],
    "qr_checkin_log": ["checkin_luc"],
    "event_feedback": ["tao_luc"],
    "audit_log": ["created_at"],
    "event_draft": ["created_at", "updated_at", "reviewed_at"],
    "email_proposal": ["created_at", "reviewed_at", "sent_at"],
    "email_template": ["updated_at"],
    "proposal_template": ["updated_at"],
    "ai_report_snapshot": ["created_at"],
    "member_planning_profile": ["updated_at"],
    "ai_usage_log": ["created_at"],
}


def _zone():
    return ZoneInfo(os.environ.get("APP_TIMEZONE") or "Asia/Ho_Chi_Minh")


def _to_club(value):
    return value.replace(tzinfo=timezone.utc).astimezone(_zone()).replace(tzinfo=None)


def _to_utc(value):
    return value.replace(tzinfo=_zone()).astimezone(timezone.utc).replace(tzinfo=None)


def _parse(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _shift(convert):
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    metadata = sa.MetaData()
    for name, columns in UTC_COLUMNS.items():
        if name not in existing:
            continue
        table = sa.Table(name, metadata, autoload_with=bind)
        keys = list(table.primary_key.columns)
        rows = bind.execute(sa.select(*keys, *[table.c[col] for col in columns])).fetchall()
        for row in rows:
            values = {col: convert(_parse(row._mapping[col])) for col in columns
                      if row._mapping[col] is not None}
            if values:
                bind.execute(table.update().where(*[key == row._mapping[key.name] for key in keys])
                             .values(**values))
    _report_payloads(bind, existing, convert)


def _report_payloads(bind, existing, convert):
    if "ai_report_snapshot" not in existing:
        return
    upgrading = convert is _to_club
    old_key, new_key = ("confirmed_at_utc", "confirmed_at") if upgrading else ("confirmed_at", "confirmed_at_utc")
    for snapshot_id, payload in bind.execute(sa.text("SELECT id, payload FROM ai_report_snapshot")).fetchall():
        data = json.loads(payload) if isinstance(payload, str) else payload
        payments = (data or {}).get("payments") or []
        if not any(old_key in row for row in payments):
            continue
        for row in payments:
            if row.get(old_key):
                row[new_key] = convert(_parse(row.pop(old_key))).isoformat()
        bind.execute(sa.text("UPDATE ai_report_snapshot SET payload = :payload WHERE id = :id"),
                     {"payload": json.dumps(data, ensure_ascii=False), "id": snapshot_id})


def upgrade():
    _shift(_to_club)


def downgrade():
    _shift(_to_utc)
