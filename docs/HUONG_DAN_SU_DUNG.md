# Hướng dẫn sử dụng FIA Workspace

Tài liệu dành cho **thành viên** và **Ban điều hành (BĐH)** của CLB, giải thích từng màn hình và thao tác của webapp. Phần 1–6 dành cho mọi người. Phần 7 trở đi dành cho người giữ chức vụ trong BĐH.

> Người cài đặt và vận hành máy chủ xem thêm `README.md` và `docs/DEVELOPER_GUIDE.md`.

## Mục lục

1. [Tài khoản: đăng ký, đăng nhập, mật khẩu](#1-tài-khoản-đăng-ký-đăng-nhập-mật-khẩu)
2. [Làm quen giao diện](#2-làm-quen-giao-diện)
3. [Hồ sơ cá nhân](#3-hồ-sơ-cá-nhân)
4. [Sự kiện và công việc](#4-sự-kiện-và-công-việc)
5. [Quỹ câu lạc bộ](#5-quỹ-câu-lạc-bộ)
6. [Trợ lý AI](#6-trợ-lý-ai)
7. [Ban điều hành: ai được làm gì](#7-ban-điều-hành-ai-được-làm-gì)
8. [Duyệt thành viên và quản lý hồ sơ](#8-duyệt-thành-viên-và-quản-lý-hồ-sơ)
9. [Vai trò, phân quyền và nhường chức vụ](#9-vai-trò-phân-quyền-và-nhường-chức-vụ)
10. [Quản lý sự kiện, task và điểm danh](#10-quản-lý-sự-kiện-task-và-điểm-danh)
11. [Quản lý quỹ](#11-quản-lý-quỹ)
12. [Trợ lý AI cho Ban điều hành](#12-trợ-lý-ai-cho-ban-điều-hành)
13. [Câu hỏi thường gặp và xử lý sự cố](#13-câu-hỏi-thường-gặp-và-xử-lý-sự-cố)

---

## 1. Tài khoản: đăng ký, đăng nhập, mật khẩu

### 1.1. Đăng ký thành viên mới

1. Mở trang web của CLB, bấm **Đăng ký thành viên mới** (hoặc vào `/register`).
2. Điền đủ các ô:

   | Ô | Yêu cầu |
   |---|---|
   | Họ và tên | 2–100 ký tự |
   | MSSV | 8 ký tự dạng **`SE201256`**: chữ campus (`H`, `D`, `Q`, `C`, `S`) + chữ ngành (`A`, `S`, `E`) + 6 số. Gõ chữ thường cũng được, hệ thống tự viết hoa. Chưa được đăng ký |
   | Ngày sinh | Từ năm 1980, trước hôm nay |
   | Số điện thoại | Ô đã có sẵn **+84**, bạn chỉ nhập **9 số** phía sau (vd: `912345678`). Lỡ gõ cả số `0` đầu hoặc `+84` thì hệ thống tự bỏ |
   | Email | Email thật bạn dùng thường xuyên (đuôi nào cũng được), chưa gắn với tài khoản khác |
   | Facebook | Ô đã có sẵn **facebook.com/**, bạn chỉ nhập **userID** (chữ, số, dấu chấm). Hệ thống lưu thành `https://facebook.com/userID`. Không nhận link `www.`, `m.`, `fb.com` hay `profile.php?id=…` |
   | Ban hoạt động | Chọn **một** ban (bấm vào thẻ ban) |
   | Mật khẩu | 8–64 ký tự, **có cả chữ và số**, không chứa MSSV, không quá phổ biến (vd: `password123`) |
   | Nhập lại mật khẩu | Phải giống ô Mật khẩu |

3. Bấm **Gửi đăng ký**. Nếu ô nào sai, lỗi hiện ngay dưới ô đó.
4. Hệ thống gửi **email xác minh**. Mở thư và bấm vào liên kết (hiệu lực 48 giờ).
5. Tài khoản ở trạng thái **Chờ xét duyệt** cho tới khi Chủ nhiệm hoặc Phó chủ nhiệm duyệt. Bạn vẫn đăng nhập được nhưng chỉ thấy trang chờ duyệt. Trên trang này có nút **Gửi lại email xác minh** nếu chưa nhận được thư.
6. Khi được duyệt (hoặc bị từ chối), bạn nhận email thông báo.

### 1.2. Đăng nhập

- Nhập **MSSV** (hoặc **tên đăng nhập** nếu bạn là Quản trị hệ thống) và **mật khẩu**, bấm **Đăng nhập**. MSSV gõ chữ thường cũng được.
- Ô **Ghi nhớ đăng nhập trên thiết bị này**: giữ đăng nhập tối đa 14 ngày. **Không tích** trên máy dùng chung (phòng lab, máy mượn).
- Nhập sai nhiều lần liên tiếp thì tài khoản bị khóa đăng nhập tạm thời khoảng 5 phút. Thông báo lỗi luôn là "MSSV hoặc mật khẩu không đúng." để kẻ gian không dò được MSSV.
- Phiên đăng nhập tự hết hạn sau 8 giờ. Nếu bạn đổi mạng/IP hoặc trình duyệt, hệ thống có thể yêu cầu đăng nhập lại để bảo vệ tài khoản.
- Luôn bấm **Đăng xuất** (cuối thanh bên trái) khi dùng xong máy chung.

### 1.3. Quên mật khẩu

1. Ở trang đăng nhập, bấm **Quên mật khẩu?**
2. Nhập email đã đăng ký và bấm **Gửi liên kết đặt lại mật khẩu**.
3. Hệ thống luôn trả cùng một thông báo, dù email có tồn tại hay không (để bảo vệ danh tính thành viên). Nếu email đúng, bạn nhận thư trong vài phút.
4. Mở liên kết **trong vòng 1 giờ**, nhập mật khẩu mới hai lần.
5. Mỗi liên kết chỉ dùng được **một lần**. Đã đặt lại xong thì liên kết cũ hết hiệu lực.

Không nhận được email? Nhờ Chủ nhiệm, Phó chủ nhiệm hoặc Trưởng ban **cấp mật khẩu tạm** (xem [mục 8.3](#83-cấp-mật-khẩu-tạm)).

### 1.4. Đổi mật khẩu

1. Vào **Hồ sơ cá nhân** → phần **Bảo mật tài khoản** → **Đổi mật khẩu** (hoặc mở hộp **Các chức năng** → **Đổi mật khẩu**).
2. Nhập mật khẩu hiện tại, mật khẩu mới và nhập lại mật khẩu mới.
3. Đổi xong, **mọi thiết bị khác đang đăng nhập tài khoản của bạn sẽ bị đăng xuất**. Thiết bị đang dùng vẫn giữ đăng nhập.

### 1.5. Mật khẩu tạm và tài khoản do BĐH tạo

Nếu tài khoản của bạn do BĐH tạo (nhập từ file danh sách), hoặc bạn được cấp mật khẩu tạm, thì lần đăng nhập đầu tiên hệ thống **bắt buộc đổi mật khẩu** trước khi dùng các chức năng khác.

### 1.6. Xác nhận lại mật khẩu

Một số thao tác nhạy cảm như đổi mật khẩu, gán chức vụ, nhường chức vụ hay cấp mật khẩu tạm yêu cầu phiên đăng nhập "mới". Nếu bạn đang đăng nhập nhờ chế độ **Ghi nhớ đăng nhập**, hệ thống sẽ mở trang **Xác nhận lại mật khẩu**. Nhập mật khẩu rồi bấm **Xác nhận**, bạn sẽ được đưa về đúng thao tác đang làm.

---

## 2. Làm quen giao diện

- **Thanh bên trái**: Tổng quan, Thành viên, Sự kiện, Công việc, Quỹ câu lạc bộ, Trợ lý AI. Người có quyền sẽ thấy thêm mục **ĐIỀU HÀNH** (Phê duyệt; Vai trò & phân quyền). Bên dưới là tên của bạn và nút **Đăng xuất**.
- **Thanh trên cùng**: chuông 🔔 hiện thông báo của bạn (nhắc deadline, nhắc kỳ quỹ…). Biểu tượng tròn bên phải mở Hồ sơ.
- **Các chức năng**: danh sách các trang bạn được phép vào.
- **Thông báo màu** ở đầu trang: xanh là thành công, đỏ là lỗi hoặc cảnh báo.
- Trên điện thoại, bấm biểu tượng lưới ở góc trái để mở menu.

**Trang Tổng quan** hiển thị lời chào, thông báo mới, sự kiện sắp tới, số task đã hoàn thành và sắp đến hạn, biểu đồ lượt điểm danh 6 tháng. Thủ quỹ thấy thêm số dư quỹ kỳ hiện tại. BĐH thấy thêm khung **Việc cần xử lý hôm nay**.

---

## 3. Hồ sơ cá nhân

Vào bằng tên của bạn ở góc dưới thanh bên trái, hoặc avatar ở góc phải trên.

| Phần | Bạn làm được gì |
|---|---|
| Thông tin liên hệ | Xem SĐT, email, Facebook, ngày sinh, chức vụ, ban. Cần sửa thông tin thì liên hệ BĐH. |
| Khả năng nhận task | Ghi kỹ năng (cách nhau bằng dấu phẩy, tối đa 10), số task đang làm tối đa (1–10), ngày tạm không nhận task. AI dùng các thông tin này khi gợi ý phân công. |
| Bảo mật tài khoản | Xem email đã xác minh chưa, **Gửi lại email xác minh**, **Đổi mật khẩu**. |
| Ảnh đại diện | Tải ảnh PNG, JPG, JPEG, GIF hoặc WEBP, tối đa 3 MB. Hệ thống kiểm tra ảnh thật và tự xóa thông tin ẩn (vị trí GPS…) trong ảnh. |
| Hoạt động theo kỳ | Số task hoàn thành / được giao, số sự kiện có mặt / phải tham gia, điểm và xếp loại của từng kỳ. Cách tính xem [mục 4.5](#45-tình-trạng-hoạt-động). |
| Tình trạng đóng quỹ | Đã đóng hoặc chưa đóng quỹ theo từng kỳ. |

**Trang Thành viên**: mọi thành viên đều thấy danh bạ CLB với thông tin cơ bản (tên, MSSV, ban, vai trò) và chỉ mở được hồ sơ chi tiết của chính mình. Người có quyền quản lý thành viên thấy thêm tình trạng hoạt động, cột **Liên hệ** (số điện thoại ở trên, link Facebook ở dưới), mở được hồ sơ mọi người và bấm **Xuất danh sách** ra Excel. Chọn tìm kiếm / ban / sắp xếp rồi bấm **Áp dụng**. Danh bạ không gồm tài khoản Quản trị hệ thống.

---

## 4. Sự kiện và công việc

### 4.1. Xem và đăng ký sự kiện

1. Vào **Sự kiện**. Danh sách xem được dạng **Thẻ** hoặc **Danh sách**, tìm theo tên.
2. Tab **CTFtime** hiển thị lịch các cuộc thi CTF công khai (chỉ để tham khảo).
3. Bấm một sự kiện → **Đăng ký tham gia**.
   - Sự kiện không giới hạn số người tham gia.
   - Thời gian sự kiện đã gồm cả setup và báo cáo sau sự kiện, nên đăng ký được tới **hạn đăng ký** (nằm trong khoảng bắt đầu – kết thúc); sự kiện không đặt hạn thì mở đăng ký tới khi kết thúc. Một số sự kiện chỉ mở cho thành viên của ban nhất định.
4. Đổi ý thì bấm **Hủy đăng ký**. Không hủy được nữa khi đã điểm danh.

### 4.2. Điểm danh bằng mã QR

1. Tại sự kiện, BĐH chiếu mã QR. Bạn quét bằng điện thoại **đang đăng nhập** FIA Workspace.
2. Trang **Xác nhận điểm danh** mở ra → bấm xác nhận.
3. Điều kiện:
   - Bạn đã đăng ký sự kiện.
   - Đang trong khoảng từ 30 phút trước giờ bắt đầu đến giờ kết thúc.
   - Mã QR còn hạn (mỗi mã dùng được 15 phút).
4. Quét trước giờ bắt đầu được tính **đúng giờ**, sau giờ bắt đầu được tính **đi muộn**. Mỗi người chỉ điểm danh một lần.

Đúng giờ và đi muộn đều tính là **có mặt** khi xếp loại hoạt động; vắng có phép không bị tính (xem [mục 4.5](#45-tình-trạng-hoạt-động)).

### 4.3. Đánh giá sự kiện

Sau khi sự kiện **kết thúc**, người đã tham gia có thể chấm 1–5 sao kèm nhận xét rồi bấm **Lưu đánh giá**. Có thể sửa lại đánh giá sau.

### 4.4. Công việc (task) của bạn

1. Vào **Công việc** để xem các task được giao cho bạn, dạng bảng (Kanban) hoặc danh sách. Bấm vào bộ lọc trạng thái để lọc.
2. Mỗi task có tên, mô tả, sự kiện, ban, hạn chót. Bạn nhận email khi được giao task và trước hạn 7 ngày.
3. Làm xong, mở task → điền nội dung hoặc đường link sản phẩm → **Nộp sản phẩm**. Trạng thái chuyển sang **Chờ duyệt**.
   - Nộp lại nội dung giống hệt lần trước thì trạng thái giữ nguyên.
4. BĐH duyệt rồi chuyển trạng thái:
   - **Hoàn thành**: task xong.
   - **Làm lại**: kèm nhận xét. Bạn sửa rồi nộp lại.
   - **Hủy**: task không còn cần, không tính vào tỉ lệ hoạt động.

**Vòng đời task:**
```
Đang làm ──nộp──► Chờ duyệt ──► Hoàn thành
   ▲                 │
   └──── Làm lại ◄───┘          (BĐH có thể Hủy ở bất kỳ bước nào trước khi hoàn thành)
```

### 4.5. Tình trạng hoạt động

Mỗi kỳ (Spring, Summer, Fall…), hệ thống chấm cho mỗi thành viên một **điểm từ 0 đến 100**:

> **Điểm = 50% tỉ lệ task hoàn thành + 50% tỉ lệ có mặt ở sự kiện**

- **Tỉ lệ task** = task hoàn thành / task được giao. Task bị hủy không tính.
- **Tỉ lệ có mặt** = số sự kiện bạn có mặt (đúng giờ hoặc đi muộn) / số sự kiện bạn phải tham gia. Sự kiện phải tham gia là sự kiện mở cho mọi người hoặc cho ban của bạn. Vắng có phép không tính; không đăng ký hoặc không điểm danh tính là vắng.
- Chỉ tính các sự kiện **đã kết thúc** trong kỳ. Nếu bạn chưa có task nào thì điểm chỉ tính theo điểm danh, và ngược lại.

| Điểm | Xếp loại |
|---|---|
| từ 80 | Tích cực |
| từ 50 | Hoạt động |
| trên 0 | Ít hoạt động |
| 0 | Không hoạt động |
| chưa có task và chưa có sự kiện phải tham gia, hoặc kỳ bạn mới vào CLB | Chưa xếp loại |
| BĐH ghi bạn đang tạm nghỉ | Tạm nghỉ |

Điểm được tính lại khi sự kiện kết thúc (BĐH bấm, hoặc hệ thống tự kết thúc 24 giờ sau giờ kết thúc), khi sự kiện được mở lại, hoặc khi sửa điểm danh của sự kiện đã kết thúc. Xem điểm của bạn ở **Hồ sơ**, hoặc hỏi Trợ lý AI "Tôi có tích cực không?".

---

## 5. Quỹ câu lạc bộ

Trang **Quỹ câu lạc bộ** gồm:

- **Khoản thu**: các khoản bạn phải đóng (quỹ tháng, quỹ kỳ, phí sự kiện…), kèm số phải đóng, đã đóng, còn thiếu và trạng thái (đã đóng / đóng một phần / chưa đóng / quá hạn / được miễn).
- **Đóng tiền**: ở khoản thu cần đóng, chọn phương thức (tiền mặt / chuyển khoản / khác), mã giao dịch (nếu chuyển khoản) → **Gửi xác nhận thanh toán**. Không cần nhập số tiền: hệ thống tự lấy toàn bộ số còn phải đóng. Giao dịch ở trạng thái **chờ xác nhận** cho tới khi Thủ quỹ duyệt.
  - Đã gửi xác nhận đủ số tiền thì không gửi thêm được cho tới khi Thủ quỹ xử lý.
- **Danh sách đóng quỹ theo kỳ**: bạn chỉ thấy dòng của mình.
- Nút **Xuất Excel**: thành viên chỉ nhận dữ liệu của chính mình.

---

## 6. Trợ lý AI

Vào **Trợ lý AI**. Trợ lý trả lời các câu hỏi về dữ liệu CLB **trong phạm vi quyền của bạn**. Số liệu được lấy trực tiếp từ database, AI không tự bịa ra.

### 6.1. Cuộc trò chuyện

- **Chat mới**: tạo cuộc trò chuyện riêng. Tên tự đặt theo câu hỏi đầu tiên.
- **Tùy chọn**: đổi tên hoặc **Lưu trữ cuộc trò chuyện**. Lưu trữ chỉ ẩn đi, không xóa. Mục **Đã lưu trữ** cho phép mở lại và **Khôi phục**.
- Lịch sử tải 40 tin/trang, có liên kết xem tin cũ hơn và mới hơn.
- Khung **Tiến trình xử lý** cho thấy hệ thống đang làm bước nào (gọi model, tra cứu, kiểm tra). Đây không phải "suy nghĩ" của AI.
- Mỗi người được gửi tối đa khoảng 20 câu/phút.

### 6.2. Một số câu hỏi mẫu

| Bạn hỏi | Trợ lý trả lời |
|---|---|
| `Task của tôi đang làm là gì?` | Danh sách task đang làm của bạn |
| `Tóm tắt dữ liệu của tôi` | Hồ sơ hiện tại của bạn |
| `Tôi còn khoản quỹ nào chưa đóng?` | Các khoản chưa đóng hoặc quá hạn của bạn |
| `Các sự kiện sắp diễn ra` | Sự kiện sắp tới |
| `Ban Kỹ thuật có bao nhiêu thành viên?` / `Ban Truyền thông gồm những ai?` | Số lượng và danh sách thành viên của ban |
| `Tôi có tích cực không?` | Xếp loại và điểm của bạn trong kỳ, kèm số task hoàn thành và số sự kiện có mặt |
| `Tôi đã đăng ký FIA CTF Fall chưa?` | Tình trạng đăng ký và điểm danh của bạn ở sự kiện đó |
| `Sự kiện nào đang diễn ra?` / `Các sự kiện đã diễn ra gần đây` | Danh sách sự kiện theo thời gian |

Dưới mỗi câu trả lời có dòng chú thích nguồn: *Model chọn công cụ và viết câu trả lời…* nghĩa là AI đang hoạt động; *Quy tắc dự phòng* kèm lý do (vd. chưa cấu hình AI, không gọi được model) nghĩa là lượt đó không dùng AI nhưng số liệu vẫn lấy từ database.

Khi câu hỏi nằm ngoài phạm vi hỗ trợ, trợ lý nói rõ là chưa hỗ trợ thay vì đoán. Không nên dùng câu trả lời AI làm căn cứ duy nhất cho quyết định quan trọng.

---

## 7. Ban điều hành: ai được làm gì

| Quyền | Quản trị hệ thống | Chủ nhiệm | Phó CN | Trưởng ban | Thư ký/Thủ quỹ | Thành viên |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Xem danh sách chờ và duyệt đăng ký | ✔ | ✔ | ✔ | – | – | – |
| Quản lý thành viên (xem hồ sơ đầy đủ, sửa, lưu trữ, nhập file, cộng/trừ điểm, cấp mật khẩu tạm) | ✔ | ✔ | ✔ | ✔ | – | – |
| Sự kiện và task (tạo, giao, duyệt, điểm danh, đề xuất AI, mẫu sự kiện/task trong Kho mẫu) | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| Quỹ (mở kỳ, ghi thu chi, khoản thu, xác nhận thanh toán) | ✔ | ✔ | ✔ | – | ✔ | – |
| Phân vai trò cho người khác | ✔** | ✔ | ✔* | – | – | – |
| Nhường chức vụ của bản thân | – | ✔ | ✔ | ✔ | ✔ | – |
| Báo cáo tổng hợp, nháp email AI | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| Mẫu email trong Kho mẫu (kể cả email tự động) | ✔ | ✔ | ✔ | – | – | – |

\* Phó chủ nhiệm chỉ gán được vai trò **thấp hơn** mình (Trưởng ban, Thư ký/Thủ quỹ, Thành viên). PCN không gán được Phó chủ nhiệm hay Chủ nhiệm, và không đổi được vai trò của Chủ nhiệm hay Phó chủ nhiệm khác.

\*\* **Quản trị hệ thống** là tài khoản đầu tiên, do người cài đặt tạo bằng lệnh `flask create-admin` (xem [mục 7.1](#71-tài-khoản-quản-trị-hệ-thống)). Đây là tài khoản duy nhất gán được **Chủ nhiệm**.

### 7.1. Tài khoản Quản trị hệ thống

- Dùng để **khởi động CLB trên hệ thống**: duyệt những đơn đăng ký đầu tiên rồi gán Chủ nhiệm, Phó chủ nhiệm, Trưởng ban, Thư ký/Thủ quỹ. Tài khoản này **không phải Chủ nhiệm** và không phải thành viên CLB: không xuất hiện trong danh sách thành viên, không đóng quỹ, không nhận task, không nhận email (không có hộp thư).
- Đăng nhập bằng **tên đăng nhập** (vd `admin`) thay cho MSSV.
- Không ai gán được, đổi được hay nhường được vai trò Quản trị hệ thống qua giao diện.
- Quên mật khẩu: người quản lý máy chủ chạy `flask --app run.py reset-admin-password`.
- Khi BĐH đã đủ người, nên hạn chế dùng tài khoản này. Chỉ dùng khi cần gán lại Chủ nhiệm, ví dụ Chủ nhiệm cũ rời CLB mà chưa kịp nhường chức.

Số người tối đa mỗi chức vụ: 1 Chủ nhiệm, 2 Phó chủ nhiệm, 3 Trưởng ban, 1 Thư ký/Thủ quỹ. Hệ thống không cho hạ chức hay lưu trữ **thành viên BĐH cuối cùng**, để CLB không bị mất quyền quản trị.

Nút nào không thuộc quyền của bạn sẽ bị ẩn. Kể cả khi gửi request trực tiếp, máy chủ vẫn kiểm tra lại quyền và trả lỗi 403.

---

## 8. Duyệt thành viên và quản lý hồ sơ

### 8.1. Duyệt đơn đăng ký *(Quản trị hệ thống, Chủ nhiệm, Phó chủ nhiệm)*

1. Vào **ĐIỀU HÀNH → Phê duyệt**. Mỗi đơn hiển thị họ tên, MSSV, ngày sinh, SĐT, email, ban đăng ký, Facebook và ngày nộp.
2. Kiểm tra thông tin (gợi ý: đối chiếu Facebook, MSSV; xem email đã xác minh chưa).
3. Bấm **Duyệt** hoặc **Từ chối** → xác nhận trong hộp thoại.
   - **Duyệt**: tài khoản dùng được ngay, được thêm vào danh sách đóng quỹ của kỳ hiện tại, nhận email thông báo.
   - **Từ chối**: nhận email thông báo, không đăng nhập được nữa.
4. Nếu gửi email thất bại (SMTP chưa cấu hình), hệ thống vẫn lưu quyết định và hiện cảnh báo đỏ. Khi đó hãy báo trực tiếp cho thành viên.

### 8.2. Quản lý hồ sơ thành viên *(Chủ nhiệm, Phó chủ nhiệm, Trưởng ban)*

- Trang **Thành viên**: lọc, sắp xếp, **Xuất danh sách** Excel kèm SĐT, email, tình trạng hoạt động.
- Bấm vào một thành viên để xem hồ sơ đầy đủ.
- Qua REST API (dành cho người biết dùng API): sửa tên/SĐT/ghi chú/tình trạng, lưu trữ thành viên rời CLB, nhập danh sách từ CSV/XLSX. Xem `docs/DEVELOPER_GUIDE.md` mục REST API.
- Thành viên tạo qua file nhập sẽ bị **bắt đổi mật khẩu** ở lần đăng nhập đầu tiên.

### 8.3. Cấp mật khẩu tạm

Dùng khi thành viên quên mật khẩu mà không nhận được email đặt lại.

1. Mở hồ sơ thành viên → phần **Hỗ trợ đăng nhập** → **Cấp mật khẩu tạm**. Có thể phải nhập lại mật khẩu của bạn.
2. Mật khẩu tạm **chỉ hiển thị một lần**. Hãy gửi cho thành viên qua kênh riêng tư (tin nhắn trực tiếp), không đăng lên nhóm chung.
3. Khi đăng nhập bằng mật khẩu tạm, thành viên bắt buộc đặt mật khẩu mới.
4. Chỉ cấp được cho người **có cấp thấp hơn** bạn. Ví dụ Trưởng ban không cấp được cho Chủ nhiệm hay Thư ký/Thủ quỹ.

---

## 9. Vai trò, phân quyền và nhường chức vụ

Vào **ĐIỀU HÀNH → Vai trò & phân quyền**.

### 9.1. Gán chức vụ *(Quản trị hệ thống, Chủ nhiệm, Phó chủ nhiệm)*

1. Ở dòng của thành viên, bấm **Gán chức vụ**. Nút chỉ hiện với người có cấp thấp hơn bạn.
2. Chọn **Chức vụ mới**. Nếu chọn **Trưởng ban** thì phải chọn thêm **Ban quản lý**.
3. Bấm **Lưu chức vụ**. Hệ thống kiểm tra cấp bậc, số lượng tối đa và quy tắc "không hạ BĐH cuối cùng".
4. Chủ nhiệm và Phó chủ nhiệm không gán được **Chủ nhiệm**. Giữa các thành viên, Chủ nhiệm chỉ thay đổi qua quy trình nhường chức vụ. Riêng **Quản trị hệ thống** gán được Chủ nhiệm (lần đầu, hoặc khi Chủ nhiệm cũ không còn).

### 9.2. Nhường chức vụ *(mọi chức vụ BĐH)*

Dùng khi bàn giao nhiệm kỳ.

1. Bấm **Nhượng chức vụ** (góc phải trên).
2. Chọn **Người nhận**: phải là thành viên thường đã được duyệt. Nếu bạn là Trưởng ban, chọn thêm ban sẽ bàn giao.
3. Bấm **Xác nhận chuyển giao**. Người nhận giữ chức vụ của bạn, còn bạn trở thành **thành viên thường** ngay lập tức.

> ⚠️ Thao tác này không tự hoàn tác được. Muốn lấy lại chức vụ, phải nhờ người có quyền gán lại.

---

## 10. Quản lý sự kiện, task và điểm danh

*(Chủ nhiệm, Phó chủ nhiệm, Trưởng ban, Thư ký/Thủ quỹ)*

### 10.1. Tạo sự kiện

1. **Sự kiện → Tạo sự kiện** (hoặc nút ở trang Tổng quan).
2. Điền:
   - **Tên sự kiện**.
   - **Mã sự kiện**: chữ, số và `- _ .`, không trùng.
   - **Thời gian bắt đầu**: phải ở tương lai theo giờ CLB.
   - **Thời gian kết thúc**: sau giờ bắt đầu.
   - **Địa điểm**, **Nội dung**.
   - **Hạn đăng ký** *(tùy chọn)*: phải nằm trong khoảng giờ bắt đầu – giờ kết thúc (thời gian sự kiện gồm cả setup và báo cáo). Để trống thì mở đăng ký tới khi sự kiện kết thúc.
   - Sự kiện không giới hạn số người tham gia.
3. Bấm **Tạo sự kiện**, sau đó thêm task con ở trang chi tiết.

**Nhập từ CTFtime**: ở tab CTFtime, bấm **Tạo sự kiện FIA** trên cuộc thi muốn tham gia. Hệ thống xác minh thông tin với nguồn, đổi giờ về giờ CLB và chống tạo trùng. Lưu ý đây **không phải** đăng ký đội thi trên CTFtime.

### 10.2. Giao và duyệt task

1. Ở trang chi tiết sự kiện → **Giao nhiệm vụ mới**. Nhập tên task (≤150 ký tự), mô tả, **MSSV người phụ trách**, **ban** (người phụ trách phải thuộc ban đó) và **hạn** (không sau giờ kết thúc sự kiện).
2. Người phụ trách nhận email. Đổi người dùng **Chỉ định lại**.
3. Khi task ở trạng thái **Chờ duyệt**, xem sản phẩm ở khung **Sản phẩm / nhận xét**, ghi **Nhận xét** trước (bắt buộc khi yêu cầu làm lại), rồi chọn **Quyết định**: **Hoàn thành**, **Yêu cầu làm lại** hoặc **Hủy** → **Cập nhật**.
4. Sự kiện đã kết thúc hoặc đã hủy thì không sửa task được nữa.

### 10.3. Điểm danh

- Trang chi tiết sự kiện có khung **Điểm danh sự kiện** với mã QR (15 phút; tải lại trang để lấy mã mới). Chiếu mã cho thành viên quét.
- **Điểm danh thủ công**: ở bảng người tham gia, chọn Có mặt / Đi muộn / Có phép / Vắng, ghi chú nếu cần → **Lưu**.
- Mẹo: mở web bằng địa chỉ mà điện thoại truy cập được (tên miền thật). Mã QR sinh từ `127.0.0.1` chỉ dùng được trên chính máy đó.

### 10.4. Trạng thái sự kiện: tự động và chuyển tay

Sự kiện có 4 trạng thái: **Sắp diễn ra**, **Đang diễn ra**, **Đã kết thúc**, **Đã hủy**. Hệ thống tự chuyển theo giờ, BĐH vẫn chuyển tay được bất cứ lúc nào.

**Hệ thống tự chuyển** (rà soát 5 phút/lần và mỗi khi có người mở web):
- Tới giờ bắt đầu → **Đang diễn ra**.
- Qua giờ kết thúc → nhãn **Chờ kết thúc** (vẫn duyệt task, sửa điểm danh được).
- 24 giờ sau giờ kết thúc → **Đã kết thúc**. Thời gian chờ đổi được bằng `EVENT_AUTO_CLOSE_HOURS`.

Trang chi tiết sự kiện ghi rõ giờ hệ thống sẽ tự chuyển.

**BĐH chuyển tay** (nút ở góc phải trang chi tiết sự kiện):

| Đang ở | Nút | Dùng khi |
|---|---|---|
| Sắp diễn ra | **Bắt đầu ngay** | Sự kiện bắt đầu sớm hơn giờ đã đặt |
| Sắp diễn ra / Đang diễn ra | **Kết thúc sự kiện** | Đã xong, muốn chốt ngay không chờ 24 giờ |
| Sắp diễn ra / Đang diễn ra | **Hủy sự kiện** | Sự kiện không tổ chức nữa (không hủy được khi đã có người điểm danh) |
| Đã kết thúc | **Mở lại sự kiện** | Chốt nhầm, cần duyệt thêm task hoặc sửa điểm danh. Hệ thống tự kết thúc lại sau 24 giờ tính từ lúc mở. |

Muốn dời sự kiện thì sửa giờ của sự kiện; trạng thái tự tính lại theo giờ mới.

Khi sự kiện **kết thúc** (tự động hay bấm tay) hoặc được **mở lại**, hệ thống tính lại tình trạng hoạt động (task + điểm danh) của mọi thành viên trong kỳ. Sau khi kết thúc: task bị khóa, thành viên đánh giá được sự kiện, điểm danh vẫn sửa được và được tính lại.

---

## 11. Quản lý quỹ

*(Chủ nhiệm, Phó chủ nhiệm, Thư ký/Thủ quỹ)*

Hệ thống có **hai cách theo dõi quỹ** dùng song song:

| | Quỹ theo kỳ (sổ thu chi) | Khoản thu (thu theo đợt) |
|---|---|---|
| Dùng cho | Sổ thu/chi tổng của CLB trong kỳ và đánh dấu ai đã đóng quỹ kỳ | Từng khoản thu cụ thể (quỹ tháng, phí sự kiện…) có số tiền và hạn |
| Thao tác | Mở kỳ, ghi thu/chi, đánh dấu đã đóng | Tạo khoản thu, thành viên gửi thanh toán, Thủ quỹ xác nhận |

Các thao tác nằm trong nút **Thao tác** ở góc phải trang Quỹ: Mở kỳ mới, Ghi nhận thu / chi, Xuất Excel (kỳ hiện tại), Xuất báo cáo khoản thu.

### 11.1. Quỹ theo kỳ

1. **Mở kỳ mới**: nhập tên kỳ (vd: Fall 2026), ngày bắt đầu, ngày kết thúc (nên nhập để hệ thống nhắc trước 30 ngày). Mở kỳ mới sẽ **tự đóng kỳ cũ** và tạo dòng "chưa đóng" cho mọi thành viên đang hoạt động.
2. **Ghi nhận thu / chi**: danh mục (≤100 ký tự), nội dung, ngày, số tiền. Khoản thu nhập số dương, khoản chi nhập số âm (vd: `-120000`).
3. **Danh sách đóng quỹ**: bấm **Đánh dấu đã đóng** / **Đánh dấu chưa đóng** cho từng người.
4. **Nhắc đóng quỹ**: gửi email cho mọi người chưa đóng. Hệ thống báo đã gửi được bao nhiêu thư.
5. **Xuất Excel**: sheet "Thu chi" và "Đóng quỹ".

### 11.2. Khoản thu và xác nhận thanh toán

1. **Tạo khoản thu**: tên, loại (**Quỹ theo kỳ** / **Quỹ sự kiện** / **Khác**), số tiền, ngày bắt đầu, hạn đóng, mô tả → **Mở khoản thu**.
2. Thành viên gửi thanh toán, giao dịch hiện ở **Giao dịch chờ xác nhận**.
3. Kiểm tra (đối chiếu sao kê, mã giao dịch) → **Xác nhận** hoặc **Từ chối**. Nếu hai người cùng duyệt một giao dịch, chỉ lượt đầu được ghi nhận.
4. **Xuất báo cáo khoản thu** (trong nút Thao tác): file Excel phải đóng / đã đóng / còn thiếu của từng người với từng khoản thu.
5. Miễn/giảm cho từng thành viên và đóng/hủy khoản thu hiện thực hiện qua REST API (xem `docs/DEVELOPER_GUIDE.md`, mục REST API).

---

## 12. Trợ lý AI cho Ban điều hành

### 12.1. Tóm tắt và báo cáo

- **Tóm tắt hôm nay và báo cáo có nguồn**: task quá hạn hoặc sắp hạn, sự kiện 7 ngày tới, khoản thu quá hạn, đề xuất và nháp email đang chờ.
- **Xem trước báo cáo tuần/tháng** → **Lưu bản xem trước** để cố định số liệu. Từ bản đã lưu, có thể **Tải Excel** đúng bản đó hoặc **Tạo nháp email báo cáo cho tôi**.
- Có thể hỏi trong chat: `Hôm nay cần xử lý gì?`, `Báo cáo tuần này`, `Báo cáo tháng này`.
- Thống kê nhanh trong chat:
  - `Có bao nhiêu thành viên tích cực?`, `Liệt kê thành viên ít hoạt động`, `Ai đang tạm nghỉ?` *(quyền Quản lý thành viên)*
  - `Có bao nhiêu thành viên trong sự kiện sắp diễn ra?`, `Ai vắng ở sự kiện vừa rồi?`, `Bao nhiêu người đăng ký CTF-FALL?` *(quyền Sự kiện & task hoặc BĐH)*

  "Sự kiện gần nhất/vừa rồi" khi hỏi vắng/có mặt là sự kiện gần nhất **đã điểm danh**; câu trả lời luôn ghi tên sự kiện được chọn. Câu hỏi kèm mốc ngày/tháng hoặc nhiều ban/sự kiện cùng lúc sẽ được yêu cầu hỏi lại cho rõ.

### 12.2. Đề xuất sự kiện và task *(người có quyền Sự kiện & task)*

AI chỉ tạo **đề xuất (bản xem trước)**. Không có gì được ghi vào dữ liệu thật cho tới khi bạn bấm **Duyệt và áp dụng**.

- Nói tự nhiên, ví dụ:
  - `Tạo sự kiện Workshop bảo mật ngày 15/11/2026 09:00 đến 15/11/2026 11:00`
  - `Giao task thiết kế poster cho SV123 trong sự kiện Workshop bảo mật ban Truyền thông hạn 14/11/2026 18:00`

  Nếu thiếu thông tin, AI hỏi lại (giữ tối đa 30 phút, gõ `hủy` để bỏ).
- Hoặc dùng cú pháp cố định:
  - `Đề xuất sự kiện: Workshop bảo mật | 2026-11-01 09:00 | 2026-11-01 11:00 | 1`
  - `Chia task: 12 | 1 | 2026-11-01 08:00 | Chuẩn bị tài liệu; Kiểm tra thiết bị | python, mạng`
- Chỉ ghi ngày/tháng thì hệ thống hiểu là **năm hiện tại**. Luôn đọc lại bản xem trước trước khi duyệt.
- Ở form **Đề xuất sự kiện hoặc chia task** có ô **Mẫu áp dụng**: để *Tự động* hoặc chọn một mẫu trong Kho mẫu. Đề xuất sự kiện mới mà bỏ trống giờ kết thúc thì hệ thống lấy **thời lượng của mẫu**.
- AI chọn khung cho đề xuất theo thứ tự (xem thêm [mục 12.4](#124-kho-mẫu)):
  1. Mẫu bạn chọn.
  2. Mẫu trong kho có **tên hoặc từ khóa** xuất hiện trong mô tả, hoặc cùng **loại sự kiện**.
  3. **Sự kiện tương tự** đã có (cùng loại hoặc tên gần giống): lấy nội dung / danh sách task của sự kiện đó làm khung.
  4. **Mẫu chung**.

  Có model AI thì AI điều chỉnh khung theo mô tả của bạn. Không có model thì: mô tả liệt kê nhiều task (ngăn bằng `;` hoặc xuống dòng) → dùng đúng danh sách đó; mô tả một dòng → dùng mẫu nếu bạn chọn mẫu hoặc mô tả chứa tên/từ khóa mẫu, ngược lại tạo đúng 1 task theo mô tả. Bản xem trước luôn ghi rõ **Khung** đã dùng.

### 12.3. Nháp email

- Trong chat: `Gửi mail cho SE201256: nhắc nộp báo cáo trước thứ Sáu` hoặc `Gửi mail mẫu nhắc đóng quỹ cho Nguyễn Văn A`. AI chỉ tạo **nháp**.
- Cách AI soạn nội dung:
  1. Bạn nhắc tên mẫu ("mail mẫu …"), hoặc mục đích chứa **tên / từ khóa** của một mẫu email trong Kho mẫu → dùng mẫu đó. Phần sau dấu `:` được điền vào biến `{{noi_dung}}`.
  2. Không có mẫu phù hợp → dùng **Mẫu chung**. Có model AI thì AI viết lại theo khung mẫu chung cho đúng mục đích; không có model thì điền thẳng mục đích vào mẫu chung.
  3. Bạn yêu cầu "mail mẫu" mà kho chưa có mẫu nào khớp → AI **không tạo nháp**, chỉ liệt kê các mẫu đang có (hoặc nhắc thêm mẫu ở Kho mẫu).
- Ở mục **Nháp email chờ BĐH duyệt**: sửa tiêu đề/nội dung nếu cần → **Duyệt và gửi** hoặc **Từ chối**. Gửi lỗi thì có nút **Thử gửi lại**.
- Nếu việc gửi bị gián đoạn giữa chừng, hãy kiểm tra hộp thư đã gửi rồi chọn **Đã gửi (đã kiểm tra)** hoặc **Chưa gửi (đã kiểm tra)**. Hệ thống không tự gửi lại để tránh gửi trùng.

### 12.4. Kho mẫu

Vào **ĐIỀU HÀNH → Kho mẫu**. Có 3 tab:

| Tab | Ai dùng được | Nội dung |
|---|---|---|
| **Mẫu email** | Quản trị hệ thống, Chủ nhiệm, Phó chủ nhiệm | **Mẫu email hệ thống**: nội dung các email tự động (xác minh email, đặt lại mật khẩu, duyệt/từ chối thành viên, giao/chỉ định/duyệt task, có sản phẩm chờ duyệt, nhắc deadline, nhắc kỳ quỹ, nhắc đóng quỹ) và **Mẫu chung** cho AI. Chỉ sửa được tiêu đề và nội dung, có nút **Khôi phục nội dung mặc định**. **Mẫu email của CLB**: tự tạo, có tên, từ khóa, mô tả, tiêu đề, nội dung; bật/tắt được |
| **Mẫu sự kiện** | Người có quyền Sự kiện & task | Tên mẫu, loại sự kiện, từ khóa, tên gợi ý, thời lượng (giờ), địa điểm, nội dung / chương trình |
| **Mẫu chia task** | Người có quyền Sự kiện & task | Tên mẫu, loại sự kiện, từ khóa, danh sách task (mỗi dòng `Tên task \| mô tả`, tối đa 10) |

- **Biến trong mẫu email**: mỗi mẫu hệ thống ghi rõ các biến được dùng (vd `{{ten}}`, `{{link}}`, `{{task}}`, `{{han}}`). Mẫu của CLB dùng `{{ten}}`, `{{mssv}}`, `{{email}}`, `{{noi_dung}}`. Dùng biến khác thì hệ thống báo lỗi, không lưu.
- **Từ khóa**: ngăn bằng dấu phẩy. Có dấu hay không dấu đều khớp ("đóng quỹ" khớp "dong quy").
- **Mẫu chung**: tích ô *Đặt làm mẫu chung* để dùng khi không tìm thấy mẫu phù hợp hơn. Mỗi loại chỉ có một mẫu chung.
- Sửa hoặc tắt mẫu **không làm thay đổi** các nháp email và đề xuất đã tạo trước đó.

---

## 13. Câu hỏi thường gặp và xử lý sự cố

| Tình huống | Cách xử lý |
|---|---|
| "MSSV hoặc mật khẩu không đúng" dù nhập đúng | Có thể tài khoản đang bị khóa tạm do sai nhiều lần. Chờ 5 phút rồi thử lại, hoặc dùng **Quên mật khẩu**. Tài khoản bị từ chối hoặc đã rời CLB cũng không đăng nhập được. |
| Bị đưa về trang "Đổi mật khẩu" liên tục | Bạn đang dùng mật khẩu tạm. Hãy đặt mật khẩu mới. |
| Bị yêu cầu nhập lại mật khẩu | Thao tác nhạy cảm khi đang đăng nhập bằng "Ghi nhớ". Nhập mật khẩu để tiếp tục. |
| "Phiên biểu mẫu không hợp lệ. Hãy tải lại trang." | Trang mở quá lâu hoặc đã đăng xuất ở tab khác. Tải lại trang (F5) rồi làm lại. |
| "Quá nhiều yêu cầu. Vui lòng thử lại sau." (lỗi 429) | Bạn thao tác quá nhanh. Chờ khoảng 1 phút. |
| Không nhận được email | Kiểm tra thư mục Spam. Nếu CLB chưa bật gửi email, hãy liên hệ BĐH. |
| Không thấy nút Gán chức vụ / Phê duyệt / Kho mẫu | Chức vụ của bạn không có quyền đó (xem [mục 7](#7-ban-điều-hành-ai-được-làm-gì)). |
| Đăng ký báo "MSSV không đúng định dạng" | MSSV phải có dạng `SE201256` (chữ campus H/D/Q/C/S + chữ ngành A/S/E + 6 số). |
| Đăng ký báo lỗi Facebook | Chỉ nhập userID sau `facebook.com/` (chữ, số, dấu chấm), không dán link `www.` / `m.` / `fb.com`. |
| Quét QR báo "Chỉ được điểm danh trong thời gian diễn ra sự kiện" | Chỉ quét được từ 30 phút trước giờ bắt đầu đến giờ kết thúc. |
| Quét QR báo "Mã QR đã hết hạn" | Nhờ BĐH tải lại trang để lấy mã mới. |
| Trang báo lỗi kèm "Mã hỗ trợ: …" | Gửi mã này cho người quản trị để tra nhật ký lỗi. |

**Giữ an toàn tài khoản:** không chia sẻ mật khẩu, không dùng chung mật khẩu với tài khoản khác, đăng xuất trên máy dùng chung, và báo ngay cho BĐH nếu thấy hoạt động lạ.
