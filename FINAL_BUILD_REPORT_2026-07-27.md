# Final build report — 2026-07-27

## Kết quả

Release `labor-law-2026-07-27-candidate` đã qua toàn bộ gate kỹ thuật có thể
chạy offline:

- 18 văn bản;
- 513 đơn vị truy hồi, không trùng `article_id` hoặc `article_code`;
- 833 chunk, không trùng `chunk_id` hoặc `chunk_key`;
- 513/513 đơn vị có ít nhất một chunk;
- 220/220 Điều của `18/VBHN-VPQH`;
- 2 Điều chính + 6 đơn vị Phụ lục I.4 của `66.18/2026/NQ-CP`;
- 45/45 câu golden v3 resolve đúng chunk và đúng mã bằng chứng;
- hai DOCX trong release giữ đúng SHA-256 của file người dùng cung cấp;
- không còn summary Qdrant từ pytest trên đường production.

Validator:

```text
data/quality/unified_release_validation.json
```

## Thay đổi logic

1. Backend và Docker đọc release `chunks.jsonl`, không còn đọc corpus Pháp
   điển legacy.
2. `retrieval_expected_chunks` và `retrieval_corpus_sha256` khớp đúng release.
3. Nghị quyết được xác minh có 7 Điều chính; config cũ ghi 6 đã được sửa.
4. Parser phân biệt Điều chính với các Điều nằm trong biểu mẫu/phụ lục.
5. Phụ lục I.4 được cắt theo cây tiêu đề và có sáu mã canonical tương thích
   golden.
6. Mã Điều của 16 văn bản cũ được chuẩn hóa để corpus và golden dùng cùng mã.
7. Golden v3 được tái sinh toàn bộ `evidence_chunk_ids`.
8. `/api/ready` kiểm số chunk, SHA-256, release ID và trạng thái duyệt.
9. DOCX gốc, raw snapshot, manifest và checksum inventory được giữ riêng.

## Gate còn chặn production

- E5 audit đã hoàn tất bằng tokenizer
  `intfloat/multilingual-e5-large`: 833/833 chunk được đo chính xác, lớn nhất
  478 token, không có chunk từ 480 token trở lên.
- `source_hashes_verified = false`: 16 snapshot VBPL đều khớp hash toàn văn
  trong manifest nhưng thiếu `SHA256SUMS.txt` và raw API response.
- `authority_review_passed = false`: cần người có thẩm quyền duyệt
  `legal_effect_review.json` và bind approval vào hash manifest.
- Qdrant chưa được index lại từ release 833 chunk trong bản bundle; chạy
  `bash scripts/index_and_evaluate_unified.sh` để index và verify an toàn.

BM25 baseline trên 44 câu golden đang bật đã chạy thành công:

- Article Any-Hit@10: `0.931818`;
- Article Recall@10: `0.776326`;
- Evidence Recall@10: `0.687663`.

Do đó kết quả đúng là `PASS_TECHNICAL_CANDIDATE`, không phải production-ready.
