# Vietnamese Labor Law RAG

MVP hỏi đáp pháp luật lao động Việt Nam theo luồng:

```text
React → FastAPI → Sparse BM25–VnCoreNLP hoặc Hybrid RRF
      → Top-5 căn cứ → OpenRouter free → câu trả lời + nguồn
```

Graph-enhanced được giữ trong thiết kế nhưng trả `501 Not Implemented` cho tới
giai đoạn Neo4j tiếp theo. Hệ thống không âm thầm thay Graph bằng Hybrid.

## Chuẩn bị

1. Collection Qdrant `labor_law` đã index đủ 1.395 chunks.
2. Corpus tại `data/processed/legal_chunks.jsonl`.
3. VnCoreNLP tại:

```text
models/vncorenlp/VnCoreNLP-1.2.jar
models/vncorenlp/models/
```

4. Tạo API key tại OpenRouter rồi cấu hình:

```bash
cp .env.example .env
```

Chỉ điền key vào `.env`:

```env
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=openrouter/free
OPENROUTER_REQUIRE_FREE_MODEL=true
```

Chế độ bảo vệ mặc định sẽ chặn model trả phí. Web search không được bật.

## Chạy nhanh khi Qdrant đã hoạt động

Terminal 1 — backend:

```bash
cd /media/hao/Data/vn-labor-law-rag
source backend/.venv/bin/activate
PYTHONPATH=backend uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Terminal 2 — frontend:

```bash
cd /media/hao/Data/vn-labor-law-rag/frontend
npm install
npm run dev -- --host 0.0.0.0
```

Mở `http://localhost:5173`. API docs ở `http://localhost:8000/docs`.

## Kiểm tra không tốn quota

```bash
cd /media/hao/Data/vn-labor-law-rag
PYTHONPATH=. backend/.venv/bin/pytest -q backend/tests
cd frontend && npm run build && npm run lint
```

Các test OpenRouter dùng HTTP client giả và không gửi request ra internet.

## Gọi API thật

```bash
curl -sS http://localhost:8000/api/ask \
  -H 'Content-Type: application/json' \
  -d '{
    "question": "Người lao động được nghỉ hằng năm bao nhiêu ngày?",
    "method": "sparse"
  }'
```

Đổi `method` thành `hybrid` để dùng BM25–VnCoreNLP + Dense E5 + RRF.
Mỗi lần gọi thành công có nguồn sẽ sử dụng một lượt OpenRouter miễn phí.

## Docker Compose

Sau khi có `.env`, corpus và thư mục model:

```bash
docker compose up --build
```

Backend container dùng Java cho VnCoreNLP và volume riêng để cache FastEmbed.

## Ingestion VBPL an toàn cho production

Lớp crawl chạy độc lập với request path của chatbot. Snapshot chỉ được publish
sau khi đúng số hiệu, đúng item ID, đủ chuỗi Điều, lưu raw response và vượt qua
kiểm tra SHA-256.

```bash
python -m pip install -r requirements-corpus.txt
python -m playwright install --with-deps chromium
make test-ingestion
make vbpl-doctor
make vbpl-canary
make vbpl-soak
```

Chạy batch có resume, retry, checkpoint và báo cáo từng văn bản:

```bash
make vbpl-fetch
```

Không crawl live trong lúc demo chatbot. Quy trình vận hành và promotion gate
được mô tả tại `docs/ingestion/VBPL_PRODUCTION_RUNBOOK.md`.
