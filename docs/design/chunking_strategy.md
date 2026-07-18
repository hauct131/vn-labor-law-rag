# CHUNKING STRATEGY — VN LABOR LAW RAG

## 1. Mục tiêu

Chiến lược chunking của dự án phải đạt đồng thời bốn mục tiêu:

1. Giữ nguyên cấu trúc pháp lý của văn bản: Chương → Mục → Điều → Khoản → Điểm → Bảng.
2. Tạo chunk đủ nhỏ để truy xuất chính xác.
3. Giữ đủ ngữ cảnh để LLM không hiểu sai một khoản hoặc điểm đứng riêng lẻ.
4. Dùng chung một tập chunk cho Sparse RAG, Hybrid RAG và Graph-enhanced RAG để việc so sánh công bằng.

Corpus đầu vào là dữ liệu canonical sinh từ parser, gồm 477 điều thuộc Đề mục 20.2 — Lao động. Parser giữ riêng `articles` và các phụ lục HTML cấp tài liệu trong `attachments`; bảng biểu mẫu của phụ lục không còn được gắn vào điều đứng ngay trước nó trong HTML.

---

## 2. Phương án được chọn

### Structural Parent–Child Chunking with token-size fallback

Đây là chiến lược chính của dự án.

```text
Parent
└── Toàn bộ Điều

Child
├── Điều ngắn
├── Khoản
├── Nhóm Điểm
├── Preamble
├── Bảng
└── Fallback segment
```

### Vai trò của Parent

Parent là toàn bộ Điều canonical, dùng để:

- Truy vết nguồn.
- Lấy lại toàn văn điều khi cần.
- Mở rộng context.
- Liên kết Qdrant với Neo4j.
- Hiển thị citation.
- Phục vụ Graph-enhanced RAG.

### Vai trò của Child

Child là đơn vị được:

- Embed.
- Index vào Qdrant.
- Tìm kiếm bằng sparse retrieval.
- Tìm kiếm bằng dense retrieval.
- Hợp nhất bằng Hybrid RAG.

Không phải mọi Điều đều bị chia nhỏ. Điều ngắn được giữ nguyên thành một chunk.

---

## 3. Vì sao không dùng fixed-size làm phương án chính?

Fixed-size chunking chia văn bản theo số token cố định, ví dụ:

```text
Chunk 1: token 1–500
Chunk 2: token 421–920
```

Cách này đơn giản nhưng có thể:

- Cắt ngang một khoản.
- Tách điểm khỏi khoản cha.
- Làm mất câu dẫn trước danh sách điểm.
- Chia đôi một điều kiện pháp lý.
- Làm chunk bắt đầu bằng nội dung như “b) ...” nhưng không còn ngữ cảnh.

Fixed-size chỉ được dùng làm:

1. Baseline để so sánh.
2. Fallback khi một đơn vị pháp lý riêng lẻ vẫn vượt giới hạn token.

---

## 4. Thông số mặc định

```python
TARGET_TOKENS = 500
MAX_TOKENS = 750
FALLBACK_OVERLAP = 80
```

Ý nghĩa:

- `TARGET_TOKENS`: kích thước mong muốn khi phải nhóm hoặc chia.
- `MAX_TOKENS`: giới hạn mềm tối đa của một chunk.
- `FALLBACK_OVERLAP`: số token lặp lại của recursive splitter cho text bảng
  quá cỡ; fallback của legal unit không overlap để tránh lặp primary source.

Đây là thông số khởi đầu. Kết quả cuối phải được xác nhận bằng đánh giá retrieval.

---

## 5. Quy tắc chunking

### 5.1. Điều ngắn

Nếu toàn bộ nội dung Điều không vượt `MAX_TOKENS`:

```text
Tạo một article chunk.
```

Ví dụ:

```text
{article_id}|article
```

Không chia một Điều ngắn thành nhiều chunk quá nhỏ.

### 5.2. Điều dài có Khoản

Nếu Điều vượt `MAX_TOKENS` và có các đơn vị `clause`:

```text
Tách theo từng Khoản.
```

Ví dụ:

```text
{article_id}|clause=1
{article_id}|clause=2
{article_id}|clause=3
```

Không gộp hai khoản khác nhau vào cùng một chunk chỉ để đạt `TARGET_TOKENS`.

### 5.3. Khoản dài có Điểm

Nếu một Khoản vượt `MAX_TOKENS` và có nhiều Điểm:

```text
Nhóm các Điểm liên tiếp theo thứ tự.
```

Ví dụ:

```text
{article_id}|clause=1|points=a-c
{article_id}|clause=1|points=d-g
```

Quy tắc:

- Giữ nguyên thứ tự.
- Không cắt giữa một Điểm nếu Điểm đó chưa vượt giới hạn.
- Không gộp Điểm thuộc hai Khoản khác nhau.
- Nhóm gần `TARGET_TOKENS`.
- Không vượt `MAX_TOKENS`, trừ trường hợp một Điểm riêng lẻ đã quá dài.

### 5.4. Preamble

`preamble` là nội dung đứng trước Khoản 1.

Xử lý:

1. Nếu toàn Điều ngắn: preamble nằm trong article chunk.
2. Nếu Điều dài:
   - Gắn preamble vào chunk đầu tiên nếu tổng token không vượt `MAX_TOKENS`.
   - Nếu preamble dài hoặc có ý nghĩa độc lập, tạo chunk riêng.

Chunk key:

```text
{article_id}|preamble
```

Preamble không được bỏ vì thường chứa câu dẫn áp dụng cho toàn bộ danh sách khoản/điểm phía sau.

### 5.5. Clause continuation

`clause_continuation` phải được gắn với Khoản hiện tại.

Ví dụ thứ tự:

```text
clause=1
clause_continuation
point=a
point=b
```

Tất cả phải được hiểu là thuộc Khoản 1.

Không được:

- Tạo Khoản mới từ continuation.
- Gán continuation sang Khoản kế tiếp.
- Bỏ continuation khỏi chunk.

### 5.6. Điểm lặp và unit occurrence

Một số nhãn Điểm hoặc số Khoản có thể xuất hiện lặp trong dữ liệu.

Khi tạo `chunk_key`, phải xét thêm:

```text
unit_occurrence
```

Ví dụ:

```text
{article_id}|clause=1|occurrence=2
{article_id}|clause=1|point=a|occurrence=2
```

Mục tiêu:

- Không trùng `chunk_key`.
- Không trùng `chunk_id`.
- Chạy lại vẫn sinh cùng ID.

### 5.7. Sentence-aware fallback

Fallback chỉ dùng khi:

- Một Khoản riêng lẻ vẫn vượt `MAX_TOKENS`.
- Một Điểm riêng lẻ vẫn vượt `MAX_TOKENS`.
- Một preamble riêng lẻ vẫn vượt `MAX_TOKENS`.
- Nội dung không có ranh giới pháp lý rõ hơn.

Quy tắc:

```text
Target: 500 token
Max: 750 token
Legal-unit overlap: 0 token

kết thúc unit
→ kết thúc câu
→ dấu chấm phẩy
→ dấu phẩy
→ khoảng trắng
→ ký tự (chỉ cho token đơn lẻ bất khả phân)
```

Phải:

- Ưu tiên ranh giới mạnh nhất còn vừa `MAX_TOKENS`, không cố lấp đủ 750 token.
- Giữ nguyên thứ tự câu.
- Không lặp hoặc làm mất primary source khi nối các segment theo thứ tự.
- Không coi marker mở đầu như `1.` hoặc `a.` là một câu riêng.
- Không dùng overlap giữa các legal unit, Khoản hoặc nhóm Điểm.
- Nếu một Điểm riêng lẻ bị chia thành nhiều segment, các segment tiếp nối phải
  có `[Ngữ cảnh điểm]` chứa marker và phần mở đầu rút gọn của Điểm đó; không
  chỉ giữ metadata `point_labels` mà thiếu marker nhìn thấy trong nội dung.

Chunk key:

```text
{article_id}|clause=1|segment=1
{article_id}|clause=1|segment=2
```

Subpoint như `c1)`, `c2)`, `d1)` được nhận diện cả khi parser lưu dưới
dạng `clause_continuation`. Mỗi fallback segment của subpoint phải có:

```text
[Ngữ cảnh khoản]
1. ...

[Ngữ cảnh điểm]
c) ...

[Nội dung]
c2) ...
```

Metadata tương ứng:

```json
{
  "point_labels": ["c"],
  "subpoint_label": "c2",
  "parent_point_unit_id": "...|point=c",
  "source_unit_ids": ["...|continuation=3"],
  "context_unit_ids": ["...|clause=1", "...|point=c"]
}
```

Điểm cha là repeated context, không được thay thế primary provenance của
subpoint. Nếu một subpoint phải chia nhiều segment, mọi segment đều lặp lại
ngữ cảnh khoản, ngữ cảnh điểm và cùng metadata subpoint.

### 5.8. Bảng

Mỗi bảng là chunk riêng.

Chunk key:

```text
{article_id}|table=1|segment=1
{attachment_id}|table=1|segment=1
```

Nếu bảng dài:

- Chia theo nhóm dòng.
- Không cắt giữa một hàng.
- Lặp lại toàn bộ header trong mỗi segment, gồm tiêu đề nhiều cấp và dòng mã
  cột như `(A)`, `(B)`, `(1)`, `(2)`.
- Khi HTML dùng `td` thay cho `th`, suy luận các dòng header đầu bảng từ dòng
  mã cột hoặc các nhãn `TT`, `STT`, `Số TT`, `Họ và tên`, `Nội dung`.
- Lưu cùng `table_title` và `shared_header_rows` trên mọi segment để validator
  có thể kiểm tra header không bị rơi khi chia bảng.
- Giữ metadata của bảng.
- Bảng nằm trong điều giữ `parent_article_id`.
- Bảng nằm trong phụ lục giữ `parent_attachment_id`, `parent_document_id` và `form_number`; `parent_article_id = null`.

Không trộn bảng vào chunk văn bản thông thường nếu bảng làm chunk vượt giới hạn hoặc gây khó truy xuất.

Nếu bảng biểu diễn công thức, table chunk phải có biểu diễn tuyến tính ổn định
trong `linearized_formula` và nội dung `Công thức chuẩn hóa: ...`. Chunk văn bản
ngay trước bảng giữ `related_table_ids` và `formula_ids`; table chunk giữ cùng
`formula_ids`, cùng `preceding_unit_id` và `following_unit_id`. Nhờ vậy retrieval
không tách câu dẫn “được tính theo công thức sau” khỏi chính công thức.

### 5.9. Attachment

Phụ lục HTML của văn bản là container cấp tài liệu:

```text
Document
└── Attachment
    └── Form
        └── Table
```

Đối với file đính kèm bên ngoài:

- Chỉ giữ metadata attachment.
- Không OCR.
- Không embed nội dung file đính kèm.
- Không tải file ngoài.

Metadata có thể gồm:

```text
filename
href
attachment_id
source_document_id
```

### 5.10. Quan hệ pháp lý

Không đưa toàn bộ citation paragraph dài vào `content`.

Giữ trong metadata:

```text
relation_target_ids
relation_target_codes
relation_types
relation_same_topic
relation_target_in_corpus
```

Mục đích:

- Xây Neo4j.
- Mở rộng graph.
- Truy vết quan hệ.
- Lấy thêm chunk của điều liên quan.

### 5.11. Nguồn văn bản

Payload phải giữ đầy đủ:

```text
source_type
source_document_id
source_note_text
source_urls
```

Trong `content` chỉ đưa nhãn nguồn ngắn.

Ví dụ:

```text
Nguồn: LQ
```

Không đưa URL dài vào nội dung embedding.

---

## 6. Token counting

### 6.1. Interface đề xuất

```python
from typing import Protocol

class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int:
        ...
```

`build_legal_chunks` phải nhận `token_counter` từ bên ngoài:

```python
def build_legal_chunks(
    canonical_corpus: dict,
    *,
    target_tokens: int = 500,
    max_tokens: int = 750,
    fallback_overlap: int = 80,
    token_counter: TokenCounter | None = None,
) -> list[dict]:
    ...
```

### 6.2. Nguyên tắc

- Không gọi `len(text.split())` là token thật.
- Nếu dùng bộ đếm gần đúng, phải ghi rõ tên như `regex-estimate-v1`.
- Sau khi chốt embedding model, có thể thay tokenizer mà không viết lại chunker.
- `tokenizer_name` phải được lưu trong `chunking_summary.json`.

---

## 7. Stable ID

### 7.1. Chunk key

`chunk_key` là mã dễ đọc và ổn định.

Ví dụ:

```text
{article_id}|article
{article_id}|preamble
{article_id}|clause=1
{article_id}|clause=1|points=a-d
{article_id}|clause=1|segment=2
{article_id}|table=1|segment=1
{attachment_id}|table=1|segment=1
```

### 7.2. Chunk ID

`chunk_id` được sinh bằng UUIDv5:

```python
chunk_id = str(uuid5(CHUNK_NAMESPACE, chunk_key))
```

Không dùng UUIDv4.

Yêu cầu:

- Cùng input.
- Cùng config.
- Cùng chunking version.
- Cùng `chunk_key`.

Thì phải sinh cùng `chunk_id`.

---

## 8. Khóa liên kết Qdrant–Neo4j

Khóa liên kết phụ thuộc container:

```text
article chunk:    Qdrant.parent_article_id    = Neo4j.Article.article_id
attachment table: Qdrant.parent_attachment_id = Neo4j.Attachment.attachment_id
```

### Trong Qdrant

```json
{
  "chunk_id": "...",
  "container_type": "article",
  "parent_article_id": "200020...",
  "parent_attachment_id": null,
  "article_code": "20.2.LQ.169"
}
```

### Trong Neo4j

```cypher
(:Article {
  article_id: "200020...",
  article_code: "20.2.LQ.169"
})
```

Luồng Graph-enhanced RAG:

```text
Qdrant tìm child chunk
→ định tuyến theo container_type
→ lấy Article hoặc Attachment cha
→ mở rộng RELATED_TO/CITES/GUIDED_BY
→ nhận các article_id liên quan
→ Qdrant lấy thêm child chunk
→ merge và deduplicate
```

---

## 9. Schema chunk

```json
{
  "chunk_id": "uuid-v5-string",
  "chunk_key": "article-id|clause=1",
  "container_type": "article",
  "parent_article_id": "article-id",
  "parent_attachment_id": null,
  "parent_document_id": null,
  "attachment_id": null,
  "attachment_title": null,
  "form_number": null,

  "document_id": "phap-dien:20.2",
  "topic_code": "20.2",
  "topic_name": "Lao động",

  "article_code": "20.2.LQ.169",
  "codification_code": "20.2.LQ.169",
  "article_title": "Tuổi nghỉ hưu",
  "heading": "Điều 20.2.LQ.169. Tuổi nghỉ hưu",

  "chapter_id": "chapter-id",
  "chapter_number": "XII",
  "chapter_title": "BẢO HIỂM XÃ HỘI...",

  "section_id": null,
  "section_number": null,
  "section_title": null,

  "chunk_type": "clause",
  "unit_type": "clause",
  "clause_number": "1",
  "point_labels": [],
  "subpoint_label": null,
  "parent_point_unit_id": null,
  "source_unit_ids": [],
  "context_unit_ids": [],
  "table_id": null,
  "table_title": null,
  "shared_header_rows": [],
  "related_table_ids": [],
  "formula_ids": [],
  "preceding_unit_id": null,
  "following_unit_id": null,
  "linearized_formula": null,
  "segment_index": 1,

  "content": "Nội dung dùng để embed và retrieve.",
  "body_text": "Nội dung pháp lý của riêng chunk.",
  "token_count": 420,
  "tokenizer_name": "regex-estimate-v1",

  "source_type": "LQ",
  "source_document_id": "vbpl:item:139264",
  "source_note_text": "...",
  "source_urls": [],

  "relation_target_ids": [],
  "relation_target_codes": [],
  "relation_types": [],

  "attachment_metadata": [],

  "parser_version": "1.3.0",
  "chunker_version": "1.1.0",
  "source_sha256": "..."
}
```

---

## 10. Content template

```text
Đề mục: 20.2 — Lao động
Chương XII — BẢO HIỂM XÃ HỘI, BẢO HIỂM Y TẾ, BẢO HIỂM THẤT NGHIỆP
Điều 20.2.LQ.169 — Tuổi nghỉ hưu
Khoản: 1
Nguồn: LQ

<nội dung pháp lý>
```

Quy tắc:

- Chỉ thêm dòng có dữ liệu.
- Không ghi `Khoản: None`.
- Không đưa URL vào `content`.
- Không đưa JSON kỹ thuật vào `content`.
- `body_text` chỉ chứa nội dung pháp lý.
- `content` chứa breadcrumb pháp lý và `body_text`.

---

## 11. API đề xuất

```python
def build_legal_chunks(
    canonical_corpus: dict,
    *,
    target_tokens: int = 500,
    max_tokens: int = 750,
    fallback_overlap: int = 80,
    token_counter=None,
) -> list[dict]:
    ...
```

```python
def validate_chunks(
    canonical_corpus: dict,
    chunks: list[dict],
    *,
    max_tokens: int = 750,
) -> dict:
    ...
```

```python
def build_chunking_summary(
    canonical_corpus: dict,
    chunks: list[dict],
    validation: dict,
    config: dict,
) -> dict:
    ...
```

Core module:

```text
backend/app/ingestion/legal_chunker.py
```

CLI:

```text
scripts/build_chunks.py
```

---

## 12. Thuật toán tổng quát

```text
FOR mỗi article:
    đọc content_units
    tách table units khỏi text units

    IF toàn article <= MAX_TOKENS:
        tạo article chunk
    ELSE:
        xác định preamble
        nhóm units theo clause

        xử lý preamble

        FOR mỗi clause:
            IF clause <= MAX_TOKENS:
                tạo clause chunk
            ELSE IF clause có points:
                nhóm points theo TARGET_TOKENS
            ELSE:
                sentence-aware fallback

    FOR mỗi table:
        IF table <= MAX_TOKENS:
            tạo table chunk
        ELSE:
            chia theo row groups và lặp header

    gắn source metadata
    gắn relation metadata
    gắn attachment metadata
    tạo chunk_key
    tạo UUIDv5 chunk_id

validate toàn bộ chunks
tạo chunking summary
```

---

## 13. Validation bắt buộc

Chunking chỉ đạt khi:

```text
[ ] Có chunk.
[ ] Mọi chunk_id là string.
[ ] Không trùng chunk_id.
[ ] Không trùng chunk_key.
[ ] Chạy lại sinh cùng ID.
[ ] Chunk cấp điều có `parent_article_id` hợp lệ và không có `parent_attachment_id`.
[ ] Chunk bảng phụ lục có `parent_attachment_id`/`parent_document_id` hợp lệ và `parent_article_id = null`.
[ ] Coverage đủ 477 Điều.
[ ] Không chunk nào trộn hai Điều.
[ ] Không chunk rỗng.
[ ] Không mất preamble.
[ ] Không mất clause continuation.
[ ] Point thuộc đúng Khoản.
[ ] Coverage đủ 63 bảng.
[ ] Mọi segment của bảng dài lặp lại cùng header nhiều cấp và dòng mã cột.
[ ] Mọi công thức bảng có liên kết hai chiều với chunk câu dẫn liền trước.
[ ] Metadata nguồn được giữ.
[ ] Metadata relation được giữ.
[ ] Attachment metadata được giữ.
[ ] Token count hợp lệ.
[ ] Oversized chunk được báo cáo.
[ ] Validation có is_valid, errors và warnings.
```

Không được bỏ qua silent error.

---

## 14. Chunking summary

`data/processed/chunking_summary.json` nên chứa:

```json
{
  "chunker_version": "1.0.0",
  "document_id": "phap-dien:20.2",
  "article_count": 477,
  "chunk_count": 0,

  "config": {
    "target_tokens": 500,
    "max_tokens": 750,
    "fallback_overlap": 80,
    "tokenizer_name": "..."
  },

  "chunk_type_counts": {
    "article": 0,
    "clause": 0,
    "points": 0,
    "preamble": 0,
    "table": 0,
    "fallback_segment": 0
  },

  "token_statistics": {
    "min": 0,
    "max": 0,
    "mean": 0,
    "median": 0,
    "p95": 0
  },

  "coverage": {
    "covered_articles": 477,
    "missing_articles": [],
    "expected_tables": 63,
    "covered_tables": 63
  },

  "validation": {
    "is_valid": true,
    "errors": [],
    "warnings": [],
    "duplicate_chunk_id_count": 0,
    "duplicate_chunk_key_count": 0,
    "empty_chunk_count": 0,
    "oversized_chunk_count": 0
  }
}
```

---

## 15. Cách đánh giá chiến lược chunking

Không kết luận chỉ dựa trên việc chunk “trông hợp lý”.

Phải so sánh ít nhất ba cấu hình:

### Baseline A — Fixed-size

```text
500 token
80 token overlap
```

### Baseline B — Article-only

```text
Mỗi Điều là một chunk
Điều quá dài mới fallback
```

### Proposed C — Structural Parent–Child

```text
Điều ngắn
→ Khoản
→ nhóm Điểm
→ sentence fallback
→ selective parent expansion
```

Có thể thêm:

### Ablation D — Structural child only

Giống C nhưng không lấy Điều cha.

So sánh C với D để đo lợi ích của parent expansion.

---

## 16. Bộ câu hỏi đánh giá

Tạo 40–45 câu:

```text
10 câu hỏi trực tiếp một Điều
10 câu hỏi chính xác Khoản/Điểm
8 câu paraphrase
6 câu tổng hợp nhiều Khoản
6 câu cần quan hệ nhiều Điều
5 câu dựa trên bảng
```

Mỗi câu có:

```json
{
  "question_id": "Q001",
  "question": "...",
  "gold_article_ids": [],
  "gold_unit_ids": [],
  "gold_answer": "...",
  "question_type": "direct_clause"
}
```

---

## 17. Metric retrieval

### Article Recall@5

Top 5 có chunk thuộc đúng Điều hay không.

### Unit Recall@5

Top 5 có đúng Khoản/Điểm chứa đáp án hay không.

### MRR@10

Vị trí của kết quả đúng đầu tiên.

### nDCG@10

Dùng khi có nhiều Điều hoặc Khoản liên quan.

### Context Precision

Tỷ lệ chunk liên quan trong tổng số chunk lấy được.

### Gold-span Coverage

Tỷ lệ nội dung ground truth được retrieval lấy về.

### Hiệu quả context

Đo thêm:

```text
retrieved token count
relevant token ratio
latency
index size
duplicate result count
parent expansion count
```

---

## 18. Metric câu trả lời

Chấm thang 10:

```text
Đúng pháp luật:                  0–4
Grounded trong context:          0–2
Dẫn đúng Điều/Khoản/Điểm:        0–2
Đầy đủ điều kiện quan trọng:     0–1
Không thêm thông tin vô nguồn:   0–1
```

Theo dõi riêng:

```text
citation accuracy
unsupported claim rate
answer completeness
```

---

## 19. Tiêu chí chọn phương án thắng

Ưu tiên:

1. Không mất cấu trúc pháp lý.
2. Unit Recall@5 cao nhất.
3. MRR@10 cao hơn.
4. Context Precision cao hơn.
5. Citation accuracy cao hơn.
6. Nếu gần bằng nhau, chọn phương án ít token và latency thấp hơn.

Mục tiêu ban đầu của project:

```text
Article coverage: 477/477
Table coverage: 63/63
Duplicate chunk ID: 0
Empty chunk: 0
Article Recall@5 >= 0.95
Unit Recall@5 >= 0.85
Citation accuracy >= 0.90
```

Đây là mục tiêu nội bộ của dự án, không phải chuẩn chung bắt buộc.

---

## 20. Quyết định cuối cùng

Phương án chính:

```text
Structural Parent–Child Chunking
+ contextual legal header
+ stable UUIDv5
+ sentence-aware fallback
+ selective parent expansion
```

Thông số khởi đầu:

```python
TARGET_TOKENS = 500
MAX_TOKENS = 750
FALLBACK_OVERLAP = 80
```

Baseline để đánh giá:

```text
Fixed-size 500/80
Article-only
Structural child only
```
