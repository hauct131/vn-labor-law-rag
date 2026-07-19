# Retrieval model probe — Ngày 4

## Mục tiêu

Khóa một dense embedding model trước khi tạo collection `labor_law` chính
thức. Probe dùng cùng 1.127 chunk đã qua strict validation và so sánh:

- Dense: `intfloat/multilingual-e5-large`, 1.024 chiều, cosine.
- Sparse: `Qdrant/bm25` với IDF do Qdrant tính theo collection.

FastEmbed không có Vietnamese stemmer cho `Qdrant/bm25`; probe vì vậy tắt
English stemmer/stopwords và dùng tokenizer đơn giản trên cả corpus lẫn query.
Query bắt buộc đi qua `query_embed()`, không dùng trọng số BM25 phía document.

Probe chỉ sử dụng collection tạm `labor_law_model_probe`. Script chủ động từ
chối collection production `labor_law`.

## Dữ liệu đánh giá ban đầu

`data/evaluation/retrieval_smoke_questions.json` gồm 15 câu hỏi:

- Thuật ngữ/số điều rõ ràng để đặc trưng cho Sparse.
- Cách hỏi đời thường để đặc trưng cho Dense.
- Ground truth ở cấp `article_code`.

Đây là smoke benchmark để phát hiện cấu hình sai và đặc trưng hóa model, chưa
phải bộ đánh giá cuối của luận văn. Bộ cuối cần mở rộng lên 40–45 câu và review
ground truth thủ công.

## Chạy probe

Probe đã được thiết kế theo bộ phiên bản đang dùng trong project:

- `qdrant-client==1.18.0`
- `fastembed==0.8.0`
- `langchain-qdrant==1.1.0`
- `langchain-core==1.4.9`

Từ repository root:

```bash
python -m scripts.test_sparse_dense --dry-run

python -m scripts.test_sparse_dense \
  --recreate \
  --batch-size 16 \
  --threads 6 \
  --top-k 5
```

Lần đầu FastEmbed tải dense model khoảng 2,24 GB. Output được ghi vào:

```text
experiments/sparse_dense_model_probe.json
experiments/sparse_dense_model_probe.csv
```

Các file experiment không commit vì được tạo lại từ corpus, model và bộ câu
hỏi đã version-control.

## Chạy lại không tạo trùng

Sau lần đầu, bỏ `--recreate`:

```bash
python -m scripts.test_sparse_dense --skip-index
```

Hoặc chạy lại upsert đầy đủ để xác nhận point ID ổn định:

```bash
python -m scripts.test_sparse_dense
```

Point count phải luôn là 1.127.

Nếu chỉ thay cấu hình BM25, giữ nguyên dense vector đã tạo và cập nhật Sparse:

```bash
python -m scripts.test_sparse_dense \
  --sparse-only-reindex \
  --batch-size 16 \
  --threads 6 \
  --top-k 5
```

Chế độ này yêu cầu collection probe đã tồn tại, không dùng cùng `--recreate`
hoặc `--skip-index`.

## Đối chứng BM25 với tách từ VnCoreNLP

`Qdrant/bm25` được giữ làm baseline thư viện. Ứng viên sparse cho tiếng Việt
dùng RDRSegmenter trong VnCoreNLP 1.2 để tách từ, sau đó adapter chỉ thực hiện
BM25 term-frequency; Qdrant tiếp tục cung cấp IDF. Phiên bản probe này không
xóa stopword và không tự sinh unigram/bigram, tránh điều chỉnh thủ công theo
15 câu smoke test.

Model VnCoreNLP được lưu ngoài repository. Sau khi baseline đã tạo collection
và dense vector, chỉ thay sparse vector rồi ghi báo cáo riêng:

```bash
python -m pip install -r backend/requirements-vncorenlp.txt

python -m scripts.test_sparse_dense \
  --sparse-model vncorenlp/rdrsegmenter-bm25-v1 \
  --vncorenlp-model-dir /absolute/path/to/vncorenlp \
  --sparse-only-reindex \
  --output experiments/vncorenlp_bm25_probe.json \
  --batch-size 16 \
  --threads 6 \
  --top-k 5
```

Không dùng collection production cho thử nghiệm này. Báo cáo baseline phải
được giữ lại trước khi reindex sparse để so sánh Hit@5, MRR@5 và từng câu miss.

## Metric

- `Hit@5`: top 5 có ít nhất một chunk thuộc Điều ground truth.
- `MRR@5`: ưu tiên Điều đúng xuất hiện ở thứ hạng cao.
- Mean/median latency: thời gian embed query và truy vấn Qdrant.

Không so sánh trực tiếp dense score và BM25 score vì chúng khác thang đo. Hybrid
ở Ngày 9 sẽ kết hợp thứ hạng bằng RRF.

## Tiêu chí quyết định

Model/config chỉ được khóa khi:

1. Dense vector đúng 1.024 chiều.
2. Collection có đúng 1.127 point và BM25 sparse vector bật IDF.
3. Không có lỗi encode tiếng Việt.
4. Kết quả thủ công không cho thấy lỗi hệ thống ở các câu hỏi đời thường.
5. Báo cáo lưu đủ Hit@5, MRR@5, latency và top result để audit.

## Rủi ro giới hạn 512 token

`multilingual-e5-large` cắt input dài về tối đa 512 token. Trong khi đó,
`token_count` của chunk được tính bằng `cl100k_base`, không phải tokenizer của
E5. Báo cáo vì vậy ghi `dense_truncation_risk_proxy` với hai mức:

- `near_or_above_count`: từ 480 token để chừa biên cho prefix và sai khác
  tokenizer.
- `at_or_above_model_limit_count`: từ 512 token.

Đây chỉ là cảnh báo gần đúng. Nếu các câu bị miss trỏ tới chunk dài, cần thử
rechunk quanh 400–450 token hoặc đo lại bằng tokenizer E5 trước khi khóa model.
Không được âm thầm coi phần văn bản bị cắt là đã được embed.

Qwen3-Embedding-0.6B có thể được đánh giá như một ablation sau khi có inference
endpoint ổn định. Không thay model giữa ba pipeline Sparse, Hybrid và
Graph-enhanced trong thí nghiệm cuối.

## Kết quả smoke benchmark ngày 19/07/2026

Bộ smoke gồm 15 câu, candidate depth 50 và ground truth ở cấp
`article_code`. Các số liệu này dùng để chọn cấu hình triển khai tạm thời,
không phải kết quả test cuối của đồ án.

| Phương pháp | Hit@5 | MRR@5 | Mean latency |
|---|---:|---:|---:|
| FastEmbed `Qdrant/bm25` | 0,666667 | 0,588889 | 2,280 ms |
| VnCoreNLP + BM25 | 0,866667 | 0,702222 | 3,284 ms |
| Dense-only, gộp theo Điều | 1,000000 | 0,846667 | — |
| Equal RRF, `k=60` | 0,933333 | 0,855556 | — |
| Weighted RRF 2:1, `k=60` | 1,000000 | 0,880000 | — |

VnCoreNLP được chọn làm sparse candidate. Miss đáng chú ý là Điều 37: câu hỏi
tự nhiên có sparse rank 28 nhưng dense raw-chunk rank 4; truy vấn đúng tiêu đề
đưa sparse lên rank 4. Đây là giới hạn từ vựng/ngữ nghĩa của BM25, không phải
mất chunk hoặc sai vector.

Cấu hình Hybrid tạm thời để triển khai:

- Dense model: `intfloat/multilingual-e5-large`.
- Sparse analyzer: `vncorenlp/rdrsegmenter-bm25-v1`.
- Candidate depth: 50 cho mỗi retriever.
- Gộp theo `article_code` bằng best rank trước khi fusion.
- Weighted RRF: `k=60`, dense weight 2, sparse weight 1.
- Output: top 5 Điều, giữ chunk đại diện làm context.

Sweep trên bộ smoke cho thấy dense weight 3 đạt MRR cao hơn, nhưng không được
chọn vì 15 câu chưa đủ để tránh overfit. Cấu hình phải được chọn lại trên dev
set 30 câu rồi khóa trước khi chạy held-out test 15 câu. Dense benchmark hiện
dùng mean pooling của `fastembed==0.8.0`; phiên bản này được pin để bảo đảm tái
lập.
