# FINAL BUILD REPORT — 2026-07-21

## Trạng thái

- Parser strict validation: **PASS**, 0 lỗi, 0 cảnh báo.
- Chunk strict validation: **PASS**, 0 lỗi, 0 cảnh báo.
- E5 pre-truncation strict audit: **PASS**, 1.395/1.395 chunk; 0 chunk vượt giới hạn và 0 chunk gần ngưỡng.
- Automated tests: **293 passed**; 1 cảnh báo deprecation không chặn build.
- `git diff --check`: **PASS**.
- Corpus đã sẵn sàng để index; Qdrant chưa được index lại trong bước này.

## Phiên bản và coverage

- Parser: `1.3.0`.
- Chunker: `1.1.0`.
- Tokenizer: `tiktoken:cl100k_base`.
- Điều: 477/477.
- Nguồn: 220 LQ, 174 NĐ, 83 TT.
- Đơn vị văn bản canonical: 2.679/2.679.
- Bảng canonical: 63/63.
- Attachment: 41.
- Tổng chunk: 1.395.
- Fallback segment: 39; `requires_fallback = true`: 0.
- Token lớn nhất: 750/750.

## Các sửa lỗi đã khóa bằng test và validator

1. Phụ lục HTML cấp tài liệu không còn bị gắn nhầm vào Điều đứng trước.
2. Fallback ưu tiên ranh giới câu/pháp lý, không làm vỡ từ và giữ ngữ cảnh
   Khoản/Điểm trên segment tiếp nối.
3. Subpoint `c1)`, `c2)`, `d1)` giữ đúng Điểm cha, primary provenance và
   repeated context riêng biệt.
4. Mọi segment bảng dài lặp lại header nhiều cấp và dòng mã cột.
5. Sáu bảng công thức có biểu diễn tuyến tính và liên kết hai chiều giữa chunk
   câu dẫn với table chunk.

## Audit mục tiêu

- Tổng mục review: 195.
- Fallback: 39/39.
- Chunk bảng phụ lục: 65/65.
- Chunk liên quan công thức: 11/11.
- Segment thuộc bảng nhiều đoạn: 15/15.

Chi tiết nằm trong `data/processed/chunk_quality_audit/`.

## Audit E5 pre-truncation

- Model/tokenizer: `intfloat/multilingual-e5-large`.
- Giới hạn model: 512 token.
- Corpus đã kiểm tra: **1.395/1.395 chunk**.
- Vượt giới hạn: **0**; gần ngưỡng: **0**.
- Kết luận: corpus đủ điều kiện chuyển sang bước index production.

Chi tiết nằm trong `data/processed/e5_token_audit/`.

## Giới hạn nguồn đã biết

HTML nguồn tham chiếu 4 ảnh công thức bằng đường dẫn cục bộ `file:///C:/...`
tại Điều `20.2.NĐ.3.56` và `20.2.NĐ.3.57`. Các tệp ảnh không có trong corpus,
vì vậy không thể OCR hay khôi phục công thức từ nguồn hiện có. Audit giữ cờ
`formula_cue_requires_source_check`; không tạo công thức giả hoặc liên kết bảng
không có thật.
