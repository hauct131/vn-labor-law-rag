# Lịch sử hội thoại, bookmark và phiên đăng nhập

## Phạm vi

Chức năng này lưu dữ liệu phát sinh của người dùng và phân tách dữ liệu theo tài
khoản đã đăng nhập. Nó không thay đổi corpus canonical Word 804, cấu hình
retrieval, Qdrant collection hoặc production alias.

| Nhóm dữ liệu | Nơi lưu | Vai trò |
|---|---|---|
| Văn bản sau tiền xử lý | JSON/JSONL release bất biến | Nguồn canonical có thể kiểm toán và rebuild |
| Embedding và metadata chunk | Qdrant | Chỉ mục vector phục vụ retrieval |
| Tài khoản, session, hội thoại, message, snapshot nguồn, bookmark | PostgreSQL | Dữ liệu nghiệp vụ phát sinh |
| Hội thoại đang mở và UUID ẩn danh cũ | `localStorage` | Trạng thái giao diện và di chuyển dữ liệu cũ một lần |

UUID tại `legal_rag_client_id_v1` không còn được dùng để cấp quyền truy cập hội
thoại. Nó chỉ được gửi khi đăng ký hoặc đăng nhập để chuyển lịch sử ẩn danh đã có
trước đây vào tài khoản. Các API dữ liệu cá nhân lấy `user_id` từ session phía
server.

Hội thoại chỉ lưu và tổ chức lịch sử. Retrieval của từng câu hỏi vẫn chạy độc
lập; message trước không được đưa vào query hoặc prompt, nên cấu hình retrieval
đã khóa không bị thay đổi.

## Luồng hoạt động

1. Người dùng đăng ký hoặc đăng nhập bằng email và mật khẩu.
2. Backend kiểm tra mật khẩu PBKDF2 và tạo một session token ngẫu nhiên.
3. Chỉ hash của session token được lưu trong bảng `user_sessions`.
4. Token gốc được gửi bằng cookie `legal_rag_session` có cờ `HttpOnly`.
5. Backend cấp thêm CSRF token để bảo vệ các request làm thay đổi dữ liệu.
6. Mỗi API hội thoại tra session, lấy `user_id` đã xác thực và luôn truy vấn kèm
   điều kiện sở hữu.
7. Khi `/api/ask` hoàn thành, backend lưu câu hỏi, câu trả lời và snapshot nguồn
   trong PostgreSQL nếu người dùng đã đăng nhập.
8. Người chưa đăng nhập vẫn hỏi đáp được, nhưng câu trả lời không được lưu vào
   lịch sử.

Nếu PostgreSQL gặp lỗi sau khi LLM đã sinh đáp án, backend vẫn trả đáp án với
`history_saved=false`. Khi tiếp tục một hội thoại đã có, backend kiểm tra quyền
sở hữu trước khi gọi LLM để tránh tốn lượt provider cho một conversation ID
không hợp lệ.

## API xác thực

| Method | Endpoint | Vai trò |
|---|---|---|
| `POST` | `/api/auth/register` | Tạo tài khoản và session |
| `POST` | `/api/auth/login` | Đăng nhập và tạo session mới |
| `GET` | `/api/auth/me` | Khôi phục người dùng hiện tại và CSRF token |
| `GET` | `/api/auth/sessions` | Liệt kê các session còn hiệu lực |
| `POST` | `/api/auth/logout` | Thu hồi session hiện tại |
| `POST` | `/api/auth/logout-all` | Thu hồi toàn bộ session của tài khoản |

Ví dụ đăng ký:

```http
POST /api/auth/register
Content-Type: application/json

{
  "email": "usera@example.com",
  "password": "MatKhauAnToan123",
  "display_name": "Nguyễn Văn A"
}
```

Cookie session được trình duyệt gửi tự động. Frontend không lưu session token
trong `localStorage`.

## API hội thoại và bookmark

Các endpoint đọc yêu cầu session hợp lệ. Các endpoint `POST`, `PUT`, `PATCH` và
`DELETE` còn yêu cầu header `X-CSRF-Token` khớp với session.

| Method | Endpoint | Vai trò |
|---|---|---|
| `GET` | `/api/conversations` | Danh sách hội thoại của tài khoản hiện tại |
| `POST` | `/api/conversations` | Tạo hội thoại rỗng |
| `GET` | `/api/conversations/{id}` | Đọc transcript và snapshot nguồn |
| `PATCH` | `/api/conversations/{id}` | Đổi tên hội thoại |
| `DELETE` | `/api/conversations/{id}` | Xóa hội thoại và dữ liệu con |
| `GET` | `/api/bookmarks` | Danh sách câu trả lời đã đánh dấu |
| `PUT` | `/api/bookmarks/{message_id}` | Tạo/cập nhật bookmark theo kiểu idempotent |
| `DELETE` | `/api/bookmarks/{message_id}` | Bỏ bookmark |

`POST /api/ask` nhận trường `conversation_id` tùy chọn. Người chưa đăng nhập chỉ
được gửi `conversation_id=null` và response có `history_saved=false`.

## Phân tách User A và User B

Backend không tin `user_id` hoặc `conversation_id` do frontend tự khai báo. Với
mỗi request, backend thực hiện hai bước:

```text
session cookie -> user_sessions -> authenticated user_id
```

sau đó truy vấn:

```sql
WHERE conversation_id = :conversation_id
  AND user_id = :authenticated_user_id
```

Vì vậy token session của User B không đọc, đổi tên, xóa hoặc bookmark dữ liệu của
User A. API trả `404` cho tài nguyên không thuộc tài khoản để không tiết lộ tài
nguyên đó có tồn tại hay không.

Hai người dùng chung cùng profile trình duyệt vẫn dùng cùng session đang đăng
nhập. Muốn đổi tài khoản phải đăng xuất rồi đăng nhập tài khoản khác.

## PostgreSQL và migration

Docker Compose dùng named volume:

```text
vn-labor-law-rag_postgres_data
```

Bên trong container, PostgreSQL lưu tại:

```text
/var/lib/postgresql/data
```

Xem mountpoint thật trên máy:

```bash
docker volume inspect vn-labor-law-rag_postgres_data \
  --format $'Name: {{.Name}}\nMountpoint: {{.Mountpoint}}'
```

Không chỉnh sửa trực tiếp file trong mountpoint.

DDL được chia thành:

```text
backend/migrations/001_conversations_bookmarks.sql
backend/migrations/002_session_auth.sql
```

Môi trường demo để `DATABASE_AUTO_CREATE=true`; backend tự mở rộng schema cũ và
tạo bảng còn thiếu. Với môi trường quản trị migration thủ công, chạy `001` rồi
`002`, sau đó đặt `DATABASE_AUTO_CREATE=false`.

Không dùng `docker compose down -v` khi cần giữ dữ liệu, vì `-v` xóa volume
PostgreSQL.

## Sao lưu

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  exec -T postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > postgres-session-history-backup.sql
```

## Kiểm thử

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -q \
  backend/tests/test_session_auth.py \
  backend/tests/test_conversation_history.py \
  backend/tests/test_ask_history_api.py
```

Cổng kiểm tra đầy đủ:

```bash
RUN_RUNTIME_SMOKE=1 \
BASE_REF=main \
bash scripts/verify_conversation_bookmarks.sh \
  "$HOME/Downloads/session-auth-final-verification.txt"
```

Smoke runtime tạo User A và User B, xác minh B không đọc hoặc bookmark dữ liệu A,
restart backend, kiểm tra session và dữ liệu còn tồn tại, rồi dọn tài khoản thử
nghiệm.
