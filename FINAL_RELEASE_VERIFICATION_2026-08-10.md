# Xác minh bản cuối Contract Review V1 — 2026-08-10

## Kết luận

Bản sửa chạy được trên Python 3.11 với toàn bộ FastAPI application
(`app.main:app`), frontend production build và các luồng Contract Review thật qua
HTTP. Bốn lỗi đã xác nhận trong lần audit trước đã được sửa và có regression
coverage.

Đây là release candidate phù hợp để demo đồ án trong phạm vi đã công bố. Kết quả
không đồng nghĩa với độ chính xác pháp lý tuyệt đối; corpus vẫn chờ authority
review và PDF scan chưa có OCR.

## Phạm vi thay đổi

1. Chặn false positive do số hợp đồng, CCCD hoặc ngày ký:
   - đoạn phải có anchor ngữ nghĩa của nhóm điều khoản;
   - con số và độ dài chỉ được cộng điểm sau khi vượt relevance gate;
   - lương thử việc không được dùng thay cho điều khoản tiền lương chính.
2. Citation:
   - loại trùng nguồn theo `article_code`;
   - đánh lại `S1...` sau khi loại trùng;
   - mọi nguồn lưu trong finding đều được dẫn trong analysis;
   - nhóm chấm dứt có đủ Điều 34, 35 và 36 trong ca kiểm tra đại diện.
3. Docker path:
   - image đặt `APP_PROJECT_ROOT=/app`;
   - relative path của official source registry được resolve từ `/app`, không
     còn rơi về `/data/reference/...`.
4. Manifest:
   - loại metadata corpus cũ 833;
   - binding đúng Canonical Word 804 và locked retrieval config.
5. Persistence/CI:
   - PostgreSQL integration áp dụng thêm migration `003_contract_reviews.sql`;
   - có kiểm tra JSONB và cascade ba bảng Contract Review;
   - CI Compose build backend và frontend, không chỉ chạy `compose config`.

## Kết quả đã chạy trực tiếp

### Python 3.11 sạch

Runtime: CPython 3.11.15; dependency được cài mới từ
`backend/requirements.txt`.

```text
python -m compileall: PASS
pytest: 623 passed, 3 skipped, 1 warning
```

Ba test bị skip đều thuộc PostgreSQL integration vì môi trường kiểm tra không có
`TEST_POSTGRES_URL`. Warning duy nhất là deprecation từ Starlette TestClient;
không phải lỗi runtime.

### Full FastAPI qua HTTP thật

`scripts/smoke_contract_review_runtime.py` chạy Uvicorn với `app.main:app`, dùng
database SQLite file tách biệt và đạt 13/13 bước:

1. backend live;
2. đăng ký hai tài khoản và session cookie;
3. authority gate chặn report trước khi persistence;
4. từ chối 11 input không hợp lệ/không an toàn/không đủ quyền;
5. upload DOCX thật và tạo bốn finding;
6. giữ đúng quan hệ giữa con số và thuật ngữ pháp lý;
7. không tạo điều khoản từ số hợp đồng/CCCD/ngày ký;
8. tiêu đề phủ định “không có thử việc” không bị nhận nhầm là điều khoản;
9. upload PDF có text layer;
10. list và detail trả đúng dữ liệu đã lưu, tách riêng số nhóm không tìm thấy;
11. tài khoản khác không đọc/xóa chéo;
12. session và năm report còn nguyên sau restart backend;
13. chủ sở hữu xóa sạch report và danh sách trở về 0.

Nhóm 11 input bị từ chối gồm: chưa đăng nhập, CSRF sai, TXT, file rỗng, DOCX
hỏng, PDF giả, PDF scan, PDF hơn 250 trang, DOCX vượt giới hạn giải nén, file hơn
10 MB và MIME không khớp.

### Frontend

```text
npm ci: PASS
npm run lint: PASS
npm run build: PASS
npm audit --omit=dev: 0 vulnerabilities
vite preview: HTTP 200, có root element và production assets
```

### Corpus/registry

```text
Canonical chunks: 804
SHA-256: fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307
Manifest count/hash: MATCH
Official source registry: 16 records, load thành công qua APP_PROJECT_ROOT
Primary manifest files: không thiếu file
```

## Phần chưa chạy được trong môi trường bàn giao

Môi trường kiểm tra không có Docker daemon và PostgreSQL server, nên không thể
chạy tại chỗ hai bước sau:

- build/start Docker Compose;
- PostgreSQL migration integration với database thật.

Source và CI đã được bổ sung để bắt buộc hai kiểm tra này trên runner có Docker
và PostgreSQL. Trước khi gắn tag release cuối hoặc demo bằng Docker, cần chạy:

```bash
cp .env.example .env
bash scripts/verify_contract_review_e2e.sh
```

Không nên tuyên bố Docker/PostgreSQL đã PASS chỉ dựa vào báo cáo này.

## Giới hạn phải giữ trong báo cáo đồ án

- Contract Review V1 là prototype deterministic cho bốn nhóm điều khoản.
- PDF scan không có OCR.
- Finding/citation cần người có chuyên môn đối chiếu; không thay thế tư vấn pháp
  lý.
- Canonical Word 804 là technical candidate; authority review vẫn pending.
- Graph RAG không thuộc runtime hiện tại.
