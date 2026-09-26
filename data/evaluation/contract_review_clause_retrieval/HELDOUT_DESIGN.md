# Contract Clause Retrieval Held-out Design

## Mục tiêu

Đánh giá khả năng truy hồi điều khoản hợp đồng trên dữ liệu chưa được dùng để:

- xây dựng retriever;
- chọn query;
- chọn RRF weight;
- sửa rule;
- quan sát kết quả trong quá trình phát triển.

Held-out chỉ được chạy sau khi:

1. toàn bộ hợp đồng được cố định;
2. segmentation được cố định;
3. annotation được hoàn tất;
4. qrels được materialize;
5. SHA-256 được khóa.

## Retrieval methods đã freeze

- Legacy V1 production `_paragraphs()` + `_excerpt_for()`
- `RuleClauseRetriever`
- `BM25ClauseRetriever`
- `E5ClauseRetriever` dùng `intfloat/multilingual-e5-large`
- Equal-weight BM25 + E5 Reciprocal Rank Fusion

Hybrid configuration:

```text
rrf_k        = 60
bm25_weight  = 1.0
e5_weight    = 1.0
candidate    = all contract sections
evaluation_k = 3
```

Không thay đổi các cấu hình này sau khi xem held-out results.

## Categories

- probation
- salary
- working_time
- termination

Annotation semantics giữ nguyên theo `ANNOTATION_GUIDELINE.md`.

## Quy mô

Target:

- 16 held-out contracts;
- 64 contract-category queries;
- khoảng 8–12 sections / contract;
- có cả positive và no-relevant queries.

## Challenge dimensions

Các hợp đồng phải bao phủ nhiều dạng biểu đạt khác nhau, không tạo case nhằm đánh bại riêng một retriever.

### H1 - Direct terminology

Thông tin được ghi rõ bằng thuật ngữ thông thường.

Ví dụ:

- thử việc;
- mức lương;
- thời giờ làm việc;
- chấm dứt hợp đồng.

Mục đích: kiểm tra regression cơ bản.

### H2 - Paraphrase

Thông tin đúng category nhưng không nhất thiết dùng keyword chính.

Ví dụ:

- “60 ngày đầu kể từ ngày nhận việc” thay cho heading “Thử việc”;
- lịch làm việc mô tả bằng giờ bắt đầu/kết thúc;
- nội dung kết thúc quan hệ lao động diễn đạt trong câu thay vì heading.

Không được làm mơ hồ đến mức con người cũng không xác định được category.

### H3 - Generic headings

Nội dung relevant nằm dưới heading chung như:

- Quyền và nghĩa vụ;
- Chế độ;
- Điều khoản khác;
- Quyền lợi người lao động.

Heading không được dùng như ground-truth shortcut.

### H4 - Multiple relevant sections

Một category có nội dung phân tán qua nhiều section.

Ví dụ:

- schedule ở một section;
- break/weekly rest ở section khác.

Gold có thể chứa relevance 3 + relevance 2.

### H5 - Distractor mentions

Có section chứa keyword nhưng chỉ là related mention.

Ví dụ:

- hoàn trả tài sản khi chấm dứt;
- lương thử việc trong probation section;
- đánh giá hiệu quả công việc không phải probation.

Các case này phải tuân theo guideline 0/1/2/3 hiện hành.

### H6 - Tables

Thông tin chính nằm trong DOCX table:

- salary;
- shift;
- allowance;
- working schedule.

Production DOCX extractor phải giữ table row order.

### H7 - Cross references

Section có thể dẫn chiếu:

- “theo Điều 7”;
- “theo thỏa thuận tại phụ lục”;
- “theo nội quy công ty”.

Cross-reference không có substantive content chỉ được relevance 1 theo guideline.

### H8 - Missing category

Một số hợp đồng cố ý không có một category.

Mục đích:

- đánh giá abstention/no-result behavior;
- không chỉ đánh giá ranking trên positive queries.

Không được tạo tất cả missing cases cho cùng một category.

## Distribution constraints

Trong 16 contracts:

- ít nhất 4 contracts có paraphrase;
- ít nhất 4 contracts có generic heading;
- ít nhất 4 contracts có relevant content trong table;
- ít nhất 4 contracts có distractor keyword;
- ít nhất 4 contracts có multiple relevant sections;
- ít nhất 4 contracts có ít nhất một no-relevant category;
- no-relevant cases phải xuất hiện ở nhiều category, không chỉ probation.

Một contract có thể thuộc nhiều challenge dimensions.

## Annotation procedure

Annotator chỉ được xem:

- source contract;
- frozen annotation guideline;
- canonical sections.

Annotator không được xem:

- output Legacy V1;
- Rule ranking;
- BM25 ranking;
- E5 similarity;
- Hybrid ranking.

Positive judgments phải lưu `reason`.

Final qrels phải materialize toàn bộ relevance `0/1/2/3`.

## Metrics

### Positive / answerable queries

- Strict Hit@1: top1 relevance = 3
- Relevant Hit@1: top1 relevance >= 2
- Recall@3: gold relevance >= 2
- MRR: rank của relevance = 3 đầu tiên
- nDCG@3: full relevance 0/1/2/3

### No-relevant queries

- abstention accuracy;
- false-positive count.

Latency báo riêng.

## Reporting

Development results và held-out results phải được báo riêng.

Không dùng development result làm final generalization claim.

Nếu held-out result không tốt, giữ nguyên kết quả và phân tích failure; không điều chỉnh retriever rồi chạy lại cùng held-out set.
