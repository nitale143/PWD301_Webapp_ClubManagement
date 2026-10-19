# Developer Guide – FIA Workspace (Flask)

Tài liệu cho người **đọc hiểu, sửa lỗi và phát triển tiếp** webapp quản lý CLB.

- Danh sách đầy đủ mọi hàm/class (chữ ký, dòng, route, decorator bảo vệ, mô tả) nằm ở [`FUNCTION_REFERENCE.md`](FUNCTION_REFERENCE.md). File này sinh tự động từ docstring.
- Hướng dẫn cho người dùng cuối: [`HUONG_DAN_SU_DUNG.md`](HUONG_DAN_SU_DUNG.md).
- Thay đổi so với bản trước: [`CHANGELOG.md`](CHANGELOG.md).

## Mục lục

1. [Kiến trúc tổng quan](#1-kiến-trúc-tổng-quan)
2. [Cài đặt môi trường phát triển](#2-cài-đặt-môi-trường-phát-triển)
3. [Vòng đời một request](#3-vòng-đời-một-request)
4. [Cấu hình](#4-cấu-hình)
5. [Mô hình dữ liệu](#5-mô-hình-dữ-liệu)
6. [Phân quyền (RBAC)](#6-phân-quyền-rbac)
7. [Xác thực và session](#7-xác-thực-và-session)
8. [Form, validate và CSRF](#8-form-validate-và-csrf)
9. [Template](#9-template)
10. [Tầng service và quy ước xử lý lỗi](#10-tầng-service-và-quy-ước-xử-lý-lỗi)
11. [REST API](#11-rest-api)
12. [Trợ lý AI](#12-trợ-lý-ai)
13. [Email, scheduler, file upload](#13-email-scheduler-file-upload)
14. [Migration database](#14-migration-database)
15. [Kiểm thử](#15-kiểm-thử)
16. [Triển khai production](#16-triển-khai-production)
17. [Bảo mật: đối chiếu bài học](#17-bảo-mật-đối-chiếu-bài-học)
18. [Công thức thường gặp (recipes)](#18-công-thức-thường-gặp-recipes)
19. [Xử lý sự cố](#19-xử-lý-sự-cố)

---

## 1. Kiến trúc tổng quan

**Công nghệ:** Python 3.12+ · Flask 3.1 · Flask-SQLAlchemy (ORM) · Flask-Migrate/Alembic · Flask-Login · Flask-WTF/WTForms · Flask-Limiter · Jinja2 · APScheduler · Gunicorn + Nginx · SQLite (mặc định) hoặc PostgreSQL.

Ứng dụng theo mô hình **MVT** của Flask (Ch1), chia lớp như sau:

```
Trình duyệt / API client
        │  HTTP(S)
        ▼
Nginx (TLS, static) ──► Gunicorn ──► ProxyFix (WSGI middleware)
                                         │
                       before_request hooks (hardening, CSRF, account guard, rate limit)
                                         │
             routes/ (Blueprint)  ── decorator: login_required → permission_required
                     │
                     ▼
             services/ (nghiệp vụ, kiểm tra quyền lần 2, DomainError)
                     │
                     ▼
             models.py (SQLAlchemy ORM) ──► database
                     │
             templates/ (Jinja2) hoặc jsonify (API)
                                         │
                       after_request (header bảo mật, CSP, log) · errorhandler
```

### Cấu trúc thư mục

```text
.
├── run.py                  # chạy dev server; nạp .env
├── wsgi.py                 # entry cho Gunicorn (production)
├── gunicorn.conf.py        # cấu hình Gunicorn
├── Dockerfile, docker-compose.yml, .dockerignore
├── deploy/                 # nginx.conf, systemd units
├── migrations/             # Flask-Migrate (Alembic): 0001 baseline, 0002 roles/auth, 0003 admin/kho mẫu/định dạng
├── scripts/gen_function_reference.py
├── docs/                   # tài liệu
├── tests/                  # unittest (+ support.py) và script Playwright (.cjs)
└── app/
    ├── __init__.py         # create_app(): app factory
    ├── config.py           # Config đọc biến môi trường
    ├── cli.py              # lệnh flask: init-db, create-admin, reset-admin-password, seed-templates, sync-roles, run-scheduler, migrate-*
    ├── models.py           # ORM models + Permission/Role
    ├── forms.py            # Flask-WTF forms
    ├── validators.py       # định dạng MSSV/SĐT/Facebook (regex + normalize_*), mật khẩu mạnh
    ├── permissions.py      # decorator: permission_required, approved_required…
    ├── security.py         # CSRF helper, chốt "bắt đổi mật khẩu"
    ├── hardening.py        # cấu hình an toàn, header, CSP, error handler, ProxyFix
    ├── tokens.py           # token có chữ ký (xác minh email, đặt lại mật khẩu)
    ├── limiter.py          # Flask-Limiter
    ├── mail.py             # gửi SMTP an toàn (+ outbox khi dev)
    ├── scheduler.py        # job định kỳ (APScheduler)
    ├── activity.py         # tính % hoạt động khi sự kiện kết thúc
    ├── legacy_sqlite.py    # nâng cấp DB SQLite tạo trước khi có migrations/
    ├── timeutil.py         # utcnow() thay datetime.utcnow()
    ├── routes/             # Blueprint: auth, main, member, profile, event, funds, ai, admin, api, kho_mau
    ├── services/           # nghiệp vụ: access, funds, events, activities, AI…
    ├── templates/          # Jinja2: base.html + partials/ + _ui.html (macro)
    └── static/             # css, js, ảnh; uploads/ (avatar – gitignore)
```

### Đối chiếu 12 chương bài học

| Chương | Áp dụng ở đâu |
|---|---|
| 1 – Web basics | Kiến trúc MVT, HTTP methods/status trong routes & API |
| 2 – Routes & views | `app/routes/*` (Blueprint, converter `<int:id>`, PRG, flash, phân trang) |
| 3 – Templates | `base.html` + `partials/`, macro `_ui.html` (`icon`, `badge`, `form_field`), filter, autoescape |
| 4 – Web forms | `app/forms.py` (FlaskForm, validators, inline validator, FileField), CSRFProtect |
| 5 – Database & ORM | `app/models.py`, quan hệ 1-n/n-n, `migrations/` (Flask-Migrate) |
| 6 – Authentication | `routes/auth.py`, `tokens.py`, Flask-Login (remember, fresh login, session protection), hash mật khẩu |
| 7 – Roles & permissions | `Permission` bit flag, bảng `role`, `permissions.py`, `services/access.py` |
| 8 – Web features | hồ sơ, avatar, phân trang, sự kiện/task, quỹ, context processor |
| 9 – REST API | `routes/api.py` (`/api/v1`, JSON, status code, phân trang, rate limit, CORS mặc định đóng) |
| 10 – Decorator, middleware, error handling | decorator 3 lớp, hook before/after_request, ProxyFix, errorhandler + log stack trace |
| 11 – Testing & deployment | `tests/`, coverage, Docker, Gunicorn, Nginx, biến môi trường, migration production |

---

## 2. Cài đặt môi trường phát triển

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt          # hoặc: pip install --require-hashes -r requirements-lock.txt
cp .env.example .env                          # điền SECRET_KEY (có thể để trống khi dev: tự tạo instance/session-secret.key)
flask --app run.py init-db                    # = flask db upgrade + seed ban, role, kho mẫu mặc định
flask --app run.py create-admin               # tạo tài khoản Quản trị hệ thống đầu tiên (tên, username, mật khẩu)
FLASK_DEBUG=1 python run.py                   # http://127.0.0.1:5000
```

Mẹo khi phát triển:

- Đặt `MAIL_OUTBOX=true` (chỉ có tác dụng khi `APP_ENV=development`): email (xác minh, đặt lại mật khẩu…) được lưu thành file `.eml` trong `instance/mail_outbox/` thay vì gửi thật. Mở file để lấy link.
- `SCHEDULER_ENABLED=false` để tắt job nền khi chạy thử.
- Server giao diện có dữ liệu mẫu, không đụng DB thật: `PYTHONPATH=. python tests/ui_fixture_server.py` (cổng 5090, tài khoản `QA001` / `ui-test-password`).

---

## 3. Vòng đời một request

Thứ tự xử lý (tham chiếu Ch10):

1. **ProxyFix** (`hardening.configure_security`): chỉ bật khi `TRUST_PROXY_HOPS > 0`. Nó tin header `X-Forwarded-For/Proto/Host` từ đúng số proxy đã khai báo, nhờ đó `request.remote_addr` và `request.is_secure` mới đúng khi chạy sau Nginx.
2. **before_request** (theo thứ tự đăng ký):
   1. `hardening.request_boundary`: tạo `g.request_id` và `g.csp_nonce`; chặn Host không nằm trong `TRUSTED_HOSTS`; production bắt buộc HTTPS; ảnh trong `/static/uploads/` chỉ phục vụ cho người được xem hồ sơ tương ứng.
   2. `CSRFProtect` (Flask-WTF): mọi POST/PUT/PATCH/DELETE phải có token hợp lệ.
   3. `security._csrf_for_unmatched_routes`: đòi token cả với URL không khớp route (trả 400, không phải 404).
   4. `security._force_password_change`: tài khoản `must_change_password` chỉ được vào trang đổi mật khẩu.
   5. Flask-Limiter: giới hạn theo IP cho `/api/v1/*` và các form xác thực.
   6. `api.authenticate` (chỉ blueprint API): bắt buộc đăng nhập và tài khoản đã duyệt.
3. **Decorator của view**, luôn theo thứ tự `@route → @login_required → [@fresh_login_required] → @permission_required(...)`.
4. **View** gọi **service**. Service kiểm tra quyền lần nữa bằng `require(...)` (phòng thủ nhiều lớp) rồi ghi DB.
5. **Template/JSON** được trả về.
6. **after_request** (`hardening.security_response`): CSP có nonce, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS (production), `Cache-Control: private, no-store` cho trang cần đăng nhập, `X-Request-ID`, và ghi log mọi thao tác ghi hoặc bị từ chối vào `instance/security.log`.
7. **Lỗi**:
   - `HTTPException` → trang HTML hoặc JSON gọn kèm "Mã hỗ trợ" (request_id).
   - `Exception` bất kỳ → rollback DB, `logger.exception` (đủ stack trace) rồi trả 500 an toàn.
   - Blueprint API: `DomainError` → `{"error": "..."}`.

---

## 4. Cấu hình

Mọi cấu hình nằm trong `app/config.py` (class `Config`) và đọc từ biến môi trường. Danh sách đầy đủ có chú thích xem `.env.example`. Các biến quan trọng:

| Biến | Mặc định | Ghi chú |
|---|---|---|
| `SECRET_KEY` | (bắt buộc ở production, ≥ 32 ký tự) | Ký session, CSRF, token email. Đổi khóa → mọi người phải đăng nhập lại, link email cũ hết hiệu lực. |
| `APP_ENV` | `development` | `production` bật kiểm tra nghiêm: cấm debug, cookie Secure, bắt HTTPS, HSTS. |
| `TRUSTED_HOSTS` | `localhost,127.0.0.1,::1` | Tên miền hợp lệ (chống Host header injection). |
| `DATABASE_URL` | `sqlite:///instance/clb.db` | PostgreSQL: `postgresql://user:pass@host/db`. |
| `TRUST_PROXY_HOPS` | `0` | `1` khi có Nginx phía trước. |
| `SESSION_PROTECTION` | `strong` | Chế độ chống chiếm phiên của Flask-Login. |
| `REMEMBER_DAYS` | `14` | Thời hạn cookie "Ghi nhớ đăng nhập". |
| `RATELIMIT_STORAGE_URI` | `memory://` | Dùng `redis://...` khi chạy nhiều worker. |
| `API_RATE_LIMIT`, `AUTH_RATE_LIMIT` | `120/min`, `20/min` | Cú pháp của Flask-Limiter. |
| `MAIL_*`, `MAIL_OUTBOX` | tắt | SMTP bắt buộc TLS/SSL. |
| `SCHEDULER_ENABLED`, `SCHEDULER_IN_WEB` | `true`, `true` | Xem [mục 13](#13-email-scheduler-file-upload). |
| `AI_*`, `GEMINI_*` | | Xem [mục 12](#12-trợ-lý-ai). |

Khi `TESTING=True`, `create_app()` tự đặt `AUTO_CREATE_TABLES=True`, `RATELIMIT_ENABLED=False`, `SESSION_PROTECTION=None`, trừ khi test truyền giá trị khác qua `config_override`.

---

## 5. Mô hình dữ liệu

Tên bảng và cột giữ tiếng Việt không dấu như database cũ. Thời gian audit, thanh toán, đăng ký lưu **UTC naive** (`timeutil.utcnow()`). Giờ sự kiện, hạn task lưu **giờ CLB naive** (`services.common.club_now()`, theo `APP_TIMEZONE`).

### Nhóm bảng

| Nhóm | Bảng | Ghi chú |
|---|---|---|
| Tài khoản & phân quyền | `user`, `role`, `user_ban`, `ban`, `member_record` | `user.chuc_vu` khớp `role.name`. `user_ban` là n-n User↔Ban (`is_truong_ban`). `member_record` 1-1 với user (tình trạng, lưu trữ). |
| Sự kiện & task | `event`, `event_detail` (1-1), `event_target_ban`, `event_registration`, `attendance` (1-1 registration), `qr_checkin_log`, `event_feedback`, `task` | `task.assignee_id` bắt buộc, `task.ban_id` để tính hoạt động theo ban. |
| Hoạt động | `member_activity`, `activity_point`, `club_setting` | `member_activity`: % task theo (user, ban, kỳ). `activity_point`: điểm danh / điều chỉnh tay. |
| Quỹ theo kỳ | `fund_period`, `fund_transaction`, `fund_due` | Sổ thu chi + đánh dấu đóng quỹ theo kỳ. `fund_period` cũng là "kỳ" để tính hoạt động. |
| Khoản thu | `fund_collection`, `fund_target_ban`, `fund_adjustment`, `fund_payment`, `payment_evidence` | Thu theo đợt, có xác nhận. |
| AI | `chat_conversation`, `chat_message`, `conversation_pending`, `assistant_pending`, `ai_proposal`, `email_proposal`, `ai_report_snapshot`, `member_planning_profile`, `ai_usage_log` | |
| Kho mẫu | `email_template`, `proposal_template` | `email_template.system_key` có giá trị = mẫu email tự động (+ `general`); rỗng = mẫu của CLB. `proposal_template.kind` = `event` / `task`, `content` là JSON, `is_default` = mẫu chung. Xem [mục 12.1](#121-kho-mẫu). |
| Khác | `notification`, `audit_log` | |

> **Hai hệ thống quỹ chạy song song.** `fund_period/fund_due/fund_transaction` (sổ quỹ theo kỳ) và `fund_collection/fund_payment` (khoản thu) **không tự đồng bộ** với nhau. Báo cáo AI chỉ tính `fund_payment` đã xác nhận. Gộp hai hệ thống là thay đổi dữ liệu lớn (`fund_period` còn được dùng làm "kỳ" cho `member_activity` và `user.ky_tham_gia_id`), nên cần migration dữ liệu riêng nếu muốn làm.

### Ràng buộc quan trọng

- `user.mssv` unique. `user.email` unique (chữ thường; migration 0002 dừng lại nếu có email trùng).
- Định dạng lưu trữ (kiểm tra ở form, API và CLI qua `app/validators.py`):

  | Trường | Định dạng | Hàm |
  |---|---|---|
  | `user.mssv` (thành viên) | `^[HDQCS][ASE]\d{6}$`, in hoa (vd `SE201256`) | `MSSV_RE`, `normalize_mssv` |
  | `user.mssv` (Quản trị hệ thống) | tên đăng nhập `^[A-Za-z0-9_-]{3,20}$` | `ADMIN_USERNAME_RE` |
  | `user.sdt` | `+84` + 9 số (`^\+84[1-9]\d{8}$`); form chỉ nhận 9 số sau +84, chấp nhận cả `0…`/`+84…` rồi chuẩn hóa | `PHONE_RE`, `normalize_phone` |
  | `user.facebook_link` | `https://facebook.com/<userID>`, userID `[A-Za-z0-9.]{1,50}` | `FACEBOOK_RE`, `normalize_facebook` |
  | mật khẩu mới | 8–64 ký tự, có chữ và số, không chứa MSSV, không phổ biến (`PASSWORD_MAX = 64`) | `password_problems`, `StrongPassword` |

  Ô mật khẩu khi **đăng nhập** vẫn nhận tới 128 ký tự (`LOGIN_PASSWORD_MAX`) để tài khoản cũ không bị khóa. Migration 0003 chuẩn hóa SĐT/Facebook cũ nếu chuyển được.
- `CheckConstraint` cho trạng thái, số tiền ≥ 0, lý do không rỗng…
- Số tiền dùng `Numeric(14,2)` và `Decimal` trong Python (`parse_money`).
- Thao tác cạnh tranh (duyệt thanh toán, gửi email, đổi vai trò BĐH cuối cùng) dùng `UPDATE ... WHERE <điều kiện>` rồi kiểm tra `rowcount`, không đọc-rồi-ghi.

---

## 6. Phân quyền (RBAC)

### 6.1. Ba khối User → Role → Permission

```python
class Permission:                # app/models.py – bit flag
    APPROVE_MEMBERS = 1          # xem danh sách chờ & duyệt đăng ký
    MANAGE_MEMBERS  = 2          # xem đầy đủ / sửa / lưu trữ / import / điểm / mật khẩu tạm
    MANAGE_EVENTS   = 4          # sự kiện, task, điểm danh, đề xuất AI sự kiện/task
    MANAGE_FUNDS    = 8          # quỹ: kỳ, thu chi, khoản thu, xác nhận thanh toán
    ASSIGN_ROLES    = 16         # phân vai trò (theo cấp bậc)
    HANDOFF_ROLE    = 32         # nhường chức vụ của chính mình
    BOARD           = 64         # khu vực BĐH: báo cáo, nháp email, digest
    MANAGE_TEMPLATES = 128       # mẫu email trong Kho mẫu (kể cả email tự động)
```

`DEFAULT_ROLES` (trong `models.py`) là bảng quyền mặc định, đã thống nhất với CLB:

| Role | rank | Quyền |
|---|---|---|
| `ADMIN` Quản trị hệ thống | -1 | tất cả trừ HANDOFF_ROLE (223). Không phải thành viên CLB |
| `CN` Chủ nhiệm | 0 | tất cả (255) |
| `PCN` Phó chủ nhiệm | 1 | tất cả (255) – nhưng bị giới hạn cấp bậc khi phân vai trò |
| `TB` Trưởng ban | 2 | MANAGE_MEMBERS, MANAGE_EVENTS, HANDOFF_ROLE, BOARD |
| `THUKY_THUQUY` | 2 | MANAGE_EVENTS, MANAGE_FUNDS, HANDOFF_ROLE, BOARD |
| `THANH_VIEN` | 9 | 0 |

Bảng `role` trong DB là nguồn sự thật. `Role.insert_roles()` seed bảng này (migration 0002 cũng tự seed). Nếu bảng chưa có dòng nào, `role_permissions()` dùng `DEFAULT_ROLES`.

### 6.2. Hàm kiểm tra quyền (`app/services/access.py`)

- `approved(user)`: tài khoản `approved` và chưa rời CLB / chưa bị lưu trữ.
- `has_permission(user, perm)` = `approved(user) and user.can(perm)`. Mọi kiểm tra quyền khác đều đi qua hàm này.
- Hàm theo nghiệp vụ: `can_approve_members`, `can_manage_members`, `can_manage_events`, `can_manage_funds` (bí danh cũ `is_treasurer`), `is_admin`/`is_board` (quyền BOARD).
- Theo đối tượng: `can_manage_member(actor, target)` (không áp dụng cho chính mình), `can_view_member`, `can_assign_role_to` (quy tắc cấp bậc), `can_issue_temp_password` (phải cấp cao hơn người nhận).
- Phạm vi dữ liệu:
  - `visible_members(actor)`: tất cả nếu có MANAGE_MEMBERS, ngược lại chỉ chính mình.
  - `scoped_members(actor, perm)`: tất cả nếu có `perm` (vd: thủ quỹ xem tình trạng đóng quỹ của mọi người).
  - `board_members(actor)`: phạm vi tra cứu tên/MSSV cho AI và nháp email của BĐH.
  - `directory_members(actor)`: danh bạ thông tin cơ bản cho mọi thành viên đã duyệt (trang Thành viên).
  - `active_members()`: mọi thành viên còn hoạt động, dùng nội bộ. **Không gồm** tài khoản `ADMIN`.

**Quy tắc cấp bậc khi phân vai trò** (`can_assign_role_to`): cần ASSIGN_ROLES; chỉ đổi vai trò của người có `rank` lớn hơn mình (cấp thấp hơn); vai trò mới cũng phải có rank lớn hơn mình; `CN` chỉ `ADMIN` gán được; không ai gán/đổi vai trò `ADMIN`. Hệ quả: PCN không gán PCN/CN và không đụng tới CN/PCN khác.

**Tài khoản Quản trị hệ thống (`ADMIN`)** tạo bằng `flask create-admin` (chỉ hỏi tên hiển thị, tên đăng nhập, mật khẩu; chỉ tạo được một lần). Đặc điểm:
- `email = <username>@admin.invalid`; `mail.send_email` bỏ qua mọi địa chỉ `*.invalid`.
- Không có `member_record`, không thuộc ban. Bị loại khỏi `active_members()`, `funds.target_members()`, danh sách phân quyền, người nhận task và người nhận nhắc kỳ quỹ.
- Quên mật khẩu: `flask reset-admin-password` (chỉ chạy được trên máy chủ). Sau đó `permissions.can_assign_role` kiểm tra thêm giới hạn số lượng `Config.ROLE_LIMITS`.

**Bảo vệ BĐH cuối cùng:** `set_member_role_safely` và `set_member_left_safely` dùng câu UPDATE có điều kiện "còn hơn 1 BĐH hoạt động".

### 6.3. Decorator (`app/permissions.py`)

```python
@bp.route("/funds/transaction/new", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_FUNDS)
def transaction_new(): ...
```

| Decorator | Dùng khi |
|---|---|
| `approved_required` | Mọi trang nội bộ cho thành viên đã duyệt |
| `permission_required(perm)` | Cần đúng một quyền |
| `any_permission_required(*perms)` | Cần ít nhất một trong các quyền (vd: trang Vai trò = ASSIGN_ROLES hoặc HANDOFF_ROLE) |
| `bdh_required` | = `permission_required(Permission.BOARD)` |
| `role_required(*roles)` | Quy tắc gắn với chức danh cụ thể (ít dùng) |
| `fresh_login_required` (Flask-Login) | Thao tác nhạy cảm: đổi mật khẩu, gán vai trò, nhường chức vụ, cấp mật khẩu tạm |

Template dùng `can(Permission.X)` (từ context processor) để ẩn/hiện nút. **Ẩn nút chỉ để tiện cho người dùng, không thay cho kiểm tra ở server.**

### 6.4. Đổi chính sách quyền

1. Sửa `DEFAULT_ROLES` trong `app/models.py`.
2. Chạy `flask --app run.py sync-roles` để ghi đè bảng `role` trên DB đang chạy.
3. Sửa bảng ma trận trong `tests/test_two_role_security.py` (`MATRIX`) và chạy test.
4. Cập nhật bảng quyền ở `docs/HUONG_DAN_SU_DUNG.md` mục 7.

---

## 7. Xác thực và session

| Luồng | Route | Ghi chú cài đặt |
|---|---|---|
| Đăng ký | `GET/POST /register` | `RegistrationForm` → `User(status="pending")` → gửi link xác minh |
| Xác minh email | `GET /confirm/<token>`, `POST /confirm/resend` | Token purpose `confirm` (48h), gắn với email hiện tại |
| Đăng nhập | `GET/POST /login` | Hash giả khi MSSV không tồn tại (thời gian phản hồi đồng đều), khóa theo HMAC(IP+MSSV), `session.clear()` rồi `login_user(remember=...)`, `next` chỉ nhận đường dẫn nội bộ |
| Đăng xuất | `POST /logout` | Chỉ POST + CSRF |
| Quên mật khẩu | `GET/POST /password/forgot` | Luôn trả cùng một thông báo |
| Đặt lại | `GET/POST /password/reset/<token>` | Token purpose `reset` (1h) chứa 16 ký tự cuối của hash mật khẩu → dùng một lần |
| Đổi mật khẩu | `GET/POST /password/change` | `fresh_login_required`; đổi xong `session_marker` đổi → phiên ở thiết bị khác bị vô hiệu |
| Xác nhận lại | `GET/POST /reauth` | `confirm_login()` → phiên thành fresh |
| Mật khẩu tạm | `POST /admin/members/<id>/temp-password` | MANAGE_MEMBERS + cấp cao hơn; hiện mật khẩu một lần (no-store); bật `must_change_password` |

**Định danh phiên:** `User.get_id()` trả `"<id>:<session_marker>"`, trong đó `session_marker` = timestamp của `password_changed_at`. `load_user` từ chối nếu marker không khớp. Dạng cũ chỉ có `"<id>"` chỉ được chấp nhận khi `TESTING`.

**Cookie:** session `HttpOnly`, `SameSite=Lax`, `Secure` + tiền tố `__Host-` ở production, hết hạn sau 8 giờ. Cookie ghi nhớ `fia_remember` cũng `HttpOnly`/`SameSite=Lax`/`Secure` (production).

**Mật khẩu:** `werkzeug.security.generate_password_hash` (mặc định scrypt, có salt). Chính sách ở `validators.password_problems`.

**Token:** `app/tokens.py`. Mỗi mục đích một `salt`. Token chỉ được ký, không mã hóa, nên không đặt dữ liệu nhạy cảm vào payload.

---

## 8. Form, validate và CSRF

- Mọi form chính là class trong `app/forms.py`, kế thừa `BaseForm` (FlaskForm có thông báo lỗi tiếng Việt).
- Trong view, dùng mẫu chuẩn: `form = XForm()` → `if form.validate_on_submit(): ... return redirect(...)` (Post/Redirect/Get) → nếu không hợp lệ thì render lại template, form tự giữ dữ liệu đã nhập.
- Validator gồm loại có sẵn (`DataRequired`, `Length`, `Email`, `EqualTo`, `Regexp`, `NumberRange`, `FileAllowed`…), loại tùy biến dạng class (`StrongPassword`, `NoControlChars`) và inline `validate_<field>` (kiểm tra trùng MSSV/email/mã sự kiện).
- **Filter chuẩn hóa trước khi validate** (`RegistrationForm`): `_mssv_filter` (viết hoa), `_phone_filter` (→ `+84xxxxxxxxx`), `_facebook_filter` (→ `https://facebook.com/<id>`). Sai định dạng thì filter giữ nguyên giá trị để `Regexp` báo lỗi tiếng Việt.
- Đăng ký chọn **một** ban: `ban_hoat_dong = RadioField(coerce=int)`. Template `register.html` có macro `prefixed_field` (ô có tiền tố `+84` / `facebook.com/`) và thẻ chọn ban `.ban-choice`; hai hàm Jinja `phone_local_part`, `facebook_id_part` tách phần người dùng nhập để hiển thị lại.
- Template:
  ```jinja
  {% from "_ui.html" import form_field %}
  <form method="post" novalidate>
    {{ form.hidden_tag() }}                       {# csrf_token + hidden fields #}
    {{ form_field(form.email, type='email', placeholder='ban@example.com') }}
  </form>
  ```
- Form nhỏ chưa có class (nút duyệt, bật/tắt…) vẫn được bảo vệ vì `CSRFProtect` chạy cho toàn app. Chỉ cần thêm `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- AJAX/fetch: đọc `<meta name="csrf-token">` trong `base.html` và gửi header `X-CSRFToken` (hoặc `X-CSRF-Token`).
- `WTF_CSRF_TIME_LIMIT=None`: token sống theo session (session tự hết hạn sau 8 giờ).

---

## 9. Template

- `base.html` định nghĩa block `title` và `content`. Các phần lặp lại tách vào `templates/partials/`: `_sidebar.html`, `_topbar.html`, `_flash.html`, `_workspace_dialog.html`.
- `_ui.html` chứa các macro `icon(name)`, `badge(text, tone)`, `avatar(name)`, `stat(...)`, `form_field(field, help=...)`.
- Context processor `services/ui_context.workspace_context` cung cấp `workspace_title`, `workspace_today`, thông báo, `Permission`, `can(perm)`.
- Jinja tự escape mọi biến. **Không dùng `|safe` cho dữ liệu người dùng.** Nếu sau này cho nhập rich-text/Markdown, phải lọc bằng Bleach theo whitelist (Ch8).
- Script inline phải có `nonce="{{ csp_nonce }}"`, vì CSP chặn script inline không có nonce. Ưu tiên đặt JS trong `static/js/`.

---

## 10. Tầng service và quy ước xử lý lỗi

- Service nhận `actor` (người thực hiện) làm tham số đầu tiên và tự kiểm tra quyền bằng `require(condition, message)`. Điều kiện sai thì ném `DomainError(message, 403)`.
- Lỗi nghiệp vụ dùng `DomainError(message, status=400)` với thông báo tiếng Việt hiển thị được cho người dùng. Route HTML bắt lỗi → `flash(str(exc), "error")`. API → JSON.
- `audit(actor, action, obj, details)` thêm dòng `AuditLog`. Người gọi (route/service) chịu trách nhiệm `db.session.commit()`.
- Chuẩn hóa dữ liệu đầu vào dùng `parse_date`, `parse_datetime`, `parse_money` (`services/common.py`).
- File Excel xuất ra phải qua `safe_excel_row` để chống chèn công thức.

---

## 11. REST API

- Tiền tố `/api/v1`. Xác thực bằng **session cookie** của Flask-Login (frontend cùng domain). Không bật CORS, nên domain khác không gọi được API.
- Request ghi dữ liệu phải gửi header `X-CSRFToken` hoặc `X-CSRF-Token`.
- Lỗi trả JSON: `{"error": "..."}` với 400/401/403/404/409/429/500. Lỗi 500 kèm `request_id`.
- Danh sách: `?page=1&page_size=20` (tối đa 100) → `{items, page, page_size, total, pages}`.
- Rate limit: `API_RATE_LIMIT` theo IP. Header `X-RateLimit-*` có trong phản hồi.
- Nếu sau này cần client ngoài (app mobile), có thể bổ sung JWT (`flask-jwt-extended`) như Ch9. Hiện chưa cần vì client duy nhất là trình duyệt cùng domain.

| Method | Endpoint | Quyền | Mô tả |
|---|---|---|---|
| GET | `/members` | đã duyệt (thấy theo phạm vi) | Danh sách thành viên, lọc `q`, `department_id`, `status` |
| POST | `/members` | MANAGE_MEMBERS | Tạo thành viên (bắt đổi mật khẩu lần đầu) |
| POST | `/members/import` | MANAGE_MEMBERS | Import CSV/XLSX ≤ 500 dòng, nguyên tử. Cột: `code,name,birth_date,email,phone,department_ids,password` |
| GET | `/members/<id>` | chính mình / MANAGE_MEMBERS | Hồ sơ |
| PATCH | `/members/<id>` | MANAGE_MEMBERS (người khác) | Sửa `name`, `phone`, `status`, `note` |
| POST | `/members/<id>/archive` | MANAGE_MEMBERS | Lưu trữ (không cho BĐH cuối cùng) |
| GET | `/members/<id>/activity` | chính mình / MANAGE_MEMBERS | Thống kê hoạt động |
| POST | `/members/<id>/points` | MANAGE_MEMBERS | `{points, reason, event_id?}` |
| GET/POST | `/funds` | đã duyệt / MANAGE_FUNDS | Liệt kê / tạo khoản thu `{name,type,amount,start_date,due_date,applies_to_all,department_ids?,status}` |
| GET | `/funds/<id>` | MANAGE_FUNDS hoặc thuộc đối tượng | Chi tiết + số dư của người xem |
| PATCH | `/funds/<id>/status` | MANAGE_FUNDS | draft→open/cancelled, open→closed/cancelled |
| GET | `/funds/<id>/balances` | MANAGE_FUNDS | Số dư từng thành viên |
| POST | `/funds/<id>/payments` | chính mình / MANAGE_FUNDS | `{amount, method, transaction_code?, member_id?}` |
| GET | `/payments` | đã duyệt (của mình) / MANAGE_FUNDS | Danh sách thanh toán |
| POST | `/payments/<id>/review` | MANAGE_FUNDS | `{decision: confirmed|rejected, note}` |
| POST/GET | `/payments/<id>/evidence`, `/payments/evidence/<id>` | chủ thanh toán / MANAGE_FUNDS | Upload / tải ảnh minh chứng |
| PUT | `/funds/<id>/adjustments/<user_id>` | MANAGE_FUNDS | Miễn/giảm `{type: exempt|reduce, amount?, reason}` |
| GET/POST | `/events` | đã duyệt / MANAGE_EVENTS | Liệt kê / tạo sự kiện |
| GET/PATCH | `/events/<id>` | đã duyệt / MANAGE_EVENTS | Chi tiết / sửa |
| POST | `/events/<id>/copy`, `/events/<id>/cancel` | MANAGE_EVENTS | Nhân bản / hủy |
| GET/POST | `/events/<id>/registrations` | MANAGE_EVENTS hoặc phụ trách / đã duyệt | Danh sách / đăng ký |
| POST | `/registrations/<id>/cancel` | chủ đăng ký / MANAGE_EVENTS | Hủy đăng ký |
| GET | `/events/<id>/qr`, `/events/<id>/qr.png` | MANAGE_EVENTS | Token / ảnh QR (15 phút) |
| POST | `/attendance/checkin` | đã duyệt | `{token}` |
| PUT | `/registrations/<id>/attendance` | MANAGE_EVENTS | `{status, note}` |
| POST | `/events/<id>/feedback` | người đã tham gia | `{rating 1–5, comment}` |
| POST | `/assistant` | đã duyệt | `{question, conversation_id?, agent?}` |
| GET/POST/PATCH | `/assistant/conversations[...]` | chủ sở hữu | Quản lý cuộc trò chuyện |
| GET/POST | `/assistant/proposals`, `/assistant/proposals/<id>/review` | MANAGE_EVENTS | Đề xuất AI |
| GET/PUT | `/settings/activity` | MANAGE_MEMBERS | Bảng điểm & ngưỡng hoạt động |
| GET | `/dashboard` | đã duyệt (theo phạm vi) | Số liệu tổng quan |
| GET | `/reports/funds.xlsx` | MANAGE_FUNDS | Báo cáo khoản thu |
| GET | `/reports/members.xlsx` | MANAGE_MEMBERS | Danh sách thành viên |

Ví dụ gọi bằng `fetch` từ trang đã đăng nhập:

```js
const token = document.querySelector('meta[name="csrf-token"]').content;
await fetch('/api/v1/funds', {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
  body: JSON.stringify({name: 'Quỹ tháng 10', type: 'monthly', amount: '100000.00',
                        start_date: '2026-10-01', due_date: '2026-10-31', applies_to_all: true, status: 'open'})
});
```

---

## 12. Trợ lý AI

| Thành phần | File | Vai trò |
|---|---|---|
| Provider | `services/assistant.py` | `RulesProvider` (không dùng model, truy vấn cố định), `GeminiProvider`, `AnthropicProvider`. `get_provider()` chọn theo `AI_PROVIDER` + key. |
| Agent | `services/agent_orchestrator.py` | Vòng lặp: model lập kế hoạch JSON → backend kiểm tra tên tool và phạm vi → chạy **reader đã kiểm chứng** → tối đa `AI_AGENT_MAX_STEPS` bước. Tiến trình stream qua SSE `/ai/stream`. |
| Truy vấn kiểm chứng | `verified_queries.py`, `briefings.py` | Hồ sơ/task thành viên, digest ngày, báo cáo tuần/tháng. |
| Đề xuất | `proposals.py`, `natural_proposals.py`, `natural_datetime.py` | Tạo `AIProposal` (event/task). Duyệt mới ghi dữ liệu thật (MANAGE_EVENTS). |
| Email | `email_proposals.py`, `email_templates.py` | Tạo `EmailProposal` nháp. BĐH duyệt mới gửi. Nội dung: mẫu trong kho → mẫu chung (xem 12.1). |
| Kho mẫu | `email_templates.py`, `proposal_templates.py`, `routes/kho_mau.py` | Lưu/sửa mẫu, chọn mẫu cho AI. |
| Hội thoại | `chat_conversations.py` | Sở hữu theo user, phân trang keyset. |

**Các bất biến an toàn (không được phá khi sửa code):**

1. Model **không bao giờ** ghi DB hay gửi mail trực tiếp. Mọi thao tác ghi đi qua bản nháp hoặc đề xuất, cần người có quyền duyệt.
2. Số liệu trong câu trả lời lấy từ truy vấn DB theo phạm vi quyền, không lấy từ text model sinh ra.
3. Ngữ cảnh gửi model chỉ gồm câu hỏi của người dùng (tối đa 6 câu), không gồm câu trả lời AI cũ.
4. Trước khi trả kết quả, gọi lại `_reauthorize`. Quyền đổi giữa chừng thì hủy lượt đó.
5. Quota: `AI_MAX_CALLS_PER_MINUTE`, `AI_DAILY_TOKEN_LIMIT`, `AI_CHAT_MESSAGES_PER_MINUTE`. Usage được ghi vào `ai_usage_log` (không lưu prompt).

### 12.1. Kho mẫu

Trang `/kho-mau` (blueprint `kho_mau`), 3 tab: Mẫu email (MANAGE_TEMPLATES), Mẫu sự kiện và Mẫu chia task (MANAGE_EVENTS). `flask init-db` và `flask seed-templates` nạp mẫu mặc định còn thiếu (không ghi đè mẫu đã sửa).

**Mẫu email** (`services/email_templates.py`):
- `SYSTEM_EMAILS`: 12 mẫu hệ thống (`general`, `confirm_email`, `reset_password`, `member_approved`, `member_rejected`, `task_assigned`, `task_reassigned`, `task_submitted`, `task_reviewed`, `deadline_reminder`, `period_reminder`, `fund_reminder`), mỗi mẫu có danh sách biến riêng.
- Gửi email tự động: `send_email(to, *render_system_email("task_assigned", ten=..., task=..., su_kien=..., han=...))`. Hàm đọc mẫu trong kho; thiếu hoặc không hợp lệ thì dùng nội dung mặc định.
- Biến chỉ được thay bằng `str.replace` (`_fill`), không bao giờ chạy code trong mẫu. `_validate_text` chặn biến lạ khi lưu.
- Mẫu của CLB dùng `{{ten}}`, `{{mssv}}`, `{{email}}`, `{{noi_dung}}`; `find_email_template(text)` chấm điểm theo tên (3) và từ khóa (1 mỗi từ).

**Soạn nháp email** (`email_proposals._draft_text`): mẫu của CLB khớp → điền biến, không gọi model. Không khớp → mẫu `general`; có model thì model viết lại theo khung `general_template`, không có model thì điền thẳng `{{noi_dung}}`.

**Mẫu sự kiện / chia task** (`services/proposal_templates.py`, bảng `proposal_template`):
- `find_proposal_template(kind, brief, event=None, template_id=None)` trả `{source, label, content, text_match}` theo thứ tự: mẫu chọn tay → mẫu trong kho (tên 3 điểm, từ khóa 1, cùng loại sự kiện 2) → sự kiện tương tự (`_similar_event`: cùng loại hoặc từ ≥4 ký tự trùng tên; task lấy từ các task cũ không bị hủy) → mẫu `is_default` → `BUILTIN`.
- `proposals._event_payload`: model nhận `template` trong context; không có model thì nội dung = mô tả + nội dung mẫu, loại/địa điểm lấy từ mẫu. Không nhập `end_at` thì `end = start + duration_hours`.
- `proposals._task_payload` khi không có model: mô tả ≥2 dòng/`;` → dùng đúng danh sách; 1 dòng → dùng task của mẫu chỉ khi `source == "store"` và `text_match` (chọn tay hoặc khớp tên/từ khóa), ngược lại 1 task theo mô tả.
- Payload lưu `template` và `template_source`; `proposal_preview` in dòng "Khung: …".

---

## 13. Email, scheduler, file upload

- **`mail.send_email(to, subject, body) -> bool`**: không ném lỗi. Bắt buộc TLS/SSL, chặn header injection. `MAIL_ENABLED=false` thì không gửi (trả False), trừ chế độ outbox khi dev. Địa chỉ `*.invalid` (tài khoản Quản trị hệ thống) luôn bị bỏ qua.
- Nội dung mọi email tự động lấy từ **Kho mẫu** qua `render_system_email(key, **values)` (mục 12.1).
- **Scheduler** (`app/scheduler.py`): `check_deadlines` chạy 07:00, `check_period_reminder` chạy 07:05 (giờ `APP_TIMEZONE`).
  - Chạy 1 process: để mặc định (`SCHEDULER_IN_WEB=true`).
  - Gunicorn nhiều worker: đặt `SCHEDULER_IN_WEB=false` cho web và chạy riêng `flask --app run.py run-scheduler` (đã có service `scheduler` trong `docker-compose.yml` và `deploy/club-scheduler.service`). Nếu không, mỗi worker chạy job một lần và mail bị gửi trùng.
- **Upload:**
  - Avatar: `AvatarForm` (FileRequired + FileAllowed) → Pillow kiểm tra định dạng thật, giới hạn pixel, chống decompression bomb → lưu bản sạch `user_<id>.<ext>`.
  - Minh chứng thanh toán: lưu trong `instance/payment_evidence/` với tên ngẫu nhiên.
  - `MAX_CONTENT_LENGTH` = 3 MB. Ảnh trong `/static/uploads/` có kiểm tra quyền xem, vì vậy **không** cấu hình Nginx phục vụ thẳng thư mục này (xem `deploy/nginx.conf`).

---

## 14. Migration database

Schema do **Flask-Migrate (Alembic)** quản lý trong thư mục `migrations/`:

| Revision | Nội dung |
|---|---|
| `0001` | Schema gốc (đúng như database cũ tạo bằng `db.create_all`) |
| `0002` | Bảng `role` (+ seed quyền), cột xác thực trên `user`, email unique |
| `0003` | Role `ADMIN`; `email_template` thêm `system_key`/`keywords`/`description`; bảng `proposal_template`; chuẩn hóa SĐT (`+84…`) và link Facebook cũ |

**Quy trình thay đổi schema** (Ch5.2):

```bash
# 1. sửa model trong app/models.py
flask --app run.py db migrate -m "mô tả thay đổi"     # sinh file trong migrations/versions/
# 2. MỞ FILE VỪA SINH RA ĐỂ KIỂM TRA (Alembic có thể bỏ sót đổi tên cột, server_default…)
flask --app run.py db upgrade                          # áp dụng
flask --app run.py db downgrade                        # quay lại 1 bước nếu cần
flask --app run.py db check                            # phát hiện model và DB lệch nhau
```

- `render_as_batch=True` đã bật để SQLite đổi được cột/constraint.
- Ứng dụng **không** tự `create_all()` khi chạy thật. Chỉ khi `TESTING` hoặc `AUTO_CREATE_TABLES=true`.

**Nâng cấp database cũ** (tạo trước khi có `migrations/`). Dừng web, sao lưu `instance/clb.db`, rồi chạy:

```bash
flask --app run.py migrate-chats
flask --app run.py migrate-money
flask --app run.py migrate-email-recipients
flask --app run.py db stamp 0001      # đánh dấu DB đang ở schema gốc
flask --app run.py db upgrade         # chạy 0002 trở đi
```

**Nâng cấp từ bản có 0002 lên 0003** (sao lưu DB trước):

```bash
flask --app run.py init-db          # = db upgrade (chạy 0003) + nạp kho mẫu mặc định
flask --app run.py create-admin     # nếu chưa có tài khoản Quản trị hệ thống
```

MSSV cũ không đúng định dạng mới vẫn đăng nhập được (định dạng chỉ kiểm tra khi đăng ký/nhập mới).

Nếu migration 0002 báo email trùng, sửa email của một trong các tài khoản trùng (SQLite: `UPDATE user SET email=... WHERE id=...`) rồi chạy lại `db upgrade`.

---

## 15. Kiểm thử

```bash
python -m unittest discover -s tests -q                    # toàn bộ test (database tạm)
python -m coverage run --source=app -m unittest discover -s tests && python -m coverage report
python -m pyflakes app                                     # lint nhanh
python scripts/gen_function_reference.py                   # cập nhật FUNCTION_REFERENCE.md
```

| File test | Nội dung chính |
|---|---|
| `test_two_role_security.py` | Ma trận quyền theo chức vụ, quy tắc gán vai trò, nhường chức vụ, BĐH cuối cùng |
| `test_account_security.py` | Mật khẩu mạnh, xác minh email, quên/đặt lại, đổi mật khẩu vô hiệu phiên cũ, fresh login, remember me, mật khẩu tạm, Flask-Limiter, ProxyFix, scheduler, migration up/down |
| `test_core_flows.py` | Quỹ theo kỳ, sự kiện → task → tính hoạt động, trang/xuất thành viên, lệnh CLI |
| `test_auth_hardening.py` | CSRF mọi method, đăng nhập (khóa, xoay session, open redirect), đăng ký, avatar |
| `test_workflows.py` | Luồng nghiệp vụ rộng: quỹ khoản thu, sự kiện, AI, email |
| `test_agent_orchestrator.py`, `test_ai_reliability.py`, `test_chat_conversations.py`, `test_natural_datetime.py` | Trợ lý AI |
| `test_security_boundaries.py`, `test_ctftime.py`, `test_real_ui.py` | Biên bảo mật, CTFtime, giao diện |

**Viết test mới:**

```python
from tests.support import csrf_for      # token CSRF hợp lệ cho session của test client

app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp}/t.db",
                  "SECRET_KEY": "test-key-...", "SCHEDULER_ENABLED": False})
with client.session_transaction() as s:           # đăng nhập nhanh
    s["_user_id"] = str(user.id); s["_fresh"] = True
client.post("/funds/period/new", data={"csrf_token": csrf_for(client), ...})
```

Mock `send_email` bằng `patch("app.routes.<module>.send_email")`. Không gửi mail thật trong test.

---

## 16. Triển khai production

### Docker Compose (khuyến nghị)

```bash
cp .env.example .env     # APP_ENV=production, SECRET_KEY, TRUSTED_HOSTS=clb.example.com, MAIL_*...
docker compose up -d --build
docker compose exec web flask --app run.py create-admin
```

Compose gồm 3 service:
- `web`: Gunicorn, tự chạy `init-db` = `db upgrade` khi khởi động.
- `scheduler`: tiến trình job riêng.
- `redis`: lưu bộ đếm rate limit.

Đặt Nginx (`deploy/nginx.conf`) phía trước để có HTTPS (certbot).

### Không dùng Docker

```bash
pip install --require-hashes -r requirements-lock.txt
flask --app run.py db upgrade
gunicorn -c gunicorn.conf.py wsgi:app       # hoặc systemd: deploy/club-webapp.service
flask --app run.py run-scheduler            # hoặc deploy/club-scheduler.service
```

### Checklist trước khi mở ra Internet

- [ ] `APP_ENV=production`, `SECRET_KEY` ngẫu nhiên ≥ 32 ký tự, `.env` quyền 600, không commit.
- [ ] `TRUSTED_HOSTS` = tên miền thật; `TRUST_PROXY_HOPS=1` khi có Nginx.
- [ ] HTTPS (Nginx + certbot); HTTP chuyển hướng sang HTTPS.
- [ ] `RATELIMIT_STORAGE_URI=redis://...` khi nhiều worker; `SCHEDULER_IN_WEB=false` + scheduler riêng.
- [ ] `flask db upgrade` chạy thành công; đã tạo Chủ nhiệm đầu tiên.
- [ ] SMTP thật (`MAIL_ENABLED=true`, TLS) và đã thử gửi tới địa chỉ của chính BĐH.
- [ ] Sao lưu định kỳ `instance/` (DB, `session-secret.key` nếu dùng, ảnh minh chứng) và `app/static/uploads/`.
- [ ] Theo dõi `instance/security.log` và log Gunicorn.
- [ ] Không chạy `python run.py` hay `FLASK_DEBUG=1` trên server thật.
- [ ] Đổi mọi API key / mật khẩu ứng dụng email từng bị lộ.

---

## 17. Bảo mật: đối chiếu bài học

| Vị trí | Mối đe dọa | Biện pháp trong code |
|---|---|---|
| Đường truyền | MITM | HTTPS bắt buộc ở production, HSTS, cookie Secure, ProxyFix để nhận diện đúng HTTPS sau Nginx |
| Host header | Host injection | `TRUSTED_HOSTS` allowlist |
| Đăng nhập | Brute-force, dò tài khoản | Khóa theo (IP, MSSV), Flask-Limiter theo IP, lỗi chung chung, so khớp hash thời gian đều |
| Mật khẩu | Lộ DB | scrypt + salt (Werkzeug), chính sách mạnh, không lưu plaintext |
| Session | Hijacking, fixation | `session.clear()` khi đăng nhập, HttpOnly/SameSite/Secure, `session_protection=strong`, marker vô hiệu phiên khi đổi mật khẩu, fresh login cho thao tác nhạy cảm |
| Redirect | Open redirect | `_safe_next` chỉ nhận đường dẫn nội bộ |
| Phân quyền | Broken access control | RBAC bit flag, decorator + `require()` trong service, kiểm tra sở hữu, quy tắc cấp bậc, chống hạ BĐH cuối cùng |
| Form | CSRF | Flask-WTF CSRFProtect toàn app, SameSite=Lax |
| Dữ liệu vào | Dữ liệu xấu | WTForms validators + kiểm tra lại trong service/API |
| Database | SQL injection | 100% ORM / tham số hóa |
| Hiển thị | XSS | Jinja autoescape, không `|safe` với dữ liệu người dùng, CSP có nonce |
| Upload | File độc hại | FileAllowed + Pillow kiểm tra nội dung, lưu bản sạch, giới hạn dung lượng, kiểm tra quyền khi phục vụ |
| Xuất Excel | Formula injection | `safe_excel_row` |
| Token email | Giả mạo / dùng lại | itsdangerous ký + hết hạn + salt theo mục đích; token reset dùng một lần |
| API | Lạm dụng | Rate limit, phân trang tối đa 100, CORS đóng, CSRF header |
| Lỗi | Lộ thông tin | Trang lỗi chung + request_id; stack trace chỉ vào log server |
| Cấu hình | Lộ secret | `.env`/biến môi trường, chặn khóa yếu ở production |
| AI | Prompt injection, hành động ngoài ý muốn | Model không ghi trực tiếp, chỉ đọc qua reader kiểm chứng, mọi hành động cần duyệt |

---

## 18. Công thức thường gặp (recipes)

### Thêm một trang mới có phân quyền

1. Viết view trong blueprint phù hợp (`app/routes/...`):
   ```python
   @funds_bp.route("/funds/summary")
   @login_required
   @permission_required(Permission.MANAGE_FUNDS)
   def funds_summary():
       """Mô tả ngắn – sẽ hiện trong FUNCTION_REFERENCE.md."""
       return render_template("funds_summary.html", ...)
   ```
2. Tạo template `{% extends "base.html" %}` với block `title` và `content`.
3. Thêm tiêu đề trang vào `PAGE_LABELS` (`services/ui_context.py`) và link (nếu cần) vào `base.html` / `partials/_sidebar.html`, bọc trong `{% if can(Permission.MANAGE_FUNDS) %}`.
4. Viết test cho người có quyền (200) và không có quyền (403).

### Thêm form

Tạo class trong `forms.py`, render bằng `form.hidden_tag()` + `form_field(...)`, xử lý theo mẫu `validate_on_submit()` + redirect.

### Thêm cột hoặc bảng

Sửa model → `flask db migrate -m "..."` → đọc lại file migration → `flask db upgrade` → test.

### Thêm quyền mới

1. Thêm bit mới vào `Permission` (số tiếp theo là 256), kèm nhãn trong `Permission.LABELS`.
2. Gán bit vào `DEFAULT_ROLES`.
3. Viết migration cập nhật cột `role.permissions` (hoặc chạy `flask sync-roles` trên server).
4. Dùng `permission_required(Permission.NEW)` và `has_permission(...)` ở chỗ cần kiểm tra.
5. Cập nhật `MATRIX` trong test và tài liệu người dùng.

### Thêm endpoint API

Thêm vào `routes/api.py`, đọc body bằng `body()`, kiểm tra quyền bằng `require(...)`, ném `DomainError` khi dữ liệu sai, trả `jsonify(...)` với status đúng (201 khi tạo mới). Danh sách thì dùng `listing(paginate(query), serializer)`.

### Thêm job định kỳ

Viết hàm nhận `app` trong `scheduler.py` (dùng `with app.app_context()`), đăng ký bằng `_scheduler.add_job(...)` trong `init_scheduler`, rồi viết test gọi hàm trực tiếp.

---

## 19. Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Mọi request trả 400 "Hệ thống yêu cầu kết nối HTTPS" sau Nginx | Thiếu `TRUST_PROXY_HOPS=1` hoặc Nginx không gửi `X-Forwarded-Proto` | Đặt biến, kiểm tra `proxy_set_header` |
| 400 "Tên máy chủ không hợp lệ" | Tên miền chưa có trong `TRUSTED_HOSTS` | Thêm tên miền |
| `RuntimeError: Production requires an unpredictable SECRET_KEY` | Khóa thiếu hoặc yếu | Tạo khóa ≥ 32 ký tự |
| 400 khi submit form | CSRF hết hạn/thiếu (mở trang lâu, đổi session) | Tải lại trang; với AJAX kiểm tra header `X-CSRFToken` |
| 429 | Vượt rate limit | Chỉnh `API_RATE_LIMIT`/`AUTH_RATE_LIMIT`; nhiều worker thì dùng Redis |
| Email gửi trùng mỗi sáng | Scheduler chạy trong mỗi worker | `SCHEDULER_IN_WEB=false` + chạy scheduler riêng |
| `flask db upgrade` báo email trùng | Dữ liệu cũ có email lặp | Sửa email rồi chạy lại |
| `no such table` khi chạy | Chưa chạy migration | `flask --app run.py init-db` |
| Người dùng bị đăng xuất khi đổi mạng | `SESSION_PROTECTION=strong` với phiên không permanent | Hành vi bảo mật dự kiến; có thể đổi sang `basic` |
| Lỗi 500 | Xem `instance/security.log` theo "Mã hỗ trợ" (request_id) | Log có stack trace đầy đủ |
