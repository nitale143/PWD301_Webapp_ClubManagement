# Bản nháp sự kiện: AI lập ngay, bổ sung dần, trả lời như người (05/11/2026)

## Tính năng
- Người quản lý sự kiện mô tả sự kiện bằng lời thường ("Tháng sau tổ chức workshop ATTT cho ban Kỹ thuật, khoảng 50 người"). AI lập **ngay** bản nháp gồm sự kiện + danh sách task (từ Kho mẫu → sự kiện tương tự → mẫu chung), kể cả khi còn thiếu ngày, địa điểm hay người phụ trách, rồi hỏi tiếp phần thiếu (tối đa 2 ý mỗi lần).
- Bổ sung qua chat ("ngày 12/12 ở Lab 304", "task 2 giao cho SE180014", "gợi ý người giúp mình", "thêm task …", "bỏ task 3", "hủy bản nháp") hoặc form ở trang bản nháp `/ai/drafts/<id>`.
- AI gợi ý người phụ trách: đúng ban, còn sức nhận việc, hợp kỹ năng, ít việc nhất; được đánh dấu "AI gợi ý".
- Sự kiện tính theo ngày (không nêu giờ: 00:00–23:59). Hạn task tính từ ngày sự kiện (mặc định trước 2 ngày, 23:59; việc tại sự kiện: giờ kết thúc).
- Duyệt tạo sự kiện + toàn bộ task trong một giao dịch, kiểm lại người phụ trách lúc duyệt; hai người bấm cùng lúc chỉ một lượt thành công.
- Câu trả lời xưng mình/bạn; khi có model, model chỉ viết lại lời thoại và vẫn qua chốt đối chiếu số + mã.
- API: `GET/POST /api/v1/assistant/drafts`, `GET /assistant/drafts/<id>`, `POST …/approve`, `POST …/discard`.

## Đã xóa (thay bằng bản nháp)
- Đề xuất AI cũ: `services/proposals.py`, `services/natural_proposals.py`, model `AIProposal`, `AssistantPending`, `ConversationPending`, route `/ai/proposals/*`, API `/assistant/proposals*`, cú pháp lệnh `Đề xuất sự kiện: … | … | …`. Đề xuất cũ đòi đủ giờ bắt đầu/kết thúc/hạn mới lập được và chỉ giữ câu hỏi dở 30 phút.
- `natural_datetime` chỉ còn `extract_schedule` (bỏ phần ghép ngày giờ riêng cho đề xuất).

## Database
- Migration `0007`: tạo `event_draft`; xóa `ai_proposal`, `assistant_pending`, `conversation_pending`. Đề xuất chưa duyệt không được chuyển sang (chỉ là bản xem trước).

- Test mới: `tests/test_event_drafts.py` (12 test); bỏ các test của đề xuất cũ. Tổng 212 test; `scripts/ai_eval.py` 30/30.

# Quỹ: khoản thu là nguồn duy nhất, quỹ kỳ thành một khoản thu (05/11/2026)

## Lỗi đã sửa
- Hai cơ chế quỹ chạy song song không nối nhau: ô "đã đóng quỹ kỳ" bật tay (không có số tiền) và khoản thu có thanh toán. Đóng đủ khoản thu vẫn hiện "chưa đóng", vẫn bị nhắc; AI trả lời "ai chưa đóng" theo một nguồn, "ai đóng thiếu" theo nguồn khác.
- Tiền thu qua khoản thu không vào sổ thu chi, nên số dư quỹ ở Tổng quan thiếu khoản này.

## Cách hoạt động mới (`app/services/funds.py`)
- Quỹ kỳ là một khoản thu loại "Quỹ theo kỳ" gắn với kỳ; mỗi kỳ tối đa một. **Mở kỳ mới** có ô số tiền và hạn đóng quỹ kỳ để tạo luôn khoản thu "Quỹ <tên kỳ>".
- Mọi khoản thu thuộc một kỳ (`period_id`).
- Xác nhận thanh toán tự ghi một dòng thu vào sổ thu chi (một thanh toán một dòng). Khoản chi vẫn ghi tay.
- Bỏ nút "Đánh dấu đã đóng/chưa đóng". Tab **Đóng quỹ kỳ** hiện đã đóng / phải đóng của từng người; nút **Ghi nhận đã đóng** tạo thanh toán đã xác nhận (cho người đóng tiền mặt).
- Mail **Nhắc đóng quỹ** gửi cho người còn phải đóng ở các khoản thu đang mở, mỗi người một thư liệt kê khoản và số còn thiếu (biến mới `{{danh_sach}}` trong mẫu email "Nhắc đóng quỹ").
- Hồ sơ thành viên, Excel "Đóng quỹ", trợ lý AI ("chưa đóng", "quá hạn", "kiểm tra quỹ") đọc cùng nguồn khoản thu.

## Đã xóa
- Bảng `fund_due`, model `FundDue`, route `/funds/due/<id>/toggle`, việc tạo dòng "chưa đóng" khi duyệt thành viên và khi mở kỳ.

## Database
- Migration `0006`: thêm `fund_collection.period_id` (điền theo kỳ chứa ngày bắt đầu), `fund_transaction.payment_id`; chuyển dữ liệu `fund_due` thành quỹ kỳ + thanh toán đã xác nhận rồi xóa bảng. Cần `LEGACY_FUND_DUE_AMOUNT` nếu sổ cũ có người đã đóng (xem DEVELOPER_GUIDE mục 14).

- Test mới: `tests/test_funds_unified.py` (6 test). Tổng 226 test.

# Tách quyền Ban điều hành và quản lý sự kiện (05/11/2026)

## Lỗi đã sửa
- Nhiều chỗ dùng lẫn quyền BĐH (`BOARD`) và quyền quản lý sự kiện (`MANAGE_EVENTS`). Hai nhóm đang trùng người nên chưa lộ lỗi, nhưng tách hai nhóm là sai ngay:
  - Trang AI hiện form đề xuất sự kiện theo `BOARD`, còn route nhận form lại kiểm `MANAGE_EVENTS`.
  - AI cho người có `BOARD` xem đăng ký/điểm danh của người khác, rộng hơn trang sự kiện.
  - Trang chi tiết sự kiện và bảng công việc đặt tên biến `is_bdh`/`is_board` nhưng kiểm `MANAGE_EVENTS`.
  - Danh sách chức vụ BĐH (chống mất BĐH cuối cùng, nhường chức vụ) viết cứng trong code, không đọc bảng `role`.

## Thay đổi
- Mỗi chức năng kiểm đúng quyền (bảng ở DEVELOPER_GUIDE mục 6.2): sự kiện, task, điểm danh, đề xuất AI, mẫu sự kiện/task → `MANAGE_EVENTS`; tóm tắt ngày, báo cáo, nháp email AI → `BOARD`.
- Trang AI tách thành hai phần theo quyền; trang sự kiện, bảng công việc, trang Thành viên dùng biến đặt theo quyền thật (`can_manage_events`, `is_board`, `can_view_members`).
- `board_role_names()` đọc bảng `role`; bỏ `User.ADMIN_ROLES` và `access.ADMIN_ROLES` viết cứng.
- Chữ hiển thị: việc duyệt task/đề xuất ghi "người quản lý sự kiện" thay vì "BĐH".
- Test mới: `tests/test_permission_split.py` (4 test, dùng vai trò chỉ có một trong hai quyền). Tổng 220 test.

# Trạng thái sự kiện: chuyển tay + hệ thống tự rà soát (05/11/2026)

## Lỗi đã sửa
- Trạng thái chỉ được ghi một lần lúc tạo, nên sự kiện đã tới giờ vẫn hiện "Sắp diễn ra", và sự kiện xong rồi mà BĐH quên bấm kết thúc thì không đánh giá được, task vẫn mở, không được tính vào tình trạng hoạt động. Mỗi trang và trợ lý AI lại đọc trạng thái theo một kiểu, nên lệch nhau.
- Sự kiện tạo từ CTFtime, đề xuất AI, API luôn bị ghi "Sắp diễn ra" kể cả khi đã bắt đầu.
- Trang danh sách sự kiện hiển thị sự kiện đã hủy là "Đã kết thúc" hoặc chữ "cancelled".

## Cách hoạt động mới (`app/services/event_status.py`)
- **Tự động** (chỉ đi tới): tới giờ bắt đầu → Đang diễn ra; qua giờ kết thúc hiện nhãn "Chờ kết thúc"; sau `EVENT_AUTO_CLOSE_HOURS` (mặc định 24 giờ) → Đã kết thúc. Rà soát bởi job scheduler 5 phút/lần, khi có người mở web (tối đa 1 lần/phút mỗi tiến trình), khi mở trang chi tiết sự kiện, và lệnh `flask sync-event-status`.
- **Bằng tay** (quyền Sự kiện & task): Bắt đầu ngay, Kết thúc sự kiện, Hủy sự kiện, Mở lại sự kiện đã kết thúc. Nút ở trang chi tiết sự kiện; API mới `POST /api/v1/events/<id>/status`. Mở lại thì hệ thống tự kết thúc lại sau 24 giờ tính từ lúc mở.
- Sự kiện vào hoặc rời "Đã kết thúc" → tính lại tình trạng hoạt động của kỳ.
- Sửa giờ sự kiện qua API → trạng thái tính lại theo giờ mới.
- Một nhãn chung cho trang, API (`status_label`) và trợ lý AI (`Config.EVENT_STATUS_LABELS`). Trợ lý AI đọc trạng thái đã lưu thay vì tự tính lại theo giờ.
- Mọi lần đổi trạng thái ghi `audit_log` (`event_status`, nguồn `manual`/`auto`) và dùng UPDATE có điều kiện để hai tiến trình không đổi trùng.

## Database
- Migration `0005`: `event.trang_thai` NOT NULL + CheckConstraint 4 giá trị; thêm `event.trang_thai_doi_tay_luc`; đổi giá trị cũ `completed` → `da_ket_thuc`, `open`/`draft` → `sap_dien_ra`. Xóa các chỗ code còn kiểm tra `open`, `draft`, `completed`.

## Nâng cấp
```bash
flask --app run.py db upgrade
flask --app run.py sync-event-status
flask --app run.py recompute-activity
```

- Test mới: `tests/test_event_status.py` (10 test). Tổng 216 test.

# Tình trạng hoạt động: một nguồn, 50% task + 50% điểm danh (05/11/2026)

## Lỗi đã sửa
- Trang Thành viên, Tổng quan và file Excel đọc cột `user.hoat_dong_status`, nhưng không có code nào ghi vào cột này nên ai cũng hiện "Đang hoạt động". Trước đó có 4 cách đo "hoạt động" không khớp nhau: cột này, % task theo ban (`member_activity`, không trang nào hiển thị), tỉ lệ điểm danh (`activity_stats`, trợ lý AI dùng) và điểm cộng/trừ (`activity_point`).

## Cách tính mới (`app/services/activities.py`)
- Điểm mỗi kỳ = 50% tỉ lệ task hoàn thành + 50% tỉ lệ có mặt, chỉ trên sự kiện đã kết thúc trong kỳ. Thiếu một phần thì phần còn lại chiếm 100%; thiếu cả hai là "Chưa xếp loại".
- Xếp loại: ≥ 80 Tích cực, ≥ 50 Hoạt động, > 0 Ít hoạt động, 0 Không hoạt động. Tạm nghỉ ghi đè điểm. Kỳ mới vào CLB là "Chưa xếp loại".
- Vắng có phép không tính vào mẫu số; không đăng ký hay chưa điểm danh tính là vắng. Sự kiện chỉ mở cho một số ban chỉ tính với thành viên các ban đó.
- Tính lại khi kết thúc sự kiện, khi sửa điểm danh của sự kiện đã kết thúc, hoặc bằng `flask recompute-activity` (cũng chạy trong `init-db`).
- Trang Thành viên (số liệu, bộ lọc, cột tình trạng), Tổng quan, hồ sơ, Excel, API `/members/<id>/activity`, `/dashboard` và trợ lý AI cùng đọc một nguồn. Nhãn chung ở `Config.ACTIVITY_LABELS`.
- Hồ sơ hiển thị điểm kỳ hiện tại và bảng theo kỳ: task hoàn thành/được giao, có mặt/phải tham gia, điểm, xếp loại.

## Đã xóa (không còn dùng)
- Cột `user.hoat_dong_status`; bảng `activity_point` (điểm +10/+6/−5) và `club_setting` (chỉ chứa bảng điểm/ngưỡng cũ). Migration `0004`; downgrade không khôi phục dữ liệu.
- `app/activity.py`, `activity_stats`, `sync_attendance_point`, `manual_point`; API `POST /members/<id>/points`, `GET/PUT /settings/activity`.
- Mức "Ngừng hoạt động" và "Bình thường" (gộp thành "Hoạt động"). Nhánh "không hoạt động 60 ngày" của trợ lý AI; câu hỏi kèm số ngày nay được báo là chưa hỗ trợ.
- API `/dashboard`: `average_participation` đổi thành `average_activity_score`.

## Nâng cấp
```bash
flask --app run.py db upgrade
flask --app run.py recompute-activity
```

- Test mới: `tests/test_activity.py` (13 test). Tổng 206 test.

# Giao diện: logo mới, favicon, bỏ tìm nhanh (03/11/2026)

- Logo CLB mới (`static/assets/fia-club-logo.png`), dùng cho thanh bên, trang giới thiệu và khung chào trang Tổng quan (thay khối tròn "CLB FIA").
- Bộ favicon tạo từ logo mới: `favicon.ico` (16/32/48 px), `favicon-32.png`, `apple-touch-icon.png`.
- Bỏ ô "Tìm nhanh trong workspace" và phím tắt Ctrl K. Nút "Các chức năng" vẫn mở danh sách trang.

# Trợ lý AI trả lời đúng trọng tâm (03/11/2026)

## Lỗi đã sửa
- Hỏi "có bao nhiêu thành viên trong sự kiện sắp diễn ra" bị trả về danh sách sự kiện: luật cũ chỉ khớp từ "sự kiện" + "sắp". Nay câu hỏi về thành viên/người tham gia được nhận diện trước.
- "Có bao nhiêu thành viên tích cực", "Ban X gồm những ai", "Sự kiện nào có nhiều người vắng nhất" trước đây không có truy vấn tương ứng.

## Công cụ đọc dữ liệu mới (`services/club_data_tools.py`)
- `member_stats`: đếm/liệt kê thành viên theo ban, mức hoạt động (Tích cực/Bình thường/Ít hoạt động/Không hoạt động), tạm nghỉ; mức hoạt động của một thành viên hoặc của chính mình.
- `event_participation`: đăng ký, có mặt, vắng của một sự kiện (sắp diễn ra, vừa diễn ra, đang diễn ra, theo tên/mã) hoặc của chính mình trong sự kiện.
- `event_list`: sự kiện sắp tới / đang diễn ra / đã diễn ra.
- Phân quyền giữ nguyên ma trận cũ (mức hoạt động: Quản lý thành viên; điểm danh: Sự kiện & task hoặc BĐH; thành viên thường chỉ hỏi về chính mình).

## Agent
- Kế hoạch của model có thêm `args` (kiểm tra khóa, kiểu, enum). Server trích tham số từ câu gốc; gợi ý của model chỉ được nhận khi bám vào câu hỏi.
- Prompt lập kế hoạch có ví dụ (few-shot) và quy tắc chọn tool theo trọng tâm câu hỏi.
- `AI_AGENT_FAST_FINISH` (mặc định bật): câu hỏi một ý kết thúc sau 1 lượt lập kế hoạch.
- `AI_AGENT_COMPOSE` (mặc định bật): model viết câu trả lời từ dữ liệu đã tra cứu; mọi con số và mã phải có trong dữ liệu, sai thì dùng bản server. Bản gốc lưu ở `verified_answer`.
- Trang chat ghi lý do khi rơi về quy tắc dự phòng (chưa cấu hình AI, không gọi được model…).

## Đo độ đúng
- `scripts/ai_eval.py`: 30 câu hỏi mẫu, chấm theo intent + con số tính độc lập, theo đúng quyền của người hỏi.
- Test mới: `tests/test_ai_data_tools.py` (12 test). Tổng 193 test.

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
