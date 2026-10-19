# Định dạng đăng ký, tài khoản Quản trị hệ thống, Kho mẫu (20/10/2026)

## Định dạng thông tin (đồng bộ form, REST API, CLI, migration)
- **MSSV**: `^[HDQCS][ASE]\d{6}$` (vd `SE201256`), tự viết hoa. Đăng nhập gõ chữ thường cũng được.
- **SĐT**: lưu `+84` + 9 số. Ô nhập có sẵn tiền tố `+84`, người dùng nhập 9 số (dán `0…`/`+84…` cũng được chuẩn hóa).
- **Facebook**: chỉ nhận `https://facebook.com/<userID>`; ô nhập có sẵn tiền tố, người dùng nhập userID. Không nhận `www.`, `m.`, `fb.com`, `profile.php?id=`.
- **Email**: kiểm tra định dạng chung, không giới hạn tên miền.
- **Mật khẩu mới**: 8–64 ký tự (ô đăng nhập vẫn nhận tới 128 để tài khoản cũ đăng nhập được).
- Migration `0003` chuẩn hóa SĐT/Facebook đã lưu nếu chuyển được.

## Đăng ký thành viên
- Chỉ chọn **một** ban (RadioField). Giao diện mới: chia nhóm Thông tin cá nhân / Liên hệ / Ban hoạt động / Mật khẩu, ô có tiền tố, thẻ chọn ban, các bước đăng ký ở cột trái.

## Tài khoản Quản trị hệ thống
- `flask create-admin` thay cho `create-cn`: chỉ hỏi tên hiển thị, tên đăng nhập, mật khẩu; chỉ tạo được một lần. Thêm `flask reset-admin-password`.
- Role `ADMIN` (rank -1): mọi quyền trừ nhường chức vụ. Là tài khoản duy nhất gán được **Chủ nhiệm**. Không ai gán/đổi được vai trò ADMIN.
- Không phải thành viên CLB: không có trong danh bạ, danh sách phân quyền, khoản thu, người nhận task, nhắc kỳ quỹ; email `*.invalid` không bao giờ được gửi.

## Kho mẫu (`/kho-mau`)
- **Mẫu email**: 12 mẫu hệ thống (nội dung mọi email tự động + mẫu chung cho AI) sửa được tiêu đề/nội dung, có khôi phục mặc định; mẫu của CLB có tên, từ khóa, mô tả, bật/tắt. Mọi email tự động nay lấy nội dung từ kho (`render_system_email`).
- **Mẫu sự kiện / mẫu chia task** (bảng mới `proposal_template`).
- AI chọn khung theo thứ tự: mẫu chọn tay → mẫu trong kho (tên, từ khóa, loại sự kiện) → sự kiện tương tự → mẫu chung; có model thì điều chỉnh theo mô tả. Bản xem trước đề xuất ghi rõ "Khung".
- Nháp email AI: mẫu trong kho khớp → dùng mẫu (điền `{{noi_dung}}`); không khớp → mẫu chung, AI viết lại cho đúng mục đích.
- Đề xuất sự kiện bỏ trống giờ kết thúc → lấy thời lượng của mẫu.
- Quản lý mail mẫu chuyển từ trang AI sang Kho mẫu (route cũ `/ai/email-templates…` → `/kho-mau/email…`).
- `flask init-db` / `flask seed-templates` nạp mẫu mặc định còn thiếu.

## Kiểm thử
- Thêm `tests/test_formats_admin_templates.py`; cập nhật test cũ theo định dạng mới. 181 test.

# Chỉnh sửa theo góp ý vận hành (16/10/2026)

## Backend
- **Sự kiện không giới hạn số người**: bỏ ô "Số người tối đa" (form, API, đề xuất AI); đăng ký luôn vào trạng thái `registered`. Cột `so_nguoi_toi_da` giữ lại trong DB cho dữ liệu cũ nên không cần migration.
- **Hạn đăng ký nằm trong khoảng bắt đầu – kết thúc** (thời gian sự kiện gồm cả setup và báo cáo). `registration_open` cho đăng ký tới hạn đăng ký, hoặc tới giờ kết thúc nếu không đặt hạn.
- **Sửa lỗi trợ lý AI không phản hồi**: generator của `/ai/stream` chạy sau khi view return nên user/cuộc trò chuyện bị tách khỏi session DB (`InvalidRequestError`). Nay nạp lại object bên trong generator.
- **Loại khoản thu còn 3**: Quỹ theo kỳ (`semester`), Quỹ sự kiện (`event`), Khác (`other`). Dữ liệu cũ (monthly/yearly/voluntary) vẫn hiển thị.
- **Gửi xác nhận đóng quỹ không cần nhập số tiền**: hệ thống tự lấy toàn bộ số còn phải đóng; API/thủ quỹ vẫn nhập được số cụ thể.
- `session_marker` tính theo micro-giây để đổi mật khẩu luôn đăng xuất phiên cũ (người dùng phải đăng nhập lại một lần sau khi cập nhật).

## Frontend
- Trang Thành viên: thành viên thường thấy danh bạ CLB (tên, MSSV, ban, vai trò), chỉ mở được hồ sơ của mình; nút Xuất danh sách chỉ hiện với BĐH.
- Cột Liên hệ (BĐH): số điện thoại ở trên, link Facebook ở dưới.
- Nút "Lọc danh sách" đổi thành **Áp dụng**.
- Xét duyệt task: ô MSSV và nút "Chỉ định lại" thẳng hàng; ghi **Nhận xét** trước khi chọn **Quyết định**.

# Thay đổi – bản chỉnh sửa theo nội dung bài học (10/2026)

Mục tiêu: bám sát kiến thức 12 chương môn Python Web Development, sửa các lỗi khi triển khai thật và bổ sung tài liệu. Toàn bộ test cũ vẫn chạy, đã bổ sung test mới (167 test, coverage ~83%).

## Ch4 – Web forms
- Thêm **Flask-WTF / WTForms** (`app/forms.py`): LoginForm, RegistrationForm, ChangePasswordForm, ForgotPasswordForm, ResetPasswordForm, ReauthForm, EventForm, FundPeriodForm, FundTransactionForm, AvatarForm.
- Thay CSRF tự viết bằng **CSRFProtect** cho toàn app. Header AJAX `X-CSRFToken`/`X-CSRF-Token`. Template dùng `form.hidden_tag()`.
- Đăng ký thêm ô **nhập lại mật khẩu** (`EqualTo`), validator **mật khẩu mạnh**, kiểm tra trùng email, giới hạn độ dài cho mọi ô (danh mục/nội dung thu chi, tên task…).
- Upload avatar dùng `FileField` + `FileRequired` + `FileAllowed`, giữ bước kiểm tra ảnh bằng Pillow.
- Thông báo lỗi WTForms được dịch sang tiếng Việt; macro `form_field` hiển thị lỗi ngay dưới từng ô.

## Ch5 – Database & ORM
- Thêm **Flask-Migrate (Alembic)**: `migrations/0001` (schema gốc), `0002` (role, xác thực, email unique). Ứng dụng không còn tự `create_all()` khi chạy thật.
- `user.email` unique (chuẩn hóa chữ thường).
- Bỏ API đã cũ: `Model.query.get()` → `db.session.get()`, `datetime.utcnow()` → `timeutil.utcnow()`.
- Đổi tên `app/migrations.py` → `app/legacy_sqlite.py` để không nhầm với thư mục Alembic. Có hướng dẫn nâng cấp DB cũ (`db stamp 0001`).

## Ch6 – Authentication
- Mới: **xác minh email**, **quên / đặt lại mật khẩu** (token itsdangerous, 1 giờ, dùng một lần), **đổi mật khẩu**, **ghi nhớ đăng nhập**, **fresh login / xác nhận lại mật khẩu** cho thao tác nhạy cảm, `session_protection="strong"`.
- Đổi mật khẩu thì mọi phiên và cookie ghi nhớ cũ mất hiệu lực (`User.get_id` gắn `session_marker`).
- BĐH có thể **cấp mật khẩu tạm** (chỉ cho người cấp thấp hơn). Tài khoản import hoặc dùng mật khẩu tạm bị **bắt đổi mật khẩu** lần đầu.
- Flask-Login nạp user bằng `db.session.get`.

## Ch7 – Roles & Permissions
- Mô hình **User → Role → Permission (bit flag)**: bảng `role`, class `Permission`, `DEFAULT_ROLES`, decorator `permission_required` / `any_permission_required`.
- Chính sách mới theo yêu cầu CLB: duyệt thành viên = CN/PCN; quản lý thành viên = CN/PCN/TB; sự kiện & task = 4 chức vụ BĐH; quỹ = CN/PCN/Thư ký–Thủ quỹ; phân vai trò = CN/PCN (PCN không gán PCN/CN); nhường chức vụ = cả 4 chức vụ BĐH. AI duyệt đề xuất theo quyền tương ứng.
- Sửa lỗi `role_required("CN","PCN")` trước đây vẫn cho mọi BĐH đi qua (mail mẫu nay đúng CN/PCN).
- Phạm vi dữ liệu theo nghiệp vụ: thủ quỹ xem tình trạng quỹ của mọi người dù không có quyền xem hồ sơ thành viên.

## Ch9 – REST API
- **Flask-Limiter** cho `/api/v1/*` và các form xác thực (Redis khi nhiều worker).
- Tài liệu endpoint đầy đủ trong `docs/DEVELOPER_GUIDE.md`.

## Ch10 – Decorator, middleware, error handling
- **ProxyFix** (bật bằng `TRUST_PROXY_HOPS`). Trước đây chạy sau Nginx thì mọi request bị chặn 400 vì app không nhận ra HTTPS.
- Lỗi 500 ghi **đủ stack trace** (`logger.exception`) kèm request_id.
- Chốt "bắt đổi mật khẩu" và CSRF cho URL không khớp route.

## Ch11 – Testing & deployment
- `run.py` không còn bật `debug=True` cố định (đọc từ `FLASK_DEBUG`, cấm ở production).
- Thêm `wsgi.py`, `gunicorn.conf.py`, `Dockerfile`, `docker-compose.yml` (web + scheduler riêng + Redis), `deploy/nginx.conf`, systemd units.
- Scheduler chạy được ở tiến trình riêng: `flask run-scheduler` + `SCHEDULER_IN_WEB=false`.
- Test mới: `test_account_security.py`, `test_core_flows.py`, ma trận quyền mới trong `test_two_role_security.py`. Helper `tests/support.py`.
- `requirements.txt` cập nhật; `requirements-lock.txt` sinh lại có hash; thêm `requirements-dev.txt`.

## Template & mã nguồn
- `base.html` tách thành `partials/` (sidebar, topbar, flash, dialog), menu hiển thị theo quyền (`can(Permission.X)`).
- Thông báo, email, tên cột Excel chuyển sang tiếng Việt có dấu.
- **Mọi hàm/class đều có docstring tiếng Việt.** `scripts/gen_function_reference.py` sinh `docs/FUNCTION_REFERENCE.md`.
- Gỡ thư mục `backups/` và `previews/` khỏi repo (lịch sử đã có trong Git).

## Chưa thay đổi (cố ý)
- Hai hệ thống quỹ (theo kỳ và khoản thu) vẫn song song. Gộp lại cần migration dữ liệu và thống nhất với CLB (xem DEVELOPER_GUIDE mục 5).
- Tên file CSS `preview-*` / `parity-*` giữ nguyên để không ảnh hưởng giao diện đã duyệt và các test UI.
- API vẫn xác thực bằng session (cùng domain). JWT chỉ cần khi có client ngoài.
