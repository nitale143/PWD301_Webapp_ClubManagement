"""App factory của webapp CLB (Ch2 – Blueprint, Ch5 – ORM, Ch6 – Flask-Login).

`create_app()` khởi tạo các extension dùng chung, đăng ký blueprint và các
hook bảo mật. Mọi extension được tạo ở cấp module rồi `init_app()` trong
factory, để test có thể tạo nhiều app độc lập.
"""
import os

from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Vui lòng đăng nhập để tiếp tục."
login_manager.login_message_category = "error"
# Fresh login (Ch6 – slide 115): thao tác nhạy cảm yêu cầu nhập lại mật khẩu
login_manager.refresh_view = "auth.reauthenticate"
login_manager.needs_refresh_message = "Vui lòng nhập lại mật khẩu để tiếp tục thao tác nhạy cảm."
login_manager.needs_refresh_message_category = "error"


def create_app(config_override=None):
    """App factory: tạo Flask app, nạp cấu hình (+ `config_override` cho test), khởi tạo extension
    (SQLAlchemy, Migrate, CSRF, Login, Limiter), đăng ký blueprint, hook bảo mật, lệnh CLI và
    scheduler.
    """
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object("app.config.Config")
    if config_override:
        app.config.update(config_override)
    if app.config.get("TESTING"):
        # Test: tạo bảng trực tiếp và tắt rate limit, trừ khi test tự cấu hình
        override = config_override or {}
        if "AUTO_CREATE_TABLES" not in override:
            app.config["AUTO_CREATE_TABLES"] = True
        if "RATELIMIT_ENABLED" not in override:
            app.config["RATELIMIT_ENABLED"] = False
        if "SESSION_PROTECTION" not in override:
            # Test dựng session thủ công (không có dấu vân tay IP/UA của Flask-Login)
            app.config["SESSION_PROTECTION"] = None

    os.makedirs(app.instance_path, exist_ok=True)
    from app.hardening import configure_security
    configure_security(app)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.session_protection = app.config.get("SESSION_PROTECTION", "strong")

    from app.limiter import limiter
    limiter.init_app(app)

    from app.services.ui_context import workspace_context
    app.context_processor(workspace_context)
    from app.validators import facebook_id_part, phone_local_part
    app.jinja_env.globals.update(phone_local_part=phone_local_part, facebook_id_part=facebook_id_part)

    from app import models  # noqa: F401  (đăng ký models với SQLAlchemy)

    # --- đăng ký blueprints ---
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.member import member_bp
    from app.routes.event import event_bp
    from app.routes.funds import funds_bp
    from app.routes.ai import ai_bp
    from app.routes.admin import admin_bp
    from app.routes.profile import profile_bp
    from app.routes.api import api_bp
    from app.routes.kho_mau import kho_mau_bp

    for blueprint in (auth_bp, main_bp, member_bp, event_bp, funds_bp, ai_bp,
                      admin_bp, profile_bp, api_bp, kho_mau_bp):
        app.register_blueprint(blueprint)

    from app.hardening import install_hardening
    install_hardening(app)
    from app.security import install_account_guards
    install_account_guards(app)

    from app.cli import register_cli
    register_cli(app)

    # Schema do Flask-Migrate quản lý (`flask db upgrade`). Chỉ test/demo nhanh
    # mới tạo bảng trực tiếp bằng create_all (AUTO_CREATE_TABLES=True).
    if app.config.get("AUTO_CREATE_TABLES"):
        with app.app_context():
            db.create_all()
            from app.models import Role
            Role.insert_roles()

    if (not app.config.get("TESTING") and app.config.get("SCHEDULER_ENABLED", True)
            and app.config.get("SCHEDULER_IN_WEB", True)):
        from app.scheduler import init_scheduler
        init_scheduler(app)

    return app
