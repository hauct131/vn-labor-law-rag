# ERD tài khoản, session, hội thoại và bookmark

```mermaid
erDiagram
    APP_USER ||--o{ USER_SESSION : has
    APP_USER ||--o{ CONVERSATION : owns
    CONVERSATION ||--o{ MESSAGE : contains
    MESSAGE ||--o{ MESSAGE_SOURCE : cites
    APP_USER ||--o{ BOOKMARK : creates
    MESSAGE ||--o{ BOOKMARK : saved_as

    APP_USER {
        varchar36 id PK
        varchar320 email UK
        varchar255 password_hash
        varchar120 display_name
        boolean is_active
        boolean is_anonymous
        timestamptz created_at
        timestamptz updated_at
    }

    USER_SESSION {
        varchar36 id PK
        varchar36 user_id FK
        varchar64 token_hash UK
        varchar64 csrf_token_hash
        timestamptz created_at
        timestamptz expires_at
        timestamptz last_seen_at
        timestamptz revoked_at
    }

    CONVERSATION {
        varchar36 id PK
        varchar36 user_id FK
        varchar160 title
        timestamptz created_at
        timestamptz updated_at
    }

    MESSAGE {
        varchar36 id PK
        varchar36 conversation_id FK
        varchar16 role
        text content
        varchar200 corpus_release_id
        varchar16 retrieval_method
        float retrieval_ms
        float generation_ms
        float total_ms
        varchar300 model
        boolean insufficient_evidence
        boolean out_of_scope
        boolean generation_failed
        timestamptz created_at
    }

    MESSAGE_SOURCE {
        varchar36 id PK
        varchar36 message_id FK
        varchar50 source_id
        varchar200 chunk_id
        varchar200 article_code
        varchar500 citation_label
        jsonb point_labels
        text quoted_text
        float score
        int source_rank
        text source_url
        jsonb component_ranks
    }

    BOOKMARK {
        varchar36 id PK
        varchar36 user_id FK
        varchar36 message_id FK
        text note
        timestamptz created_at
    }
```

## Giải thích khi trình bày

`APP_USER` là tài khoản nghiệp vụ. Email được chuẩn hóa và đặt unique; mật khẩu
chỉ lưu dưới dạng PBKDF2 hash. Các dòng ẩn danh cũ được giữ tạm với
`is_anonymous=true` để có thể chuyển lịch sử vào tài khoản khi người dùng đăng
ký hoặc đăng nhập lần đầu.

`USER_SESSION` lưu phiên đăng nhập phía server. Trình duyệt chỉ giữ session token
ngẫu nhiên trong cookie `HttpOnly`; PostgreSQL chỉ giữ SHA-256 hash của token.
Một tài khoản có thể có nhiều session tương ứng nhiều trình duyệt hoặc thiết bị.
Logout đặt `revoked_at`, nên session bị thu hồi ngay.

Một tài khoản có nhiều hội thoại; một hội thoại có nhiều message. Message
assistant có thể có nhiều snapshot nguồn và chỉ có tối đa một bookmark cho mỗi
người dùng nhờ unique constraint `(user_id, message_id)`.

`MESSAGE_SOURCE` lưu snapshot căn cứ đúng tại thời điểm câu trả lời được tạo. Vì
vậy lịch sử vẫn hiển thị căn cứ đã dùng ngay cả khi Qdrant được rebuild hoặc có
corpus release mới.

Corpus JSON/JSONL và Qdrant không được biến thành bảng trong ERD này:

- JSON/JSONL là release canonical bất biến;
- Qdrant là vector database và thuộc sơ đồ kiến trúc hệ thống;
- `article_code`, `chunk_id` và `corpus_release_id` là liên kết logic từ snapshot
  nguồn về corpus đã dùng.
