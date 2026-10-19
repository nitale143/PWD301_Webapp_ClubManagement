"""Điểm khởi chạy webapp CLB.

Phát triển (máy cá nhân):
    python run.py                         # http://127.0.0.1:5000, debug theo FLASK_DEBUG
Production (Ch11): không dùng file này để chạy server, dùng Gunicorn:
    gunicorn -c gunicorn.conf.py wsgi:app

Lệnh quản trị (init-db, create-admin, db upgrade, …) xem app/cli.py.
"""
import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402  (phải nạp .env trước khi tạo app)

app = create_app()


if __name__ == "__main__":
    # Debug chỉ bật khi được yêu cầu rõ ràng và không bao giờ ở production
    debug = (os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"}
             and app.config.get("APP_ENV") != "production")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=debug)
