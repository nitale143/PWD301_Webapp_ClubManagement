"""Gửi email qua SMTP có mã hóa (TLS/SSL), không làm lộ lỗi hoặc nội dung thư.

`send_email()` trả về True/False thay vì ném lỗi, để view tự báo cho người dùng.
Khi MAIL_ENABLED=false: không gửi. Riêng môi trường development có thể bật
MAIL_OUTBOX=true để lưu thư thành file .eml trong instance/mail_outbox/ (dùng
để lấy link xác minh email / đặt lại mật khẩu khi chạy thử trên máy).
"""

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

from flask import current_app

from app.timeutil import utcnow


_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"
)


def _safe_header(value, max_length):
    """Chuỗi header email hợp lệ: không rỗng, không quá dài, không chứa ký tự xuống dòng (chống
    header injection).
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_length
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _valid_email(value):
    """Địa chỉ email hợp lệ theo regex và độ dài cho phép."""
    return _safe_header(value, 254) and _EMAIL_RE.fullmatch(value) is not None and ".." not in value


def _valid_sender(value):
    """Người gửi hợp lệ: "fia@gmail.com" hoặc có tên hiển thị "FIA Club <fia@gmail.com>"."""
    if not _safe_header(value, 320):
        return False
    name, address = parseaddr(value)
    return bool(address) and _valid_email(address) and "<" not in name and ">" not in name


def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send one plain-text message, returning false if it cannot be sent safely."""
    cfg = current_app.config

    # Tài khoản Quản trị hệ thống dùng địa chỉ *.invalid (không có hộp thư) → không gửi
    if isinstance(to_email, str) and to_email.lower().endswith(".invalid"):
        return False
    if not cfg.get("MAIL_ENABLED"):
        if cfg.get("MAIL_OUTBOX") and cfg.get("APP_ENV") == "development":
            return _write_outbox(to_email, subject, body)
        return False
    if not _valid_email(to_email) or not _safe_header(subject, 255) or not isinstance(body, str):
        current_app.logger.warning("Email rejected because its address or subject was invalid")
        return False

    sender = cfg.get("MAIL_DEFAULT_SENDER", "")
    server_name = cfg.get("MAIL_SERVER", "")
    if not _valid_sender(sender) or not _safe_header(server_name, 253) or any(c.isspace() for c in server_name):
        current_app.logger.warning("Email delivery configuration is invalid")
        return False

    use_ssl = bool(cfg.get("MAIL_USE_SSL", False))
    use_tls = bool(cfg.get("MAIL_USE_TLS", False))
    if not (use_ssl or use_tls):
        current_app.logger.warning("Email delivery requires an encrypted SMTP connection")
        return False
    if use_ssl and use_tls:
        current_app.logger.warning("Email delivery configuration selects conflicting SMTP transports")
        return False

    try:
        timeout = float(cfg.get("MAIL_TIMEOUT_SECONDS", 10))
        port = int(cfg.get("MAIL_PORT", 587))
    except (TypeError, ValueError, OverflowError):
        current_app.logger.warning("Email delivery configuration is invalid")
        return False
    if not 0 < timeout <= 120 or not 1 <= port <= 65535:
        current_app.logger.warning("Email delivery configuration is invalid")
        return False

    username = cfg.get("MAIL_USERNAME", "")
    password = cfg.get("MAIL_PASSWORD", "")
    if username and (not _safe_header(username, 254) or not password):
        current_app.logger.warning("Email authentication configuration is invalid")
        return False

    message = EmailMessage()
    try:
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = to_email
        message.set_content(body)
    except (TypeError, ValueError):
        current_app.logger.warning("Email rejected because its headers were invalid")
        return False

    context = ssl.create_default_context()
    try:
        if use_ssl:
            smtp_context = smtplib.SMTP_SSL(server_name, port, timeout=timeout, context=context)
        else:
            smtp_context = smtplib.SMTP(server_name, port, timeout=timeout)
        with smtp_context as smtp:
            if use_tls:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return True
    except Exception as exc:
        current_app.logger.warning("SMTP delivery failed (%s)", type(exc).__name__)
        return False


def _write_outbox(to_email, subject, body):
    """Chỉ dùng khi phát triển: lưu thư vào instance/mail_outbox/<thời-gian>.eml (quyền 0600)."""
    if not _valid_email(to_email) or not _safe_header(subject, 255) or not isinstance(body, str):
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = current_app.config.get("MAIL_DEFAULT_SENDER") or "dev@localhost"
    message["To"] = to_email
    message.set_content(body)
    folder = os.path.join(current_app.instance_path, "mail_outbox")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, utcnow().strftime("%Y%m%d-%H%M%S-%f") + ".eml")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(bytes(message))
    current_app.logger.info("Development mail saved to outbox")
    return True
