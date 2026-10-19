# FIA Workspace – Webapp quản lý câu lạc bộ

Ứng dụng web nội bộ quản lý **thành viên, sự kiện, công việc, quỹ** của câu lạc bộ, có **trợ lý AI** hỗ trợ tra cứu và lập đề xuất. Dự án viết bằng **Python/Flask** theo nội dung môn Python Web Development: Blueprint, Jinja2, Flask-WTF, SQLAlchemy + Flask-Migrate, Flask-Login, RBAC, REST API, middleware và deploy bằng Docker/Gunicorn/Nginx.

| Tài liệu | Dành cho |
|---|---|
| [docs/HUONG_DAN_SU_DUNG.md](docs/HUONG_DAN_SU_DUNG.md) | Thành viên và Ban điều hành: cách dùng từng màn hình |
| [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) | Developer: kiến trúc, phân quyền, API, migration, test, deploy, recipes |
| [docs/FUNCTION_REFERENCE.md](docs/FUNCTION_REFERENCE.md) | Developer: tham chiếu mọi hàm/class (sinh tự động từ docstring) |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Những gì đã thay đổi trong bản này |
| [SECURITY.md](SECURITY.md) | Chính sách và kiểm soát bảo mật |

## Chạy nhanh (máy cá nhân)

Yêu cầu: Python 3.12+ và Git.

```bash
git clone <repo> clb-webapp && cd clb-webapp
python3 -m venv .venv && source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # có thể để trống SECRET_KEY khi dev

flask --app run.py init-db       # tạo/cập nhật database bằng migration + dữ liệu nền (ban, role, kho mẫu)
flask --app run.py create-admin  # tạo tài khoản Quản trị hệ thống đầu tiên: tên, tên đăng nhập, mật khẩu
python run.py                    # http://127.0.0.1:5000  (FLASK_DEBUG=1 để bật debug khi dev)
```

Đăng nhập bằng tài khoản Quản trị hệ thống để duyệt những đơn đăng ký đầu tiên và gán Chủ nhiệm, Phó chủ nhiệm, Trưởng ban, Thư ký/Thủ quỹ. Tài khoản này không phải thành viên CLB (không có trong danh sách, không đóng quỹ, không nhận task). Quên mật khẩu: `flask --app run.py reset-admin-password`.

Database SQLite mặc định nằm ở `instance/clb.db`. Khi đã có dữ liệu, **không xóa file này**. Nếu đang có database của bản cũ, làm theo [DEVELOPER_GUIDE § 14](docs/DEVELOPER_GUIDE.md#14-migration-database) để nâng cấp.

Muốn thử luồng email (xác minh, quên mật khẩu) mà không cần SMTP: đặt `MAIL_OUTBOX=true` trong `.env`. Thư sẽ được lưu ở `instance/mail_outbox/*.eml`.

## Chức năng

| Nhóm | Chức năng |
|---|---|
| Tài khoản | Đăng ký (chờ BĐH duyệt; MSSV dạng `SE201256`, SĐT `+84` + 9 số, Facebook `https://facebook.com/userID`, chọn 1 ban, mật khẩu 8–64 ký tự), xác minh email, đăng nhập có ghi nhớ, quên/đặt lại/đổi mật khẩu, xác nhận lại mật khẩu cho thao tác nhạy cảm, mật khẩu tạm do BĐH cấp |
| Phân quyền | Quản trị hệ thống (tài khoản khởi tạo), Chủ nhiệm, Phó chủ nhiệm, Trưởng ban, Thư ký/Thủ quỹ, Thành viên. Quyền theo bit flag, gán vai trò theo cấp bậc, nhường chức vụ |
| Kho mẫu | Mẫu email (kể cả nội dung mọi email tự động), mẫu sự kiện, mẫu chia task. AI dùng mẫu trong kho trước, rồi sự kiện tương tự, rồi mẫu chung |
| Thành viên | Danh sách/lọc/xuất Excel, hồ sơ, avatar, kỹ năng & khả năng nhận task, nhập danh sách CSV/XLSX qua API |
| Sự kiện & công việc | Tạo sự kiện (kể cả nhập từ CTFtime), đăng ký (không giới hạn số người), điểm danh QR hoặc thủ công, đánh giá; giao task, nộp sản phẩm, duyệt; tính % hoạt động theo ban/kỳ |
| Quỹ | Sổ thu chi theo kỳ và đánh dấu đóng quỹ; khoản thu theo đợt, thành viên gửi thanh toán, thủ quỹ xác nhận; miễn/giảm; báo cáo Excel |
| Trợ lý AI | Chat có lịch sử, tra cứu số liệu đã kiểm chứng theo quyền, tóm tắt ngày, báo cáo tuần/tháng, đề xuất sự kiện/task và nháp email. Mọi hành động đều cần người có quyền duyệt |
| Thông báo | Email khi duyệt thành viên, giao/duyệt task; nhắc deadline và kỳ quỹ bằng job định kỳ |

## Bảng quyền

| Quyền | Admin | CN | PCN | TB | TK/TQ | TV |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Xem/duyệt đăng ký | ✔ | ✔ | ✔ | – | – | – |
| Quản lý thành viên | ✔ | ✔ | ✔ | ✔ | – | – |
| Sự kiện & task (+ mẫu sự kiện/task) | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| Quỹ (ghi/duyệt) | ✔ | ✔ | ✔ | – | ✔ | – |
| Phân vai trò | ✔** | ✔ | ✔* | – | – | – |
| Nhường chức vụ của bản thân | – | ✔ | ✔ | ✔ | ✔ | – |
| Mẫu email trong Kho mẫu | ✔ | ✔ | ✔ | – | – | – |

\* Phó chủ nhiệm không gán được Phó chủ nhiệm hay Chủ nhiệm. \*\* Chỉ Quản trị hệ thống gán được Chủ nhiệm. AI duyệt đề xuất theo quyền tương ứng. Muốn đổi chính sách: sửa `DEFAULT_ROLES` trong `app/models.py` rồi chạy `flask --app run.py sync-roles`.

## Trợ lý AI

Cấu hình trong `.env`:

```dotenv
AI_PROVIDER=gemini            # gemini | anthropic | rules (không dùng model)
GEMINI_API_KEY=your-private-key
GEMINI_MODEL=gemini-3.5-flash-lite
```

Khi không có key, hệ thống dùng chế độ quy tắc (vẫn tra cứu số liệu được). Số liệu luôn lấy từ database theo quyền người hỏi, model không được tự ghi dữ liệu hay gửi email. Chi tiết luồng Agent, giới hạn và các bất biến an toàn: [DEVELOPER_GUIDE § 12](docs/DEVELOPER_GUIDE.md#12-trợ-lý-ai). Các câu lệnh mẫu cho người dùng: [HUONG_DAN_SU_DUNG § 6 và § 12](docs/HUONG_DAN_SU_DUNG.md).

## Kiểm thử

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -q
python -m coverage run --source=app -m unittest discover -s tests && python -m coverage report
```

Test dùng database tạm và mock SMTP/AI, không đụng `instance/clb.db`. Script giao diện (Playwright): `PYTHONPATH=. python tests/ui_fixture_server.py` rồi `node tests/test_live_ui.cjs`.

## Triển khai

```bash
cp .env.example .env    # APP_ENV=production, SECRET_KEY, TRUSTED_HOSTS, MAIL_*...
docker compose up -d --build
docker compose exec web flask --app run.py create-admin
```

Đặt Nginx (`deploy/nginx.conf`) phía trước để có HTTPS. Không dùng Docker thì chạy `gunicorn -c gunicorn.conf.py wsgi:app` và `flask --app run.py run-scheduler` (systemd units có sẵn trong `deploy/`). Checklist đầy đủ: [DEVELOPER_GUIDE § 16](docs/DEVELOPER_GUIDE.md#16-triển-khai-production).

## Cấu trúc thư mục

```text
app/            # mã nguồn Flask (routes/, services/, templates/, static/, models, forms…)
migrations/     # Flask-Migrate (Alembic)
tests/          # unittest + Playwright scripts
docs/           # tài liệu người dùng & developer
deploy/         # nginx, systemd
scripts/        # công cụ (sinh FUNCTION_REFERENCE.md)
run.py, wsgi.py, gunicorn.conf.py, Dockerfile, docker-compose.yml
```
