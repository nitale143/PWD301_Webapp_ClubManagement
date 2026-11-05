# Tham chiếu hàm (Function Reference)

> File này được sinh tự động bởi `python scripts/gen_function_reference.py` từ docstring trong code.
> Muốn sửa mô tả: sửa docstring của hàm rồi chạy lại script. Đọc kèm `docs/DEVELOPER_GUIDE.md`.

- [Khởi tạo & cấu hình](#khởi-tạo--cấu-hình)
- [Bảo mật & xác thực](#bảo-mật--xác-thực)
- [Dữ liệu](#dữ-liệu)
- [Routes (giao diện web & REST API)](#routes-giao-diện-web--rest-api)
- [Services (nghiệp vụ & trợ lý AI)](#services-nghiệp-vụ--trợ-lý-ai)
- [Khác](#khác)

## Khởi tạo & cấu hình

### `app/__init__.py`

App factory của webapp CLB (Ch2 – Blueprint, Ch5 – ORM, Ch6 – Flask-Login).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `create_app(config_override=None)` | 28 | App factory: tạo Flask app, nạp cấu hình (+ `config_override` cho test), khởi tạo extension (SQLAlchemy, Migrate, CSRF, Login, Limiter), đăng ký blueprint, hook bảo mật, lệnh CLI và scheduler. |

### `app/config.py`

Cấu hình ứng dụng, đọc từ biến môi trường / file .env (Ch11 – slide 18).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_env_bool(name, default)` | 12 | Đọc biến môi trường kiểu true/false; trả `default` nếu biến không đặt hoặc rỗng. |
| class `Config` | 20 | Toàn bộ cấu hình ứng dụng, đọc từ biến môi trường (.env). Xem .env.example để biết ý nghĩa từng biến. |

### `app/cli.py`

Lệnh quản trị chạy bằng `flask --app run.py <lệnh>` (Ch11 – vận hành).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `register_cli(app)` | 25 | Đăng ký các lệnh `flask --app run.py ...` (init-db, sync-roles, create-admin, reset-admin-password, seed-templates, recompute-activity, sync-event-status, run-scheduler, migrate-*) vào app. |

### `app/limiter.py`

Giới hạn tần suất request bằng Flask-Limiter (Ch9 – slide 34).

_(không có hàm)_

### `app/timeutil.py`

Tiện ích thời gian dùng chung.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `utcnow()` | 10 | Thời điểm hiện tại theo UTC, dạng naive datetime (giống utcnow() cũ). |

## Bảo mật & xác thực

### `app/hardening.py`

Ranh giới bảo mật và xử lý lỗi an toàn cho mọi route (Ch10 – Middleware & Error handling).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `configure_security(app)` | 25 | Kiểm tra cấu hình lúc khởi động: APP_ENV hợp lệ, SECRET_KEY đủ mạnh ở production (dev tự tạo khóa trong instance/), cấm debug ở production, bật cookie Secure, gắn ProxyFix theo TRUST_PROXY_HOPS và mở file log instance/security.log. |
| `install_hardening(app)` | 82 | Gắn hook before_request (host allowlist, bắt buộc HTTPS ở production, kiểm tra quyền xem ảnh upload), after_request (CSP có nonce, header bảo mật, Cache-Control, log thao tác) và error handler trả lỗi an toàn dạng HTML/JSON. |

### `app/security.py`

Bảo vệ CSRF (Flask-WTF) và các chốt chặn tài khoản dùng chung.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `csrf_token()` | 20 | Token CSRF cho session hiện tại (giữ tên cũ cho code/test đang gọi). |
| `require_csrf()` | 25 | Kiểm tra CSRF tường minh trong view. CSRFProtect đã kiểm tra trước mọi view; hàm này giữ lại để các view nhạy cảm thể hiện rõ yêu cầu và vẫn an toàn nếu sau này view được `csrf.exempt`. |
| `install_account_guards(app)` | 35 | Hook trước mỗi request: - CSRFProtect bỏ qua URL không khớp route nào; hook này vẫn đòi token cho mọi request ghi dữ liệu để hành vi đồng nhất (trả 400 thay vì 404). - Tài khoản bị đánh dấu `must_change_password` (mật khẩu tạm / tài khoản import) chỉ được vào trang đổi mật khẩu. |

### `app/permissions.py`

Decorator phân quyền dùng chung cho mọi blueprint (Ch7 – slide 58-87).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `approved_required(view_func)` | 23 | Chỉ cho phép thành viên đã được BĐH duyệt (và chưa rời CLB) truy cập. |
| `permission_required(permission)` | 37 | Decorator 3 lớp: chỉ người có bit quyền `permission` mới vào được view. Ví dụ: ``@permission_required(Permission.MANAGE_FUNDS)`` |
| `any_permission_required(*permissions)` | 55 | Vào được nếu có ÍT NHẤT MỘT trong các quyền đã liệt kê. |
| `bdh_required(view_func)` | 71 | Chỉ thành viên Ban điều hành (quyền BOARD) đã được duyệt. |
| `admin_required(view_func)` | 76 | Người có quyền phân vai trò (CN/PCN theo bảng mặc định). |
| `role_required(*roles)` | 81 | Chỉ đúng các chức vụ được liệt kê. Ví dụ: ``@role_required('CN', 'PCN')``. Ưu tiên dùng `permission_required`; decorator này dành cho quy tắc gắn với chức danh cụ thể. |
| `can_assign_role(assigner, target, new_role, current_counts)` | 101 | Kiểm tra `assigner` có được chỉ định `new_role` cho `target` không. Trả về (True, "") hoặc (False, "lý do"). |

### `app/tokens.py`

Token có chữ ký và thời hạn (Ch6 – slide 91-92, itsdangerous).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_serializer(purpose)` | 13 | Bộ ký URLSafeTimedSerializer theo mục đích (mỗi mục đích một salt). |
| `dumps(payload, purpose)` | 20 | Ký payload thành token theo mục đích. |
| `loads(token, purpose, max_age)` | 25 | Giải mã token; trả về None nếu sai chữ ký, sai mục đích hoặc hết hạn. |

### `app/validators.py`

Quy tắc kiểm tra dữ liệu dùng chung cho form (WTForms) và REST API.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `contains_control(value)` | 34 | True nếu chuỗi có ký tự điều khiển (xuống dòng, tab, ký tự ẩn…). |
| `normalize_mssv(value)` | 39 | Bỏ khoảng trắng và viết hoa MSSV (se201256 → SE201256). |
| `normalize_phone(value)` | 44 | Chuẩn hóa SĐT về dạng +84xxxxxxxxx; trả None nếu không hợp lệ. Nhận 9 số sau +84 (dạng chuẩn trên form). Để thân thiện, cũng nhận khi người dùng dán cả "+84…", "84…" hoặc "0…" (10 số); khoảng trắng, dấu chấm, gạch ngang được bỏ qua. |
| `phone_local_part(value)` | 61 | Phần 9 số sau +84 để hiển thị lại trong ô nhập. |
| `normalize_facebook(value)` | 67 | Chuẩn hóa link Facebook về https://facebook.com/<userID>; trả None nếu sai dạng. Chỉ nhận userID (chữ, số, dấu chấm) hoặc đúng link https://facebook.com/<userID>. Không nhận http://, www., m., fb.com, tham số ?id=… hay đường dẫn con. |
| `facebook_id_part(value)` | 78 | Phần userID sau https://facebook.com/ để hiển thị lại trong ô nhập. |
| `password_problems(password, mssv=None)` | 84 | Trả về danh sách lý do mật khẩu chưa đủ mạnh (rỗng = hợp lệ). Chính sách (Ch6 – slide 79-80): 8–64 ký tự, có ít nhất một chữ cái và một chữ số, không chứa MSSV, không nằm trong danh sách mật khẩu phổ biến. |
| class `StrongPassword` | 104 | Validator WTForms tùy biến dùng `password_problems` (Ch6 – slide 80, Ch4 – slide 35). `mssv_field`: tên field MSSV trong cùng form (nếu có) để chặn mật khẩu chứa MSSV. |
| `StrongPassword.__init__(self, mssv_field=None)` | 110 | `mssv_field`: tên field MSSV trong form để chặn mật khẩu chứa MSSV. |
| `StrongPassword.__call__(self, form, field)` | 114 | Chạy password_problems; có vấn đề thì báo ValidationError. |
| class `NoControlChars` | 126 | Chặn ký tự điều khiển trong các ô văn bản một dòng. |
| `NoControlChars.__init__(self, message='Dữ liệu chứa ký tự không hợp lệ.')` | 129 | Thông báo lỗi tùy chỉnh. |
| `NoControlChars.__call__(self, form, field)` | 133 | Báo lỗi nếu chuỗi có ký tự điều khiển. |

### `app/forms.py`

Các form của webapp viết bằng Flask-WTF / WTForms (Ch4 – Web forms).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| class `_VietnameseTranslations` | 68 | Bộ dịch tối giản WTForms: đổi thông báo lỗi tiếng Anh sang tiếng Việt theo bảng _VI_MESSAGES. |
| `_VietnameseTranslations.gettext(self, string)` | 72 | Dịch một thông báo; không có trong bảng thì giữ nguyên. |
| `_VietnameseTranslations.ngettext(self, singular, plural, n)` | 76 | Dịch thông báo có dạng số ít/số nhiều. |
| class `BaseForm` | 81 | FlaskForm có thông báo lỗi tiếng Việt. |
| class `BaseForm.Meta` | 84 | Meta của WTForms: gắn bộ dịch tiếng Việt cho mọi form kế thừa BaseForm. |
| `BaseForm.Meta.get_translations(self, form)` | 86 | Trả về bộ dịch tiếng Việt cho form. |
| `_strip(value)` | 91 | Filter WTForms: bỏ khoảng trắng đầu/cuối của chuỗi. |
| `_lower_strip(value)` | 96 | Filter WTForms: bỏ khoảng trắng và đổi về chữ thường (dùng cho email). |
| `_phone_filter(value)` | 101 | Filter: chuẩn hóa SĐT về +84xxxxxxxxx; sai định dạng thì giữ nguyên để Regexp báo lỗi. |
| `_facebook_filter(value)` | 106 | Filter: chuẩn hóa userID / link về https://facebook.com/<userID>; sai thì giữ nguyên. |
| `_mssv_filter(value)` | 111 | Filter: bỏ khoảng trắng và viết hoa MSSV. |
| class `LoginForm` | 119 | Form đăng nhập: MSSV, mật khẩu, ghi nhớ đăng nhập, `next` (trang quay lại sau khi đăng nhập). |
| class `RegistrationForm` | 130 | Đơn đăng ký thành viên. Danh sách ban (`ban_hoat_dong.choices`) được gán trong view; mỗi người chỉ chọn MỘT ban. SĐT lưu dạng +84xxxxxxxxx, Facebook dạng https://facebook.com/userID. |
| `RegistrationForm.validate_ngay_sinh(self, field)` | 156 | Ngày sinh phải từ năm 1980 trở đi và trước hôm nay. |
| `RegistrationForm.validate_mssv(self, field)` | 163 | Chặn MSSV đã có tài khoản (kiểm tra trùng trong database). |
| `RegistrationForm.validate_email(self, field)` | 169 | Chặn email đã được dùng cho tài khoản khác. |
| class `ChangePasswordForm` | 176 | Form đổi mật khẩu: mật khẩu hiện tại, mật khẩu mới (StrongPassword) và nhập lại. |
| class `ForgotPasswordForm` | 185 | Form quên mật khẩu: nhập email đã đăng ký để nhận link đặt lại. |
| class `ResetPasswordForm` | 192 | Form đặt mật khẩu mới từ link trong email. |
| class `ReauthForm` | 200 | Form nhập lại mật khẩu để làm mới phiên (fresh login) trước thao tác nhạy cảm. |
| class `ResendConfirmationForm` | 207 | Form chỉ có nút bấm (kèm CSRF) để gửi lại email xác minh. |
| class `EventForm` | 215 | Form tạo sự kiện: tên, mã, thời gian bắt đầu/kết thúc, địa điểm, nội dung, hạn đăng ký (nằm trong khoảng bắt đầu – kết thúc). Sự kiện không giới hạn số người. |
| `EventForm.validate_thoi_gian_ket_thuc(self, field)` | 234 | Thời gian kết thúc phải sau thời gian bắt đầu. |
| `EventForm.validate_han_dang_ky(self, field)` | 239 | Hạn đăng ký (nếu có) phải nằm trong khoảng [bắt đầu, kết thúc] vì thời gian sự kiện đã gồm cả setup và báo cáo sau sự kiện. |
| `EventForm.validate_ma_su_kien(self, field)` | 247 | Mã sự kiện không được trùng sự kiện đã có. |
| class `FundPeriodForm` | 257 | Form mở kỳ quỹ mới: tên kỳ, ngày bắt đầu, ngày kết thúc (tùy chọn). |
| `FundPeriodForm.validate_ngay_ket_thuc(self, field)` | 265 | Ngày kết thúc (nếu có) không được trước ngày bắt đầu. |
| class `FundTransactionForm` | 271 | Ghi một giao dịch thu (+) hoặc chi (số tiền bắt đầu bằng dấu -). |
| `FundTransactionForm.validate_so_tien(self, field)` | 282 | Kiểm tra số tiền (cho phép dấu - với khoản chi) và lưu giá trị Decimal vào `self.amount`. |
| class `AvatarForm` | 298 | Upload ảnh đại diện (Ch4 – slide 52-53). |

### `app/mail.py`

Gửi email qua SMTP có mã hóa (TLS/SSL), không làm lộ lỗi hoặc nội dung thư.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_safe_header(value, max_length)` | 26 | Chuỗi header email hợp lệ: không rỗng, không quá dài, không chứa ký tự xuống dòng (chống header injection). |
| `_valid_email(value)` | 37 | Địa chỉ email hợp lệ theo regex và độ dài cho phép. |
| `send_email(to_email: str, subject: str, body: str)` | 42 | Send one plain-text message, returning false if it cannot be sent safely. |
| `_write_outbox(to_email, subject, body)` | 118 | Chỉ dùng khi phát triển: lưu thư vào instance/mail_outbox/<thời-gian>.eml (quyền 0600). |

## Dữ liệu

### `app/models.py`

Các model SQLAlchemy của webapp CLB (Ch5 – Database & ORM, Ch7 – RBAC).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| class `Permission` | 18 | Các quyền dạng bit flag. Một Role lưu tổng (OR) các bit nó có. Kiểm tra quyền: ``role.permissions & Permission.X == Permission.X``. |
| class `Role` | 74 | Một chức vụ trong CLB và tập quyền của nó (Ch7 – slide 43-48). |
| `Role.has_permission(self, perm)` | 85 | True nếu role có bit quyền `perm`. |
| `Role.add_permission(self, perm)` | 89 | Thêm bit quyền vào role (không đổi nếu đã có). |
| `Role.remove_permission(self, perm)` | 94 | Bỏ bit quyền khỏi role (không đổi nếu chưa có). |
| `Role.reset_permissions(self)` | 99 | Xóa mọi quyền của role. |
| `Role.insert_roles(overwrite=False)` | 104 | Tạo các Role mặc định nếu chưa có. overwrite=True ghi đè quyền theo DEFAULT_ROLES. |
| `Role.__repr__(self)` | 118 | Chuỗi mô tả role khi debug. |
| `role_permissions(role_name)` | 123 | Quyền của một chức vụ: đọc bảng role; nếu chưa seed thì dùng DEFAULT_ROLES. |
| `role_rank(role_name)` | 131 | Cấp bậc của một chức vụ (số nhỏ = cấp cao): đọc bảng role, chưa seed thì dùng DEFAULT_ROLES. |
| class `UserBan` | 142 | Bảng trung gian nhiều-nhiều User ↔ Ban; `is_truong_ban` đánh dấu Trưởng ban của ban đó. |
| class `Ban` | 155 | Ban hoạt động: Kỹ thuật, Nhân sự, Truyền thông. |
| class `User` | 164 | Tài khoản thành viên CLB: thông tin cá nhân, trạng thái duyệt, chức vụ (khớp Role.name), xác thực (hash mật khẩu, xác minh email, bắt đổi mật khẩu). |
| `User.password(self)` | 209 | Không cho đọc mật khẩu (chỉ lưu hash). |
| `User.password(self, raw_password)` | 214 | Gán mật khẩu mới: tự băm và lưu vào password_hash. |
| `User.set_password(self, raw_password)` | 218 | Băm mật khẩu bằng Werkzeug (scrypt) và ghi lại thời điểm đổi mật khẩu. |
| `User.check_password(self, raw_password)` | 223 | So sánh mật khẩu nhập vào với hash đã lưu. |
| `User.session_marker(self)` | 229 | Đổi mỗi khi đổi mật khẩu → mọi phiên/cookie ghi nhớ cũ tự mất hiệu lực. |
| `User.get_id(self)` | 234 | Định danh lưu trong session/cookie của Flask-Login: "<id>:<session_marker>". |
| `User.generate_token(self, purpose)` | 239 | Token ký bằng SECRET_KEY. purpose: 'confirm' hoặc 'reset'. Token reset gắn với hash mật khẩu hiện tại nên chỉ dùng được một lần: đổi mật khẩu xong thì token cũ tự mất hiệu lực. |
| `User.from_token(token, purpose, max_age)` | 254 | Trả về User nếu token hợp lệ, còn hạn và đúng mục đích; ngược lại None. |
| `User.confirm_email(self)` | 269 | Đánh dấu email đã xác minh và ghi thời điểm xác minh. |
| `User.permissions(self)` | 277 | Tổng bit quyền của chức vụ hiện tại. |
| `User.can(self, perm)` | 281 | True nếu chức vụ hiện tại có quyền `perm` (chưa xét trạng thái duyệt). |
| `User.is_administrator(self)` | 285 | True nếu chức vụ có quyền phân vai trò (CN/PCN theo bảng mặc định). |
| `User.rank(self)` | 290 | Cấp bậc của chức vụ hiện tại (số nhỏ = cấp cao). |
| `User.role_label(self)` | 295 | Tên hiển thị tiếng Việt của chức vụ. |
| `User.outranks(self, other_user)` | 299 | True khi người BĐH (quyền BOARD) nhường chức vụ cho một thành viên thường. |
| `User.is_bdh(self)` | 303 | Thuộc Ban điều hành (có quyền BOARD). |
| `User.is_system_admin(self)` | 308 | Tài khoản quản trị hệ thống (không phải thành viên CLB). |
| `User.quan_ly_ban_ids(self)` | 312 | Các ban_id mà Trưởng ban này quản lý (rỗng nếu không phải TB). |
| `User.__repr__(self)` | 318 | Chuỗi mô tả user khi debug. |
| class `AnonymousUser` | 323 | Người chưa đăng nhập: không có quyền nào (để template gọi can() an toàn). |
| `AnonymousUser.can(self, perm)` | 329 | Người chưa đăng nhập không có quyền nào. |
| `AnonymousUser.is_bdh(self)` | 333 | Người chưa đăng nhập không thuộc BĐH. |
| `AnonymousUser.is_administrator(self)` | 337 | Người chưa đăng nhập không phải quản trị. |
| `load_user(user_id)` | 348 | Nạp user từ session/cookie. Định dạng id: "<id>:<session_marker>". Nếu marker không khớp (mật khẩu đã đổi sau khi phiên được tạo) → coi như chưa đăng nhập. Dạng cũ chỉ có "<id>" chỉ được chấp nhận khi TESTING. |
| class `Event` | 370 | Sự kiện của CLB; `tasks` là các task con. Trạng thái: sap_dien_ra &#124; dang_dien_ra &#124; da_ket_thuc &#124; cancelled – chỉ được đổi qua app/services/event_status.py (BĐH chuyển tay hoặc hệ thống tự chuyển theo giờ). |
| class `Task` | 396 | Đầu việc thuộc một sự kiện, giao cho một người phụ trách (assignee) thuộc một ban. Trạng thái: dang_lam → cho_duyet → lam_lai/hoan_thanh, hoặc huy. |
| class `FundPeriod` | 427 | Kỳ quỹ / kỳ hoạt động (Spring, Summer, Fall…). Chỉ một kỳ `is_current`; chứa sổ thu chi và trạng thái đóng quỹ theo kỳ. |
| class `FundTransaction` | 446 | Một dòng trong sổ thu chi của kỳ: thu (số dương) hoặc chi (số âm). |
| class `FundDue` | 458 | Trạng thái đóng quỹ của từng thành viên trong 1 kỳ (sổ quỹ theo kỳ). |
| class `MemberActivity` | 474 | Tình trạng hoạt động của một thành viên trong một kỳ (tính bởi app/services/activities.py). Điểm = 50% tỉ lệ task hoàn thành + 50% tỉ lệ có mặt, chỉ trên sự kiện đã kết thúc trong kỳ. |
| class `ChatMessage` | 504 | Một tin nhắn trong lịch sử chat AI (role 'user' hoặc 'ai'), thuộc một cuộc trò chuyện. |
| class `ChatConversation` | 519 | A bounded, user-owned tab in the AI chat history. |
| class `Notification` | 540 | Thông báo hiện ở chuông trên thanh trên cùng (nhắc deadline, nhắc kỳ mới…). |
| class `MemberRecord` | 551 | Hồ sơ thành viên mở rộng: ngày gia nhập, tình trạng (active/paused/left), ghi chú, thời điểm lưu trữ. |
| class `FundCollection` | 567 | Khoản thu (thu theo tháng/kỳ/năm/sự kiện/tự nguyện…), có số tiền, hạn đóng và trạng thái draft/open/closed/cancelled. |
| class `FundTargetBan` | 592 | Ban áp dụng của một khoản thu (khi khoản thu không áp dụng cho tất cả thành viên). |
| class `FundAdjustment` | 599 | Miễn (exempt) hoặc giảm (reduce) khoản thu cho một thành viên, bắt buộc có lý do. |
| class `FundPayment` | 618 | Một lần đóng tiền cho khoản thu; chờ người quản lý quỹ xác nhận (pending → confirmed/rejected). |
| class `PaymentEvidence` | 642 | Ảnh minh chứng thanh toán, lưu trong instance/payment_evidence với tên ngẫu nhiên. |
| class `EventDetail` | 651 | Thông tin chi tiết của sự kiện: loại, nội dung, địa điểm, hạn đăng ký, người phụ trách (cột so_nguoi_toi_da giữ lại cho dữ liệu cũ, không còn dùng), khoản thu liên quan. |
| class `EventTargetBan` | 672 | Ban được phép đăng ký sự kiện (khi sự kiện không mở cho tất cả). |
| class `EventRegistration` | 679 | Đăng ký tham gia sự kiện: registered &#124; waitlist &#124; cancelled. |
| class `Attendance` | 697 | Điểm danh của một lượt đăng ký: on_time &#124; late &#124; excused &#124; absent &#124; pending. |
| class `QRCheckinLog` | 712 | Nhật ký mỗi lần quét QR điểm danh (phục vụ đối soát). |
| class `EventFeedback` | 722 | Đánh giá 1–5 sao và bình luận của thành viên sau sự kiện. |
| class `AuditLog` | 737 | Nhật ký thao tác: ai làm gì, trên đối tượng nào, lúc nào (không lưu mật khẩu/nội dung nhạy cảm). |
| class `AIProposal` | 751 | An AI suggestion is inert until a board member explicitly approves it. |
| class `EmailProposal` | 773 | Email draft: chat creation never sends; a separate BDH action does. |
| class `EmailTemplate` | 796 | Mẫu email trong kho mẫu. - `system_key` có giá trị: mẫu của email tự động (xác minh email, giao task…) và mẫu chung `general`; chỉ sửa nội dung, không xóa/đổi tên. - `system_key` rỗng: mẫu do BĐH tạo, AI tìm theo tên/từ khóa khi soạn nháp. |
| class `ProposalTemplate` | 819 | Mẫu sự kiện / mẫu chia task trong kho mẫu, AI dùng khi lập đề xuất. `content` (JSON): - kind="event": {"name", "content", "type", "location", "duration_hours"} - kind="task": {"tasks": [{"name", "description", "days_before"}]} `is_default=True`: mẫu chung, dùng khi không tìm thấy mẫu phù hợp hơn. |
| class `AssistantPending` | 846 | The incomplete natural-language proposal currently being clarified. |
| class `ConversationPending` | 857 | A pending clarification scoped to one conversation tab. |
| class `AIReportSnapshot` | 869 | Immutable reviewed numbers and source rows for export/email. |
| class `MemberPlanningProfile` | 880 | Member-provided task preferences; absent rows use conservative defaults. |
| class `AIUsageLog` | 896 | Successful model calls, without prompts, replies, secrets or price guesses. |

### `app/legacy_sqlite.py`

Nâng cấp database SQLite cũ (tạo trước khi dự án dùng Flask-Migrate), có sao lưu trước.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_sqlite_table_exists(connection, table_name)` | 27 | True nếu bảng tồn tại trong file SQLite. |
| `_sqlite_columns(connection, table_name)` | 35 | Danh sách cột (PRAGMA table_info) của một bảng SQLite. |
| `_sqlite_index_columns(connection, table_name)` | 42 | Ánh xạ tên index → các cột của index trên một bảng SQLite. |
| `_sqlite_foreign_keys(connection, table_name)` | 54 | Danh sách khóa ngoại của một bảng SQLite. |
| `_validate_chat_schema(connection)` | 62 | Kiểm tra bảng chat_conversation/chat_message đã đúng cấu trúc mới chưa; sai thì báo lỗi. |
| `_ensure_chat_index(connection, table_name, index)` | 96 | Tạo index cho bảng chat nếu chưa có. |
| `_create_chat_tables(connection)` | 109 | Tạo bảng chat_conversation và cột conversation_id cho database cũ. |
| `_make_sqlite_backup(connection, database_path)` | 137 | Sao lưu file SQLite (cùng thư mục, tên có thời gian) trước khi sửa cấu trúc; trả về đường dẫn bản sao. |
| `_chat_migration_needed(connection)` | 156 | True nếu database cũ chưa có cấu trúc hội thoại AI. |
| `_timestamp_bounds(connection, user_id, include_pending=True)` | 201 | Thời điểm sớm/muộn nhất của tin nhắn (và nháp đang chờ) của một user, dùng khi gom lịch sử cũ. |
| `_backfill_legacy_chat(connection)` | 228 | Gom tin nhắn cũ chưa thuộc cuộc trò chuyện nào vào một cuộc trò chuyện "legacy" cho từng user. |
| `migrate_chat_conversations(database_path)` | 294 | Add conversation tabs to an existing SQLite DB without dropping chat data. A missing on-disk file is a fresh application database; ``db.create_all`` creates its schema. ``:memory:`` is accepted for migration checks and tests. Returns the protected backup path when an on-disk upgrade was needed. |
| `migrate_legacy_money(database_path)` | 407 | Sao lưu rồi đổi cột fund_transaction.so_tien từ FLOAT sang NUMERIC(14,2); trả về đường dẫn bản sao hoặc None nếu đã đúng kiểu. |
| `migrate_email_recipients(database_path)` | 468 | Allow external email drafts while preserving existing member drafts. |

### `app/scheduler.py`

Job định kỳ bằng APScheduler.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `init_scheduler(app, force=False)` | 28 | Khởi động scheduler (một lần cho mỗi process). |
| `sync_event_statuses(app)` | 51 | Tự chuyển trạng thái sự kiện theo giờ (xem app/services/event_status.py). Trả về số sự kiện đã đổi. |
| `check_deadlines(app)` | 59 | Tìm task sắp đến hạn và gửi mail cảnh báo cho người phụ trách. Trả về số mail đã gửi. |
| `check_period_reminder(app)` | 78 | Kỳ quỹ hiện tại sắp kết thúc mà chưa nhắc → tạo Notification + mail cho người có quyền quản lý quỹ. Đánh dấu `da_nhac_ky_moi` để không nhắc lặp lại. Trả về số người được nhắc. |

## Routes (giao diện web & REST API)

### `app/routes/auth.py`

Xác thực người dùng (Ch6): đăng nhập, đăng ký, đăng xuất, xác minh email, đổi mật khẩu, quên/đặt lại mật khẩu, xác nhận lại mật khẩu (fresh login).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_auth_rate_limit()` | 50 | Giới hạn tần suất cho các form xác thực, đọc từ AUTH_RATE_LIMIT. |
| class `_LoginRateLimiter` | 55 | Khóa đăng nhập tạm thời trong RAM, khóa theo (IP, MSSV) đã được HMAC. Bổ sung cho Flask-Limiter: Flask-Limiter giới hạn số request theo IP, còn lớp này khóa một tài khoản sau LOGIN_MAX_ATTEMPTS lần sai liên tiếp. |
| `_LoginRateLimiter.__init__(self)` | 62 | Khởi tạo bảng đếm số lần sai (khóa bằng threading.Lock). |
| `_LoginRateLimiter.is_locked(self, key, now)` | 67 | True nếu khóa (IP, MSSV) đang bị tạm khóa. |
| `_LoginRateLimiter.failed(self, key, now, *, max_attempts, window_seconds, lockout_seconds)` | 79 | Ghi nhận một lần đăng nhập sai; đủ LOGIN_MAX_ATTEMPTS lần trong cửa sổ thời gian thì khóa. |
| `_LoginRateLimiter.succeeded(self, key)` | 100 | Xóa bộ đếm khi đăng nhập thành công. |
| `_safe_next(value)` | 106 | Chỉ cho phép chuyển hướng tới đường dẫn nội bộ dạng "/..." (Ch6 – slide 70). |
| `_limiter_settings()` | 124 | Đọc (số lần tối đa, cửa sổ, thời gian khóa) từ cấu hình. |
| `_login_limiter()` | 134 | Lấy (hoặc tạo) bộ khóa đăng nhập dùng chung của app. |
| `_login_key(mssv)` | 143 | Khóa nhận diện HMAC(IP + MSSV) – không lưu MSSV dạng rõ trong RAM. |
| `_login_failed(form)` | 151 | Phản hồi đăng nhập thất bại với thông báo chung chung (không tiết lộ lý do). |
| `_existing_session_redirect()` | 159 | Người đã đăng nhập vào trang login/register: chuyển tới /pending hoặc /home; tài khoản không còn hợp lệ thì đăng xuất. |
| `send_confirmation_email(user)` | 172 | Gửi link xác minh email (token itsdangerous, hạn EMAIL_CONFIRM_MAX_AGE). |
| `_send_reset_email(user)` | 181 | Gửi email chứa link đặt lại mật khẩu (hiệu lực 1 giờ, dùng một lần). |
| `index()` | 193 | Trang giới thiệu công khai; người đã đăng nhập được chuyển vào workspace.<br>route: `auth_bp.route('/', methods=['GET'])` |
| `login()` | 202 | Đăng nhập bằng LoginForm: kiểm tra khóa tạm, so khớp mật khẩu (thời gian như nhau), tạo session mới, hỗ trợ "ghi nhớ đăng nhập" và `next` nội bộ.<br>route: `auth_bp.route('/login', methods=['GET', 'POST'])` · bảo vệ: `limiter.limit(_auth_rate_limit, methods=['POST'])` |
| `pending()` | 263 | Trang chờ duyệt cho tài khoản mới đăng ký (kèm gửi lại email xác minh).<br>route: `auth_bp.route('/pending')` · bảo vệ: `login_required` |
| `register()` | 272 | Đăng ký thành viên bằng RegistrationForm; tạo tài khoản trạng thái pending và gửi email xác minh.<br>route: `auth_bp.route('/register', methods=['GET', 'POST'])` · bảo vệ: `limiter.limit(_auth_rate_limit, methods=['POST'])` |
| `logout()` | 321 | Đăng xuất (chỉ nhận POST) và xóa session.<br>route: `auth_bp.route('/logout', methods=['POST'])` · bảo vệ: `login_required` |
| `confirm_email(token)` | 333 | Mở link xác minh email: token hợp lệ → đánh dấu email đã xác minh.<br>route: `auth_bp.route('/confirm/<token>')` |
| `resend_confirmation()` | 350 | Gửi lại email xác minh (giới hạn 3 lần/giờ).<br>route: `auth_bp.route('/confirm/resend', methods=['POST'])` · bảo vệ: `login_required`, `limiter.limit('3 per hour')` |
| `change_password()` | 370 | Đổi mật khẩu (cần fresh login); đổi xong mọi phiên khác bị đăng xuất, phiên hiện tại được cấp lại.<br>route: `auth_bp.route('/password/change', methods=['GET', 'POST'])` · bảo vệ: `login_required`, `fresh_login_required` |
| `forgot_password()` | 399 | Nhận email và gửi link đặt lại mật khẩu; luôn trả cùng một thông báo để không lộ email nào có tài khoản.<br>route: `auth_bp.route('/password/forgot', methods=['GET', 'POST'])` · bảo vệ: `limiter.limit(_auth_rate_limit, methods=['POST'])` |
| `reset_password(token)` | 419 | Đặt mật khẩu mới từ link email; token hết hiệu lực ngay sau khi dùng.<br>route: `auth_bp.route('/password/reset/<token>', methods=['GET', 'POST'])` · bảo vệ: `limiter.limit(_auth_rate_limit, methods=['POST'])` |
| `reauthenticate()` | 444 | Xác nhận lại mật khẩu để phiên trở thành "fresh" (Ch6 – slide 115).<br>route: `auth_bp.route('/reauth', methods=['GET', 'POST'])` · bảo vệ: `login_required`, `limiter.limit(_auth_rate_limit, methods=['POST'])` |

### `app/routes/admin.py`

Khu vực Ban điều hành: duyệt thành viên, phân vai trò, nhường chức vụ, cấp mật khẩu tạm.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_role_counts()` | 26 | Đếm số người đang giữ mỗi chức vụ (chỉ tính thành viên còn hoạt động). |
| `approve_list()` | 38 | Trang danh sách đơn đăng ký đang chờ duyệt (cần quyền APPROVE_MEMBERS).<br>route: `admin_bp.route('/approve')` · bảo vệ: `login_required`, `permission_required(Permission.APPROVE_MEMBERS)` |
| `approve_user(user_id)` | 47 | Duyệt (accept) hoặc từ chối (reject) một đơn đăng ký; khi duyệt thì tạo MemberRecord và dòng đóng quỹ kỳ hiện tại, sau đó gửi email thông báo.<br>route: `admin_bp.route('/approve/<int:user_id>', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.APPROVE_MEMBERS)` |
| `role_list()` | 84 | Trang vai trò & phân quyền: danh sách thành viên, nút gán chức vụ (chỉ với người thấp cấp hơn) và hộp nhường chức vụ.<br>route: `admin_bp.route('/roles')` · bảo vệ: `login_required`, `any_permission_required(Permission.ASSIGN_ROLES, Permission.HANDOFF_ROLE)` |
| `assign_role(user_id)` | 109 | Gán chức vụ mới cho một thành viên sau khi kiểm tra cấp bậc, giới hạn số lượng và quy tắc "không hạ BĐH cuối cùng" (cần fresh login + ASSIGN_ROLES).<br>route: `admin_bp.route('/roles/<int:user_id>/assign', methods=['POST'])` · bảo vệ: `login_required`, `fresh_login_required`, `permission_required(Permission.ASSIGN_ROLES)` |
| `nhuong_quyen()` | 155 | Nhường chức vụ: người BĐH hiện tại giao chức vụ của mình cho một thành viên thường (target) và tự trở thành THANH_VIEN.<br>route: `admin_bp.route('/roles/nhuong-quyen', methods=['POST'])` · bảo vệ: `login_required`, `fresh_login_required`, `permission_required(Permission.HANDOFF_ROLE)` |
| `issue_temp_password(user_id)` | 202 | Cấp mật khẩu tạm khi thành viên quên mật khẩu mà email không dùng được. Mật khẩu tạm chỉ hiển thị MỘT lần trên trang kết quả (không đưa vào flash vì flash nằm trong cookie session). Thành viên bị bắt đổi mật khẩu ngay lần đăng nhập kế tiếp.<br>route: `admin_bp.route('/members/<int:user_id>/temp-password', methods=['POST'])` · bảo vệ: `login_required`, `fresh_login_required`, `permission_required(Permission.MANAGE_MEMBERS)` |

### `app/routes/main.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_task_scope(actor, query)` | 24 | Admins see the club-wide task board; members see only their own tasks. |
| `_recent_attendance_chart(now)` | 31 | Số lượt điểm danh có mặt của 6 tháng gần nhất và đường SVG cho biểu đồ trang Tổng quan. |
| `_format_currency(value)` | 74 | Định dạng số tiền kiểu 1.000.000 ₫. |
| `home()` | 85 | Trang Tổng quan: thông báo, sự kiện sắp tới, số liệu thành viên/task, số dư quỹ (người quản lý quỹ), biểu đồ điểm danh.<br>route: `main_bp.route('/home')` · bảo vệ: `login_required`, `approved_required` |
| `tasks()` | 162 | Bảng công việc: người quản lý sự kiện thấy mọi task, thành viên chỉ thấy task của mình; lọc theo trạng thái, xem dạng bảng/danh sách.<br>route: `main_bp.get('/tasks')` · bảo vệ: `login_required`, `approved_required` |

### `app/routes/member.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_parse_filters()` | 15 | Đọc bộ lọc của trang Thành viên: q, ban_id, sort, status. |
| `_filtered_members(base_query)` | 24 | Áp bộ lọc và sắp xếp lên `base_query` (danh bạ hoặc phạm vi xem chi tiết). |
| `member_list()` | 51 | Trang Thành viên: mọi thành viên thấy danh bạ CLB (thông tin cơ bản); người có MANAGE_MEMBERS thấy thêm liên hệ, tình trạng hoạt động và hồ sơ chi tiết.<br>route: `member_bp.route('/member')` · bảo vệ: `login_required`, `approved_required` |
| `export_excel()` | 88 | Xuất danh sách thành viên (theo bộ lọc hiện tại) ra file Excel.<br>route: `member_bp.route('/member/export')` · bảo vệ: `login_required`, `approved_required` |

### `app/routes/profile.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_allowed_avatar(filename)` | 21 | Đuôi file ảnh đại diện nằm trong ALLOWED_AVATAR_EXTENSIONS. |
| `_sanitize_avatar(file, extension)` | 29 | Kiểm tra ảnh thật bằng Pillow (đúng định dạng, không quá lớn, chống decompression bomb) và lưu lại bản sạch, bỏ metadata. |
| `view_profile(user_id=None)` | 72 | Xem hồ sơ. Không truyền user_id → xem chính mình. Xem người khác cần quyền quản lý thành viên (MANAGE_MEMBERS).<br>route: `profile_bp.route('/profile')` · bảo vệ: `login_required`, `approved_required` |
| `task_preferences()` | 110 | Members control the information used by AI task suggestions.<br>route: `profile_bp.route('/profile/task-preferences', methods=['POST'])` · bảo vệ: `login_required`, `approved_required` |
| `upload_avatar()` | 143 | Upload ảnh đại diện (AvatarForm: FileRequired + FileAllowed), rồi kiểm tra nội dung thật bằng Pillow và lưu lại ảnh đã làm sạch.<br>route: `profile_bp.route('/profile/avatar', methods=['POST'])` · bảo vệ: `login_required`, `approved_required` |

### `app/routes/event.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_can_manage_task(task)` | 31 | Người đang đăng nhập được quản lý task (quyền MANAGE_EVENTS). |
| `event_list()` | 39 | Trang danh sách sự kiện (kèm tab lịch CTFtime).<br>route: `event_bp.route('/event')` · bảo vệ: `login_required`, `approved_required` |
| `ctftime_events()` | 48 | Fetch the public feed only when an approved member opens its tab.<br>route: `event_bp.get('/api/ctftime/events')` · bảo vệ: `login_required`, `approved_required` |
| `ctftime_import()` | 64 | Create an FIA event from a server-verified public CTFtime calendar row.<br>route: `event_bp.post('/event/import/ctftime')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `event_new()` | 141 | Tạo sự kiện bằng EventForm (Flask-WTF). Validate xong mới ghi database.<br>route: `event_bp.route('/event/new', methods=['GET', 'POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `event_detail(event_id)` | 177 | Trang chi tiết sự kiện: đăng ký, điểm danh, phản hồi, danh sách task (thành viên chỉ thấy task của mình).<br>route: `event_bp.route('/event/<int:event_id>')` · bảo vệ: `login_required`, `approved_required` |
| `event_register(event_id)` | 203 | Thành viên tự đăng ký tham gia sự kiện.<br>route: `event_bp.post('/event/<int:event_id>/register')` · bảo vệ: `login_required`, `approved_required` |
| `event_cancel_registration(event_id)` | 219 | Thành viên hủy đăng ký của mình.<br>route: `event_bp.post('/event/<int:event_id>/cancel-registration')` · bảo vệ: `login_required`, `approved_required` |
| `event_qr_checkin(event_id, token)` | 235 | Trang xác nhận và xử lý điểm danh bằng link QR.<br>route: `event_bp.route('/event/<int:event_id>/checkin/<token>', methods=['GET', 'POST'])` · bảo vệ: `login_required`, `approved_required` |
| `event_manual_attendance(event_id, registration_id)` | 253 | Người quản lý sự kiện điểm danh thủ công cho một lượt đăng ký.<br>route: `event_bp.post('/event/<int:event_id>/attendance/<int:registration_id>')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `event_feedback(event_id)` | 269 | Thành viên gửi đánh giá sự kiện đã tham gia.<br>route: `event_bp.post('/event/<int:event_id>/feedback')` · bảo vệ: `login_required`, `approved_required` |
| `task_new(event_id)` | 285 | Tạo task con. Người phụ trách BẮT BUỘC được chỉ định ngay từ đầu (không có cơ chế thành viên tự xung phong).<br>route: `event_bp.route('/event/<int:event_id>/task/new', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `task_reassign(task_id)` | 340 | Đổi người phụ trách một task đã tồn tại (vẫn là chỉ định, không phải xung phong).<br>route: `event_bp.route('/task/<int:task_id>/chi-dinh-lai', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `task_submit(task_id)` | 371 | Chỉ người được giao task mới được nộp; nộp xong trạng thái chuyển sang cho_duyet.<br>route: `event_bp.route('/task/<int:task_id>/nop-san-pham', methods=['POST'])` · bảo vệ: `login_required`, `approved_required` |
| `task_update_status(task_id)` | 405 | Duyệt task: lam_lai (bắt buộc nhận xét) / hoan_thanh / huy. Không được đặt 'dang_lam' hay 'cho_duyet'.<br>route: `event_bp.route('/task/<int:task_id>/cap-nhat-trang-thai', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `_change_event_status(event_id, new_status)` | 444 | — |
| `event_change_status(event_id)` | 461 | Người quản lý sự kiện (MANAGE_EVENTS) chuyển trạng thái bằng tay: bắt đầu sớm, kết thúc, hủy, mở lại (xem event_status.MANUAL).<br>route: `event_bp.route('/event/<int:event_id>/trang-thai', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `event_end(event_id)` | 470 | Kết thúc sự kiện (giữ đường dẫn cũ). Tình trạng hoạt động của kỳ được tính lại.<br>route: `event_bp.route('/event/<int:event_id>/ket-thuc', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |

### `app/routes/funds.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_flash_form_errors(form)` | 21 | Hiển thị mọi lỗi validate của form dưới dạng flash. |
| `_is_thu_quy()` | 28 | Người đang đăng nhập có quyền quản lý quỹ (MANAGE_FUNDS). |
| `period_new()` | 36 | Mở kỳ quỹ mới (Spring/Summer/Fall…). Kỳ đang mở tự đóng lại – tại một thời điểm chỉ có đúng một kỳ hiện tại.<br>route: `funds_bp.route('/funds/period/new', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_FUNDS)` |
| `funds_home()` | 69 | Trang Quỹ: kỳ hiện tại, sổ thu chi (người quản lý quỹ), tình trạng đóng quỹ, khoản thu và thanh toán chờ xác nhận.<br>route: `funds_bp.route('/funds')` · bảo vệ: `login_required`, `approved_required` |
| `collection_new()` | 124 | Mở khoản thu mới từ form trên trang Quỹ (MANAGE_FUNDS, kiểm tra trong service).<br>route: `funds_bp.post('/funds/collections/new')` · bảo vệ: `login_required`, `approved_required` |
| `collection_pay(fund_id)` | 144 | Thành viên khai báo đã đóng tiền cho khoản thu (chờ xác nhận).<br>route: `funds_bp.post('/funds/collections/<int:fund_id>/payments')` · bảo vệ: `login_required`, `approved_required` |
| `collection_payment_review(payment_id)` | 160 | Người quản lý quỹ xác nhận/từ chối một lần thanh toán.<br>route: `funds_bp.post('/funds/payments/<int:payment_id>/review')` · bảo vệ: `login_required`, `approved_required` |
| `transaction_new()` | 176 | Ghi một giao dịch vào sổ thu chi của kỳ đang mở (FundTransactionForm).<br>route: `funds_bp.route('/funds/transaction/new', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_FUNDS)` |
| `toggle_due(user_id)` | 205 | Đánh dấu đã đóng / chưa đóng quỹ kỳ hiện tại cho một thành viên.<br>route: `funds_bp.route('/funds/due/<int:user_id>/toggle', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_FUNDS)` |
| `remind_unpaid()` | 230 | Gửi mail nhắc các thành viên chưa đóng quỹ trong kỳ hiện tại.<br>route: `funds_bp.route('/funds/nhac-dong-quy', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_FUNDS)` |
| `export_funds_excel()` | 254 | Xuất Excel kỳ hiện tại: sổ thu chi (chỉ người quản lý quỹ) và tình trạng đóng quỹ (thành viên chỉ thấy dòng của mình).<br>route: `funds_bp.route('/funds/export')` · bảo vệ: `login_required`, `approved_required` |

### `app/routes/ai.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `ai_stream()` | 37 | Actual operational progress; never a model's private reasoning trace.<br>route: `ai_bp.post('/ai/stream')` · bảo vệ: `login_required`, `approved_required` |
| `ai_chat()` | 123 | Trang Trợ lý AI: lịch sử hội thoại, danh sách cuộc trò chuyện. BĐH (BOARD) thấy thêm tóm tắt ngày, báo cáo, nháp email chờ duyệt; người quản lý sự kiện (MANAGE_EVENTS) thấy form và danh sách đề xuất sự kiện/task.<br>route: `ai_bp.route('/ai', methods=['GET', 'POST'])` · bảo vệ: `login_required`, `approved_required` |
| `conversation_new()` | 228 | Tạo cuộc trò chuyện AI mới cho người đang đăng nhập.<br>route: `ai_bp.post('/ai/conversations/new')` · bảo vệ: `login_required`, `approved_required` |
| `_change_conversation(conversation_id, action)` | 241 | Đổi tên / lưu trữ / khôi phục một cuộc trò chuyện thuộc sở hữu của người dùng. |
| `conversation_rename(conversation_id)` | 266 | Đổi tên cuộc trò chuyện.<br>route: `ai_bp.post('/ai/conversations/<int:conversation_id>/rename')` · bảo vệ: `login_required`, `approved_required` |
| `conversation_archive(conversation_id)` | 274 | Lưu trữ (ẩn) cuộc trò chuyện.<br>route: `ai_bp.post('/ai/conversations/<int:conversation_id>/archive')` · bảo vệ: `login_required`, `approved_required` |
| `conversation_restore(conversation_id)` | 282 | Khôi phục cuộc trò chuyện đã lưu trữ.<br>route: `ai_bp.post('/ai/conversations/<int:conversation_id>/restore')` · bảo vệ: `login_required`, `approved_required` |
| `report_save(period)` | 290 | Lưu bản chụp báo cáo tuần/tháng (AIReportSnapshot) để tải Excel hoặc tạo nháp email.<br>route: `ai_bp.post('/ai/reports/<period>/save')` · bảo vệ: `login_required`, `bdh_required` |
| `report_export(report_id)` | 305 | Tải file Excel của đúng bản báo cáo đã lưu.<br>route: `ai_bp.get('/ai/reports/<int:report_id>.xlsx')` · bảo vệ: `login_required`, `bdh_required` |
| `report_draft_email(report_id)` | 319 | Tạo nháp email chứa báo cáo gửi tới email hồ sơ của chính người dùng (chưa gửi).<br>route: `ai_bp.post('/ai/reports/<int:report_id>/email-draft')` · bảo vệ: `login_required`, `bdh_required` |
| `email_proposal_review(draft_id, decision)` | 337 | Duyệt và gửi (approved) hoặc từ chối (rejected) một nháp email do AI tạo.<br>route: `ai_bp.route('/ai/email-proposals/<int:draft_id>/<decision>', methods=['POST'])` · bảo vệ: `login_required`, `bdh_required` |
| `email_proposal_edit(draft_id)` | 359 | Sửa tiêu đề/nội dung nháp email trước khi gửi.<br>route: `ai_bp.route('/ai/email-proposals/<int:draft_id>/edit', methods=['POST'])` · bảo vệ: `login_required`, `bdh_required` |
| `email_proposal_reconcile(draft_id, outcome)` | 376 | Xác nhận thủ công kết quả một lần gửi email bị gián đoạn (sent/failed), không tự gửi lại.<br>route: `ai_bp.route('/ai/email-proposals/<int:draft_id>/reconcile/<outcome>', methods=['POST'])` · bảo vệ: `login_required`, `bdh_required` |
| `proposal_new()` | 392 | Tạo đề xuất AI (sự kiện/task) từ form; chỉ là bản xem trước, chưa ghi dữ liệu thật.<br>route: `ai_bp.route('/ai/proposals/new', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `proposal_review(proposal_id, decision)` | 407 | Duyệt đề xuất AI (tạo sự kiện/task thật) hoặc từ chối (quyền MANAGE_EVENTS).<br>route: `ai_bp.route('/ai/proposals/<int:proposal_id>/<decision>', methods=['POST'])` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |

### `app/routes/api.py`

REST API /api/v1 (Ch9): thành viên, quỹ, sự kiện, điểm danh, trợ lý AI, báo cáo.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `authenticate()` | 73 | Hook trước mọi request /api/v1: chưa đăng nhập → 401, tài khoản chưa duyệt/đã lưu trữ → 403. |
| `domain_error(error)` | 82 | Đổi DomainError thành JSON {"error": ...} với status tương ứng và rollback giao dịch. |
| `not_found(_error)` | 89 | Trả 404 dạng JSON cho API. |
| `body()` | 94 | Đọc body JSON của request; không phải object JSON → DomainError 400. |
| `paginate(query)` | 102 | Phân trang query theo ?page= và ?page_size= (1–100). |
| `listing(page, serializer)` | 114 | Đóng gói kết quả phân trang thành JSON {items, page, page_size, total, pages}. |
| `member_json(user)` | 120 | Chuyển User thành JSON (gồm liên hệ, ban, trạng thái thành viên) – chỉ trả cho người có quyền xem. |
| `_create_member(data)` | 133 | Tạo thành viên đã duyệt từ dữ liệu JSON/CSV (quyền MANAGE_MEMBERS); bắt đổi mật khẩu ở lần đăng nhập đầu. |
| `fund_json(fund, member=None)` | 183 | Chuyển khoản thu thành JSON; kèm số dư của `member` nếu có. |
| `payment_json(payment)` | 199 | Chuyển một lần thanh toán thành JSON. |
| `event_json(event)` | 209 | Chuyển sự kiện (và chi tiết) thành JSON. |
| `can_manage_event(event)` | 225 | Người đang đăng nhập có quyền quản lý sự kiện (MANAGE_EVENTS). |
| `members()` | 231 | GET /members – danh sách thành viên trong phạm vi được xem, lọc theo q, department_id, status.<br>route: `api_bp.get('/members')` |
| `member_create()` | 251 | POST /members – tạo một thành viên (MANAGE_MEMBERS).<br>route: `api_bp.post('/members')` |
| `members_import()` | 262 | POST /members/import – nhập tối đa 500 thành viên từ CSV/XLSX, nguyên tử (lỗi một dòng thì không tạo dòng nào).<br>route: `api_bp.post('/members/import')` |
| `member_detail(user_id)` | 311 | GET /members/<id> – hồ sơ một thành viên (chính mình hoặc người có MANAGE_MEMBERS).<br>route: `api_bp.get('/members/<int:user_id>')` |
| `member_update(user_id)` | 319 | PATCH /members/<id> – sửa tên, SĐT, trạng thái, ghi chú của thành viên khác.<br>route: `api_bp.patch('/members/<int:user_id>')` |
| `member_archive(user_id)` | 351 | POST /members/<id>/archive – lưu trữ thành viên (không cho lưu trữ BĐH cuối cùng).<br>route: `api_bp.post('/members/<int:user_id>/archive')` |
| `member_activity(user_id)` | 366 | GET /members/<id>/activity – tình trạng hoạt động kỳ hiện tại (task + điểm danh).<br>route: `api_bp.get('/members/<int:user_id>/activity')` |
| `funds()` | 376 | GET /funds – khoản thu (người quản lý quỹ thấy tất cả, thành viên chỉ thấy khoản áp dụng cho mình).<br>route: `api_bp.get('/funds')` |
| `fund_create()` | 388 | POST /funds – tạo khoản thu (MANAGE_FUNDS).<br>route: `api_bp.post('/funds')` |
| `fund_detail(fund_id)` | 395 | GET /funds/<id> – chi tiết khoản thu kèm số dư của người xem.<br>route: `api_bp.get('/funds/<int:fund_id>')` |
| `fund_status(fund_id)` | 403 | PATCH /funds/<id>/status – chuyển trạng thái khoản thu theo luồng draft→open→closed/cancelled.<br>route: `api_bp.patch('/funds/<int:fund_id>/status')` |
| `fund_balances(fund_id)` | 421 | GET /funds/<id>/balances – số phải đóng/đã đóng/còn thiếu của từng thành viên.<br>route: `api_bp.get('/funds/<int:fund_id>/balances')` |
| `payment_create(fund_id)` | 431 | POST /funds/<id>/payments – ghi nhận một lần đóng tiền (chờ xác nhận).<br>route: `api_bp.post('/funds/<int:fund_id>/payments')` |
| `payments()` | 441 | GET /payments – danh sách thanh toán (thành viên chỉ thấy của mình), lọc theo trạng thái.<br>route: `api_bp.get('/payments')` |
| `payment_review(payment_id)` | 452 | POST /payments/<id>/review – xác nhận hoặc từ chối thanh toán (MANAGE_FUNDS).<br>route: `api_bp.post('/payments/<int:payment_id>/review')` |
| `payment_evidence_upload(payment_id)` | 461 | POST /payments/<id>/evidence – tải ảnh minh chứng (PNG/JPEG/WebP ≤ 3MB), lưu ngoài thư mục static.<br>route: `api_bp.post('/payments/<int:payment_id>/evidence')` |
| `payment_evidence_download(evidence_id)` | 498 | GET /payments/evidence/<id> – tải ảnh minh chứng (chủ thanh toán hoặc người quản lý quỹ).<br>route: `api_bp.get('/payments/evidence/<int:evidence_id>')` |
| `fund_adjustment(fund_id, user_id)` | 508 | PUT /funds/<id>/adjustments/<user_id> – miễn/giảm khoản thu cho một thành viên.<br>route: `api_bp.put('/funds/<int:fund_id>/adjustments/<int:user_id>')` |
| `events()` | 517 | GET /events – danh sách sự kiện, lọc theo status.<br>route: `api_bp.get('/events')` |
| `event_create()` | 526 | POST /events – tạo sự kiện kèm chi tiết, ban áp dụng, người phụ trách (MANAGE_EVENTS).<br>route: `api_bp.post('/events')` |
| `event_detail(event_id)` | 570 | GET /events/<id> – chi tiết sự kiện.<br>route: `api_bp.get('/events/<int:event_id>')` |
| `event_update(event_id)` | 576 | PATCH /events/<id> – sửa sự kiện (hạn đăng ký phải nằm trong khoảng bắt đầu – kết thúc).<br>route: `api_bp.patch('/events/<int:event_id>')` |
| `event_copy(event_id)` | 613 | POST /events/<id>/copy – nhân bản sự kiện sang thời gian mới.<br>route: `api_bp.post('/events/<int:event_id>/copy')` |
| `event_cancel(event_id)` | 644 | POST /events/<id>/cancel – hủy sự kiện (không hủy được sự kiện đã điểm danh).<br>route: `api_bp.post('/events/<int:event_id>/cancel')` |
| `event_status_change(event_id)` | 653 | POST /events/<id>/status – chuyển trạng thái bằng tay {status}: dang_dien_ra (bắt đầu sớm / mở lại), da_ket_thuc (kết thúc), cancelled (hủy).<br>route: `api_bp.post('/events/<int:event_id>/status')` |
| `event_register(event_id)` | 663 | POST /events/<id>/registrations – đăng ký tham gia (không giới hạn số người).<br>route: `api_bp.post('/events/<int:event_id>/registrations')` |
| `registration_cancel(registration_id)` | 673 | POST /registrations/<id>/cancel – hủy đăng ký; người đầu danh sách chờ được đôn lên.<br>route: `api_bp.post('/registrations/<int:registration_id>/cancel')` |
| `event_registrations(event_id)` | 681 | GET /events/<id>/registrations – danh sách đăng ký và điểm danh (MANAGE_EVENTS hoặc người phụ trách).<br>route: `api_bp.get('/events/<int:event_id>/registrations')` |
| `event_qr(event_id)` | 693 | GET /events/<id>/qr – token QR điểm danh (hiệu lực 15 phút).<br>route: `api_bp.get('/events/<int:event_id>/qr')` |
| `event_qr_png(event_id)` | 700 | GET /events/<id>/qr.png – ảnh QR chứa link điểm danh.<br>route: `api_bp.get('/events/<int:event_id>/qr.png')` |
| `event_checkin()` | 711 | POST /attendance/checkin – điểm danh bằng token QR.<br>route: `api_bp.post('/attendance/checkin')` |
| `attendance_manual(registration_id)` | 719 | PUT /registrations/<id>/attendance – điểm danh thủ công (có mặt/muộn/có phép/vắng).<br>route: `api_bp.put('/registrations/<int:registration_id>/attendance')` |
| `event_feedback(event_id)` | 728 | POST /events/<id>/feedback – đánh giá sự kiện sau khi kết thúc.<br>route: `api_bp.post('/events/<int:event_id>/feedback')` |
| `assistant()` | 737 | POST /assistant – hỏi trợ lý AI (chế độ thường hoặc Agent); lưu câu hỏi/trả lời vào cuộc trò chuyện.<br>route: `api_bp.post('/assistant')` |
| `assistant_conversations()` | 788 | GET/POST /assistant/conversations – liệt kê hoặc tạo cuộc trò chuyện.<br>route: `api_bp.route('/assistant/conversations', methods=['GET', 'POST'])` |
| `assistant_conversation_messages(conversation_id)` | 802 | GET /assistant/conversations/<id>/messages – tin nhắn theo trang (before/after).<br>route: `api_bp.get('/assistant/conversations/<int:conversation_id>/messages')` |
| `assistant_conversation_rename(conversation_id)` | 814 | PATCH /assistant/conversations/<id> – đổi tên cuộc trò chuyện.<br>route: `api_bp.patch('/assistant/conversations/<int:conversation_id>')` |
| `assistant_conversation_archive(conversation_id)` | 825 | POST /assistant/conversations/<id>/archive – lưu trữ/khôi phục cuộc trò chuyện.<br>route: `api_bp.post('/assistant/conversations/<int:conversation_id>/archive')` |
| `proposal_json(proposal)` | 838 | Chuyển đề xuất AI thành JSON (kèm bản xem trước). |
| `assistant_proposals()` | 848 | GET /assistant/proposals – danh sách đề xuất AI (MANAGE_EVENTS).<br>route: `api_bp.get('/assistant/proposals')` |
| `assistant_proposal_create()` | 858 | POST /assistant/proposals – tạo đề xuất AI.<br>route: `api_bp.post('/assistant/proposals')` |
| `assistant_proposal_review(proposal_id)` | 866 | POST /assistant/proposals/<id>/review – duyệt/từ chối đề xuất AI.<br>route: `api_bp.post('/assistant/proposals/<int:proposal_id>/review')` |
| `dashboard()` | 876 | GET /dashboard – số liệu tổng quan: thành viên, hoạt động, tình hình quỹ, sự kiện sắp tới (theo phạm vi quyền).<br>route: `api_bp.get('/dashboard')` |
| `fund_report()` | 907 | GET /reports/funds.xlsx – báo cáo tình trạng đóng quỹ (MANAGE_FUNDS).<br>route: `api_bp.get('/reports/funds.xlsx')` |
| `member_report()` | 927 | GET /reports/members.xlsx – danh sách thành viên (MANAGE_MEMBERS).<br>route: `api_bp.get('/reports/members.xlsx')` |

## Services (nghiệp vụ & trợ lý AI)

### `app/services/access.py`

Chính sách phân quyền tập trung (Ch7 – RBAC).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `approved(user)` | 14 | Tài khoản đã được duyệt và vẫn đang là thành viên (không rời CLB/lưu trữ). |
| `board_role_names()` | 22 | Các chức vụ thuộc Ban điều hành (BĐH) = các role có bit BOARD trong bảng `role` (chưa seed bảng role thì dùng DEFAULT_ROLES). Dùng để chống mất BĐH cuối cùng. |
| `has_permission(user, perm)` | 33 | Kiểm tra quyền chuẩn: approved + chức vụ có bit `perm`. |
| `is_admin(user)` | 38 | Thuộc BĐH (có quyền BOARD). |
| `is_board(user)` | 43 | Bí danh của is_admin (thuộc BĐH). |
| `can_approve_members(user)` | 48 | Có quyền xem danh sách chờ và duyệt đăng ký (APPROVE_MEMBERS). |
| `can_manage_members(user)` | 53 | Có quyền quản lý thành viên (MANAGE_MEMBERS). |
| `can_manage_events(user)` | 58 | Có quyền quản lý sự kiện & task (MANAGE_EVENTS). |
| `can_manage_funds(user)` | 63 | Có quyền ghi/duyệt quỹ (MANAGE_FUNDS). |
| `is_treasurer(user)` | 68 | Giữ tên cũ cho tương thích: người có quyền quản lý quỹ. |
| `can_view_all_members(user)` | 73 | Xem đầy đủ hồ sơ/liên hệ của mọi thành viên. |
| `can_manage_member(actor, target)` | 78 | Sửa/lưu trữ/cộng điểm cho một thành viên khác (không áp dụng cho chính mình). |
| `can_issue_temp_password(actor, target)` | 83 | Cấp mật khẩu tạm: cần quyền quản lý thành viên và cấp cao hơn người nhận (Trưởng ban không thể chiếm tài khoản Chủ nhiệm bằng cách đặt lại mật khẩu). |
| `can_assign_role_to(actor, target, new_role)` | 91 | Quy tắc phân vai trò theo cấp bậc (rank: số nhỏ = cấp cao). - Cần quyền ASSIGN_ROLES và không tự đổi vai trò của mình. - Chỉ đổi vai trò của người có cấp THẤP hơn mình. - Vai trò mới phải thấp hơn cấp của mình (PCN không gán PCN/CN). - Chủ nhiệm: chỉ Quản trị hệ thống gán được (lần đầu / khi CN cũ không còn); giữa các thành viên, CN đổi qua quy trình nhường quyền. - Không ai gán hoặc đổi vai trò Quản trị hệ thống qua giao diện. |
| `_active_admin_count()` | 114 | Subquery đếm số thành viên BĐH còn hoạt động (dùng trong câu UPDATE có điều kiện). |
| `_active_member_row(user_id)` | 125 | Điều kiện SQL: thành viên chưa rời CLB / chưa lưu trữ. |
| `set_member_role_safely(target, new_role)` | 136 | Atomically update a role without removing the last active admin. |
| `set_member_left_safely(target, record, archived_at=None)` | 153 | Mark a member left while atomically preserving one active admin. |
| `can_view_member(actor, target)` | 174 | Xem hồ sơ: chính mình, hoặc người có quyền quản lý thành viên. |
| `require(condition, message='Bạn không có quyền thực hiện thao tác này.')` | 179 | Ném DomainError 403 nếu điều kiện sai – dùng trong service để chặn thao tác không đủ quyền. |
| `require_current_approval(actor)` | 185 | Re-read account and membership after a potentially slow external call. |
| `actor_scope_stamp(actor)` | 197 | "Dấu" phạm vi quyền hiện tại (chức vụ) để phát hiện quyền bị đổi giữa chừng khi AI xử lý lâu. |
| `require_unchanged_scope(actor, stamp)` | 204 | Chặn nếu chức vụ đã thay đổi kể từ khi bắt đầu xử lý yêu cầu. |
| `active_members()` | 210 | Mọi thành viên đã duyệt và chưa bị lưu trữ (dùng nội bộ cho quỹ/sự kiện). Không gồm tài khoản Quản trị hệ thống (không phải thành viên CLB). |
| `visible_members(actor)` | 220 | Thành viên mà `actor` được xem hồ sơ: tất cả nếu có MANAGE_MEMBERS, ngược lại chỉ bản thân. |
| `directory_members(actor)` | 228 | Danh bạ CLB: mọi thành viên đã duyệt đều thấy danh sách thành viên đang hoạt động. Chỉ dùng cho thông tin CƠ BẢN (tên, MSSV, ban, vai trò). Liên hệ, tình trạng hoạt động và hồ sơ chi tiết vẫn chỉ dành cho người có MANAGE_MEMBERS (visible_members). |
| `board_members(actor)` | 239 | Phạm vi tra cứu tên/MSSV cho AI và nháp email: mọi thành viên nếu thuộc BĐH, ngược lại chỉ bản thân. Không bao gồm quyền xem thông tin liên hệ. |
| `scoped_members(actor, perm)` | 245 | Thành viên trong phạm vi một nghiệp vụ: tất cả nếu có quyền `perm`, ngược lại chỉ bản thân. Ví dụ thủ quỹ (MANAGE_FUNDS) xem tình trạng đóng quỹ của mọi người dù không có quyền quản lý hồ sơ thành viên. |

### `app/services/activities.py`

Tình trạng hoạt động của thành viên – nguồn DUY NHẤT cho toàn hệ thống.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `current_period()` | 38 | Kỳ đang mở (FundPeriod.is_current) hoặc None. |
| `period_of(day)` | 43 | Kỳ có khoảng [ngày bắt đầu, ngày kết thúc] chứa `day` (kỳ chưa có ngày kết thúc = còn mở). |
| `_in_period(event, period)` | 51 | Sự kiện kết thúc trong kỳ. |
| `_ended_events(period)` | 57 | Các sự kiện đã kết thúc thuộc kỳ. |
| `_target_bans(event_ids)` | 63 | {event_id: tập ban áp dụng} – chỉ cho sự kiện không mở cho mọi người. |
| `score_to_level(score)` | 76 | Đổi điểm 0–100 thành mã mức theo Config.ACTIVITY_THRESHOLDS; None → chưa xếp loại. |
| `combine(task_rate, attendance_rate)` | 86 | Gộp hai tỉ lệ (%, có thể None) theo trọng số; thiếu một phần thì dùng phần còn lại. |
| `_rate(done, total)` | 96 | — |
| `compute(member, period, events=None, targets=None)` | 100 | Số liệu hoạt động của một thành viên trong một kỳ (không ghi database). |
| `_save(member, period, data)` | 138 | — |
| `_members()` | 153 | Thành viên được xếp loại: đã duyệt, chưa lưu trữ/rời CLB, không phải tài khoản quản trị. |
| `refresh_period(period)` | 160 | Tính lại và lưu tình trạng của mọi thành viên trong một kỳ. Trả về số dòng đã ghi. |
| `refresh_for_event(event)` | 173 | Tính lại kỳ chứa ngày kết thúc của sự kiện. Gọi khi sự kiện vào/rời trạng thái đã kết thúc hoặc khi dữ liệu của sự kiện đã kết thúc thay đổi (vd. sửa điểm danh). |
| `refresh_all()` | 179 | Tính lại mọi kỳ (dùng sau khi nâng cấp hoặc sửa dữ liệu hàng loạt). |
| `_paused(member)` | 185 | — |
| `stored(member, period=None)` | 190 | Dòng MemberActivity của kỳ (mặc định kỳ hiện tại) hoặc None. |
| `level_of(member, period=None)` | 198 | Mã tình trạng hiển thị của thành viên: tạm nghỉ ghi đè, chưa có dữ liệu = chưa xếp loại. |
| `levels_for(members, period=None)` | 206 | {user_id: mã tình trạng} cho nhiều thành viên bằng 2 truy vấn (dùng cho bảng danh sách). |
| `label_of(member, period=None)` | 223 | Nhãn tiếng Việt của tình trạng hiện tại. |
| `summary(member, period=None)` | 228 | Số liệu đã lưu để hiển thị / trả lời AI / API (không tính lại). |
| `level_filter(level, period=None)` | 248 | Điều kiện SQL lọc User theo mã tình trạng của kỳ hiện tại (dùng subquery, không JOIN). |
| `rules_note()` | 265 | Một câu giải thích công thức đang áp dụng (cho người đọc / trợ lý AI). |

### `app/services/agent.py`

One policy path for the browser chat and JSON assistant API.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `check_chat_rate(actor)` | 18 | Giới hạn số câu hỏi AI mỗi phút cho một người (AI_CHAT_MESSAGES_PER_MINUTE). |
| `respond(actor, message, conversation=None)` | 28 | Trả lời một câu hỏi chat: thử lần lượt nháp email → đề xuất → đọc dữ liệu đã kiểm chứng → nhà cung cấp AI. |
| `respond_events(actor, message, conversation=None, message_id=None)` | 47 | Stream safe status and terminal result events for the browser/API agent. |

### `app/services/agent_orchestrator.py`

Bounded, model-selected loop over deterministic club data tools.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| class `_PlanError` | 142 | Lỗi nội bộ khi kế hoạch do model trả về không hợp lệ (sẽ chuyển sang phương án rules). |
| `_PlanError.__init__(self, reason)` | 144 | Lưu lý do lỗi kế hoạch. |
| `_bounded_setting(name, default, lower, upper)` | 150 | Đọc một cấu hình số nguyên và kẹp trong khoảng [lower, upper]. |
| `_conversation_history(actor, message, conversation, message_id)` | 159 | Lấy lịch sử gần nhất của cuộc trò chuyện (trước tin nhắn hiện tại) để làm ngữ cảnh. |
| `_user_history(history)` | 184 | Chỉ giữ tối đa 6 câu hỏi gần nhất của người dùng (không đưa câu trả lời AI vào ngữ cảnh). |
| `_scope_markers(actor, text, members, bans, periods)` | 190 | Trích các "dấu phạm vi" (thành viên, ban, kỳ, thời gian, trạng thái) xuất hiện trong câu hỏi. |
| `_tool_request(question, tool)` | 226 | Separate explicit business clauses, not conjunctions inside one operation. |
| `_query_preserves_scope(actor, question, query)` | 252 | Kiểm tra truy vấn model viết lại vẫn giữ đúng phạm vi của câu hỏi gốc. |
| `_parse_plan(raw, actor, question)` | 264 | Đọc và kiểm tra JSON kế hoạch của model (công cụ hợp lệ, số bước giới hạn, giữ phạm vi). |
| `_valid_args(args)` | 296 | args của model chỉ được là object gồm các khóa đã khai báo, giá trị chuỗi ngắn. |
| `_unsupported_scope(message)` | 310 | Câu trả lời chuẩn khi câu hỏi lọc theo ban/thành viên mà công cụ chưa hỗ trợ. |
| `_unsupported_event_scope()` | 321 | Câu trả lời chuẩn khi câu hỏi sự kiện có bộ lọc chưa hỗ trợ. |
| `_unsupported_member_info_scope()` | 331 | Câu trả lời chuẩn khi hỏi hồ sơ theo ngày/trạng thái chưa hỗ trợ. |
| `_unsupported_member_task_scope()` | 340 | Câu trả lời chuẩn khi hỏi task theo thời gian chưa hỗ trợ. |
| `_clarify_member_target()` | 350 | Yêu cầu người dùng nêu rõ MSSV/họ tên khi không xác định được thành viên. |
| `_task_query_for_original(actor, original)` | 360 | Resolve the human's target first, then copy only supported task filters. |
| `_member_for_original(actor, original)` | 376 | Tìm thành viên được nhắc trong câu hỏi gốc (trong phạm vi được xem). |
| `_visible_member_scope(actor)` | 381 | Ánh xạ id → {MSSV, họ tên chuẩn hóa} của thành viên trong phạm vi tra cứu. |
| `_target_scope_was_revoked(original, prior_scope, current_members)` | 391 | True nếu thành viên được hỏi đã ra khỏi phạm vi quyền trong lúc xử lý. |
| `_explicit_member_code(text)` | 406 | True nếu câu hỏi có ghi rõ một MSSV. |
| `_unsupported_task_status(markers)` | 412 | True nếu câu hỏi chứa trạng thái task mà bộ đọc chưa hỗ trợ. |
| `_wants_member_tasks(original)` | 423 | Nhận diện câu hỏi muốn xem task của một thành viên. |
| `_safe_member_read(actor, message, conversation=None)` | 432 | Đọc hồ sơ/task thành viên bằng truy vấn đã kiểm chứng (không qua model). |
| `_safe_briefing_read(actor, message)` | 442 | Trả lời tóm tắt ngày/báo cáo bằng dữ liệu kiểm chứng nếu câu hỏi phù hợp. |
| `_safe_rules_answer(actor, message, history)` | 452 | Trả lời bằng RulesProvider (truy vấn cố định) khi không cần model. |
| `_mentions_funds(text)` | 484 | Nhận diện câu hỏi liên quan quỹ. |
| `_fund_result(result, markers)` | 490 | Chỉ chấp nhận kết quả tra cứu quỹ thuộc các intent quỹ đã hỗ trợ. |
| `_read_filter_guard(message, tool, markers)` | 505 | Do not silently discard explicit filters the deterministic readers lack. |
| `_read_before_pending(actor, message, conversation)` | 561 | Ưu tiên trả lời câu hỏi đọc dữ liệu trước khi xử lý đề xuất đang chờ bổ sung. |
| `_scope_markers_for_actor(actor, text)` | 579 | Trích dấu phạm vi của câu hỏi dựa trên dữ liệu người dùng được xem. |
| `_execute_tool(actor, tool, query, original, conversation, prior_scope=None, args=None)` | 589 | Chạy đúng một công cụ đọc dữ liệu đã kiểm chứng theo tên tool; text của model chỉ dùng làm truy vấn đã validate, không bao giờ làm SQL hay dữ liệu ghi. |
| `_result_verification(result)` | 688 | Xác định mức kiểm chứng của kết quả: database &#124; clarification &#124; unsupported &#124; draft. |
| `_combine_results(results, progress, mode, step_count, fallback_reason=None)` | 701 | Gộp kết quả nhiều bước thành một câu trả lời, bỏ trùng lặp, kèm tiến trình. |
| `_error_result(exc)` | 762 | Chuyển DomainError thành kết quả trả lời an toàn. |
| `_rules_fallback(actor, message, history, conversation, allow_prepare)` | 771 | Phương án dự phòng khi model lỗi: trả lời bằng rules/dữ liệu kiểm chứng, không gọi model. |
| `_status(stage, label, tool=None)` | 798 | Tạo sự kiện tiến trình (status) gửi về trình duyệt qua SSE. |
| `respond_events(actor, message, conversation=None, message_id=None)` | 806 | Yield fixed-label progress events and one server-composed result event. |
| `_is_compound(question)` | 986 | Câu hỏi có nhiều ý (vd. "... và tổng đã thu") thì cần thêm lượt lập kế hoạch. |
| `_numbers(text)` | 996 | Tập các con số trong chuỗi, bỏ dấu phân cách (1,234 ≡ 1.234 ≡ 1234). |
| `_codes(text)` | 1001 | Các mã dạng MSSV/mã sự kiện (chữ + số) trong chuỗi, viết hoa. |
| `compose_answer(complete, question, facts)` | 1006 | Nhờ model viết lại câu trả lời đúng trọng tâm từ dữ kiện đã kiểm chứng. Chốt chặn: mọi con số và mã (MSSV, mã sự kiện) trong câu trả lời phải xuất hiện nguyên văn trong dữ kiện hoặc câu hỏi; sai → ném _PlanError để dùng bản của server. |
| `_finish(actor, conversation, permission_stamp, complete, message, completed, progress, deadline, fallback_reason=None)` | 1031 | Gộp kết quả tool; nếu được bật, nhờ model viết câu trả lời đúng trọng tâm (có đối chiếu số). |
| `_tool_description(name)` | 1060 | Mô tả ngắn từng công cụ để đưa vào prompt lập kế hoạch. |
| `_reauthorize(actor, conversation, permission_stamp=None)` | 1082 | Kiểm tra lại tài khoản còn được duyệt và quyền không đổi trước khi trả kết quả. |

### `app/services/assistant.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_normalize(text)` | 28 | Chuẩn hóa chuỗi để so khớp: chữ thường, bỏ dấu tiếng Việt, đ → d. |
| `_money(value)` | 34 | Định dạng số tiền cho câu trả lời (vd: 150,000 đ). |
| `_matching_periods(text)` | 39 | Các kỳ quỹ có tên xuất hiện trong câu hỏi. |
| `_has_specific_timeframe(text)` | 45 | True nếu câu hỏi có mốc thời gian cụ thể (năm, ngày/tháng, quý…). |
| `_model_quota()` | 58 | Bound shared-key use per member; the final approval gates remain separate. |
| `_record_model_use(provider, model, purpose, data, elapsed_ms)` | 89 | Ghi AIUsageLog: nhà cung cấp, model, số token, thời gian (không lưu prompt/nội dung). |
| class `AssistantProvider` | 109 | Lớp cơ sở cho nhà cung cấp trả lời chat. |
| `AssistantProvider.answer(self, actor, question, history=None)` | 113 | Trả lời câu hỏi; lớp con phải cài đặt. |
| class `TemporaryAIError` | 118 | A transient provider failure; safe to answer from verified local rules. |
| class `RulesProvider` | 122 | Câu hỏi chỉ ánh xạ tới các truy vấn ORM được định nghĩa trước. |
| `RulesProvider.answer(self, actor, question, history=None)` | 125 | Trả lời bằng truy vấn cố định (không dùng model) và gắn mức kiểm chứng. |
| `RulesProvider._answer(self, actor, question)` | 153 | Bộ luật nhận diện câu hỏi (quỹ, chưa đóng, tổng đã thu, không hoạt động, vắng nhiều nhất, báo cáo…) và truy vấn dữ liệu theo phạm vi quyền. |
| class `AnthropicProvider` | 339 | Read-only Claude Messages API adapter; never gives the model write tools. |
| `AnthropicProvider.complete(self, system, prompt, max_tokens=800, response_schema=None, purpose='chat')` | 344 | Gọi Claude API với giới hạn quota/timeout và ghi nhận usage. |
| `AnthropicProvider.answer(self, actor, question, history=None)` | 384 | Ưu tiên dữ liệu kiểm chứng; chỉ dùng model cho câu hỏi chung khi AI_STRICT_FACTS tắt. |
| class `GeminiProvider` | 449 | Gemini transport with the same scoped, read-only context as Claude. |
| `GeminiProvider.complete(self, system, prompt, max_tokens=800, response_schema=None, purpose='chat')` | 454 | Gọi Gemini API với giới hạn quota/timeout và ghi nhận usage. |
| `GeminiProvider.answer(self, actor, question, history=None)` | 510 | Như Anthropic; lỗi tạm thời thì quay về RulesProvider. |
| `get_provider()` | 521 | Chọn nhà cung cấp theo AI_PROVIDER và API key đã cấu hình (mặc định RulesProvider). |

### `app/services/briefings.py`

Deterministic, permission-scoped club briefings and report snapshots.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_event_scope(actor, query)` | 24 | Phạm vi sự kiện cho báo cáo (hiện là toàn CLB). |
| `_task_scope(actor, query)` | 29 | Phạm vi task cho báo cáo (hiện là toàn CLB). |
| `daily_digest(actor, today=None)` | 34 | Same date + same database state yields the same text; no LLM involved. |
| `_window(period, today)` | 71 | Khoảng ngày của báo cáo tuần (từ thứ Hai) hoặc tháng (từ ngày 1) đến hết hôm nay. |
| `report_data(actor, period, today=None)` | 82 | Số liệu báo cáo tuần/tháng (sự kiện, task, điểm danh, thanh toán) kèm nguồn – chỉ BĐH. |
| `report_text(payload)` | 126 | Diễn giải payload báo cáo thành văn bản tiếng Việt. |
| `save_report(actor, period)` | 147 | Lưu bản chụp báo cáo (bỏ qua nếu giống hệt một bản gần đây). |
| `can_access_report(actor, snapshot)` | 163 | Recheck current permissions; an old snapshot must not outlive its scope. |
| `report_workbook(snapshot)` | 171 | Tạo file Excel từ bản chụp báo cáo (đã chống chèn công thức). |
| `report_email_draft(actor, snapshot)` | 196 | Tạo nháp email báo cáo gửi về email hồ sơ của chính người dùng. |
| `briefing_kind(message)` | 214 | Nhận diện câu hỏi tóm tắt hôm nay (daily), báo cáo tuần/tháng, hoặc mốc chưa hỗ trợ. |
| `maybe_answer_briefing(actor, message)` | 233 | Trả lời câu hỏi tóm tắt/báo cáo nếu nhận diện được; ngược lại trả None. |

### `app/services/chat_conversations.py`

Owned, persistent conversations with bounded keyset message pages.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `positive_id(value)` | 17 | Ép kiểu id/trang thành số nguyên dương hợp lệ, sai → DomainError. |
| `owned_conversation(actor, conversation_id, active=False)` | 27 | Lấy cuộc trò chuyện thuộc đúng người dùng (không thì 404); `active=True` thì chặn cuộc đã lưu trữ. |
| `checked_title(title)` | 40 | Kiểm tra tên cuộc trò chuyện 1–100 ký tự. |
| `create_conversation(actor, title=None)` | 47 | Tạo cuộc trò chuyện mới và ghi nhớ là cuộc đang chọn trong session. |
| `_import_unscoped_history(actor)` | 57 | Compatibility for legacy callers inserting messages after startup migration. |
| `select_conversation(actor, requested_id=None, create=False, active=False)` | 82 | Chọn cuộc trò chuyện theo id yêu cầu hoặc cuộc đang mở trong session (tạo mới nếu `create`). |
| `append_exchange(actor, conversation, question, reply)` | 104 | Lưu cặp câu hỏi – trả lời vào cuộc trò chuyện (đặt tiêu đề từ câu hỏi đầu tiên). |
| `begin_turn(actor, conversation, question)` | 116 | Persist the human question before a potentially slow model call. |
| `finish_turn(actor, conversation, reply, user_message_id, run_id, permission_stamp=None)` | 131 | Save a grounded reply and safe operational audit before streaming it. |
| `conversation_list(actor, page=1, archived=False)` | 156 | Danh sách cuộc trò chuyện theo trang (20 mục/trang), mới cập nhật trước. |
| `message_page(actor, conversation, before=None, after=None)` | 165 | Tin nhắn của cuộc trò chuyện theo trang dạng keyset (before/after theo id). |
| `conversation_json(conversation)` | 191 | Chuyển cuộc trò chuyện thành JSON. |

### `app/services/club_data_tools.py`

Công cụ đọc dữ liệu CÓ THAM SỐ cho trợ lý AI.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `is_highest_absence(text)` | 80 | Câu hỏi "sự kiện nào vắng nhiều nhất" (luật cũ trong RulesProvider xử lý). |
| `_has(pattern, text)` | 85 | Tìm cụm từ trọn vẹn (không khớp giữa chừng một từ khác). |
| `_empty_args()` | 90 | Bộ tham số rỗng (mọi khóa đều có mặt). |
| `_bans_in(text)` | 96 | Các ban có tên xuất hiện trong câu (đã chuẩn hóa). |
| `_events_in(text)` | 106 | Các sự kiện (không tính nháp) có mã hoặc tên xuất hiện trong câu. Bỏ khớp nằm gọn trong khớp dài hơn của sự kiện khác (vd. "CTF" trong "FIA CTF Fall"). |
| `parse_args(question)` | 122 | Trích tham số từ câu hỏi gốc bằng luật (không dùng model). |
| `merge_args(question, model_args)` | 173 | Gộp tham số luật trích được với gợi ý của model. - Giá trị luật trích được từ chính câu chữ luôn thắng (không để model bỏ bộ lọc). - Giá trị liệt kê (enum) của model chỉ được dùng khi luật không thấy gì (model hiểu cách nói khác, vd. "chăm đi sự kiện" = tích cực). - Ban / sự kiện do model nêu chỉ được dùng nếu thực sự xuất hiện trong câu hỏi. |
| `_member_target(actor, question, scope)` | 211 | Một thành viên được nhắc đích danh (MSSV/họ tên) hoặc chính người hỏi; None nếu không có. |
| `detect_tool(question)` | 227 | Đoán công cụ phù hợp chỉ bằng luật; None nếu không chắc (để luật cũ xử lý). |
| `_result(intent, answer, sources=None, verification='database', facts=None)` | 263 | Kết quả chuẩn cho chat/agent. |
| `_clarify(answer)` | 274 | Câu hỏi cần nói rõ thêm (không đoán). |
| `_unsupported(answer)` | 279 | Bộ lọc chưa hỗ trợ: báo rõ thay vì trả dữ liệu rộng hơn câu hỏi. |
| `member_stats(actor, args, question)` | 289 | Đếm/liệt kê thành viên theo ban, mức hoạt động, trạng thái (trong phạm vi được xem). |
| `_one_member_activity(member)` | 348 | Mức hoạt động của một thành viên kèm số liệu để người đọc tự kiểm. |
| `_timing_label(event, now)` | 364 | Nhãn trạng thái giống hệt trang Sự kiện (event_status.display). |
| `_live_events()` | 369 | Sự kiện chưa hủy. |
| `_upcoming_events()` | 374 | — |
| `_ongoing_events(now)` | 378 | Đang diễn ra (kể cả được bắt đầu sớm bằng tay), chưa qua giờ kết thúc. |
| `_past_events(now)` | 383 | Đã kết thúc hoặc đã qua giờ kết thúc (chờ kết thúc). |
| `_has_attendance(event)` | 389 | Sự kiện đã có ít nhất một kết quả điểm danh (khác "pending"). |
| `_pick_event(args)` | 395 | Chọn đúng MỘT sự kiện theo selector; trả (event, câu hỏi lại nếu chưa chọn được). |
| `event_participation(actor, args, question)` | 432 | Đăng ký và điểm danh của một sự kiện (hoặc của một thành viên trong sự kiện đó). |
| `event_list(actor, args, question)` | 520 | Sự kiện sắp tới / đang diễn ra / đã diễn ra gần đây (thông tin chung, ai cũng xem được). |
| `run_tool(actor, tool, question, model_args=None)` | 540 | Chạy một công cụ có tham số; tham số được gộp và kiểm tra theo câu hỏi gốc. |
| `answer_by_rules(actor, question)` | 552 | Đường dự phòng không dùng model: nhận diện bằng luật rồi chạy công cụ. |

### `app/services/common.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| class `DomainError` | 11 | Lỗi nghiệp vụ có thông báo tiếng Việt và HTTP status (mặc định 400); route bắt và hiển thị cho người dùng. |
| `DomainError.__init__(self, message, status=400)` | 15 | Lưu thông báo và status. |
| `club_now()` | 21 | Event/task wall-clock values in the database are club-local naive datetimes. |
| `parse_date(value, label)` | 26 | Đọc ngày dạng YYYY-MM-DD (hoặc date/datetime); sai → DomainError. |
| `parse_datetime(value, label)` | 38 | Đọc thời gian ISO 8601; có múi giờ thì đổi về giờ CLB (naive). |
| `parse_money(value, label='Số tiền', allow_zero=False)` | 47 | Đọc số tiền Decimal tối đa 2 chữ số thập phân, không âm; `allow_zero` cho phép 0. |
| `audit(actor, action, obj, details=None)` | 63 | Thêm một dòng AuditLog (chưa commit) cho thao tác của `actor` trên `obj`. |
| `member_record(user)` | 74 | Lấy MemberRecord của user, chưa có thì tạo mới (ngày gia nhập = ngày tạo tài khoản). |

### `app/services/ctftime.py`

Bounded, read-only access to CTFtime's public calendar feed.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| class `CalendarUnavailable` | 25 | Raised when no usable public calendar data is available. |
| `_text(value)` | 29 | Giải mã ký tự escape trong iCalendar (\n, \, \; …). |
| `_timestamp(value)` | 34 | Parse only UTC iCalendar timestamps; do not guess source timezones. |
| `parse_calendar(raw, now=None)` | 42 | Parse the public iCalendar feed using the preview's bounded rules. |
| `calendar_data()` | 110 | Return fresh or honest stale cache data, with a bounded public fetch. |
| `event_by_id(event_id)` | 147 | Look up an importable event from parsed server-side calendar data. |

### `app/services/email_proposals.py`

Reviewable email drafts created from BDH requests, including saved templates.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_split_command(message)` | 27 | Tách lệnh "gửi mail cho X: nội dung" thành (người nhận, mục đích). |
| `is_email_write_command(message)` | 41 | True nếu câu chat là yêu cầu soạn email (lệnh trực tiếp hoặc dùng mail mẫu). |
| `_is_template_request(message)` | 54 | Nhận diện câu dạng "gửi/soạn mail … mẫu …". |
| `_mentioned_template(message)` | 63 | Mail mẫu đang bật được nhắc tên trong câu (nếu nhiều mẫu khớp thì yêu cầu chọn). |
| `_mentioned_recipient(actor, message)` | 93 | Xác định người nhận từ câu: một địa chỉ email ngoài CLB, hoặc một thành viên theo MSSV/họ tên. |
| `_draft_from_template(actor, message)` | 147 | Tạo nháp email từ mail mẫu cho người nhận được nhắc trong câu. |
| `_recipient(actor, name)` | 183 | Tìm đúng một thành viên theo MSSV hoặc họ tên; trùng tên thì yêu cầu dùng MSSV. |
| `_validate_address(value)` | 201 | Kiểm tra địa chỉ email người nhận. |
| `_general_text(display_name, purpose)` | 209 | Tiêu đề/nội dung từ mẫu chung `general` trong kho (điền tên + mục đích). |
| `_draft_text(display_name, purpose, provider=None, code=None, email='')` | 218 | Soạn tiêu đề/nội dung email theo thứ tự: 1. Mẫu email trong kho có tên/từ khóa khớp với mục đích → điền biến, không gọi model. 2. Không có mẫu phù hợp → lấy mẫu chung `general`; nếu có model AI thì nhờ model viết lại theo khung mẫu chung cho đúng mục đích, không có model thì điền thẳng. |
| `maybe_create_email_draft(actor, message, provider=None)` | 270 | Nếu câu chat là yêu cầu email: tạo EmailProposal trạng thái pending (không gửi) và trả lời xem trước. |
| `can_review_email(actor, draft)` | 323 | Người được sửa/duyệt/gửi nháp email (thành viên BĐH). |
| `edit_email_draft(actor, draft, subject, body)` | 328 | Sửa tiêu đề/nội dung một nháp chưa gửi. |
| `reconcile_email_draft(actor, draft, outcome)` | 345 | Manual recovery only; never replay an uncertain SMTP send automatically. |
| `review_email_draft(actor, draft, decision, confirm_retry=False)` | 363 | Duyệt và gửi nháp (chống gửi trùng bằng UPDATE có điều kiện) hoặc từ chối; gửi lỗi thì cho phép thử lại. |

### `app/services/email_templates.py`

Kho mẫu email (plain text). Thay biến bằng str.replace, không bao giờ chạy code trong mẫu.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_require_manager(actor)` | 120 | Chỉ người có MANAGE_TEMPLATES (Quản trị hệ thống, CN, PCN) được sửa mẫu email. |
| `allowed_placeholders(template)` | 126 | Biến được phép trong một mẫu (mẫu hệ thống có bộ biến riêng). |
| `_validate_text(subject, body, allowed=PLACEHOLDERS)` | 133 | Kiểm tra tiêu đề (1 dòng ≤180) / nội dung (≤3000) và chỉ cho phép các biến trong `allowed`. |
| `save_template(actor, name, subject, body, template=None, keywords='', description='')` | 150 | Tạo mới hoặc cập nhật mẫu email (MANAGE_TEMPLATES). Mẫu hệ thống chỉ sửa tiêu đề/nội dung. |
| `set_template_active(actor, template, active)` | 184 | Bật/tắt mẫu email của CLB (mẫu hệ thống luôn bật). |
| `reset_system_template(actor, template)` | 194 | Khôi phục nội dung mặc định cho một mẫu email hệ thống. |
| `_fill(text, values)` | 205 | Thay {{bien}} bằng giá trị (chuỗi); biến không có giá trị → chuỗi rỗng. |
| `render_template(template, display_name, code, email, purpose='')` | 210 | Điền thông tin người nhận vào mẫu email của CLB. |
| `system_template(key)` | 223 | (subject, body) của mẫu hệ thống: lấy trong kho; thiếu/không hợp lệ thì dùng mặc định. |
| `render_system_email(key, **values)` | 235 | Nội dung email tự động đã điền biến, ví dụ: ``send_email(user.email, *render_system_email("member_approved", ten=..., mssv=...))``. |
| `find_email_template(text)` | 244 | Tìm mẫu email của CLB phù hợp nhất với câu yêu cầu: tên mẫu hoặc từ khóa xuất hiện trong câu. Trả về EmailTemplate hoặc None (khi đó AI dùng mẫu chung `general`). |
| `seed_system_templates()` | 260 | Nạp các mẫu email hệ thống còn thiếu vào kho (không ghi đè mẫu đã sửa). |

### `app/services/event_status.py`

Trạng thái sự kiện – nơi DUY NHẤT được đổi `Event.trang_thai`.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_grace()` | 50 | — |
| `initial_status(start, end=None, now=None)` | 54 | Trạng thái lúc tạo sự kiện: chưa tới giờ bắt đầu là sắp diễn ra, ngược lại đang diễn ra (lần rà soát kế tiếp sẽ kết thúc nếu đã quá hạn). |
| `auto_close_at(event)` | 60 | Thời điểm hệ thống tự kết thúc sự kiện đang mở. |
| `due_status(event, now=None)` | 68 | Trạng thái hệ thống sẽ tự chuyển tới (None nếu không cần đổi). Chỉ đi tới, không lùi. |
| `display(event, now=None)` | 80 | {code, label, tone} để hiển thị – dùng chung cho mọi trang, AI và API. |
| `is_terminal(event)` | 89 | Đã kết thúc hoặc đã hủy: khóa task, đăng ký. |
| `_apply(event, new_status, actor, source)` | 95 | Ghi trạng thái bằng UPDATE có điều kiện (hai tiến trình không chuyển trùng) rồi xử lý hệ quả. |
| `allowed_targets(event)` | 115 | {trạng thái mới: nhãn nút} người quản lý sự kiện được chọn. |
| `refresh(event, now=None)` | 120 | Áp ngay chuyển trạng thái tự động đang đến hạn của MỘT sự kiện (không chờ lượt rà soát). Commit nếu có thay đổi; trả về True khi đã đổi. |
| `change_status(actor, event, new_status)` | 130 | Người quản lý sự kiện (MANAGE_EVENTS) chuyển trạng thái bằng tay. Commit và trả về sự kiện. |
| `reschedule(event, now=None)` | 146 | Gọi sau khi sửa giờ một sự kiện đang mở: đặt lại trạng thái theo giờ mới (chưa commit). |
| `sync_statuses(now=None)` | 153 | Rà soát mọi sự kiện đang mở và tự chuyển theo giờ. Commit; trả về số sự kiện đã đổi. |
| `sync_if_due(app)` | 170 | Rà soát khi có request, tối đa 1 lần mỗi EVENT_STATUS_SYNC_SECONDS cho mỗi tiến trình. Lỗi không làm hỏng request (chỉ ghi log). |

### `app/services/events.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_serializer()` | 24 | Bộ ký token QR điểm danh (itsdangerous, salt riêng). |
| `event_is_terminal(event)` | 29 | Sự kiện đã kết thúc hoặc bị hủy (không cho sửa task/đăng ký). |
| `registration_open(event, detail, now=None)` | 34 | Sự kiện còn mở đăng ký: chưa kết thúc/hủy và chưa quá hạn đăng ký. Thời gian sự kiện gồm cả setup và báo cáo, nên vẫn đăng ký được sau giờ bắt đầu. Không đặt hạn đăng ký thì mở đến khi sự kiện kết thúc. |
| `eligible(event, member)` | 47 | Thành viên thuộc đối tượng được đăng ký (mọi người, hoặc thuộc ban áp dụng). |
| `register(actor, event, member=None)` | 56 | Đăng ký tham gia (cho mình, hoặc người quản lý sự kiện đăng ký hộ). Sự kiện không giới hạn số người nên luôn vào trạng thái registered. |
| `cancel_registration(actor, registration)` | 85 | Hủy đăng ký (không cho hủy sau khi đã điểm danh); dữ liệu waitlist cũ (nếu có) vẫn được đôn lên. |
| `qr_token(actor, event)` | 109 | Tạo token QR điểm danh có chữ ký (MANAGE_EVENTS). |
| `qr_checkin(actor, token, now=None, expected_event_id=None)` | 115 | Điểm danh bằng token QR: kiểm tra chữ ký, hạn 15 phút, đúng sự kiện, đã đăng ký, trong thời gian sự kiện; xác định đúng giờ/muộn. |
| `manual_attendance(actor, registration, state, note='')` | 153 | Điểm danh thủ công cho lượt đăng ký chính thức (sự kiện đã kết thúc thì tính lại hoạt động). |
| `feedback(actor, event, rating, comment='')` | 177 | Lưu/cập nhật đánh giá 1–5 sao của người đã tham gia, sau khi sự kiện kết thúc. |

### `app/services/export_security.py`

Treat user-controlled workbook values as text, never executable formulas.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `safe_excel_text(value)` | 4 | Chống chèn công thức Excel (CSV/Formula injection): thêm dấu ' trước ô bắt đầu bằng = + - @. |
| `safe_excel_row(values)` | 13 | Áp safe_excel_text cho cả một dòng. |

### `app/services/funds.py`

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `target_members(fund)` | 20 | Query thành viên phải đóng khoản thu (tất cả hoặc theo ban áp dụng), bỏ người đã rời/lưu trữ. |
| `applies_to_member(fund, member)` | 35 | True nếu khoản thu áp dụng cho thành viên. |
| `create_collection(actor, data)` | 40 | Tạo khoản thu mới sau khi kiểm tra tên, loại, số tiền, ngày, ban áp dụng (MANAGE_FUNDS). |
| `balance(fund, member, as_of=None)` | 81 | Số phải đóng (sau miễn/giảm), đã đóng (đã xác nhận), còn thiếu và trạng thái (exempt/paid/partial/overdue/unpaid) của một thành viên. |
| `record_payment(actor, fund, member, data)` | 114 | Ghi nhận một lần đóng tiền (pending); chặn khi khoản thu không mở, sai phương thức, hoặc số tiền vượt phần còn thiếu (tính cả khoản đang chờ xác nhận). |
| `review_payment(actor, payment, decision, note='')` | 157 | Xác nhận/từ chối thanh toán đang chờ bằng UPDATE có điều kiện (chống hai người duyệt cùng lúc). |
| `adjust_fund(actor, fund, member, data)` | 181 | Miễn hoặc giảm khoản thu cho một thành viên, bắt buộc lý do. |

### `app/services/natural_datetime.py`

Conservative date/time extraction for natural proposal requests.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_date_value(year, month, day, now)` | 63 | Tạo ngày từ (năm, tháng, ngày); thiếu năm thì dùng năm hiện tại; ngày không tồn tại → lỗi. |
| `_extract_dates(message, now)` | 73 | Tìm các ngày trong câu (ISO, dd/mm[/yyyy], hôm nay, ngày mai…) kèm vị trí. |
| `_period_key(value)` | 113 | Đổi buổi trong ngày (sáng/trưa/chiều/tối/đêm) thành khóa chuẩn. |
| `_resolve_hour(hour, minute, period)` | 125 | Đổi giờ theo buổi (vd: 7 giờ tối → 19:00), kiểm tra giờ hợp lệ. |
| `_extract_times(message, dates)` | 162 | Tìm các mốc giờ trong câu kèm vị trí và buổi. |
| `_connected(message, first, second)` | 223 | True nếu ngày và giờ nằm liền nhau trong câu (thuộc cùng một mốc). |
| `_explicit_range(message, first, second)` | 236 | True nếu hai mốc được nối bằng từ chỉ khoảng (đến, -, tới…). |
| `parse_natural_datetimes(message)` | 244 | Return complete, safely paired datetimes and their removable phrases. Supported forms include ISO datetimes, Vietnamese day/month dates with an optional four-digit year, today/tomorrow, and explicit clock times in either date-first or time-first order. A second time shares a date only in a clear ``from ... to ...`` schedule. |

### `app/services/natural_proposals.py`

Conservative natural-language intake for reviewable event/task proposals.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_kind(message)` | 21 | Nhận diện câu chat là yêu cầu tạo sự kiện (event) hay giao task (task). |
| `_pending(actor, conversation=None)` | 34 | Đề xuất đang chờ bổ sung thông tin của người dùng (theo cuộc trò chuyện). |
| `_new_pending(actor, kind, text, conversation=None)` | 43 | Tạo bản ghi đề xuất đang chờ bổ sung. |
| `is_natural_proposal_command(message, actor=None, conversation=None)` | 51 | True nếu câu chat là yêu cầu tạo đề xuất hoặc đang có đề xuất chờ bổ sung. |
| `is_pending_deadline_reply(actor, message, conversation=None)` | 58 | Recognize an explicit deadline continuation, not a task lookup. |
| `_entity(rows, name_attr, message, label)` | 71 | Tìm đúng một bản ghi có tên xuất hiện trong câu (ưu tiên tên dài nhất). |
| `_event(message)` | 88 | Sự kiện đang mở được nhắc trong câu (theo mã hoặc tên). |
| `_ban(message)` | 98 | Ban được nhắc trong câu (theo số hoặc tên); nhiều ban thì yêu cầu chọn. |
| `_assignee(actor, message)` | 114 | Người phụ trách được nhắc trong câu (MSSV hoặc họ tên), trong phạm vi quản lý sự kiện. |
| `_dates(message)` | 142 | Các mốc thời gian đầy đủ trong câu (dùng natural_datetime). |
| `_brief(message, kind, event=None, ban=None, member=None, date_parts=())` | 147 | Phần mô tả còn lại của câu sau khi bỏ các cụm đã nhận diện (sự kiện, ban, người, thời gian). |
| `maybe_create_natural_proposal(actor, message, conversation=None)` | 168 | Xử lý yêu cầu tạo sự kiện/task bằng ngôn ngữ tự nhiên: hỏi lại thông tin thiếu, đủ thì tạo AIProposal chờ duyệt. |

### `app/services/proposal_templates.py`

Kho mẫu sự kiện / mẫu chia task cho AI đề xuất.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_key(text)` | 70 | Chuẩn hóa chuỗi để so khớp (bỏ dấu, chữ thường, gộp khoảng trắng). |
| `_keywords(value)` | 75 | Danh sách từ khóa (ngăn bằng dấu phẩy), giữ nguyên chữ người dùng nhập, bỏ trùng. |
| `keyword_hits(keywords, text)` | 88 | Số từ khóa (đã bỏ dấu) xuất hiện trong `text` (đã chuẩn hóa). |
| `parse_task_lines(text)` | 93 | Đọc danh sách task từ textarea: mỗi dòng "Tên task &#124; mô tả" (mô tả có thể bỏ trống). |
| `task_lines(content)` | 109 | Ngược lại của parse_task_lines: hiển thị danh sách task trong ô sửa. |
| `save_proposal_template(actor, kind, form, template=None)` | 115 | Tạo/sửa mẫu sự kiện hoặc mẫu chia task (quyền MANAGE_EVENTS). |
| `set_proposal_template_active(actor, template, active)` | 169 | Bật/tắt mẫu sự kiện/task. |
| `_from_row(row, source, text_match=False)` | 178 | Kết quả chọn mẫu. `text_match`: mẫu được chọn tay hoặc khớp tên/từ khóa trong mô tả (không chỉ khớp loại sự kiện) – chế độ không có model AI chỉ áp mẫu khi True. |
| `_similar_event(kind, brief, event=None)` | 186 | Sự kiện cũ tương tự (cùng loại, hoặc có từ ≥4 ký tự trùng tên với mô tả). |
| `find_proposal_template(kind, brief, event=None, template_id=None)` | 231 | Chọn khung cho đề xuất theo thứ tự: mẫu được chọn → mẫu trong kho → sự kiện tương tự → mẫu chung trong kho → mẫu chung viết sẵn. |
| `seed_proposal_templates()` | 262 | Nạp các mẫu sự kiện/task mặc định nếu kho chưa có mẫu nào của loại đó. |

### `app/services/proposals.py`

Reviewable AI suggestions. No model output is executed directly.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_integer(value, label)` | 22 | Ép kiểu số nguyên, sai → DomainError có nhãn trường. |
| `_json_object(text)` | 30 | Đọc JSON object từ text model trả về (bỏ ```json nếu có). |
| `_model_suggestion(provider, kind, brief, context)` | 44 | Nhờ model gợi ý tên/mô tả/địa điểm cho đề xuất; lỗi thì trả gợi ý rỗng. |
| `_event_payload(actor, data, brief, provider)` | 74 | Dựng payload đề xuất sự kiện đã kiểm tra thời gian và ban (sự kiện không giới hạn số người). |
| `_task_payload(actor, data, brief, provider)` | 108 | Dựng payload đề xuất task: kiểm tra sự kiện, ban, hạn, chọn người phụ trách phù hợp (kỹ năng, số task đang làm, lịch bận). |
| `create_proposal(actor, data, provider=None)` | 212 | Tạo AIProposal (event/task) trạng thái pending – chưa ghi dữ liệu thật (MANAGE_EVENTS). |
| `proposal_preview(proposal)` | 232 | Văn bản xem trước của đề xuất để người duyệt đọc. |
| `can_review(actor, proposal)` | 250 | Duyệt đề xuất AI theo quyền tương ứng: sự kiện/task cần MANAGE_EVENTS. |
| `review_proposal(actor, proposal, decision)` | 255 | Duyệt (tạo sự kiện/task thật, kiểm tra lại sức chứa người phụ trách) hoặc từ chối đề xuất. |
| `is_chat_proposal_command(message)` | 328 | True nếu câu chat là lệnh cú pháp cố định 'đề xuất sự kiện: ...' hoặc 'chia task: ...'. |
| `maybe_create_chat_proposal(actor, message, provider=None)` | 333 | Tạo đề xuất từ lệnh chat cú pháp cố định. |

### `app/services/ui_context.py`

Biến dùng chung cho mọi template (context processor – Ch8 slide 149).

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `workspace_context()` | 29 | Biến chung cho mọi template: tiêu đề trang, ngày giờ CLB, thông báo, `Permission` và hàm `can(perm)`. |

### `app/services/verified_queries.py`

Small, auditable read tools: no model generates member or task facts.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `member_query_kind(message)` | 10 | Nhận diện câu hỏi xem hồ sơ (member) hay task (tasks) của một thành viên. |
| `_member(actor, message)` | 24 | Tìm đúng một thành viên được nhắc trong câu (MSSV/họ tên), trong phạm vi tra cứu. |
| `maybe_answer_member_query(actor, message)` | 53 | Trả lời hồ sơ/task của thành viên bằng truy vấn kiểm chứng; mốc thời gian chưa hỗ trợ thì báo rõ. |

## Khác

### `app/routes/kho_mau.py`

Kho mẫu: mẫu email (hệ thống + của CLB), mẫu sự kiện và mẫu chia task cho AI.

| Hàm / class | Dòng | Mô tả |
|---|---|---|
| `_back(tab)` | 27 | Quay lại đúng tab của trang Kho mẫu. |
| `_run(action, success, tab)` | 32 | Chạy một thao tác lưu; lỗi nghiệp vụ → rollback và báo lỗi. |
| `index()` | 47 | Trang Kho mẫu với 3 tab: Mẫu email · Mẫu sự kiện · Mẫu chia task.<br>route: `kho_mau_bp.route('')` · bảo vệ: `login_required`, `any_permission_required(Permission.MANAGE_TEMPLATES, Permission.MANAGE_EVENTS)` |
| `email_create()` | 73 | Tạo mẫu email của CLB.<br>route: `kho_mau_bp.post('/email')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_TEMPLATES)` |
| `email_update(template_id)` | 85 | Sửa mẫu email (mẫu hệ thống: chỉ tiêu đề/nội dung).<br>route: `kho_mau_bp.post('/email/<int:template_id>')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_TEMPLATES)` |
| `email_active(template_id)` | 98 | Bật/tắt mẫu email của CLB.<br>route: `kho_mau_bp.post('/email/<int:template_id>/active')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_TEMPLATES)` |
| `email_reset(template_id)` | 108 | Khôi phục nội dung mặc định của mẫu email hệ thống.<br>route: `kho_mau_bp.post('/email/<int:template_id>/reset')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_TEMPLATES)` |
| `proposal_create(kind)` | 119 | Tạo mẫu sự kiện (kind=event) hoặc mẫu chia task (kind=task).<br>route: `kho_mau_bp.post('/<kind>')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `proposal_update(kind, template_id)` | 130 | Sửa mẫu sự kiện / mẫu chia task.<br>route: `kho_mau_bp.post('/<kind>/<int:template_id>')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
| `proposal_active(kind, template_id)` | 142 | Bật/tắt mẫu sự kiện / mẫu chia task.<br>route: `kho_mau_bp.post('/<kind>/<int:template_id>/active')` · bảo vệ: `login_required`, `permission_required(Permission.MANAGE_EVENTS)` |
