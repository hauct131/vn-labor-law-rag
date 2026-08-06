# Canonical Word 804 — Final Technical Evaluation

## Trạng thái

Corpus canonical Word 804 là bản kỹ thuật cuối cùng đã đóng băng cho đồ án.
Authority review và legal-effect review vẫn chưa hoàn tất.

## Nguồn dữ liệu

Toàn bộ 18 văn bản được tải từ web và lưu thành snapshot bất biến:

- 15 văn bản qua `official-gazette-word`;
- 2 văn bản DOCX tải trực tiếp từ website cơ quan nhà nước qua `official-docx`;
- 1 văn bản `10/2020/TT-BLĐTBXH` dùng fallback `vbpl:item:146696`.

Post-rebuild provenance audit: `PASS`.

## Corpus

- Release: `labor-law-canonical-word-20260804-164432-candidate`
- Chunk count: `804`
- Corpus SHA256: `fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307`
- Physical collection: `labor_law_canonical_word_20260804_fdbec539`
- Local/demo alias: `labor_law_dev`
- Runtime legacy alias: `labor_law_active`

## Golden split

- Source: 45
- Development: 31
- Test: 13
- Disabled: 1
- Golden SHA256: `92856a7dd92b8e65d84c161da9a0a61f72d33d11731be211aac08f8c6aaddf21`

Test split đã được sử dụng đúng một lần. Không chạy lại test và không tuning
retrieval sau khi xem kết quả test.

## Locked retrieval configuration

```text
top_k         = 5
candidate_k   = 30
rrf_k         = 60
dense_weight  = 0.9
sparse_weight = 0.1
```

## Locked test metrics

| Metric | Value |
|---|---:|
| Any article hit@5 | 1.000000 |
| All article hit@5 | 0.923077 |
| Article recall@5 | 0.948718 |
| Article MRR | 0.848718 |
| All evidence hit@5 | 0.692308 |
| Evidence recall@5 | 0.817949 |

## Authoritative files

- `data/quality/canonical_word_804_provenance_audit.json`
- `data/evaluation/runtime-benchmark/canonical_word_804/locked/`
- `data/evaluation/runtime-benchmark/canonical_word_804/FINAL_MANIFEST.json`

## Giới hạn kết luận

Kết quả trên xác nhận trạng thái kỹ thuật, nguồn gốc dữ liệu và retrieval của
corpus. Nó không thay thế authority review hoặc xác nhận tính đúng pháp lý cuối
cùng của câu trả lời do LLM sinh ra.
