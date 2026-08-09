# Rà soát hợp đồng lao động v1

## Mục tiêu

Luồng sản phẩm chạy từ trình duyệt đến FastAPI và PostgreSQL:

```text
PDF/DOCX -> trích xuất text -> nhận diện điều khoản -> truy hồi corpus canonical
-> findings có citation -> lưu PostgreSQL -> mở lại theo tài khoản
```

Chức năng không đưa hợp đồng người dùng vào Qdrant hoặc corpus pháp luật và
không lưu file nhị phân sau khi request hoàn tất.

## Phạm vi

Bốn nhóm được rà soát:

1. thử việc;
2. tiền lương và phương thức trả lương;
3. thời giờ làm việc và nghỉ ngơi;
4. chấm dứt hợp đồng và thời hạn báo trước.

Mỗi finding có mức `info`, `attention`, `warning` hoặc
`insufficient_evidence`. Nhận xét dùng ngôn ngữ thận trọng và luôn gắn với
snapshot nguồn pháp luật. Đây không phải kết luận pháp lý cuối cùng.

## Truy hồi

V1 dùng `contract_canonical_lexical_v1`, một index từ khóa chỉ đọc được dựng từ
804 canonical chunks đã khóa. Mỗi nhóm có tập điều luật lõi được ưu tiên để
tránh tài liệu đặc thù lấn át quy định chung. Điểm hiển thị vẫn là điểm lexical
thô; mức ưu tiên chỉ dùng để sắp xếp nội bộ.

Việc này không sửa `get_retriever`, Qdrant alias, golden split hoặc tham số
Sparse/Dense/Hybrid của Q&A.

## Bảo mật dữ liệu

- endpoint yêu cầu server-side session;
- request thay đổi dữ liệu yêu cầu CSRF;
- mọi truy vấn lọc bằng `user_id` lấy từ session;
- truy cập báo cáo của tài khoản khác trả `404`;
- file gốc không được lưu;
- chỉ lưu metadata, SHA-256, đoạn điều khoản dùng trong finding và snapshot nguồn.

## API

- `POST /api/contract-reviews` — multipart gồm `file` và `method=sparse`;
- `GET /api/contract-reviews`;
- `GET /api/contract-reviews/{review_id}`;
- `DELETE /api/contract-reviews/{review_id}`.

PDF phải có text layer. DOCX đọc cả paragraph và table. Giới hạn file 10 MB,
giới hạn nội dung trích xuất 200.000 ký tự.

## Persistence

Ba bảng:

- `contract_reviews`;
- `contract_review_findings`;
- `contract_review_sources`.

DDL tham khảo ở `backend/migrations/003_contract_reviews.sql`. Runtime local và
Docker dùng SQLAlchemy `create_all` theo cơ chế hiện có để tạo bảng còn thiếu.

## Kiểm chứng

`backend/tests/test_contract_reviews.py` kiểm tra DOCX thật, PDF thật, validation,
citation mapping và cách ly tài khoản.

`scripts/smoke_contract_review_runtime.py` khởi động Uvicorn thật, gọi HTTP thật,
đăng ký hai tài khoản, upload DOCX, đọc danh sách/chi tiết, kiểm tra cách ly,
restart process backend và xác nhận session/report còn tồn tại.

`scripts/verify_contract_review_e2e.sh` chạy Docker Compose và
`scripts/smoke_contract_review_docker.py`; kịch bản này còn restart PostgreSQL,
kiểm tra volume persistence rồi dọn tài khoản thử nghiệm.

## Giới hạn v1

- không OCR PDF scan;
- chưa phân tích phụ lục hoặc nhiều file trong một báo cáo;
- không thay thế luật sư hoặc authority review;
- chức danh và hoàn cảnh thực tế vẫn cần con người xác nhận;
- corpus canonical vẫn ở trạng thái hoàn thiện kỹ thuật, chờ rà soát pháp lý có thẩm quyền.
