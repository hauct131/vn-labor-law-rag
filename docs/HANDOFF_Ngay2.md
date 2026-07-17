# HANDOFF — Kết thúc Ngày 2, chuẩn bị bắt đầu Ngày 3

## 0. Thông tin bàn giao

- **Dự án:** Xây dựng hệ thống hỏi đáp pháp luật lao động Việt Nam sử dụng Retrieval-Augmented Generation
- **Mục tiêu nghiên cứu:** So sánh Sparse RAG, Hybrid RAG và Graph-enhanced RAG
- **Repository:** `https://github.com/hauct131/vn-labor-law-rag.git`
- **Nhánh làm việc hiện tại:** `main`
- **Thời điểm bàn giao:** 17/07/2026
- **Điểm bàn giao:** Hoàn thành khảo sát HTML, parser canonical, validation và kiểm thử parser; chuẩn bị triển khai chunking ở Ngày 3
- **Corpus chính đã khóa:** Bộ pháp điển — Đề mục `20.2`, tên đề mục `Lao động`
- **Phạm vi chính xác:** 477 điều pháp điển thuộc Đề mục 20.2; không mô tả hệ thống là bao phủ toàn bộ pháp luật lao động Việt Nam

---

# 1. Mục đích của file handoff

File này dùng để chuyển giao đầy đủ trạng thái dự án cho:

- Một phiên làm việc mới của AI coding agent.
- Một thành viên khác tiếp tục project.
- Chính người phát triển khi quay lại sau một khoảng thời gian.
- Người hướng dẫn cần kiểm tra tiến độ và quyết định kỹ thuật.

Người tiếp nhận phải đọc toàn bộ file này trước khi chỉnh sửa code.

**Nguyên tắc quan trọng:** Không viết lại parser từ đầu. Parser Ngày 2 đã chạy đúng và đã được kiểm thử. Công việc tiếp theo là xây dựng chunker dựa trên schema canonical hiện tại.

---

# 2. Mục tiêu tổng thể của dự án

Hệ thống dự kiến có ba pipeline truy xuất sử dụng chung một corpus, cùng cách chunking và cùng bộ câu hỏi đánh giá:

1. **Sparse RAG**
   - Truy xuất từ khóa hoặc sparse vector.
   - Đóng vai trò baseline.

2. **Hybrid RAG**
   - Kết hợp sparse retrieval và dense retrieval.
   - Hợp nhất xếp hạng bằng Reciprocal Rank Fusion hoặc cơ chế tương đương.

3. **Graph-enhanced RAG**
   - Sử dụng kết quả Hybrid làm tập seed.
   - Mở rộng một bước qua quan hệ trong Neo4j.
   - Lấy thêm các chunk liên quan từ Qdrant.
   - Hợp nhất, loại trùng và tạo context cuối.

Kiến trúc mục tiêu:

```text
React + Vite + TypeScript
              ↓
          FastAPI
              ↓
       LangChain chains
              ↓
 ┌────────────┼────────────┐
 │            │            │
Sparse      Hybrid    Graph-enhanced
 │            │            │
 └────────────┴────────────┘
              ↓
       Answer + citations
```

Hạ tầng dữ liệu:

```text
Qdrant
- dense vector
- sparse vector
- metadata payload
- canonical chunk set

Neo4j Community
- cấu trúc pháp lý
- quan hệ giữa các điều
- quan hệ nguồn/hướng dẫn/liên quan
```

---

# 3. Các quyết định đã khóa

## 3.1. Corpus

Chỉ sử dụng corpus chính:

```text
data/raw/DeMuc_20.2_Lao_Dong.html
```

File này đã được đẩy lên GitHub, dung lượng khoảng 1,3 MB.

Không cần dùng OCR vì nguồn là HTML có cấu trúc.

Không đưa file ZIP trùng nội dung lên repository nếu không có lý do đặc biệt.

## 3.2. Phạm vi văn bản

Trong corpus hiện tại có:

- `LQ`: Luật hoặc Bộ luật.
- `NĐ`: Nghị định.
- `TT`: Thông tư.

Phân bố đã xác nhận:

```text
LQ: 220
NĐ: 174
TT: 83
Tổng: 477
```

Parser hiện được kiểm thử cho cấu trúc HTML Bộ pháp điển và ba loại mã trên. Không tuyên bố parser hỗ trợ mọi định dạng văn bản pháp luật Việt Nam.

## 3.3. Quan hệ pháp lý

Các quan hệ cần phục vụ graph gồm:

```text
RELATED_TO
CITES
GUIDED_BY
```

Quan hệ cấu trúc dự kiến:

```text
CONTAINS
HAS_CLAUSE
```

Trong dữ liệu canonical Ngày 2, quan hệ hiện có chủ yếu được chuẩn hóa thành:

```text
RELATED_TO
EXTERNAL_REFERENCE
```

Các trường quan trọng:

```text
source_id
source_code
target_id
target_code
relation_type
target_in_corpus
same_topic
href
text
mention_count
```

Không được khôi phục field cũ:

```text
same_topic_20_2
internal_same_topic_20_2
```

Chỉ dùng:

```text
same_topic
internal_same_topic
```

## 3.4. Nguyên tắc đánh giá

Ba pipeline phải giữ giống nhau về:

- Corpus.
- Canonical parser.
- Chunking.
- Bộ test retrieval.
- LLM trả lời.
- Prompt.
- Temperature.
- Context budget.
- Bộ câu hỏi đánh giá.

Chỉ thay đổi chiến lược retrieval để so sánh công bằng.

Metric retrieval dự kiến:

```text
Hit@5
Recall@5
MRR
Latency
```

Metric câu trả lời:

```text
Rubric 0–10
```

Bộ câu hỏi đánh giá dự kiến:

```text
30–45 câu hỏi có nhãn
```

---

# 4. Trạng thái repository tại thời điểm bàn giao

Repository trên GitHub hiện có các nhóm chính:

```text
backend/
data/
docs/
experiments/
frontend/
scripts/
.env.example
.gitignore
Makefile
README.md
docker-compose.yml
```

Module ingestion hiện có:

```text
backend/app/ingestion/
├── __init__.py
├── document_factory.py
├── index_neo4j.py
├── index_qdrant.py
├── legal_chunker.py
└── legal_parser.py
```

Bộ test hiện có:

```text
backend/tests/
├── test_health.py
└── test_legal_parser.py
```

## 4.1. Thành phần đã hoàn thành

```text
Khởi tạo cấu trúc repository                 HOÀN THÀNH
Docker Compose                              HOÀN THÀNH CƠ BẢN
Backend health endpoint                     HOÀN THÀNH
Frontend scaffold                           HOÀN THÀNH
Qdrant service                              ĐÃ CHẠY THỬ
Neo4j service                               ĐÃ CHẠY THỬ
Khảo sát HTML Đề mục 20.2                   HOÀN THÀNH
Parser canonical                            HOÀN THÀNH
Refactor parser core và CLI                 HOÀN THÀNH
Validation corpus                           PASS
Test parser                                 13 PASSED
Corpus HTML trên GitHub                     HOÀN THÀNH
```

## 4.2. Thành phần chưa triển khai

```text
legal_chunker.py                            STUB
test_legal_chunker.py                       CHƯA CÓ
scripts/build_chunks.py                     CHƯA CÓ
legal_chunks.jsonl                          CHƯA CÓ
chunking_summary.json                       CHƯA CÓ
index_qdrant.py                             CHƯA TRIỂN KHAI
index_neo4j.py                              CHƯA TRIỂN KHAI
Sparse retrieval                           CHƯA TRIỂN KHAI
Dense retrieval                            CHƯA TRIỂN KHAI
Hybrid retrieval                           CHƯA TRIỂN KHAI
Graph-enhanced retrieval                    CHƯA TRIỂN KHAI
Evaluation dataset                         CHƯA TRIỂN KHAI
README đầy đủ                               CHƯA HOÀN THÀNH
```

`legal_chunker.py` hiện chỉ là stub:

```python
"""Structural chunking theo Điều → Khoản → Điểm."""

def build_legal_chunks(records: list[dict]) -> list[dict]:
    raise NotImplementedError("Sẽ triển khai ở Ngày 3.")
```

Không sửa `index_qdrant.py` hoặc `index_neo4j.py` trước khi chunker và validation chunk hoàn thành.

---

# 5. Kết quả Ngày 2 đã xác nhận

## 5.1. Thống kê HTML và parser

```text
Topic: 20.2 - Lao động
Document ID: phap-dien:20.2
Parser version: 1.2.0

Articles: 477
Sources: {'LQ': 220, 'NĐ': 174, 'TT': 83}
Chapters: 17
Sections: 24
Tables: 63
Attachments: 39
Article relations: 928
Structure relations: 6
Validation: PASS
```

## 5.2. Validation report

Kết quả đã xác nhận:

```json
{
  "is_valid": true,
  "errors": [],
  "warnings": [],
  "article_count": 477,
  "source_counts": {
    "LQ": 220,
    "NĐ": 174,
    "TT": 83
  },
  "missing_article_id_count": 0,
  "duplicate_article_id_count": 0,
  "duplicate_unit_id_count": 0,
  "repeated_unit_occurrence_count": 55,
  "empty_article_count": 0,
  "articles_without_chapter_count": 0,
  "articles_without_source_note_count": 0,
  "table_count": 63,
  "attachment_count": 39,
  "p_noi_dung_count": 477,
  "empty_p_noi_dung_count": 477,
  "article_relation_count": 928,
  "structure_relation_count": 6,
  "same_topic_unresolved_relation_count": 0
}
```

`repeated_unit_occurrence_count = 55` không phải lỗi. Đây là các trường hợp số khoản hoặc nhãn điểm lặp trong phạm vi một điều; parser thêm `unit_occurrence` để bảo đảm `unit_id` duy nhất.

## 5.3. Parser tests

Bộ test hiện có 13 test và đã được người phát triển chạy thành công:

```text
13 passed
```

Các nhóm kiểm tra:

1. Parser version.
2. Topic metadata.
3. Tổng số điều.
4. Phân bố loại nguồn.
5. Article ID hợp lệ và duy nhất.
6. Unit ID không trùng.
7. Điều không rỗng.
8. Mọi điều có chapter.
9. Validation metrics.
10. Schema relation.
11. Khoản/điểm của Điều `20.2.NĐ.4.2`.
12. Preamble unit.
13. Mọi ID là string.

---

# 6. Parser canonical — contract không được phá vỡ

## 6.1. File parser core

```text
backend/app/ingestion/legal_parser.py
```

Vai trò:

- Đọc HTML.
- Nhận diện metadata đề mục.
- Parse chương và mục.
- Parse điều.
- Parse khoản, điểm, preamble và clause continuation.
- Parse bảng.
- Parse attachment.
- Parse ghi chú nguồn.
- Parse citation và relation.
- Tạo stable ID.
- Validation corpus.
- Không xử lý argparse.
- Không tự ghi file khi import.
- Không có side effect khi import.

Public API hiện cần được bảo toàn:

```python
PARSER_VERSION
parse_legal_document(...)
build_inspection_report(...)
validate_corpus(...)
```

## 6.2. CLI parser

```text
scripts/inspect_html.py
```

Vai trò:

- Nhận tham số terminal.
- Cấu hình logging.
- Gọi parser core.
- Ghi JSON.
- Ghi Markdown.
- In thống kê.
- Thực hiện strict exit khi validation fail.

Không đưa parser logic trở lại file CLI.

## 6.3. Canonical corpus schema

`articles_raw.json` có cấu trúc cấp cao:

```json
{
  "metadata": {},
  "structure": {},
  "structure_relations": [],
  "articles": []
}
```

Mỗi article có các trường quan trọng:

```text
document_id
source_file
source_sha256
parser_version
topic_code
topic_name
index
article_id
article_code
codification_code
source_type
article_title
heading
chapter
section
source_note
source_note_text
source_urls
source_document_id
content_blocks
content_units
content_text_raw
content_text
tables
attachments
citations
relations
warnings
```

`content_units` có thể gồm:

```text
preamble
clause
point
clause_continuation
table
```

Không đổi tên các field trên trong Ngày 3.

---

# 7. Lệnh preflight trước khi bắt đầu Ngày 3

Chạy tại repository root:

```bash
git pull origin main
git status --short
```

Kỳ vọng:

```text
Working tree clean
```

Chạy parser strict:

```bash
python3 scripts/inspect_html.py \
  data/raw/DeMuc_20.2_Lao_Dong.html \
  --strict \
  --expected-article-count 477 \
  --expected-source-counts '{"LQ":220,"NĐ":174,"TT":83}' \
  --expected-table-count 63 \
  --expected-attachment-count 39
```

Kỳ vọng:

```text
Articles: 477
Sources: {'LQ': 220, 'NĐ': 174, 'TT': 83}
Chapters: 17
Sections: 24
Tables: 63
Attachments: 39
Relations: 928 article + 6 structure
Validation: PASS
```

Chạy test parser:

```bash
PYTHONPATH=. backend/.venv/bin/pytest -q \
  backend/tests/test_legal_parser.py
```

Kỳ vọng:

```text
13 passed
```

Kiểm tra compile:

```bash
python3 -m compileall \
  backend/app/ingestion/legal_parser.py \
  scripts/inspect_html.py
```

Chỉ bắt đầu sửa `legal_chunker.py` khi các kiểm tra trên đều đạt.

---

# 8. Các vấn đề nhỏ đang mở nhưng không chặn Ngày 3

## 8.1. README quá ngắn

`README.md` hiện gần như chỉ có tiêu đề project.

Cần bổ sung trước khi demo hoặc nộp:

- Mục tiêu đề tài.
- Phạm vi corpus.
- Ba pipeline retrieval.
- Kiến trúc.
- Cách chạy Docker.
- Cách chạy parser.
- Cách chạy test.
- Cách tạo chunk.
- Cách index Qdrant và Neo4j.
- Trạng thái roadmap.

README không cần chặn việc xây chunker.

## 8.2. Nhật ký Ngày 2 chưa có trên GitHub

`docs/daily/` hiện chỉ có `.gitkeep`.

Nên thêm:

```text
docs/daily/day02.md
```

Nội dung đã được chuẩn bị ở phiên làm việc trước.

## 8.3. Makefile và import test

`Makefile` hiện chạy:

```makefile
test:
	cd backend && pytest
```

Trong khi `test_legal_parser.py` đang import:

```python
from backend.app.ingestion.legal_parser import ...
```

Cách đã chạy thành công là:

```bash
PYTHONPATH=. backend/.venv/bin/pytest -q backend/tests/test_legal_parser.py
```

Cần thống nhất sau:

- Hoặc sửa import test thành `from app...` và chạy từ `backend/`.
- Hoặc giữ import `from backend...` và sửa target Makefile chạy từ repository root với `PYTHONPATH=.`.

Không thay đổi kiểu import giữa chừng trong lúc đang viết chunker nếu chưa chạy lại toàn bộ test.

## 8.4. Logging

Parser đã có logger module, nhưng nên kiểm tra xem đã có đủ các lệnh `logger.info`, `logger.warning` và `logger.error` thực tế hay chưa.

Không log từng điều vì sẽ gây nhiễu.

## 8.5. Dependency chưa khóa version

`requirements.txt` và Docker image cần khóa version trước giai đoạn đánh giá cuối/deploy.

Chưa cần thực hiện trong Ngày 3.

---

# 9. Mục tiêu Ngày 3

## 9.1. Mục tiêu chính

Chuyển canonical articles thành retrieval chunks có cấu trúc pháp lý, ổn định, kiểm thử được và sử dụng chung cho cả ba pipeline.

Luồng bắt buộc:

```text
data/raw/DeMuc_20.2_Lao_Dong.html
                    ↓
          legal_parser.py
                    ↓
 data/processed/articles_raw.json
                    ↓
          legal_chunker.py
                    ↓
 data/processed/legal_chunks.jsonl
                    ↓
 data/processed/chunking_summary.json
```

## 9.2. Đầu ra Ngày 3

Cần tạo:

```text
backend/app/ingestion/legal_chunker.py
backend/tests/test_legal_chunker.py
scripts/build_chunks.py
docs/design/chunking_strategy.md
data/processed/legal_chunks.jsonl
data/processed/chunking_summary.json
```

Lưu ý `.gitignore` hiện bỏ qua phần lớn `data/processed/*`.

Quyết định Git:

- Nên commit `chunking_summary.json` nếu nhỏ.
- Có thể không commit `legal_chunks.jsonl` nếu được sinh tự động và lớn.
- Nếu cần reproducibility trực tiếp, cân nhắc ngoại lệ riêng cho file chunk sau khi kiểm tra dung lượng.

---

# 10. Chiến lược chunking đã chọn

## 10.1. Tên chiến lược

**Structural Parent–Child Chunking with token-size fallback**

## 10.2. Parent và child

- **Parent:** toàn bộ Điều trong canonical corpus.
- **Child:** đơn vị truy xuất đưa vào Qdrant.

Child có thể là:

```text
article
clause
points
preamble
table
fallback_segment
```

Parent article không nhất thiết được embed riêng trong mọi trường hợp, nhưng phải được giữ để:

- Truy vết nguồn.
- Ghép context.
- Lấy toàn văn điều khi cần.
- Xây graph.
- Hiển thị citation.

## 10.3. Thông số khởi đầu

```python
TARGET_TOKENS = 500
MAX_TOKENS = 750
FALLBACK_OVERLAP = 80
```

Đây là hyperparameter khởi đầu, không phải tiêu chuẩn pháp lý tuyệt đối.

Phải lưu các thông số vào `chunking_summary.json`.

## 10.4. Quy tắc chunking

### Quy tắc 1 — Điều ngắn

Nếu toàn bộ nội dung điều không vượt `MAX_TOKENS`:

```text
Tạo một article chunk.
```

### Quy tắc 2 — Điều dài có khoản

Nếu điều vượt `MAX_TOKENS` và có khoản:

```text
Tách theo khoản.
```

Không trộn hai khoản khác nhau vào cùng một chunk chỉ để đạt target token.

### Quy tắc 3 — Khoản dài có điểm

Nếu một khoản vượt `MAX_TOKENS` và có điểm:

```text
Nhóm các điểm liên tiếp theo thứ tự.
Target khoảng 500 token.
Không vượt 750 token.
Không cắt giữa một điểm nếu điểm vẫn nằm dưới max.
```

Ví dụ chunk key:

```text
{article_id}|clause=1|points=a-d
```

### Quy tắc 4 — Đơn vị pháp lý vẫn quá dài

Nếu một clause, point hoặc preamble đơn lẻ vẫn vượt `MAX_TOKENS`:

```text
Dùng sentence-aware fallback.
Target 500–600 token.
Max 750 token.
Overlap 80 token.
```

Chỉ sử dụng overlap ở fallback theo câu.

Không overlap giữa:

- Hai khoản.
- Hai nhóm điểm.
- Bảng và nội dung văn bản.

### Quy tắc 5 — Preamble

Nội dung đứng trước khoản 1:

- Gắn vào child đầu tiên nếu không vượt max.
- Nếu dài, tạo chunk riêng:

```text
{article_id}|preamble
```

### Quy tắc 6 — Clause continuation

`clause_continuation` phải đi cùng khoản hiện hành.

Không được biến continuation thành một khoản mới.

Khi nhóm clause:

```text
clause
+ clause_continuation
+ các point thuộc cùng clause
```

### Quy tắc 7 — Bảng

Bảng tạo chunk riêng.

Chunk bảng phải giữ:

- `table_id`.
- Tiêu đề hoặc context của điều.
- Header cột.
- Dòng dữ liệu.
- Số thứ tự segment nếu bảng dài.

Nếu bảng dài:

```text
Lặp lại header cho từng segment.
Chia theo nhóm dòng.
Không chia giữa một hàng.
```

### Quy tắc 8 — Attachment

Attachment chỉ lưu metadata:

```text
filename
href
article_id
source
```

Chưa thực hiện OCR hoặc embed nội dung attachment trong Ngày 3.

### Quy tắc 9 — Citation và relation

Không đưa toàn bộ đoạn `pChiDan` dài vào embedding content mặc định.

Giữ relation trong metadata để:

- Xây Neo4j.
- Graph expansion.
- Provenance.

Có thể thêm một nhãn ngắn như:

```text
Quan hệ: RELATED_TO
```

nhưng tránh làm chunk bị nhiễu bởi danh sách liên kết dài.

### Quy tắc 10 — Source note

Trong payload giữ đầy đủ:

```text
source_note_text
source_document_id
source_urls
```

Trong nội dung embed chỉ thêm nhãn nguồn ngắn, ví dụ:

```text
Nguồn: Bộ luật Lao động 2019, Điều 1.
```

Không lặp toàn bộ URL vào nội dung embedding.

---

# 11. Token counting

## 11.1. Yêu cầu

Chunker phải có một abstraction rõ ràng:

```python
TokenCounter
```

Hoặc tối thiểu:

```python
def count_tokens(text: str) -> int:
    ...
```

## 11.2. Không gắn cứng sai tokenizer

Embedding model cuối chưa khóa hoàn toàn.

Có hai hướng hợp lệ:

### Hướng A — Tạm dùng tokenizer cố định

Ví dụ:

```text
cl100k_base
```

Phải ghi rõ trong summary:

```text
tokenizer_name
tokenizer_version
```

### Hướng B — Token counter có thể inject

```python
def build_legal_chunks(
    canonical_corpus,
    *,
    token_counter,
    target_tokens=500,
    max_tokens=750,
    fallback_overlap=80,
):
    ...
```

Hướng B linh hoạt hơn.

Không dùng `len(text.split())` rồi gọi đó là token count mà không ghi rõ đó chỉ là estimate.

---

# 12. Stable chunk ID

## 12.1. Human-readable key

Mỗi chunk có `chunk_key` ổn định.

Ví dụ:

```text
{article_id}|article
{article_id}|preamble
{article_id}|clause=1
{article_id}|clause=1|points=a-d
{article_id}|clause=1|segment=2
{article_id}|table=1|segment=1
```

## 12.2. Machine ID

Dùng UUIDv5 từ `chunk_key`.

Ví dụ:

```python
from uuid import UUID, uuid5

CHUNK_NAMESPACE = UUID("...")
chunk_id = str(uuid5(CHUNK_NAMESPACE, chunk_key))
```

Yêu cầu:

- Chạy lại với cùng input phải sinh cùng `chunk_id`.
- Không dùng UUIDv4.
- Không dùng vị trí list đơn thuần làm ID.
- Không thay đổi namespace sau khi đã index production, trừ khi có migration.

---

# 13. Schema chunk đề xuất

Mỗi chunk nên có tối thiểu:

```json
{
  "chunk_id": "uuid-v5-string",
  "chunk_key": "article-id|clause=1",
  "parent_article_id": "article-id",
  "document_id": "phap-dien:20.2",
  "topic_code": "20.2",
  "topic_name": "Lao động",

  "article_code": "20.2.LQ.1",
  "codification_code": "20.2.LQ.1",
  "article_title": "Phạm vi điều chỉnh",
  "heading": "Điều 20.2.LQ.1. Phạm vi điều chỉnh",

  "chapter_id": "chapter-anchor-id",
  "chapter_number": "I",
  "chapter_title": "NHỮNG QUY ĐỊNH CHUNG",

  "section_id": null,
  "section_number": null,
  "section_title": null,

  "chunk_type": "article",
  "unit_type": "article",
  "clause_number": null,
  "point_labels": [],
  "table_id": null,
  "segment_index": 1,

  "content": "Nội dung dùng để embed và retrieve.",
  "body_text": "Nội dung pháp lý gốc của chunk.",
  "token_count": 420,

  "source_type": "LQ",
  "source_document_id": "vbpl:item:139264",
  "source_note_text": "...",
  "source_urls": [],

  "relation_target_ids": [],
  "relation_target_codes": [],

  "parser_version": "1.2.0",
  "chunker_version": "1.0.0"
}
```

`content` là trường mà `document_factory.py` có thể dùng làm `page_content`.

Các field còn lại được đưa vào metadata.

## 13.1. Nội dung `content`

Nội dung embed nên có header ngắn:

```text
Đề mục: 20.2 — Lao động
Chương I — NHỮNG QUY ĐỊNH CHUNG
Điều 20.2.LQ.1 — Phạm vi điều chỉnh
Nguồn: LQ

Bộ luật Lao động quy định...
```

Không đưa:

- URL dài.
- Toàn bộ citation paragraph.
- JSON.
- Metadata kỹ thuật không mang nghĩa ngữ nghĩa.

---

# 14. API chunker nên triển khai

Khuyến nghị:

```python
def build_legal_chunks(
    canonical_corpus: dict,
    *,
    target_tokens: int = 500,
    max_tokens: int = 750,
    fallback_overlap: int = 80,
    token_counter=None,
) -> list[dict]:
    """
    Chuyển canonical corpus thành retrieval chunks.
    Không ghi file.
    Không print.
    Không gọi Qdrant hoặc Neo4j.
    """
```

Thêm:

```python
def validate_chunks(
    canonical_corpus: dict,
    chunks: list[dict],
    *,
    max_tokens: int = 750,
) -> dict:
    ...
```

Thêm:

```python
def build_chunking_summary(
    canonical_corpus: dict,
    chunks: list[dict],
    validation: dict,
    config: dict,
) -> dict:
    ...
```

Module core:

```text
backend/app/ingestion/legal_chunker.py
```

Không chứa argparse.

CLI:

```text
scripts/build_chunks.py
```

Có thể nhận:

```text
--input
--output
--summary-output
--target-tokens
--max-tokens
--overlap
--strict
--log-level
```

---

# 15. Validation chunk bắt buộc

`validate_chunks` phải kiểm tra:

```text
[ ] Có chunk.
[ ] Mọi chunk_id là string.
[ ] Không có chunk_id trùng.
[ ] Không có chunk_key trùng.
[ ] Chạy lại sinh cùng chunk_id.
[ ] Mọi chunk có parent_article_id hợp lệ.
[ ] Không chunk nào chứa nội dung từ hai article.
[ ] 477 article đều được coverage.
[ ] Không article nào mất toàn bộ content.
[ ] Mọi chunk có content không rỗng.
[ ] token_count không âm.
[ ] Không vượt MAX_TOKENS, trừ exception được ghi rõ.
[ ] Clause không bị gán sai article.
[ ] Point không bị gán sai clause.
[ ] Preamble được giữ.
[ ] Clause continuation được giữ.
[ ] 63 bảng được coverage.
[ ] Attachment metadata không bị mất.
[ ] Source metadata được giữ.
[ ] Relation metadata được giữ.
```

Các exception quá max phải có danh sách:

```text
oversized_chunks
```

Không được im lặng bỏ qua.

---

# 16. Test chunker bắt buộc

Tạo:

```text
backend/tests/test_legal_chunker.py
```

Dùng fixture session scope để parse/chunk một lần.

Test tối thiểu:

1. `build_legal_chunks` trả về list không rỗng.
2. Tất cả chunk có `chunk_id`, `chunk_key`, `content`.
3. `chunk_id` duy nhất.
4. `chunk_key` duy nhất.
5. Stable ID: chạy hai lần cho cùng input cho cùng danh sách ID.
6. Tất cả parent article ID tồn tại.
7. Coverage đủ 477 article.
8. Không chunk nào trộn article.
9. Chunk content không rỗng.
10. Điều ngắn tạo article chunk.
11. Điều dài chia theo clause.
12. Clause dài chia theo nhóm point hoặc fallback.
13. Preamble được giữ.
14. `clause_continuation` gắn đúng clause.
15. Bảng tạo chunk riêng.
16. Tổng table coverage bằng 63.
17. Source metadata tồn tại.
18. Relation metadata tồn tại.
19. Không vượt max token, trừ exception.
20. Validation chunk PASS.
21. UUIDv5 là string hợp lệ.
22. Điều `20.2.NĐ.4.2` không gán nhầm điểm giữa khoản 1 và khoản 2.

Không truy cập internet.

Không gọi Qdrant.

Không gọi Neo4j.

---

# 17. `chunking_summary.json` cần chứa

Ví dụ:

```json
{
  "chunker_version": "1.0.0",
  "input_document_id": "phap-dien:20.2",
  "article_count": 477,
  "chunk_count": 0,

  "config": {
    "target_tokens": 500,
    "max_tokens": 750,
    "fallback_overlap": 80,
    "tokenizer": "..."
  },

  "chunk_type_counts": {
    "article": 0,
    "clause": 0,
    "points": 0,
    "preamble": 0,
    "table": 0,
    "fallback": 0
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
    "table_count_expected": 63,
    "table_count_covered": 63
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

# 18. Thứ tự triển khai Ngày 3

## Bước 1 — Viết tài liệu trước

Tạo:

```text
docs/design/chunking_strategy.md
```

Nội dung:

- Lý do chọn structural chunking.
- Parent–child.
- Token parameters.
- Stable ID.
- Table handling.
- Attachment handling.
- Relation handling.
- Metadata schema.
- Validation.
- Hạn chế.

## Bước 2 — Viết token counter

Tách riêng logic đếm token.

Viết test nhỏ cho token counter.

## Bước 3 — Tạo helper chunk

Viết hàm tạo:

```text
chunk_key
chunk_id
content header
metadata
```

## Bước 4 — Xử lý article ngắn

Một article chunk.

## Bước 5 — Xử lý clause

Nhóm:

```text
clause
clause_continuation
points
```

## Bước 6 — Xử lý point grouping

Greedy grouping theo thứ tự.

Không đảo thứ tự.

## Bước 7 — Sentence fallback

Chỉ dùng khi đơn vị pháp lý vẫn quá dài.

## Bước 8 — Table chunks

Tách riêng và giữ header.

## Bước 9 — Validation và summary

Không ghi file trước khi validation core hoạt động.

## Bước 10 — CLI build_chunks.py

Core module không phụ thuộc CLI.

## Bước 11 — Test đầy đủ

Chạy parser tests và chunker tests.

## Bước 12 — Sinh artifact

```text
legal_chunks.jsonl
chunking_summary.json
```

---

# 19. Lệnh dự kiến sau khi hoàn thành Ngày 3

Compile:

```bash
python3 -m compileall \
  backend/app/ingestion/legal_chunker.py \
  scripts/build_chunks.py
```

Build chunks:

```bash
python3 scripts/build_chunks.py \
  --input data/processed/articles_raw.json \
  --output data/processed/legal_chunks.jsonl \
  --summary-output data/processed/chunking_summary.json \
  --target-tokens 500 \
  --max-tokens 750 \
  --overlap 80 \
  --strict
```

Test parser và chunker:

```bash
PYTHONPATH=. backend/.venv/bin/pytest -q \
  backend/tests/test_legal_parser.py \
  backend/tests/test_legal_chunker.py
```

Kiểm tra Git:

```bash
git status --short
git diff --stat
git diff --check
```

---

# 20. Tiêu chí hoàn thành Ngày 3

Chỉ đánh dấu Ngày 3 hoàn thành khi:

```text
[ ] chunking_strategy.md hoàn chỉnh.
[ ] legal_chunker.py không còn NotImplementedError.
[ ] build_chunks.py chạy được.
[ ] legal_chunks.jsonl sinh được.
[ ] chunking_summary.json sinh được.
[ ] 477 article được coverage.
[ ] 63 bảng được coverage.
[ ] Không trùng chunk_id.
[ ] Không trùng chunk_key.
[ ] Stable ID chạy lại không đổi.
[ ] Không chunk rỗng.
[ ] Không trộn hai article.
[ ] Không gán sai clause/point.
[ ] Source metadata được giữ.
[ ] Relation metadata được giữ.
[ ] Oversized chunk được báo cáo.
[ ] Validation chunk PASS.
[ ] Parser tests vẫn pass.
[ ] Chunker tests pass.
[ ] Nhật ký Ngày 3 được viết.
[ ] Commit và push GitHub.
```

---

# 21. Những việc không được làm trong Ngày 3

Không:

- Viết lại parser canonical.
- Đổi schema article mà không có migration.
- Index Qdrant trước khi chunk validation pass.
- Index Neo4j trước khi quan hệ và ID ổn định.
- Trộn nhiều article vào một chunk.
- Cắt giữa clause hoặc point khi chưa cần fallback.
- Dùng overlap giữa các đơn vị pháp lý.
- Dùng UUIDv4.
- Bỏ bảng.
- OCR attachment.
- Nhét citation paragraph dài vào mọi chunk.
- Dùng hai cách chunk khác nhau cho Sparse và Hybrid.
- Tối ưu theo kết quả đánh giá trước khi có baseline.
- Commit file môi trường `.env`.
- Commit ZIP trùng corpus.

---

# 22. Git workflow đề xuất cho Ngày 3

Tạo branch:

```bash
git checkout -b day-03-legal-chunking
```

Commit theo từng phần:

```text
docs: define legal chunking strategy
feat: implement structural legal chunker
test: add legal chunker validation tests
chore: add chunk building CLI and summary
docs: add day 3 development journal
```

Trước push:

```bash
git status
git diff --check
PYTHONPATH=. backend/.venv/bin/pytest -q
```

Push:

```bash
git push -u origin day-03-legal-chunking
```

Nếu tiếp tục làm trực tiếp trên `main`, phải kiểm tra kỹ trước commit; branch riêng vẫn an toàn hơn.

---

# 23. Prompt ngắn cho coding agent tiếp nhận Ngày 3

Có thể giao cho coding agent bằng prompt sau:

```text
Làm việc trong repository vn-labor-law-rag.

Trước tiên đọc:
- HANDOFF Ngày 2 → Ngày 3.
- backend/app/ingestion/legal_parser.py
- backend/app/ingestion/legal_chunker.py
- backend/tests/test_legal_parser.py
- data/processed/articles_raw.json schema
- docs/design/html_structure.md

Không viết lại parser và không đổi canonical schema.

Nhiệm vụ Ngày 3:
1. Viết docs/design/chunking_strategy.md.
2. Triển khai Structural Parent–Child Chunking trong legal_chunker.py.
3. Dùng target=500, max=750, fallback overlap=80.
4. Chia theo Điều → Khoản → nhóm Điểm → sentence fallback.
5. Bảng là chunk riêng.
6. Attachment chỉ metadata.
7. Dùng chunk_key ổn định và UUIDv5.
8. Tạo validate_chunks và chunking summary.
9. Tạo scripts/build_chunks.py.
10. Tạo backend/tests/test_legal_chunker.py.
11. Bảo đảm coverage 477 điều và 63 bảng.
12. Không gọi Qdrant hoặc Neo4j.
13. Chạy lại toàn bộ parser tests.

Không commit hoặc push cho đến khi người dùng xác nhận.
```

---

# 24. Nguồn tham chiếu kỹ thuật trong repository

Các file cần đọc đầu tiên:

```text
README.md
Makefile
.gitignore
docker-compose.yml
backend/requirements.txt
backend/app/ingestion/legal_parser.py
backend/app/ingestion/legal_chunker.py
backend/app/ingestion/document_factory.py
backend/app/ingestion/index_qdrant.py
backend/app/ingestion/index_neo4j.py
backend/tests/test_legal_parser.py
scripts/inspect_html.py
docs/design/html_structure.md
docs/design/project_scope.md
data/processed/html_inspection.json
data/processed/parser_report.json
```

Repository:

```text
https://github.com/hauct131/vn-labor-law-rag.git
```

---

# 25. Kết luận bàn giao

Điểm xuất phát của Ngày 3 đã đủ chắc:

```text
Corpus có sẵn trên GitHub
Parser canonical ổn định
Validation PASS
13 parser tests PASS
Schema content_units đủ chi tiết
Chunker chưa triển khai
```

Rủi ro lớn nhất của Ngày 3 là làm mất cấu trúc pháp lý khi chia chunk. Vì vậy phải ưu tiên:

```text
structure preservation
stable IDs
coverage validation
reproducibility
```

Không cần theo đuổi số lượng chunk thấp nhất. Mục tiêu là tạo tập chunk có chất lượng truy xuất, có thể giải thích, có thể tái tạo và dùng chung cho cả Sparse, Hybrid và Graph-enhanced RAG.