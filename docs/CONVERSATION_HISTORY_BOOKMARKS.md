# Lịch sử hội thoại và bookmark

## Phạm vi

Chức năng này bổ sung lưu trữ bền vững cho dữ liệu phát sinh khi người dùng sử
dụng ứng dụng. Nó không thay đổi corpus canonical Word 804, cấu hình retrieval,
Qdrant collection hoặc production alias.

Dữ liệu được tách như sau:

| Nhóm dữ liệu | Nơi lưu | Vai trò |
|---|---|---|
| Văn bản sau tiền xử lý | JSON/JSONL release bất biến | Nguồn canonical có thể kiểm toán và rebuild |
| Embedding và metadata chunk | Qdrant | Chỉ mục vector phục vụ retrieval |
| Hội thoại, tin nhắn, nguồn đã dùng, bookmark | PostgreSQL | Dữ liệu nghiệp vụ phát sinh |
| Client UUID và hội thoại đang mở | `localStorage` | Nhận diện ẩn danh phía trình duyệt |

`X-Client-Id` chỉ là định danh ẩn danh cho MVP, không phải cơ chế xác thực. Không
lưu dữ liệu nhạy cảm và không dùng mô hình này cho hệ thống nhiều tài khoản trước
khi bổ sung đăng nhập, phiên đăng nhập và phân quyền server-side.

Hội thoại trong phiên bản này là chức năng lưu và tổ chức lịch sử. Mỗi câu hỏi
vẫn được retrieval xử lý độc lập; nội dung các message trước không được đưa vào
query hoặc prompt. Thiết kế này tránh thay đổi retrieval đã khóa.

## Luồng hoạt động

1. Frontend tạo một UUID và lưu dưới key `legal_rag_client_id_v1`.
2. Mỗi yêu cầu lịch sử gửi UUID qua header `X-Client-Id`.
3. Khi gọi `POST /api/ask`, backend sinh đáp án như trước.
4. Sau khi có đáp án, backend lưu câu hỏi, câu trả lời và snapshot nguồn trong
   một transaction.
5. Frontend tải lại hội thoại từ PostgreSQL và hiển thị transcript.
6. Bookmark liên kết người dùng ẩn danh với đúng message assistant đã lưu.

Nếu PostgreSQL gặp lỗi sau khi LLM đã sinh đáp án, backend vẫn trả đáp án với
`history_saved=false`. Nhờ đó lỗi của chức năng lịch sử không làm mất kết quả RAG
đã tạo. Khi tiếp tục một hội thoại đã có, backend kiểm tra quyền sở hữu trước khi
gọi LLM để tránh tốn lượt provider cho một conversation ID không hợp lệ.

## API

Mọi endpoint dưới đây yêu cầu header:

```http
X-Client-Id: <UUID>
```

| Method | Endpoint | Vai trò |
|---|---|---|
| `GET` | `/api/conversations` | Danh sách hội thoại của client |
| `POST` | `/api/conversations` | Tạo hội thoại rỗng |
| `GET` | `/api/conversations/{id}` | Đọc transcript và nguồn đã lưu |
| `PATCH` | `/api/conversations/{id}` | Đổi tên hội thoại |
| `DELETE` | `/api/conversations/{id}` | Xóa hội thoại và dữ liệu con |
| `GET` | `/api/bookmarks` | Danh sách câu trả lời đã đánh dấu |
| `PUT` | `/api/bookmarks/{message_id}` | Tạo/cập nhật bookmark theo kiểu idempotent |
| `DELETE` | `/api/bookmarks/{message_id}` | Bỏ bookmark |

`POST /api/ask` nhận thêm trường tùy chọn:

```json
{
  "question": "Người lao động được nghỉ hằng năm bao nhiêu ngày?",
  "method": "hybrid",
  "conversation_id": null
}
```

Response có thêm:

```json
{
  "history_saved": true,
  "history_error": null,
  "conversation_id": "...",
  "user_message_id": "...",
  "assistant_message_id": "..."
}
```

Không gửi `X-Client-Id` thì `/api/ask` vẫn tương thích với client cũ và không lưu
lịch sử.

## PostgreSQL và migration

Docker Compose khởi tạo service `postgres` và volume `postgres_data`. Môi trường
demo để `DATABASE_AUTO_CREATE=true`, vì vậy SQLAlchemy tạo các bảng còn thiếu
khi backend khởi động.

DDL tương ứng được lưu tại:

```text
backend/migrations/001_conversations_bookmarks.sql
```

Với môi trường quản trị chặt chẽ, chạy migration trước rồi đặt:

```dotenv
DATABASE_AUTO_CREATE=false
```

Ví dụ chạy migration trong Docker:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  exec -T postgres \
  sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < backend/migrations/001_conversations_bookmarks.sql
```

Không dùng `docker compose down -v` khi cần giữ lịch sử, vì tùy chọn `-v` sẽ xóa
volume `postgres_data`.

## Sao lưu dữ liệu

Ví dụ xuất database:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  exec -T postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > postgres-history-backup.sql
```

## Kiểm thử

Test tập trung không gọi LLM thật:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -q \
  backend/tests/test_conversation_history.py \
  backend/tests/test_ask_history_api.py
```

Kiểm thử runtime đầy đủ sau khi Docker Compose đã chạy:

```bash
python3 scripts/smoke_conversation_bookmarks.py --restart-backend
```

Smoke runtime tạo một client tạm, gọi một câu hỏi thật, tạo bookmark, restart
backend, kiểm tra dữ liệu PostgreSQL vẫn tồn tại, đổi tên và cuối cùng dọn toàn
bộ dữ liệu thử nghiệm.
