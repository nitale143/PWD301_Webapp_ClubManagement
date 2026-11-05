"""Cấu hình ứng dụng, đọc từ biến môi trường / file .env (Ch11 – slide 18).

Không bao giờ ghi secret (SECRET_KEY, mật khẩu SMTP, API key) trực tiếp vào code.
Xem `.env.example` để biết danh sách biến.
"""
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _env_bool(name, default):
    """Đọc biến môi trường kiểu true/false; trả `default` nếu biến không đặt hoặc rỗng."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Toàn bộ cấu hình ứng dụng, đọc từ biến môi trường (.env). Xem .env.example để biết ý nghĩa
    từng biến.
    """
    SECRET_KEY = os.environ.get("SECRET_KEY")
    APP_ENV = os.environ.get("APP_ENV", "development").lower()
    SECURITY_TRUSTED_HOSTS = [host.strip().lower() for host in os.environ.get(
        "TRUSTED_HOSTS", "localhost,127.0.0.1,::1").split(",") if host.strip()]
    SESSION_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    SESSION_COOKIE_NAME = "__Host-fia" if APP_ENV == "production" else "fia_session"
    LOGIN_LIMIT_ENABLED = True
    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_WINDOW_SECONDS = 300
    LOGIN_LOCKOUT_SECONDS = 300
    MAX_FORM_PARTS = 200
    MAX_FORM_MEMORY_SIZE = 128 * 1024
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Bangkok")
    SESSION_COOKIE_SAMESITE = "Lax"
    # Flask-Login: "strong" đăng xuất khi IP hoặc trình duyệt thay đổi (Ch6 – slide 71)
    SESSION_PROTECTION = os.environ.get("SESSION_PROTECTION", "strong")
    # Cookie "Ghi nhớ đăng nhập" (Ch6 – slide 113-114)
    REMEMBER_COOKIE_DURATION = timedelta(days=int(os.environ.get("REMEMBER_DAYS", 14)))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = APP_ENV == "production"
    REMEMBER_COOKIE_NAME = "fia_remember"
    # Thời hạn token gửi qua email (giây)
    EMAIL_CONFIRM_MAX_AGE = 48 * 3600
    PASSWORD_RESET_MAX_AGE = 3600

    # --- Flask-WTF (Ch4) ---
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None          # token sống theo session (session tự hết hạn sau 8 giờ)
    WTF_CSRF_HEADERS = ["X-CSRFToken", "X-CSRF-Token"]

    # --- Flask-Limiter (Ch9) ---
    RATELIMIT_ENABLED = _env_bool("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    API_RATE_LIMIT = os.environ.get("API_RATE_LIMIT", "120 per minute")
    AUTH_RATE_LIMIT = os.environ.get("AUTH_RATE_LIMIT", "20 per minute")

    # --- Triển khai sau reverse proxy (Ch10 – ProxyFix, Ch11 – Nginx) ---
    # Số proxy tin cậy đứng trước app (Nginx = 1). 0 = chạy trực tiếp, không tin header X-Forwarded-*.
    TRUST_PROXY_HOPS = int(os.environ.get("TRUST_PROXY_HOPS", 0))

    # --- Database ---
    # Tạo bảng bằng create_all thay vì migration. Chỉ dành cho test/demo nhanh;
    # mặc định bật khi TESTING. Môi trường thật dùng `flask db upgrade`.
    AUTO_CREATE_TABLES = _env_bool("AUTO_CREATE_TABLES", None)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "clb.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Danh sách chức vụ (thứ tự quyền hạn giảm dần). Quyền chi tiết: app.models.DEFAULT_ROLES
    ROLE_ORDER = ["CN", "PCN", "TB", "THUKY_THUQUY", "THANH_VIEN"]
    ROLE_LABELS = {
        "CN": "Chủ nhiệm",
        "PCN": "Phó chủ nhiệm",
        "TB": "Trưởng ban",
        "THUKY_THUQUY": "Thư ký / Thủ quỹ",
        "THANH_VIEN": "Thành viên",
    }
    # Giới hạn số lượng theo chức vụ (BĐH)
    ROLE_LIMITS = {"CN": 1, "PCN": 2, "TB": 3, "THUKY_THUQUY": 1}

    MEMBER_STATUS = ["pending", "approved", "rejected"]
    # --- Tình trạng hoạt động (app/services/activities.py) ---
    # Điểm hoạt động của một kỳ = TASK% × tỉ lệ task hoàn thành + ĐIỂM DANH% × tỉ lệ có mặt.
    # Chỉ tính sự kiện ĐÃ KẾT THÚC trong kỳ. Thiếu một phần (không có task / không có sự kiện
    # nào phải điểm danh) thì phần còn lại chiếm 100%. Thiếu cả hai → "Chưa xếp loại".
    ACTIVITY_WEIGHTS = {"task": 50, "attendance": 50}
    # Ngưỡng điểm (0–100), xét từ trên xuống; điểm 0 là "Không hoạt động".
    ACTIVITY_THRESHOLDS = [
        (80, "tich_cuc"),
        (50, "hoat_dong"),
        (0.01, "it_hoat_dong"),
        (0, "khong_hoat_dong"),
    ]
    # Nhãn hiển thị duy nhất cho toàn hệ thống (trang, Excel, trợ lý AI).
    # "tam_nghi" lấy từ hồ sơ thành viên (MemberRecord.tinh_trang = paused) và ghi đè điểm.
    ACTIVITY_LABELS = {
        "tich_cuc": "Tích cực",
        "hoat_dong": "Hoạt động",
        "it_hoat_dong": "Ít hoạt động",
        "khong_hoat_dong": "Không hoạt động",
        "chua_xep_loai": "Chưa xếp loại",
        "tam_nghi": "Tạm nghỉ",
    }
    TASK_STATUS = ["dang_lam", "cho_duyet", "lam_lai", "hoan_thanh", "huy"]
    EVENT_STATUS = ["sap_dien_ra", "dang_dien_ra", "da_ket_thuc", "cancelled"]

    # --- Cấu hình gửi mail (SMTP) ---
    # Mặc định MAIL_ENABLED=False: webapp vẫn chạy, email không được gửi.
    # Khi phát triển có thể đặt MAIL_OUTBOX=true để lưu email thành file .eml
    # trong instance/mail_outbox/ (chỉ dùng ở development).
    MAIL_ENABLED = os.environ.get("MAIL_ENABLED", "false").lower() == "true"
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "false").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", MAIL_USERNAME)
    MAIL_OUTBOX = _env_bool("MAIL_OUTBOX", False)

    # --- Scheduler ---
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"
    # False khi chạy nhiều worker Gunicorn: scheduler chạy riêng bằng `flask run-scheduler`
    SCHEDULER_IN_WEB = _env_bool("SCHEDULER_IN_WEB", True)
    DEADLINE_WARNING_DAYS = 7
    # Số ngày trước khi kỳ hiện tại kết thúc thì bắt đầu nhắc BĐH nhập kỳ mới
    PERIOD_REMINDER_DAYS = 30

    # --- AI Agent ---
    # Dùng 1 API key CHUNG do CLB quản lý (không cho từng user tự nhập key
    # riêng, để tránh phải lưu/mã hóa key cá nhân và để kiểm soát chi phí).
    AI_PROVIDER = os.environ.get("AI_PROVIDER", "gemini")
    AI_API_KEY = os.environ.get("AI_API_KEY", "")
    AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-4-6")
    AI_WORKSPACE_ID = os.environ.get("AI_WORKSPACE_ID", "")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    AI_TIMEOUT_SECONDS = 20
    AI_MAX_CALLS_PER_MINUTE = int(os.environ.get("AI_MAX_CALLS_PER_MINUTE", 12))
    AI_DAILY_TOKEN_LIMIT = int(os.environ.get("AI_DAILY_TOKEN_LIMIT", 100000))
    AI_CHAT_MESSAGES_PER_MINUTE = int(os.environ.get("AI_CHAT_MESSAGES_PER_MINUTE", 20))
    # Verified chat reads use deterministic queries; model text remains a draft.
    AI_STRICT_FACTS = os.environ.get("AI_STRICT_FACTS", "true").lower() == "true"
    # Bounded model/tool loop; facts still come from permission-scoped services.
    AI_AGENT_ENABLED = os.environ.get("AI_AGENT_ENABLED", "true").lower() == "true"
    AI_AGENT_MAX_STEPS = int(os.environ.get("AI_AGENT_MAX_STEPS", 3))
    AI_AGENT_MAX_SECONDS = int(os.environ.get("AI_AGENT_MAX_SECONDS", 90))
    # Câu hỏi một ý đã có dữ liệu thì kết thúc ngay, không tốn thêm lượt gọi model.
    AI_AGENT_FAST_FINISH = os.environ.get("AI_AGENT_FAST_FINISH", "true").lower() == "true"
    # Model viết câu trả lời đúng trọng tâm từ dữ liệu đã tra cứu; mọi con số được đối chiếu.
    AI_AGENT_COMPOSE = os.environ.get("AI_AGENT_COMPOSE", "true").lower() == "true"

    # --- Upload avatar (lưu trực tiếp trên server) ---
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "static", "uploads", "avatars")
    ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024  # 3MB mỗi request
