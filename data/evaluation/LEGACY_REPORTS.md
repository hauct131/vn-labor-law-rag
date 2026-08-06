# Legacy evaluation and audit artifacts

Các artifact dưới đây được giữ lại để truy vết lịch sử nhưng không được dùng
làm kết quả cuối của canonical Word 804.

## Pre-rebuild source divergence audit

`data/quality/canonical_source_binding_audit.json` so sánh corpus VBPL cũ
`data/processed/vbpl_articles_raw.json` với các nguồn canonical được chọn.
Trạng thái `FAIL` của báo cáo này là bằng chứng đã dẫn đến việc rebuild corpus;
nó không phải release gate hậu rebuild của corpus 804.

Release gate provenance hậu rebuild là:

```text
data/quality/canonical_word_804_provenance_audit.json
```

## Retrieval reports cũ

Các báo cáo trực tiếp dưới
`data/evaluation/runtime-benchmark/` và các báo cáo corpus 833/golden v3 cũ
chỉ có giá trị lịch sử.

Kết quả authoritative của corpus 804 nằm tại:

```text
data/evaluation/runtime-benchmark/canonical_word_804/locked/
data/evaluation/runtime-benchmark/canonical_word_804/FINAL_MANIFEST.json
```

Không chạy lại test split và không tuning retrieval sau khi test đã được mở.
