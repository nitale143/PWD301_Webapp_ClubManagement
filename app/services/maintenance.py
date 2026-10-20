"""Sao lưu SQLite nhất quán; phục hồi luôn sang đường dẫn riêng để không ghi đè web đang chạy."""
from pathlib import Path

from flask import current_app

from app import db
from .common import DomainError


def backup_database(directory=None):
    """Sao lưu DB và tập tin nghiệp vụ; không tự đưa .env/API key vào gói dữ liệu."""
    from .backups import create_backup
    if db.engine.url.get_backend_name() != "sqlite" or not db.engine.url.database or db.engine.url.database == ":memory:":
        raise DomainError("Sao lưu tích hợp hiện hỗ trợ SQLite trên đĩa; DB khác cần công cụ riêng.")
    source = Path(db.engine.url.database).resolve()
    destination = directory or current_app.config["BACKUP_DIRECTORY"]
    return create_backup(source, destination,
                         avatar_dir=current_app.config.get("UPLOAD_FOLDER"),
                         evidence_dir=(current_app.config.get("PAYMENT_EVIDENCE_DIR") or
                                       Path(current_app.instance_path) / "payment_evidence"))


def run_daily_backup(app):
    """Job nền chỉ tạo/kiểm tra bản sao, không bao giờ thay database đang phục vụ web."""
    with app.app_context():
        try:
            path = backup_database()
            app.logger.info("Daily database backup verified: %s", path.name)
        except Exception as exc:
            # No DB content, credentials, or raw exception text in the log.
            app.logger.error("Daily database backup failed (%s)", type(exc).__name__)
