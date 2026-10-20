"""Lệnh quản trị chạy bằng `flask --app run.py <lệnh>` (Ch11 – vận hành).

Lệnh có sẵn:
    flask --app run.py db upgrade           # (Flask-Migrate) cập nhật schema mới nhất
    flask --app run.py init-db              # upgrade + 3 ban mặc định + bảng role + kho mẫu mặc định
    flask --app run.py create-admin         # tạo tài khoản Quản trị hệ thống đầu tiên
    flask --app run.py reset-admin-password # đặt lại mật khẩu Quản trị hệ thống
    flask --app run.py seed-templates       # nạp lại các mẫu mặc định còn thiếu
    flask --app run.py sync-roles           # ghi đè quyền của các role theo DEFAULT_ROLES
    flask --app run.py run-scheduler        # chạy job định kỳ ở tiến trình riêng
    flask --app run.py migrate-chats | migrate-money | migrate-email-recipients
                                            # nâng cấp database SQLite cũ (trước khi có migrations/)
"""
from datetime import date
import time

import click
from flask_migrate import upgrade

from app import db


def register_cli(app):
    """Đăng ký các lệnh `flask --app run.py ...` (init-db, sync-roles, create-admin,
    reset-admin-password, seed-templates, run-scheduler, migrate-*) vào app.
    """
    from app.legacy_sqlite import (
        migrate_chat_conversations, migrate_email_recipients, migrate_legacy_money,
    )
    from app.models import SYSTEM_ADMIN_ROLE, Ban, Role, User
    from app.services.email_templates import seed_system_templates
    from app.services.proposal_templates import seed_proposal_templates
    from app.validators import ADMIN_USERNAME_RE, password_problems

    def _require_sqlite_file():
        if db.engine.url.get_backend_name() != "sqlite" or not db.engine.url.database:
            raise click.ClickException("Lệnh này chỉ hỗ trợ SQLite database trên đĩa.")

    @app.cli.command("init-db")
    def init_db():
        """Cập nhật schema bằng migration rồi tạo dữ liệu nền (ban, role)."""
        upgrade()
        Role.insert_roles()
        if not Ban.query.first():
            for ten in ["Kỹ thuật", "Nhân sự", "Truyền thông"]:
                db.session.add(Ban(ten_ban=ten))
            db.session.commit()
            click.echo("Đã tạo 3 ban hoạt động mặc định.")
        seed_system_templates()
        seed_proposal_templates()
        click.echo("Database đã sẵn sàng (đã nạp kho mẫu mặc định).")

    @app.cli.command("sync-roles")
    def sync_roles():
        """Ghi đè quyền trong bảng role theo app.models.DEFAULT_ROLES."""
        Role.insert_roles(overwrite=True)
        for role in Role.query.order_by(Role.rank, Role.name):
            click.echo(f"{role.name:<14} rank={role.rank} permissions={role.permissions}")

    def _ask_admin_password(username):
        """Hỏi mật khẩu 2 lần và kiểm tra theo chính sách mật khẩu mạnh."""
        password = click.prompt("Mật khẩu", hide_input=True, confirmation_prompt=True)
        problems = password_problems(password, username)
        if problems:
            raise click.ClickException(" ".join(problems))
        return password

    @app.cli.command("create-admin")
    def create_admin():
        """Tạo tài khoản Quản trị hệ thống đầu tiên: chỉ cần tên, tên đăng nhập và mật khẩu.

        Tài khoản này có quyền cao nhất, KHÔNG phải thành viên/Chủ nhiệm CLB; dùng để
        duyệt đơn đăng ký và gán những thành viên, BĐH đầu tiên (kể cả Chủ nhiệm).
        """
        Role.insert_roles()
        if User.query.filter_by(chuc_vu=SYSTEM_ADMIN_ROLE).first():
            raise click.ClickException(
                "Đã có tài khoản Quản trị hệ thống. Quên mật khẩu thì dùng: flask reset-admin-password")
        ho_ten = click.prompt("Tên hiển thị", default="Quản trị hệ thống").strip()
        if not 2 <= len(ho_ten) <= 100:
            raise click.ClickException("Tên phải từ 2 đến 100 ký tự.")
        username = click.prompt("Tên đăng nhập (username)").strip()
        if not ADMIN_USERNAME_RE.fullmatch(username):
            raise click.ClickException("Tên đăng nhập gồm 3–20 ký tự chữ, số, dấu _ hoặc -.")
        if User.query.filter(db.func.lower(User.mssv) == username.lower()).first():
            raise click.ClickException("Tên đăng nhập này đã được dùng.")
        password = _ask_admin_password(username)
        user = User(
            mssv=username, ho_ten=ho_ten, ngay_sinh=date(2000, 1, 1), sdt="",
            # Địa chỉ .invalid: không có hộp thư thật, hệ thống không gửi mail tới đây
            email=f"{username.lower()}@admin.invalid",
            status="approved", chuc_vu=SYSTEM_ADMIN_ROLE,
            email_confirmed=True,
        )
        user.password = password
        db.session.add(user)
        db.session.commit()
        click.echo(f"Đã tạo Quản trị hệ thống '{username}'. Đăng nhập bằng tên đăng nhập này "
                   "để duyệt thành viên và gán Ban điều hành đầu tiên.")

    @app.cli.command("reset-admin-password")
    def reset_admin_password():
        """Đặt lại mật khẩu tài khoản Quản trị hệ thống (chỉ chạy được trên máy chủ)."""
        username = click.prompt("Tên đăng nhập Quản trị hệ thống").strip()
        user = User.query.filter_by(mssv=username, chuc_vu=SYSTEM_ADMIN_ROLE).first()
        if user is None:
            raise click.ClickException("Không tìm thấy tài khoản Quản trị hệ thống này.")
        user.password = _ask_admin_password(username)
        db.session.commit()
        click.echo("Đã đặt lại mật khẩu. Mọi phiên đăng nhập cũ của tài khoản này đã hết hiệu lực.")

    @app.cli.command("seed-templates")
    def seed_templates():
        """Nạp các mẫu mặc định còn thiếu vào kho mẫu (email hệ thống, mẫu sự kiện/task)."""
        emails, proposals = seed_system_templates(), seed_proposal_templates()
        click.echo(f"Đã thêm {emails} mẫu email và {proposals} mẫu sự kiện/task.")

    @app.cli.command("run-scheduler")
    def run_scheduler():
        """Chạy APScheduler ở tiến trình riêng (dùng khi web chạy nhiều worker Gunicorn)."""
        from app.scheduler import init_scheduler
        init_scheduler(app, force=True)
        click.echo("Scheduler đang chạy. Nhấn Ctrl+C để dừng.")
        try:
            while True:
                time.sleep(3600)
        except (KeyboardInterrupt, SystemExit):
            pass

    @app.cli.command("backup-create")
    @click.option("--directory", type=click.Path(file_okay=False), default=None,
                  help="Thư mục riêng lưu bản sao; mặc định BACKUP_DIRECTORY.")
    def backup_create(directory):
        """Sao lưu DB, avatar, minh chứng thanh toán kèm manifest kiểm tra toàn vẹn."""
        from app.services.maintenance import backup_database
        try:
            result = backup_database(directory)
        except Exception as exc:
            raise click.ClickException(f"Không thể tạo bản sao ({type(exc).__name__}).") from None
        click.echo(f"Đã tạo bản sao riêng tư: {result}")

    @app.cli.command("backup-verify")
    @click.argument("backup_path", type=click.Path(exists=True, file_okay=False))
    def backup_verify(backup_path):
        """Kiểm tra manifest/hash/integrity và khóa ngoại, không đổi database thật."""
        from app.services.backups import verify_backup
        try:
            verify_backup(backup_path)
        except Exception as exc:
            raise click.ClickException(f"Bản sao không qua kiểm tra ({type(exc).__name__}).") from None
        click.echo("Bản sao đã qua kiểm tra toàn vẹn.")

    @app.cli.command("backup-restore")
    @click.argument("backup_path", type=click.Path(exists=True, file_okay=False))
    @click.option("--target", required=True, type=click.Path(dir_okay=False),
                  help="File SQLite MỚI, tách biệt database hiện tại; không ghi đè file có sẵn.")
    @click.option("--artifacts-target", type=click.Path(file_okay=False), default=None,
                  help="Thư mục MỚI phục hồi avatar/minh chứng, không ghi đè tập tin đang dùng.")
    def backup_restore(backup_path, target, artifacts_target):
        """Phục hồi vào file riêng; dùng để diễn tập hoặc phục hồi ngoại tuyến đã kiểm tra."""
        from pathlib import Path
        from app.services.backups import restore_backup
        if db.engine.url.database and Path(target).resolve() == Path(db.engine.url.database).resolve():
            raise click.ClickException("Không được ghi đè database web đang dùng.")
        try:
            restored = restore_backup(backup_path, target, target_artifacts_dir=artifacts_target)
        except Exception as exc:
            raise click.ClickException(f"Không thể phục hồi ({type(exc).__name__}).") from None
        click.echo(f"Đã phục hồi và kiểm tra vào: {restored}. Database đang chạy không đổi.")

    # ---- nâng cấp database SQLite tạo trước khi có thư mục migrations/ ----
    @app.cli.command("migrate-chats")
    def migrate_chats():
        """Kiểm tra/nâng cấp bảng hội thoại AI trên SQLite cũ (an toàn khi chạy lại)."""
        if db.engine.url.get_backend_name() != "sqlite":
            raise click.ClickException("Lệnh này chỉ hỗ trợ SQLite.")
        backup = migrate_chat_conversations(db.engine.url.database or ":memory:")
        click.echo(f"Đã cập nhật hội thoại AI. Bản sao lưu: {backup}" if backup else
                   "Cấu trúc hội thoại AI đã đúng; không cần cập nhật.")

    @app.cli.command("migrate-money")
    def migrate_money():
        """Sao lưu rồi đổi cột tiền FLOAT cũ sang NUMERIC."""
        _require_sqlite_file()
        backup = migrate_legacy_money(db.engine.url.database)
        click.echo(f"Đã cập nhật cột tiền. Bản sao lưu: {backup}" if backup else
                   "Cột tiền đã đúng kiểu; không cần cập nhật.")

    @app.cli.command("migrate-email-recipients")
    def migrate_email_drafts():
        """Sao lưu rồi cho phép nháp email gửi tới địa chỉ ngoài CLB."""
        _require_sqlite_file()
        backup = migrate_email_recipients(db.engine.url.database)
        click.echo(f"Đã cập nhật nháp email. Bản sao lưu: {backup}" if backup else
                   "Cấu trúc nháp email đã đúng; không cần cập nhật.")
