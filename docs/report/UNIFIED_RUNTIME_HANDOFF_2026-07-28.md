# Unified runtime handoff — 2026-07-28

## Trạng thái đã xác minh trong bundle

- Release: `labor-law-2026-07-27-candidate`.
- 18 văn bản, 513 article containers, 833 chunks.
- SHA-256 chunks:
  `fd35bb1a94a3036f7977781de17bb1b49b12c58be61fc74efac68dcf8a7a8c54`.
- 9/9 file trong `SHA256SUMS.txt` hợp lệ.
- Unified release validator: `PASS`, không có lỗi.
- E5 audit: đo chính xác 833/833 chunks; lớn nhất 478 token; không có chunk
  từ ngưỡng vận hành 480 token trở lên.
- Golden v3 audit: 45/45 câu, 175/175 evidence references, không thiếu mã
  Điều hoặc chunk ID; 44 câu đang bật cho benchmark.
- BM25 Article Any-Hit@10: `0.931818`.
- BM25 Article Recall@10: `0.776326`.
- BM25 Evidence Recall@10: `0.687663`.

## Lỗi runtime đã sửa

Script cũ gọi `python` trước khi kích hoạt `.venv`, nên dừng ở bước đếm chunks.
Workflow mới luôn gọi trực tiếp `.venv/bin/python3` và tự xác định repository
từ vị trí file script; không còn phụ thuộc `/media/hao/Data/...` hoặc lệnh
`python` trong `PATH`.

Indexer, retrieval evaluator, golden audit và Makefile hiện cùng bind vào release
833, golden v3 và E5 audit có fingerprint tương ứng. Các default legacy 1.395
chunks không còn nằm trên đường chạy production.

## Chạy trên máy có Docker và `.venv`

Từ thư mục repository:

```bash
bash scripts/index_and_evaluate_unified.sh
```

Workflow thực hiện theo thứ tự:

1. kiểm tra Python dependencies;
2. kiểm tra count, UUID, manifest, audit và SHA-256;
3. chạy unified validator và golden audit;
4. chạy BM25 baseline;
5. khởi động Qdrant;
6. dry-run indexer;
7. giữ nguyên collection cũ `labor_law`;
8. index/resume collection phiên bản
   `labor_law_20260727_fd35bb1a`;
9. verify đủ 833 points và đúng fingerprint;
10. chạy dense smoke trực tiếp trên collection phiên bản;
11. chạy full dense/hybrid golden benchmark khi `sentence-transformers` có
    trong `.venv`;
12. tạo/chuyển atomic alias `labor_law_active` sang collection phiên bản;
13. verify alias và chạy lại dense smoke qua alias;
14. cập nhật duy nhất `QDRANT_COLLECTION` trong `.env` sang alias, đồng thời
    giữ một file backup `.env.pre-qdrant-alias-<timestamp>`.

Mọi output được ghi vào `logs/unified_runtime_<timestamp>.log`. Script không có
lệnh xóa collection và không ghi vào collection cũ `labor_law`. Tên collection
phiên bản được khóa theo release/fingerprint; nếu collection phiên bản đã tồn
tại nhưng khác fingerprint, workflow dừng trước khi ghi.

Nếu `.venv` chưa có dependency dense/hybrid:

```bash
.venv/bin/python3 -m pip install -r requirements-evaluation.txt
```

## Gate cố ý chưa đóng

- `authority_review_passed = false`.
- 16 snapshot VBPL chưa có raw provenance đầy đủ
  (`SHA256SUMS.txt` và `portal/api_responses.json`).

Do đó release vẫn là technical candidate, chưa phải production-publishable.
