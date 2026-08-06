# ERD dữ liệu hội thoại và bookmark

```mermaid
erDiagram
    APP_USER ||--o{ CONVERSATION : owns
    CONVERSATION ||--o{ MESSAGE : contains
    MESSAGE ||--o{ MESSAGE_SOURCE : cites
    APP_USER ||--o{ BOOKMARK : creates
    MESSAGE ||--o{ BOOKMARK : saved_as

    APP_USER {
        varchar36 id PK
        timestamptz created_at
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

ERD trên mô tả dữ liệu nghiệp vụ được lưu trong PostgreSQL. `APP_USER` hiện là
người dùng ẩn danh được nhận diện bằng UUID của trình duyệt. Một người dùng có
nhiều hội thoại; một hội thoại có nhiều message; một message assistant có thể có
nhiều snapshot nguồn và được đánh dấu tối đa một lần cho mỗi người dùng.

`MESSAGE_SOURCE` lưu snapshot nguồn đúng tại thời điểm câu trả lời được tạo. Vì
vậy lịch sử vẫn hiển thị đúng căn cứ đã dùng ngay cả khi Qdrant được rebuild hoặc
corpus mới được phát hành sau này.

Corpus JSON/JSONL và Qdrant không được biểu diễn thành bảng trong ERD này:

- JSON/JSONL là release canonical bất biến, không phải dữ liệu quan hệ phát sinh;
- Qdrant là vector database và được thể hiện trong sơ đồ kiến trúc, không phải ERD
  quan hệ;
- `document_id`, `article_code` và `chunk_id` trong `MESSAGE_SOURCE` là khóa tham
  chiếu logic sang corpus tại `corpus_release_id` đã ghi trên message.

Khi bổ sung đăng nhập, `APP_USER` có thể mở rộng với email, password hash hoặc
liên kết OAuth mà không phải thay đổi quan hệ Conversation–Message–Bookmark.
