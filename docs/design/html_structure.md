# Cấu trúc HTML — Đề mục 20.2 Lao động

## 1. File được khảo sát

- File: `data/raw/DeMuc_20.2_Lao_Dong.html`
- Encoding phát hiện: `utf-8`
- SHA-256: `ee1d819f6c5650adbadbd7ac6306ad6c08630794987759ef118b87ee64376471`
- Phiên bản parser: `1.2.0`
- Nguồn là HTML có cấu trúc, vì vậy **không sử dụng OCR**.

## 2. Thống kê tổng quan

- Tổng số điều: **477**
- Chương: **17**
- Mục: **24**
- Bảng nằm trong nội dung điều: **63**
- Tệp/phụ lục đính kèm: **39**
- Anchor điều bị thiếu: **0**
- Anchor điều bị trùng: **0**
- Điều rỗng: **0**
- Điều không xác định được chương: **0**

### Phân bố theo nguồn

| Mã nguồn | Loại nguồn | Số điều |
|---|---|---:|
| `LQ` | Bộ luật/Luật | 220 |
| `NĐ` | Nghị định | 174 |
| `TT` | Thông tư | 83 |

## 3. Các class HTML chính

| Class | Số lượng | Vai trò thực tế |
|---|---:|---|
| `pChuong` | 82 | Dùng chung cho mã chương, tên chương, mã mục và tên mục |
| `pDieu` | 477 | Tiêu đề điều pháp điển và anchor ID |
| `pGhiChu` | 477 | Ghi chú nguồn, số văn bản, hiệu lực và URL nguồn |
| `pNoiDung` | 477 | Marker bắt đầu nội dung; rỗng 477/477 thẻ |
| `pChiDan` | 307 | Chỉ dẫn/quan hệ liên quan giữa điều, mục hoặc chương |

## 4. Cấu trúc chương và mục

File không có class riêng `pMuc`. Cả chương và mục đều dùng `pChuong` theo cặp marker–tiêu đề.

Quy tắc nhận diện:

- Text bắt đầu bằng `Chương` → marker chương.
- Text bắt đầu bằng `Mục` → marker mục.
- `pChuong` tiếp theo không có marker → tên của cấu trúc vừa gặp.
- Khi bắt đầu Chương mới, Mục hiện hành được đặt lại về `null`.
- Mỗi điều được gắn bản sao metadata Chương/Mục hiện hành.

## 5. Ranh giới và nội dung điều

- Bắt đầu tại `pDieu`.
- Duyệt các sibling kế tiếp.
- Dừng khi gặp `pDieu` hoặc `pChuong` mới.
- `pNoiDung` là marker rỗng; nội dung thật nằm ở các sibling sau nó.
- Giữ cả `content_text_raw` và `content_text` đã chuẩn hóa.
- Nhận diện `content_units`: preamble, clause, point, clause_continuation và table.

## 6. Mã pháp điển và ID

- Giữ nguyên toàn bộ mã dưới trường `codification_code`; không suy diễn số điều gốc từ phần tử cuối của mã.
- `article_id` lấy từ `<a name>` và luôn lưu dưới dạng chuỗi.
- `source_sha256` và `parser_version` được lưu để tái lập thí nghiệm.

## 7. Quan hệ chỉ dẫn

- Tổng đoạn `pChiDan`: **307**
- Gắn với điều: **301**
- Gắn với chương/mục: **6**
- Liên kết nội bộ: **919**
- Cùng Đề mục 20.2: **864**
- Sang đề mục khác: **55**
- Liên kết ngoài: **15**
- Quan hệ cấp điều sau loại trùng: **928**
- Quan hệ cấp cấu trúc sau loại trùng: **6**

Quan hệ được làm phẳng thành `RELATED_TO`, `EXTERNAL_REFERENCE` hoặc `UNKNOWN`. `target_in_corpus` được xác định bằng tập `article_id` thực tế, không chỉ dựa vào mã đề mục.

## 8. Bảng và attachment

- Bảng được lưu thành `headers`, `rows` và `text`.
- Attachment lưu tên, URL, phần mở rộng và `downloaded = false`.
- Không tải, OCR hoặc nhúng nội dung attachment trong giai đoạn này.

## 9. Kết quả parse thử

| STT | Mã điều | Loại | Tên điều | Chương | Mục | Số unit | Chỉ dẫn |
|---:|---|---|---|---|---|---:|---:|
| 1 | `20.2.LQ.1` | `LQ` | Phạm vi điều chỉnh | I |  | 1 | 4 |
| 2 | `20.2.LQ.2` | `LQ` | Đối tượng áp dụng | I |  | 4 | 5 |
| 3 | `20.2.NĐ.2.1` | `NĐ` | Phạm vi điều chỉnh | I |  | 1 | 1 |
| 4 | `20.2.NĐ.2.2` | `NĐ` | Đối tượng áp dụng | I |  | 2 | 1 |
| 5 | `20.2.NĐ.3.1` | `NĐ` | Phạm vi điều chỉnh | I |  | 11 | 25 |
| 6 | `20.2.NĐ.3.2` | `NĐ` | Đối tượng áp dụng | I |  | 3 | 1 |
| 7 | `20.2.NĐ.4.1` | `NĐ` | Phạm vi điều chỉnh | I |  | 3 | 3 |
| 8 | `20.2.NĐ.4.2` | `NĐ` | Đối tượng áp dụng | I |  | 33 | 6 |
| 9 | `20.2.NĐ.6.1` | `NĐ` | Phạm vi điều chỉnh | I |  | 6 | 3 |
| 10 | `20.2.NĐ.6.2` | `NĐ` | Đối tượng áp dụng | I |  | 9 | 6 |

## 10. Validation

- Trạng thái: **ĐẠT**
- Lỗi: **0**
- Cảnh báo: **0**

## 11. Kết luận

Dữ liệu đủ nhất quán để khóa làm corpus canonical trước khi thực hiện structural parent–child chunking. Parser bảo toàn cấu trúc Chương/Mục/Điều, nội dung thô và chuẩn hóa, khoản/điểm, bảng, attachment, provenance và quan hệ graph.
