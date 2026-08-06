# Vietnamese Labor Law RAG

Hệ thống hỏi đáp pháp luật lao động Việt Nam sử dụng Retrieval-Augmented Generation (RAG). Ứng dụng truy hồi căn cứ từ corpus pháp luật đã khóa, sinh câu trả lời có dẫn nguồn và từ chối trả lời khi câu hỏi nằm ngoài phạm vi hoặc chứng cứ không đủ.

> Corpus canonical Word 804 là **bản phát hành kỹ thuật cuối cùng đã đóng băng**
> cho hệ thống, benchmark và báo cáo hiện tại. Corpus, hash, golden split và cấu
> hình retrieval không được tiếp tục thay đổi.
>
> Corpus chưa được xác nhận cuối cùng về hiệu lực và tính đúng pháp lý bởi người
> có thẩm quyền. Hệ thống không thay thế tư vấn của luật sư hoặc cơ quan nhà nước.

## 1. Trạng thái phiên bản hiện tại

### Chức năng sản phẩm

- Hỏi đáp RAG bằng Sparse, Dense hoặc Hybrid, có trích nguồn;
- thư viện 18 văn bản và nội dung điều luật từ corpus canonical;
- lịch sử hội thoại lưu bền vững trong PostgreSQL;
- đánh dấu và mở lại câu trả lời cùng snapshot nguồn đã sử dụng.

Lịch sử chỉ tổ chức dữ liệu đã hỏi. Retrieval của từng câu vẫn chạy độc lập và
không sử dụng message trước làm context, nhằm giữ nguyên cấu hình retrieval đã khóa.

### Corpus runtime

| Thuộc tính | Giá trị |
|---|---|
| Release | `labor-law-canonical-word-20260804-164432-candidate` |
| Chunk artifact | `data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl` |
| Số chunk | `804` |
| Corpus SHA256 | `fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307` |
| Physical collection | `labor_law_canonical_word_20260804_fdbec539` |
| Alias local/demo | `labor_law_dev` |
| Alias runtime legacy | `labor_law_active` |

`labor_law_active` hiện vẫn trỏ đến collection legacy từng được runtime sử dụng.
Collection legacy này cũng chưa hoàn thành authority review và không được xem là
bản đã kiểm chứng pháp lý. Nó chỉ được giữ lại để rollback kỹ thuật.

Demo local hiện sử dụng corpus canonical Word 804 thông qua alias `labor_law_dev`.

### Nguồn dữ liệu

Corpus gồm 18 văn bản được pipeline tự động thu thập từ web và lưu thành
snapshot bất biến trước khi parse, chuẩn hóa và chia chunk.

- 17 văn bản được lấy từ các website chính thức của cơ quan nhà nước;
- 1 văn bản sử dụng cơ chế fallback sang Cơ sở dữ liệu quốc gia về văn bản
  pháp luật.

Về mặt kỹ thuật, 17 nguồn chính thức gồm:

- 15 tài liệu được tải qua adapter `official-gazette-word`;
- 2 tài liệu DOCX được pipeline tải trực tiếp từ website chính thức của cơ quan
  ban hành qua adapter `official-docx`.

### Golden benchmark

Golden source:

```text
data/evaluation/golden_questions_v4_canonical_word_candidate.json
```

Split đã khóa:

```text
data/evaluation/splits/canonical_word_804/
```

Thành phần:

- source: 45 câu;
- development: 31 câu;
- test: 13 câu;
- disabled: 1 câu.

Test split đã được mở đúng một lần. Không tuning lại retrieval bằng test split.

Cấu hình retrieval đã khóa:

```text
top_k           = 5
candidate_k     = 30
rrf_k           = 60
dense_weight    = 0.9
sparse_weight   = 0.1
```

Kết quả retrieval test đã khóa:

| Chỉ số | Kết quả |
|---|---:|
| Any article hit@5 | 1.000000 |
| All article hit@5 | 0.923077 |
| Article recall@5 | 0.948718 |
| Article MRR | 0.848718 |
| All evidence hit@5 | 0.692308 |
| Evidence recall@5 | 0.817949 |

Các chỉ số trên đo retrieval, không chứng minh câu trả lời cuối cùng đúng hoàn toàn về pháp lý.

## 2. Kiến trúc runtime

```text
Vite frontend
      |
      +--> localStorage: client UUID + hội thoại đang mở
      |
      v
FastAPI API
      |
      +--> PostgreSQL: hội thoại, message, snapshot nguồn, bookmark
      |
      +--> Sparse retrieval: VnCoreNLP + BM25
      |
      +--> Dense retrieval: FastEmbed E5 + Qdrant
      |
      +--> Hybrid retrieval: weighted Reciprocal Rank Fusion
      |
      v
Structured answer guardrail
      |
      v
OpenRouter LLM
```

Graph RAG và Neo4j chưa thuộc MVP runtime công khai. Giao diện hiện chỉ sử dụng Sparse, Dense và Hybrid.

### Thành phần chính

- `frontend/`: giao diện Vite.
- `backend/app/api/`: API FastAPI.
- `backend/app/retrieval/`: truy hồi Sparse, Dense và Hybrid.
- `backend/app/services/`: điều phối RAG, citation và guardrail.
- `backend/app/models/` và `backend/app/repositories/`: dữ liệu hội thoại PostgreSQL.
- `backend/migrations/`: DDL quản lý schema dữ liệu nghiệp vụ.
- `data/releases/`: các corpus release bất biến.
- `data/evaluation/`: golden set, split và báo cáo benchmark.
- `scripts/`: ingestion, index, audit và evaluation.
- `config/`: quyết định hiệu lực, corpus và runtime binding.

## 3. Structured answer guardrail

LLM phải trả về một trong ba trạng thái:

| Trạng thái | Ý nghĩa |
|---|---|
| `answerable` | Câu hỏi thuộc pháp luật lao động và nguồn đủ để trả lời |
| `out_of_scope` | Vấn đề chính không thuộc pháp luật lao động Việt Nam |
| `insufficient_evidence` | Có thể đúng phạm vi nhưng nguồn chưa đủ căn cứ |

Với câu trả lời `answerable`, backend bắt buộc:

- có ít nhất một citation;
- citation phải thuộc các nguồn đã cung cấp cho LLM;
- inline citation phải khớp với `cited_source_ids`;
- chỉ trả về các source thực sự được trích dẫn;
- loại source trùng lặp hoặc thiếu metadata cần thiết.

Với `out_of_scope` và `insufficient_evidence`, hệ thống không cho phép citation giả. JSON không hợp lệ, citation ngoài context hoặc lỗi provider đều được xử lý theo hướng fail-closed.

## 4. API

Base URL mặc định:

```text
http://localhost:8000/api
```

Endpoint chính:

| Method | Endpoint | Vai trò |
|---|---|---|
| `GET` | `/api/live` | Kiểm tra process backend |
| `GET` | `/api/health` | Health check tương thích |
| `GET` | `/api/ready` | Kiểm tra corpus, Qdrant và authority gate |
| `POST` | `/api/ask` | Hỏi đáp và tùy chọn lưu vào hội thoại |
| `GET` | `/api/sources/{article_code}` | Đọc thông tin nguồn theo mã điều |
| `GET` | `/api/documents` | Danh sách thư viện văn bản |
| `GET` | `/api/documents/{document_id}` | Chi tiết một văn bản |
| `GET` | `/api/documents/{document_id}/articles` | Danh sách điều thuộc văn bản |
| `GET` | `/api/conversations` | Danh sách hội thoại của client UUID |
| `POST` | `/api/conversations` | Tạo hội thoại rỗng |
| `GET` | `/api/conversations/{id}` | Đọc transcript và snapshot nguồn |
| `PATCH` | `/api/conversations/{id}` | Đổi tên hội thoại |
| `DELETE` | `/api/conversations/{id}` | Xóa hội thoại |
| `GET` | `/api/bookmarks` | Danh sách câu trả lời đã đánh dấu |
| `PUT` | `/api/bookmarks/{message_id}` | Tạo hoặc cập nhật bookmark |
| `DELETE` | `/api/bookmarks/{message_id}` | Bỏ bookmark |

Swagger UI:

```text
http://localhost:8000/docs
```

Các endpoint hội thoại dùng header `X-Client-Id` là UUID ẩn danh của trình
duyệt. Đây là định danh MVP, không phải xác thực người dùng. Chi tiết dữ liệu,
API và ERD nằm tại:

```text
docs/CONVERSATION_HISTORY_BOOKMARKS.md
docs/ERD_CONVERSATION_HISTORY.md
```

## 5. Chạy local bằng Docker

### Yêu cầu

- Docker Engine;
- Docker Compose v2;
- collection Qdrant 804 đã được index;
- alias `labor_law_dev` trỏ đến `labor_law_canonical_word_20260804_fdbec539`;
- cổng PostgreSQL cấu hình trong `.env` chưa bị dịch vụ khác chiếm dụng;
- API key OpenRouter khi cần sinh câu trả lời thật.

### Tạo file môi trường

```bash
cp .env.example .env
```

Điền khóa thật và đổi mật khẩu PostgreSQL local trong `.env`:

```dotenv
OPENROUTER_API_KEY=...
POSTGRES_PASSWORD=mot-mat-khau-local-khong-commit
DATABASE_URL=sqlite+pysqlite:///./application.db
DATABASE_URL_DOCKER=postgresql+psycopg://labor_law:mot-mat-khau-local-khong-commit@postgres:5432/labor_law_rag
```

Ba giá trị `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` phải khớp với
`DATABASE_URL_DOCKER`. Ký tự đặc biệt trong mật khẩu phải được URL-encode trong
URL kết nối. `DATABASE_URL` dùng SQLite khi chạy backend trực tiếp ngoài Docker.
Không commit `.env` hoặc khóa API.

### Chuẩn bị model runtime

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  --profile tools \
  run --rm runtime-assets
```

### Khởi động demo local

Luôn dùng cả hai compose file để backend sử dụng alias `labor_law_dev` và bỏ authority gate chỉ trong môi trường demo:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up --build
```

Truy cập:

```text
Frontend: http://localhost:5173
Backend:  http://localhost:8000
Swagger:  http://localhost:8000/docs
Qdrant:   http://localhost:6333/dashboard
```

Xem log:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  logs -f backend frontend
```

Dừng dịch vụ:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  down
```

Không thêm `-v` trừ khi chủ động muốn xóa Docker volumes. Tùy chọn `-v`
cũng xóa `postgres_data`, tức toàn bộ lịch sử hội thoại và bookmark local.

PostgreSQL local mặc định chỉ bind ở `127.0.0.1:5432`. Corpus vẫn nằm trong
JSON/JSONL release bất biến và vector vẫn nằm ở Qdrant; chỉ dữ liệu nghiệp vụ
phát sinh được lưu trong PostgreSQL.

## 6. Chạy backend không dùng Docker

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements.txt
```

Chạy API:

```bash
PYTHONPATH=backend \
.venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
```

Qdrant, VnCoreNLP model và các biến môi trường vẫn phải được chuẩn bị trước.

## 7. Chạy frontend

```bash
cd frontend
npm ci
npm run dev
```

Production build:

```bash
cd frontend
npm ci
npm run build
```

## 8. Kiểm thử

### Toàn bộ backend

Từ thư mục gốc:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests
```

Số lượng test có thể tăng khi bổ sung chức năng. Báo cáo CI hoặc báo cáo bàn
giao của đúng commit là nguồn xác nhận, không sử dụng một con số cũ trong README.
Các test integration bị skip có thể yêu cầu model cache, corpus runtime hoặc
external service.

Test riêng cho hội thoại và bookmark:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -q \
  backend/tests/test_conversation_history.py \
  backend/tests/test_ask_history_api.py
```

### Kiểm tra frontend

```bash
cd frontend
npm ci
npm run build
```

### Kiểm tra Docker Compose

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  config
```

Chạy toàn bộ cổng kiểm tra của chức năng mới và ghi báo cáo:

```bash
bash scripts/verify_conversation_bookmarks.sh
```

Sau khi Compose đang chạy, smoke test runtime tạo một hội thoại thật, bookmark,
restart backend, reload từ PostgreSQL, đổi tên và dọn dữ liệu thử nghiệm:

```bash
python3 scripts/smoke_conversation_bookmarks.py --restart-backend
```

Lệnh smoke gọi `/api/ask` một lần và do đó sử dụng một lượt provider.

## 9. Continuous Integration

Repository có hai workflow:

```text
.github/workflows/application-ci.yml
.github/workflows/ingestion-ci.yml
```

`Application CI` kiểm tra:

- toàn bộ backend test suite;
- frontend production build;
- cấu hình Docker Compose.

`VBPL ingestion CI` kiểm tra crawler và các invariant fail-closed của ingestion.

CI phải chạy trên checkout sạch. Vì vậy những evidence snapshot và quality artifact được test tham chiếu phải được Git theo dõi, không chỉ tồn tại trên máy phát triển.

## 10. Quy tắc an toàn dữ liệu và release

- Không chỉnh sửa corpus release đã khóa tại chỗ.
- Candidate mới phải dùng release ID và thư mục mới.
- Không thay đổi golden test sau khi đã xem kết quả test.
- Không tuning retrieval bằng test split.
- Không chuyển production alias khi authority review còn pending.
- Không commit `.env`, API key, model cache hoặc Qdrant storage.
- Mọi snapshot bắt buộc phải khớp SHA256 trong manifest hoặc quyết định hiệu lực.
- Giữ collection cũ để rollback khi promotion collection mới.

## 11. Giới hạn hiện tại

- Authority review và legal-effect review chưa hoàn tất.
- Retrieval benchmark không thay thế đánh giá độ đúng pháp lý của câu trả lời.
- OpenRouter và model miễn phí có thể thay đổi chất lượng hoặc khả dụng.
- Client UUID hiện là định danh ẩn danh, chưa phải tài khoản hoặc cơ chế xác thực.
- Lịch sử không đồng bộ giữa các trình duyệt nếu chưa có chức năng đăng nhập.
- Graph RAG chưa được đưa vào API và giao diện chính thức.
- Alias runtime legacy chưa được chuyển sang corpus canonical Word 804; cả collection legacy và collection 804 đều chưa hoàn thành authority review.

## 12. Trạng thái phát hành và triển khai

### Bản phát hành kỹ thuật

Corpus canonical Word 804 là bản kỹ thuật cuối cùng đã đóng băng cho đồ án:

```text
Release: labor-law-canonical-word-20260804-164432-candidate
Chunks: 804
SHA256: fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307
```

Hậu tố `candidate` được giữ nguyên vì release ID, đường dẫn và manifest đã được
khóa. Hậu tố này không có nghĩa corpus kỹ thuật vẫn đang được chỉnh sửa.

### Local/demo

```text
labor_law_dev
    -> labor_law_canonical_word_20260804_fdbec539
```

Demo local đang sử dụng corpus canonical Word 804 cuối cùng.

### Alias runtime legacy

```text
labor_law_active
    -> labor_law_20260728_fd35bb1a
```

Collection này là bản legacy từng được runtime sử dụng. Nó cũng chưa được xác
nhận pháp lý cuối cùng và chỉ được giữ lại để rollback kỹ thuật.

Sau khi corpus 804 hoàn thành authority review và legal-effect review, alias
`labor_law_active` mới được chuyển sang collection 804. Sau khi chuyển phải chạy
readiness check, retrieval smoke test và kiểm tra rollback.
