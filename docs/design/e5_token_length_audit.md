# E5 Token-Length Audit: Design, Method, and Operations

## 1. Mục đích (Purpose)

- **Audit chính xác độ dài token trước khi index E5**: Đo lường pre-truncation token length cho từng legal chunk trước khi thực hiện embedding và lưu trữ vào Qdrant.
- **Ngăn FastEmbed âm thầm truncate chunk**: Mặc định FastEmbed cắt ngắn dữ liệu vượt 512 tokens mà không thông báo, làm mất thông tin quan trọng.
- **Production Gate**: Audit đóng vai trò là chốt chặn tự động (gatekeeper) bắt buộc phải vượt qua trước khi tạo Qdrant collection và đưa vào production.

---

## 2. Hai tầng dữ liệu (Two Data Layers)

Phần mềm xử lý hai tầng dữ liệu khác nhau:

- **Canonical Legal Chunk**:
  Nội dung văn bản pháp luật chuẩn được tạo ra từ `legal_chunker.py`.
- **E5 Passage Input**:
  Chuỗi ký tự thực tế được đưa vào model embedding:
  `passage: <chunk content>`

> [!IMPORTANT]
> Không được nhầm lẫn `token_count` của canonical chunk (tính bằng tiktoken/cl100k) với token count thực tế của E5 input (tính bằng XLM-RoBERTa với prefix và special tokens).

---

## 3. Cách đo chính xác (Exact Measurement Method)

- **Model**: `intfloat/multilingual-e5-large`.
- **FastEmbed Version**: Pin chính xác ở phiên bản `0.8.0`.
- **Sử dụng chính tokenizer của model**: Tải tokenizer chính thức từ thư mục model FastEmbed bằng `load_tokenizer(model_dir)`.
- **Clone tokenizer dùng cho inference**: Tạo bản sao độc lập thông qua `Tokenizer.from_str(inference_tokenizer.to_str())` để tránh làm biến đổi tokenizer dùng cho embedding thật.
- **Tắt Truncation và Padding**: Gọi `no_truncation()` và `no_padding()` trên bản clone để đếm được tổng số token pre-truncation.
- **Đếm với Special Tokens**: Đếm toàn bộ chuỗi passage input có tính thêm các special token (BOS `<s>` và EOS `</s>`) bằng `add_special_tokens=True`.
- **Exact Measurement**: Flag `exact_measurement: true` khẳng định kết quả được đo trực tiếp bằng tokenizer E5 thật, không phải ước lượng hay dùng proxy.

---

## 4. Prefix Overhead

- Document input của E5 luôn được tiền tố hóa bằng `passage: `.
- Audit đo lường song song cả content-only (`content`) và passage input (`passage: <content>`).
- Kết quả đo đạc thực tế xác nhận prefix `passage: ` cùng với khoảng trắng và special tokens gây ra overhead **2 tokens** (`<s> passage: `).
- Giới hạn model (512 tokens) được kiểm tra dựa trên toàn bộ chuỗi passage input hoàn chỉnh.

---

## 5. Giới hạn Token (Token Limits)

Với giới hạn tối đa `model_max = 512` tokens:

- **< 480 tokens**: An toàn (bình thường).
- **>= 480 và <= 512 tokens**: Cảnh báo gần giới hạn (near-limit warning).
- **= 512 tokens**: Hợp lệ (vừa đủ 512 tokens, không bị cắt bớt).
- **> 512 tokens**: Lỗi vượt giới hạn (validation error - thông tin bị cắt xén).

> [!NOTE]
> Ngưỡng `480` là ngưỡng cảnh báo an toàn do project tự lựa chọn để phát hiện sớm các chunk có rủi ro, không phải là giới hạn cứng về mặt kiến trúc của model.

---

## 6. So sánh với cl100k (Comparison with cl100k)

- `chunker_token_count` trong metadata sử dụng `tiktoken:cl100k_base` (proxy ban đầu).
- `cl100k_base` chỉ là proxy xấp xỉ, không trùng khớp với SentencePiece tokenizer của E5.
- **Difference definition**: `difference = e5_passage_token_count - chunker_token_count`.
- **Proxy positive**: `chunker_token_count >= model_max_tokens`.
- **Exact positive**: `e5_passage_token_count > model_max_tokens`.
- **Matrix phân loại (trên các record có chunker_token_count hợp lệ)**:
  - **True Positive (TP)**: Proxy positive và Exact positive.
  - **True Negative (TN)**: Proxy negative và Exact negative.
  - **False Positive (FP)**: Proxy positive nhưng Exact negative (bao gồm cả trường hợp exactly 512).
  - **False Negative (FN)**: Proxy negative nhưng Exact positive.
- Comparison chỉ nhằm mục đích phân tích tương quan và đánh giá proxy, **không thay thế** cho exact E5 audit.

---

## 7. Các Output Artifacts

Khi thực thi, audit script sinh ra 4 file kết quả tại thư mục output:

1. `summary.json`: Tổng hợp toàn bộ thống kê, phân bố token, thống kê tương quan cl100k, risk counts, và trạng thái validation.
2. `chunks.csv`: Bảng chi tiết toàn bộ 1127 chunks với đầy đủ thông tin định danh và số đo token.
3. `near_limit.json`: Danh sách các chunks có `e5_passage_token_count >= 480` (bao gồm cả các chunks vượt 512).
4. `over_limit.json`: Danh sách các chunks có `e5_passage_token_count > 512` (chỉ chứa oversized chunks).

**Các Locator Metadata Quan Trọng**:
Các output JSON/CSV lưu trữ đầy đủ các thuộc tính truy vết:
`source_document_id`, `document_id`, `parent_document_id`, `attachment_id`, `attachment_title`, `table_id`, `table_index`, `table_title`, `table_segment_index`, `form_number`.

> [!TIP]
> `article_code` có thể nhận giá trị `null` đối với các bảng biểu nằm trong Phụ lục văn bản, do đó các locator bổ sung (như `attachment_title`, `table_title`, `form_number`) rất quan trọng để truy vết nguồn gốc.

---

## 8. Cách Chạy (Execution Commands)

### Dry Run (Kiểm tra cấu hình & input):
```bash
python -m scripts.audit_e5_token_lengths --dry-run
```

### Normal Audit:
```bash
python -m scripts.audit_e5_token_lengths \
  --local-files-only \
  --batch-size 128 \
  --threads 6
```

### Strict Production Gate:
```bash
python -m scripts.audit_e5_token_lengths \
  --local-files-only \
  --batch-size 128 \
  --threads 6 \
  --strict
```

**Mã Exit Code**:
- `0`: Hợp lệ hoặc chạy ở chế độ non-strict.
- `2`: Chế độ `--strict` phát hiện có ít nhất 1 chunk vượt quá `model_max_tokens` (> 512).
- **Đảm bảo ghi report**: Mọi report/artifact luôn được ghi thành công xuống đĩa trước khi `--strict` trả exit code `2`.
- `--dry-run --strict` trả exit code `0` nếu cấu hình đầu vào hợp lệ mà không cần khởi tạo model hay tokenizer.

---

## 9. Kết quả Audit Hiện tại (Current Audit Findings)

Dựa trên kết quả quan sát thực tế từ dữ liệu `legal_chunks.jsonl`:

- **Tổng số chunks**: 1127.
- **Near-limit chunks (>= 480)**: 10 chunks.
- **Oversized chunks (> 512)**: 4 chunks.
- **Đặc điểm**:
  - Cả 10 chunks trong nhóm near-limit đều có `chunk_type = "table"`.
  - Cả 4 chunks vượt giới hạn 512 tokens đều thuộc các bảng phụ lục của văn bản gốc `vbpl:item:169619`.
- **Kết luận**: Production indexing hiện tại bị chặn (blocked) bởi gatekeeper cho tới khi rủi ro truncation được giải quyết.

---

## 10. Quyết định và Báo cáo Tiếp theo (Next Steps)

- **Không giảm global chunk size**: Không thay đổi tham số chunking chung cho toàn bộ văn bản để tránh xé nhỏ các chunk văn bản thường.
- **Không âm thầm truncate**: Không bật tính năng cắt bớt token tự động.
- **Xử lý riêng Table Segmentation**: Điều chỉnh quy tắc phân đoạn (segmentation) dành riêng cho bảng biểu/phụ lục.
- **Quy trình sau khi điều chỉnh**:
  1. Rebuild corpus chunks.
  2. Rerun exact E5 token audit (`--strict`).
  3. Rerun full unit test suite (278+ tests).
  4. Rerun retrieval benchmark để xác nhận chất lượng truy vấn.
