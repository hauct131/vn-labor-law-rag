# Vietnamese Labor Law RAG

Hệ thống hỏi đáp pháp luật lao động Việt Nam sử dụng RAG, truy hồi kết hợp
Sparse/Dense, sinh câu trả lời bằng OpenRouter và hiển thị căn cứ được trích dẫn.
Ứng dụng còn hỗ trợ tài khoản, lịch sử hội thoại, bookmark và rà soát hợp đồng
lao động PDF/DOCX.

> **Phạm vi phát hành:** bản hiện tại đã vượt qua kiểm tra kỹ thuật và runtime.
> Corpus và kết quả retrieval chưa hoàn thành authority review, vì vậy hệ thống
> không thay thế tư vấn của luật sư hoặc cơ quan nhà nước.

## 1. Trạng thái đã xác minh

| Thành phần | Giá trị |
|---|---|
| Nhánh release | `integrate/final-release-verified-e4a39ba` |
| Baseline runtime đã xác minh | `70ee1c8318f30e0127936c4a6717907d88eaab1c` |
| Corpus | `labor-law-canonical-word-20260804-164432-candidate` |
| Số chunk | `804` |
| SHA256 | `fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307` |
| Qdrant collection | `labor_law_canonical_word_20260804_fdbec539` |
| Alias local/demo | `labor_law_dev` |
| Embedding | `intfloat/multilingual-e5-large`, 1024 chiều |
| Trạng thái kỹ thuật | `PASS` |
| Authority review | `PENDING` |

Dependency Python và các Docker base image của backend, frontend, PostgreSQL và
Qdrant đã được khóa. Frontend được cài bằng `package-lock.json` và `npm ci`.

## 2. Chức năng chính

- Hỏi đáp RAG bằng Sparse, Dense hoặc Hybrid RRF, có citation.
- Đăng ký, đăng nhập và đăng xuất bằng server-side session.
- Lưu lịch sử hội thoại và bookmark trong PostgreSQL.
- Phân tách dữ liệu theo tài khoản.
- Xem thư viện văn bản và nội dung điều luật.
- Rà soát hợp đồng PDF/DOCX theo bốn nhóm điều khoản.
- Lưu, mở lại và xóa báo cáo rà soát hợp đồng.

Kiến trúc runtime:

```text
Vite/React -> FastAPI -> PostgreSQL
                      -> VnCoreNLP + BM25
                      -> FastEmbed E5 + Qdrant
                      -> Hybrid RRF -> OpenRouter -> Citation guardrail
```

Graph RAG và Neo4j chưa thuộc runtime chính thức của bản này.

## 3. Yêu cầu

### Chạy ứng dụng bằng Docker

- Git;
- Docker Engine;
- Docker Compose v2;
- API key OpenRouter để sinh câu trả lời thật.

### Chạy kiểm thử đầy đủ

Ngoài Docker, máy cần có:

- Python 3.11 hoặc 3.12;
- Node.js và npm;
- Java JDK khi chạy backend trực tiếp ngoài Docker;
- Chromium do Playwright quản lý.

## 4. Cài đặt lần đầu

```bash
git clone https://github.com/hauct131/vn-labor-law-rag.git
cd vn-labor-law-rag
git switch integrate/final-release-verified-e4a39ba

cp .env.example .env
```

Mở `.env` và tối thiểu cấu hình:

```dotenv
OPENROUTER_API_KEY=your_key
POSTGRES_DB=labor_law_rag
POSTGRES_USER=labor_law
POSTGRES_PASSWORD=your_local_password
DATABASE_URL_DOCKER=postgresql+psycopg://labor_law:your_local_password@postgres:5432/labor_law_rag
SESSION_COOKIE_SECURE=false
```

`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` phải khớp với
`DATABASE_URL_DOCKER`. Nếu mật khẩu chứa ký tự đặc biệt, phải URL-encode phần mật
khẩu trong URL. Không commit `.env` hoặc API key.

Tạo môi trường kiểm thử host:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install \
  -r backend/requirements.txt \
  -c backend/requirements-lock.txt
.venv/bin/python -m pip install -r requirements-e2e.txt
.venv/bin/playwright install chromium
```

Frontend luôn cài theo lock file:

```bash
cd frontend
npm ci
cd ..
```

## 5. Các chế độ chạy

### 5.1. Docker local/demo — khuyến nghị

Lần đầu, chuẩn bị VnCoreNLP và cache embedding:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  --profile tools \
  run --rm runtime-assets
```

Khởi động toàn bộ ứng dụng:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up -d --build
```

Nếu Qdrant volume mới chưa có collection 804, chạy cổng xác minh đầy đủ ở mục
6.1 một lần. Script sẽ kiểm tra trước và chỉ index khi collection thiếu hoặc
không hợp lệ.

Kiểm tra trạng thái:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  ps

curl -fsS http://localhost:8000/api/live
curl -fsS http://localhost:8000/api/ready
```

Địa chỉ truy cập:

| Thành phần | URL |
|---|---|
| Frontend | <http://localhost:5173> |
| Backend | <http://localhost:8000> |
| Swagger | <http://localhost:8000/docs> |
| Qdrant | <http://localhost:6333/dashboard> |

Xem log:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  logs -f backend frontend postgres qdrant
```

Dừng ứng dụng nhưng giữ dữ liệu:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  down
```

Không thêm `-v` nếu không chủ động muốn xóa tài khoản, lịch sử, bookmark, báo
cáo hợp đồng và Qdrant index.

### 5.2. Frontend chạy ngoài Docker

Giữ backend và các dịch vụ dữ liệu trong Docker:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up -d qdrant postgres backend

cd frontend
npm ci
npm run dev
```

### 5.3. Backend chạy ngoài Docker

Chế độ này phù hợp để debug. Qdrant phải đang chạy và alias `labor_law_dev` đã
được tạo trước đó.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up -d qdrant

QDRANT_COLLECTION=labor_law_dev \
CORPUS_REQUIRE_AUTHORITY_APPROVAL=false \
PYTHONPATH=backend \
.venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
```

Backend ngoài Docker sử dụng `DATABASE_URL` trong `.env`; cấu hình mặc định là
SQLite. Muốn dùng PostgreSQL từ host, đặt `DATABASE_URL` trỏ tới
`127.0.0.1:${POSTGRES_PORT}`.

### 5.4. Base Compose có authority gate

`docker-compose.yml` không kèm file dev giữ `CORPUS_REQUIRE_AUTHORITY_APPROVAL=true`.
Do authority review còn pending, đây không phải chế độ demo và không được dùng
để tuyên bố production-ready.

## 6. Tái hiện kết quả

### 6.1. Full technical verification

Đây là lệnh chính để tái hiện kết quả. Cổng này kiểm tra checksum corpus, locked
retrieval evidence, backend tests, frontend lint/build/audit, Docker Compose,
Qdrant 804, PostgreSQL persistence, Contract Review, conversation/bookmark và
Chromium browser E2E.

Trước khi chạy, bảo đảm working tree sạch và không có stack khác chiếm các cổng
5432, 6333, 8000 hoặc 5173.

```bash
ARTIFACT_DIR="$HOME/Downloads/vn-labor-law-rag-final"

PYTHON_BIN="$PWD/.venv/bin/python3" \
bash scripts/verify_final_release.sh "$ARTIFACT_DIR"
```

Kết quả chính:

```text
$ARTIFACT_DIR/final-verification.txt
$ARTIFACT_DIR/runtime-ready.json
$ARTIFACT_DIR/qdrant-verify.json
$ARTIFACT_DIR/qdrant-alias-verify.json
$ARTIFACT_DIR/contract-review-docker-e2e.json
$ARTIFACT_DIR/contract-review-browser-e2e.json
$ARTIFACT_DIR/conversation-bookmarks-e2e.txt
```

Kỳ vọng cuối log:

```text
FINAL VERIFICATION: PASS
Authority review remains pending.
```

Locked test split không được chạy hoặc tuning lại; script chỉ xác minh checksum
và manifest của evidence đã khóa.

### 6.2. RAG backend runtime E2E

Stack Docker phải đang chạy và `/api/ready` phải trả `status=ready`.

```bash
.venv/bin/python scripts/smoke_rag_runtime_e2e.py \
  http://localhost:8000/api \
  "$HOME/Downloads/rag-runtime-e2e.json"
```

Gate này gửi câu hỏi tiếng Việt qua API thật, chạy dense+sparse retrieval trên
Qdrant, gọi OpenRouter thật và kiểm tra câu trả lời cùng citation.

### 6.3. RAG browser E2E bằng Playwright

```bash
.venv/bin/python scripts/smoke_rag_browser_e2e.py \
  --frontend-url http://localhost:5173 \
  --output "$HOME/Downloads/rag-browser-e2e.json" \
  --screenshot "$HOME/Downloads/rag-browser.png" \
  --trace "$HOME/Downloads/rag-browser-trace.zip" \
  --video-dir "$HOME/Downloads/rag-browser-videos"
```

Thêm `--headed` nếu muốn nhìn thấy cửa sổ Chromium. Không thêm tùy chọn này thì
Playwright vẫn dùng Chromium thật nhưng chạy headless.

Gate này thao tác trực tiếp trên frontend: đăng ký, hỏi RAG, kiểm tra câu trả
lời/citation, conversation, bookmark, reload persistence và logout. Một request
`GET /api/auth/me` trả 401 trước khi đăng nhập là hành vi dự kiến.

### 6.4. Contract Review browser E2E

```bash
.venv/bin/python scripts/smoke_contract_review_browser.py \
  --frontend-url http://localhost:5173 \
  --output "$HOME/Downloads/contract-review-browser-e2e.json" \
  --screenshot "$HOME/Downloads/contract-review-browser.png"
```

Thêm `--headed` để xem Chromium. Gate kiểm tra DOCX, PDF, bốn findings, reload
persistence, xóa báo cáo và logout.

### 6.5. Các kiểm tra riêng

Backend:

```bash
PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests
.venv/bin/python -m pip check
```

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run build
npm audit
```

Docker Compose:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  config --quiet
```

## 7. Đánh giá chất lượng truy hồi

Golden set có 45 câu: 31 câu development, 13 câu test và 1 câu disabled. Test
split đã được dùng đúng một lần; không chạy lại test và không tuning sau khi đã
xem kết quả.

Cấu hình Hybrid đã khóa:

```text
top_k           = 5
candidate_k     = 30
rrf_k           = 60
dense_weight    = 0.9
sparse_weight   = 0.1
```

Kết quả test đã khóa:

| Metric | Kết quả |
|---|---:|
| Any article hit@5 | 1.000000 |
| All article hit@5 | 0.923077 |
| Article recall@5 | 0.948718 |
| Article MRR | 0.848718 |
| All evidence hit@5 | 0.692308 |
| Evidence recall@5 | 0.817949 |

`verify_final_release.sh` không chạy lại test split. Script xác minh checksum,
manifest, corpus binding, cấu hình và evidence đã khóa tại:

```text
data/evaluation/runtime-benchmark/canonical_word_804/locked/
data/evaluation/runtime-benchmark/canonical_word_804/FINAL_MANIFEST.json
docs/evaluation/CANONICAL_WORD_804_FINAL_RESULTS.md
```

Có thể chạy lại **dev split** để kiểm tra retrieval trên môi trường hiện tại mà
không tác động locked test:

```bash
PYTHONPATH=.:backend \
.venv/bin/python scripts/benchmark_runtime_retrieval.py \
  --phase evaluate \
  --split-role dev \
  --golden data/evaluation/splits/canonical_word_804/golden_v3_dev.json \
  --output-dir "$HOME/Downloads/retrieval-dev-verification" \
  --methods dense sparse hybrid \
  --k 5 \
  --qdrant-url http://localhost:6333 \
  --collection labor_law_dev \
  --candidate-k 30 \
  --rrf-k 60 \
  --dense-weight 0.9 \
  --sparse-weight 0.1
```

Không truyền `--allow-test-evaluation` trong quá trình tái hiện thông thường.
Các metric retrieval đo khả năng lấy đúng điều/chunk, không chứng minh câu trả
lời LLM đúng hoàn toàn về pháp lý.

## 8. Kết quả cần đối chiếu

| Gate | Kỳ vọng |
|---|---|
| `/api/live` | HTTP 200 |
| `/api/ready` | `status=ready`, không có lỗi |
| Qdrant | 804/804 points |
| Alias | `labor_law_dev -> labor_law_canonical_word_20260804_fdbec539` |
| Chunk SHA256 | `fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307` |
| Dense vector | 1024, `intfloat/multilingual-e5-large` |
| Contract Review browser | PASS 8/8 |
| RAG backend runtime | PASS |
| RAG browser | PASS |
| Conversation/Bookmark persistence | PASS |
| `pip check` | Không có broken requirements |
| `npm audit` | 0 vulnerabilities |

Kết luận đúng khi toàn bộ gate đạt yêu cầu:

```text
Technical runtime and dependency-lock verification: PASS.
Authority review: PENDING.
```

## 9. Lỗi thường gặp

### Cổng đã bị chiếm

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

Dừng stack cũ bằng `docker compose ... down`, không dùng `down -v`.

### Backend không kết nối PostgreSQL sau khi đổi `.env`

`POSTGRES_PASSWORD` chỉ khởi tạo role khi volume được tạo lần đầu. Nếu đổi mật
khẩu trong `.env`, chạy full verifier; script sẽ đồng bộ role mà không xóa dữ
liệu. Không xóa volume để sửa lỗi mật khẩu.

### `/api/ready` báo thiếu model hoặc collection

Chạy lại `runtime-assets`, sau đó chạy full verifier. Verifier sẽ xác minh
collection hiện có và chỉ index 804 chunks khi cần.

### OpenRouter không sinh câu trả lời

Kiểm tra `OPENROUTER_API_KEY`, model miễn phí và rate limit. Không mock provider
nếu mục tiêu là tái hiện runtime E2E.

## 10. An toàn dữ liệu và giới hạn

- Không commit `.env`, API key, database, model cache hoặc Qdrant storage.
- Không sửa corpus, golden split hoặc locked retrieval evidence tại chỗ.
- Không chạy lại hoặc tuning bằng locked test split.
- Không xóa volume trừ khi chủ động muốn xóa dữ liệu.
- OpenRouter/model miễn phí có thể thay đổi khả dụng.
- PDF scan không có lớp text chưa được OCR.
- Authority review và legal-effect review vẫn chưa hoàn tất.
- Bản hiện tại chỉ được kết luận là **technical runtime PASS**, chưa phải phê
  duyệt pháp lý hoặc production approval.

## 11. Tài liệu liên quan

- `docs/CONTRACT_REVIEW_V1.md`
- `docs/SESSION_AUTH.md`
- `docs/CONVERSATION_HISTORY_BOOKMARKS.md`
- `docs/ERD_CONVERSATION_HISTORY.md`
- `docs/evaluation/CANONICAL_WORD_804_FINAL_RESULTS.md`
- `data/evaluation/runtime-benchmark/canonical_word_804/FINAL_MANIFEST.json`
