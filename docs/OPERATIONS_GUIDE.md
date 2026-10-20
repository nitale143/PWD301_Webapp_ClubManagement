# Vận hành: việc chờ xử lý, email, nguồn AI và phục hồi dữ liệu

## Trung tâm việc cần xử lý

Mở **Việc cần xử lý** (`/operations`) trong menu Điều hành. Các nhóm đếm và phân trang độc lập: đăng ký thành viên, task nộp chờ duyệt, thanh toán chờ xác nhận, đề xuất AI và nháp email. Mỗi người chỉ thấy nghiệp vụ mình có quyền duyệt. Nút xem dẫn tới màn hình nghiệp vụ gốc để đọc nội dung trước khi ra quyết định; không có phê duyệt hàng loạt bỏ qua bước xem.

Chính sách phân quyền của bản nhóm được giữ: CN/PCN, Trưởng ban, Thư ký–Thủ quỹ có các tập quyền khác nhau; không tự đổi mọi BOD thành cùng một cấp. Thành viên quản lý không được sửa/lưu trữ người ngang cấp hoặc cấp cao hơn. ADMIN hệ thống không thuộc vòng đời thành viên CLB.

## Thông báo trong web

Mở chuông hoặc `/notifications` để xem lịch sử có phân trang. Có nút đánh dấu từng thông báo hoặc tất cả là đã đọc. Các thao tác này dùng POST + CSRF và chỉ tác động thông báo của chính người đăng nhập. Không tự đánh dấu đọc khi chỉ mở trang.

Giao/chỉ định lại task, nộp sản phẩm, yêu cầu làm lại, kết quả duyệt và nhắc hạn được lưu trong web độc lập với kết quả gửi email. Nhắc hạn cần scheduler chạy. Không có thông báo đẩy ra thiết bị khi người dùng đóng website.

## Email

Mở **Theo dõi email** (`/mail-deliveries`) với tài khoản BOD. Đây là lịch sử gửi **đồng bộ**, không phải một hàng đợi gửi nền tự động. Giao diện phân biệt đang chờ, đang gửi, đã gửi, thất bại, chưa rõ kết quả, bỏ qua và đã lưu vào hộp thư phát triển. “Đã gửi” chỉ có nghĩa máy chủ SMTP chấp nhận thư, không chứng minh thư đã vào Inbox hoặc người nhận đã đọc.

Email có trạng thái thất bại có thể thử lại theo quyền và điều kiện kiểm tra. Gửi dở/mất kết nối sau khi bắt đầu gửi có thể là **chưa rõ kết quả**: cần chờ, kiểm tra Sent/nhật ký nhà cung cấp, rồi xác nhận thủ công trước khi thử lại. Mã Message-ID và claim trong DB giảm gửi trùng, nhưng SMTP không cung cấp cam kết exactly-once khi kết nối bị ngắt. Không bấm gửi lại nếu chưa biết thư trước có được nhận hay không.

Thư xác minh, đặt lại mật khẩu hoặc nội dung được nhận diện nhạy cảm không lưu nội dung/link/token trong lịch sử và không cho phát lại từ trang này. Dùng lại luồng gửi mã mới của trang tài khoản. Nháp do AI tạo vẫn phải duyệt và đối soát tại trang AI; lịch sử email không cho vượt qua chốt duyệt đó. Nội dung khác có thể được giữ riêng trong DB phục vụ gửi lại; chỉ cấp quyền máy chủ/backup cho người được phép.

## Nguồn kiểm chứng AI

Câu trả lời mới từ truy vấn dữ liệu cố định có liên kết **Xem dữ liệu kiểm chứng**. Liên kết được lưu cùng tin nhắn nên vẫn có sau khi tải lại lịch sử. Lời chào/bản nháp/câu hỏi chưa được hỗ trợ không được gắn nguồn giả.

Trang nguồn ghi thời điểm UTC, phiên bản truy vấn, phạm vi, quy tắc đã áp dụng, liên kết bản ghi tham chiếu (tối đa 100 mỗi loại) và dấu SHA-256 của câu trả lời đã lưu. Tại mỗi lần mở, hệ thống kiểm tra chủ hội thoại, trạng thái tài khoản, vai trò/tập quyền và quyền xem nguồn. Đổi quyền thì phải hỏi lại; bản ghi bị xóa/mất quyền bị ẩn. Tin nhắn cũ trước cập nhật không được hồi tố nguồn giả.

Giới hạn quan trọng:

- Đây là **bằng chứng thực thi truy vấn**, không phải chuỗi suy nghĩ riêng của model.
- Snapshot là kết quả tại thời điểm trả lời; trang dữ liệu đích là dữ liệu hiện tại, có thể đã thay đổi.
- Hash phát hiện thay đổi nội dung, không chứng minh dữ liệu đầu vào đúng hoặc chống được quản trị DB cố ý sửa cả nội dung và hash.
- Không bảo đảm tuyệt đối AI không hallucination. Phần sáng tạo là bản nháp; dữ liệu định lượng dùng công cụ định trước; hành động ghi/gửi vẫn cần người có quyền duyệt.
- Câu trả lời cố định có thể lặp lại với cùng câu hỏi **khi dữ liệu, quyền, thời điểm/bộ lọc và phiên bản truy vấn không đổi**. Không hứa câu trả lời luôn giống nhau sau khi dữ liệu hoặc thời gian đã đổi.

## Sao lưu và diễn tập khôi phục

Áp dụng cho SQLite trên đĩa. Lệnh tạo bản sao nhất quán DB, avatar và minh chứng thanh toán, kèm manifest/hash/schema; thư mục 0700 và tập tin 0600. Không đưa `.env`, session secret hoặc API key vào gói này. Giữ các cấu hình đó ở nơi riêng tư khác để phục hồi máy chủ đầy đủ.

```bash
flask --app run.py backup-create
# Lệnh in ra thư mục club-backup-... vừa tạo. Thay đường dẫn dưới bằng thư mục đó.
flask --app run.py backup-verify /duong-dan-rieng/club-backup-...
flask --app run.py backup-restore /duong-dan-rieng/club-backup-... \
  --target /duong-dan-rieng/recovery/clb-restored.db \
  --artifacts-target /duong-dan-rieng/recovery/uploads-restored
```

Đích phải chưa tồn tại và tách biệt DB/thư mục nguồn. Lệnh không ghi đè web đang chạy. Tập tin được khôi phục ở `uploads-restored/avatars/` và `uploads-restored/payment_evidence/`. Trước khi đưa bản phục hồi vào dùng: dừng web và scheduler, giữ bản hiện tại để có thể quay lại, kiểm tra phiên bản code/schema, cấu hình `DATABASE_URL` và thư mục tệp tương ứng, rồi thử đăng nhập/tra cứu trước khi mở lại cho thành viên. Không đổi đường dẫn DB trong khi web còn đang chạy.

Khi scheduler chạy, `BACKUP_ENABLED=true` thêm job sao lưu lúc **02:00 theo múi giờ scheduler**. `BACKUP_DIRECTORY` mặc định ở thư mục `backups/fia-verified` cạnh project. Chạy scheduler **một tiến trình** (web một worker hoặc `flask run-scheduler` riêng), không đồng thời hai nơi.

Không có job nếu `SCHEDULER_ENABLED=false`; riêng bật `BACKUP_ENABLED` không tự khởi chạy scheduler. Kiểm tra nhật ký định kỳ để phát hiện backup thất bại. Bản sao hiện không mã hóa và chưa tự xóa bản cũ: cần bảo vệ ổ đĩa, theo dõi dung lượng, sao chép ra nơi lưu trữ khác an toàn và có chính sách lưu giữ riêng. Checksum dùng phát hiện hỏng dữ liệu, không thay chữ ký số.

## Migration và kiểm thử

Các revision bổ sung: `0004` đồng bộ tên ràng buộc hội thoại cũ; `0005` theo dõi giao thư; `0006` nguồn kiểm chứng AI. Sao lưu rồi dùng `flask --app run.py init-db`, sau đó `flask --app run.py db check` để kiểm tra model/schema.

Các test dùng database tạm và SMTP/model giả. Kiểm thử tự động không thay thế việc kiểm tra email với nhà cung cấp thật, cấu hình HTTPS/proxy hay diễn tập phục hồi trên hạ tầng triển khai.
