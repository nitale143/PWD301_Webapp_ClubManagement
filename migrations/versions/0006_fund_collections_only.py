"""Quỹ: khoản thu là nguồn duy nhất; quỹ kỳ thành một khoản thu.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05

- `fund_collection.period_id`: khoản thu thuộc kỳ nào (điền sẵn theo kỳ chứa ngày bắt đầu).
- `fund_transaction.payment_id`: dòng sổ thu chi do hệ thống ghi khi xác nhận thanh toán.
- Chuyển `fund_due` (ô "đã đóng" bật tay, không có số tiền) sang khoản thu rồi xóa bảng:
    * Mỗi kỳ có dữ liệu → khoản thu "Quỹ <tên kỳ>" loại semester (kỳ hiện tại: đang mở; kỳ cũ: đã đóng).
    * Dòng "đã đóng" → một thanh toán ĐÃ XÁC NHẬN, ghi chú "Chuyển từ sổ đóng quỹ cũ".
      Thanh toán này KHÔNG ghi thêm vào sổ thu chi (tiền đã được ghi tay trước đây).
  Sổ cũ không lưu số tiền, nên cần biết quỹ mỗi kỳ là bao nhiêu qua biến môi trường
  LEGACY_FUND_DUE_AMOUNT trước khi chạy `flask db upgrade`:
      LEGACY_FUND_DUE_AMOUNT=100000                          # mọi kỳ cùng một mức
      LEGACY_FUND_DUE_AMOUNT="Fall 2026=100000;Summer 2026=80000"
  Không có dòng "đã đóng" nào thì không cần biến này (bảng chỉ còn "chưa đóng", bỏ qua).
  Có dòng "đã đóng" mà thiếu số tiền của kỳ → migration dừng, báo rõ kỳ nào.

Hạ cấp tạo lại bảng `fund_due` rỗng và xóa hai cột mới; không khôi phục dữ liệu.
"""
import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def _amounts():
    raw = os.environ.get("LEGACY_FUND_DUE_AMOUNT", "").strip()
    if not raw:
        return None, {}
    if "=" not in raw:
        return Decimal(raw), {}
    per_period = {}
    for part in raw.split(";"):
        if part.strip():
            name, value = part.rsplit("=", 1)
            per_period[name.strip()] = Decimal(value.strip())
    return None, per_period


def _date(value):
    if value is None or hasattr(value, "year"):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def upgrade():
    bind = op.get_bind()
    periods = bind.execute(sa.text(
        "SELECT id, ten_ky, ngay_bat_dau, ngay_ket_thuc, is_current FROM fund_period")).fetchall()
    dues = bind.execute(sa.text("SELECT period_id, user_id, da_dong, ngay_dong FROM fund_due")).fetchall()
    paid_periods = {row.period_id for row in dues if row.da_dong}
    default, per_period = None, {}
    if paid_periods:
        # Kiểm tra TRƯỚC mọi thay đổi schema: SQLite không rollback được DDL nếu dừng giữa chừng.
        try:
            default, per_period = _amounts()
        except (InvalidOperation, ValueError):
            raise RuntimeError("LEGACY_FUND_DUE_AMOUNT không hợp lệ (vd: 100000 hoặc \"Fall 2026=100000\").")
        missing = [p.ten_ky for p in periods if p.id in paid_periods
                   and per_period.get(p.ten_ky, default) in (None, Decimal("0"))]
        if missing:
            raise RuntimeError(
                "Sổ đóng quỹ cũ có thành viên đã đóng nhưng không lưu số tiền. Đặt LEGACY_FUND_DUE_AMOUNT "
                "(vd: 100000, hoặc \"" + "=100000;".join(missing) + "=100000\") rồi chạy lại "
                "`flask db upgrade`. Kỳ thiếu số tiền: " + ", ".join(missing))

    with op.batch_alter_table('fund_collection') as batch:
        batch.add_column(sa.Column('period_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_fund_collection_period', 'fund_period', ['period_id'], ['id'])
        batch.create_index('ix_fund_collection_period_id', ['period_id'])
    with op.batch_alter_table('fund_transaction') as batch:
        batch.add_column(sa.Column('payment_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_fund_transaction_payment', 'fund_payment', ['payment_id'], ['id'])
        batch.create_unique_constraint('uq_fund_transaction_payment', ['payment_id'])

    for collection_id, start in bind.execute(sa.text("SELECT id, ngay_bat_dau FROM fund_collection")).fetchall():
        start = _date(start)
        match = [p for p in periods if _date(p.ngay_bat_dau) <= start
                 and (p.ngay_ket_thuc is None or start <= _date(p.ngay_ket_thuc))]
        if match:
            bind.execute(sa.text("UPDATE fund_collection SET period_id = :p WHERE id = :c"),
                         {"p": max(match, key=lambda p: _date(p.ngay_bat_dau)).id, "c": collection_id})

    if paid_periods:
        owner = bind.execute(sa.text(
            "SELECT id FROM \"user\" ORDER BY CASE chuc_vu WHEN 'ADMIN' THEN 0 WHEN 'CN' THEN 1 ELSE 2 END, id "
            "LIMIT 1")).scalar()
        for period in periods:
            if period.id not in paid_periods:
                continue
            amount = per_period.get(period.ten_ky, default)
            start = _date(period.ngay_bat_dau)
            due = _date(period.ngay_ket_thuc) or start + timedelta(days=30)
            existing = bind.execute(sa.text(
                "SELECT id FROM fund_collection WHERE period_id = :p AND loai = 'semester' "
                "AND trang_thai != 'cancelled' ORDER BY id LIMIT 1"), {"p": period.id}).scalar()
            if existing is None:
                bind.execute(sa.text(
                    "INSERT INTO fund_collection (ten_khoan_thu, loai, so_tien, ngay_bat_dau, han_dong, "
                    "tat_ca_thanh_vien, mo_ta, trang_thai, tao_boi_id, tao_luc, period_id) VALUES "
                    "(:name, 'semester', :amount, :start, :due, :all, :desc, :state, :owner, :now, :p)"),
                    {"name": f"Quỹ {period.ten_ky}", "amount": str(amount), "start": start, "due": due,
                     "all": True, "desc": "Chuyển từ sổ đóng quỹ theo kỳ cũ.",
                     "state": "open" if period.is_current else "closed", "owner": owner,
                     "now": datetime.utcnow(), "p": period.id})
                existing = bind.execute(sa.text(
                    "SELECT id FROM fund_collection WHERE period_id = :p AND loai = 'semester' "
                    "ORDER BY id DESC LIMIT 1"), {"p": period.id}).scalar()
            for row in dues:
                if row.period_id != period.id or not row.da_dong:
                    continue
                paid_on = _date(row.ngay_dong) or start
                bind.execute(sa.text(
                    "INSERT INTO fund_payment (fund_id, user_id, so_tien, ngay_dong, phuong_thuc, ma_giao_dich, "
                    "trang_thai, xac_nhan_boi_id, xac_nhan_luc, ghi_chu) VALUES (:f, :u, :amount, :paid, "
                    "'other', 'SO-CU', 'confirmed', :owner, :paid, :note)"),
                    {"f": existing, "u": row.user_id, "amount": str(amount),
                     "paid": datetime.combine(paid_on, datetime.min.time()), "owner": owner,
                     "note": "Chuyển từ sổ đóng quỹ cũ"})
    op.drop_table('fund_due')


def downgrade():
    op.create_table(
        'fund_due',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('period_id', sa.Integer(), sa.ForeignKey('fund_period.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('da_dong', sa.Boolean(), nullable=True),
        sa.Column('ngay_dong', sa.Date(), nullable=True),
    )
    with op.batch_alter_table('fund_transaction') as batch:
        batch.drop_constraint('uq_fund_transaction_payment', type_='unique')
        batch.drop_constraint('fk_fund_transaction_payment', type_='foreignkey')
        batch.drop_column('payment_id')
    with op.batch_alter_table('fund_collection') as batch:
        batch.drop_index('ix_fund_collection_period_id')
        batch.drop_constraint('fk_fund_collection_period', type_='foreignkey')
        batch.drop_column('period_id')
