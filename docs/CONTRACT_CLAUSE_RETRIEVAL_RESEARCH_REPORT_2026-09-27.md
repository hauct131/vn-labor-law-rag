# Báo cáo audit & annotation — Contract Clause Retrieval

> Ngày lập: 2026-09-27. Báo cáo này được tạo từ project archive người dùng cung cấp. Không sử dụng output held-out retrieval để quyết định annotation.

## 1. Phạm vi đã kiểm tra

- Đọc cấu trúc project, các retriever Rule/BM25/E5/Hybrid, evaluator và bộ dev/held-out.
- Kiểm tra held-out source/export đã có 16 hợp đồng và canonical sections.
- Gán nhãn held-out theo `ANNOTATION_GUIDELINE.md` với relevance 0/1/2/3.
- Mọi judgment dương (1/2/3) đều có `reason` nêu bằng chứng trực tiếp từ section.
- Chưa chạy held-out retrieval sau annotation trong bản bàn giao này; mục đích là giữ human sign-off tách khỏi kết quả model.

## 2. Nguyên tắc chống hard-code / leakage

- Không thay đổi Rule/BM25/E5/Hybrid dựa trên held-out.
- Không sửa query terms sau khi nhìn held-out result.
- Rule và BM25 hiện có **query phrase/term được khai báo thủ công trong code**. Đây là đặc tả baseline đã tồn tại trước held-out, không phải output hard-code; cần mô tả rõ trong luận văn.
- Hybrid giữ `rrf_k=60`, `bm25_weight=1.0`, `e5_weight=1.0`, candidate = toàn bộ sections. Không có tuning theo held-out.
- Script tạo qrels template trước đây hard-code đường dẫn `dev/`; đã đổi thành CLI `--source/--output` mà không đổi semantics.
- Thêm validator generic để kiểm tra coverage, hash, flags và bắt buộc reason cho positive judgments.

## 3. Dev benchmark đã có trong project

Bộ dev có 10 synthetic contracts, 40 queries và đã gần bão hòa; do đó chỉ phù hợp sanity/regression, không dùng làm bằng chứng generalization cuối cùng.

| System | Strict H@1 | Relevant H@1 | Recall@3 | MRR | nDCG@3 | Abstention | FP no-rel |
|---|---:|---:|---:|---:|---:|---:|---:|
| Legacy V1 | 1.0000 | 1.0000 | — | — | — | 1.0000 | 0 |
| rule_clause_retriever | 1.0000 | 1.0000 | 0.9722 | 1.0000 | 0.9810 | 1.0000 | 0 |
| bm25_phrase | 0.9722 | 1.0000 | 0.9583 | 0.9861 | 0.9705 | 1.0000 | 0 |
| e5_dense | 0.8611 | 0.8889 | 0.9722 | 0.9259 | 0.9259 | 0.0000 | 4 |
| hybrid_rrf | 0.9722 | 1.0000 | 0.9861 | 0.9861 | 0.9834 | 0.0000 | 4 |

Điểm đáng chú ý trên dev: Hybrid đạt Recall@3 và nDCG@3 cao nhất trong các baseline Rule/BM25/E5/Hybrid nhưng không cải thiện Strict Hit@1 so với BM25; E5/Hybrid không abstain vì dense ranking luôn trả về section khi không có threshold.

## 4. Held-out annotation summary

- 16 contracts × 4 categories = **64 queries**.
- Tổng section-category judgments: **556**.
- Label distribution: **478 × rel0, 10 × rel1, 8 × rel2, 60 × rel3**.
- 4 no-relevant queries, phân tán đều qua 4 category:
  - `H10_Office_Administrator:probation`
  - `H12_Finance_Assistant:termination`
  - `H15_Data_Engineer:salary`
  - `H16_Operations_Coordinator:working_time`

## 5. Cơ sở gán nhãn

Quy tắc được áp dụng đúng theo guideline: rel3 = principal evidence; rel2 = substantive secondary/supplementary; rel1 = mention/cross-reference; rel0 = không có direct topical content. Duration-only không tự động là termination; annual leave-only không tự động là working_time; probation salary chủ yếu thuộc probation.

### Các case cần chú ý

- **H02/H04/H06/H09/H11/H13/H15 probation = rel3 dù không dùng từ “thử việc”**: section mô tả giai đoạn đầu có thời hạn, đánh giá và quyết định tiếp tục, nên topical content là probation chứ không dựa vào keyword.
- **H07 salary S2 = rel1**: chỉ có lương thử việc; guideline nói probation salary chủ yếu thuộc probation và thường chỉ là related mention cho salary.
- **H08 salary S5 = rel2**: câu “chỉ tiêu không làm thay đổi mức lương cố định...” là nội dung thực chất về điều chỉnh lương, nhưng supplementary vì S3 mới là salary section chính.
- **Termination rel1** ở H04/H06/H07/H10/H13/H15: chỉ asset-return/cross-reference, không có đầy đủ grounds/notice.
- **Working-time rel2** ở H03/H05/H09/H11/H13/H15: section phụ chứa shift/rest/overtime/on-call, trong khi section chính chứa schedule/hours.

## 6. Evidence theo từng query

- `H01_Backend_Developer:probation` — S2=rel3: Section trực tiếp quy định thử việc 60 ngày, công việc trong thời gian thử việc và đánh giá kết quả cuối kỳ.
- `H01_Backend_Developer:salary` — S4=rel3: Section chính quy định mức lương theo công việc, phụ cấp, ngày trả lương và phương thức chuyển khoản.
- `H01_Backend_Developer:working_time` — S5=rel3: Section chính quy định 08 giờ/ngày, 05 ngày/tuần, khung giờ làm việc, nghỉ trưa và nghỉ hằng tuần.
- `H01_Backend_Developer:termination` — S8=rel3: Section chính quy định chấm dứt hợp đồng, thời hạn báo trước và nghĩa vụ bàn giao.
- `H02_UI_UX_Designer:probation` — S2=rel3: Dù không dùng từ “thử việc”, section mô tả 60 ngày đầu, đánh giá cuối giai đoạn và quyết định tiếp tục thực hiện hợp đồng; đây là nội dung probation trực tiếp theo guideline.
- `H02_UI_UX_Designer:salary` — S4=rel3: Section chính quy định khoản thanh toán cố định hàng tháng, hỗ trợ thiết bị và ngày/phương thức chuyển khoản.
- `H02_UI_UX_Designer:working_time` — S5=rel3: Section chính quy định giờ làm 09:00–18:00 từ thứ Hai đến thứ Sáu và thời gian nghỉ trưa.
- `H02_UI_UX_Designer:termination` — S7=rel3: Section chính quy định kết thúc quan hệ lao động trước thời hạn, thời gian thông báo và nghĩa vụ bàn giao.
- `H03_Network_Engineer:probation` — S2=rel3: Section trực tiếp quy định thời gian thử việc 60 ngày và đánh giá kết quả.
- `H03_Network_Engineer:salary` — S3=rel3: Section chính quy định mức lương, phụ cấp trực và ngày thanh toán.
- `H03_Network_Engineer:working_time` — S4=rel3: Section chính quy định thời giờ làm việc bình thường 08 giờ/ngày, 05 ngày/tuần và khung giờ hành chính.; S5=rel2: Section bổ sung lịch trực theo ca, khung giờ từng ca và khoảng nghỉ tối thiểu trước ca tiếp theo.
- `H03_Network_Engineer:termination` — S8=rel3: Section chính quy định chấm dứt hợp đồng, thời hạn báo trước và bàn giao thiết bị/tài liệu.
- `H04_Customer_Support:probation` — S2=rel3: Section mô tả 30 ngày đầu, đánh giá khả năng xử lý yêu cầu và dùng kết quả để quyết định tiếp tục bố trí; nội dung trực tiếp tương đương probation theo guideline.
- `H04_Customer_Support:salary` — S3=rel3: Section chính quy định khoản thu nhập cố định, hỗ trợ điện thoại và ngày chuyển tiền.
- `H04_Customer_Support:working_time` — S4=rel3: Section chính quy định hai ca làm việc, khung giờ, luân phiên ca và nghỉ giữa ca.
- `H04_Customer_Support:termination` — S5=rel1: Section chỉ nhắc hoàn trả thiết bị khi hợp đồng chấm dứt, không chứa căn cứ hay thời hạn thông báo; đây là related mention.; S7=rel3: Section chính quy định căn cứ kết thúc hợp đồng, thời gian thông báo và bàn giao hồ sơ.
- `H05_Warehouse_Supervisor:probation` — S2=rel3: Section trực tiếp quy định thử việc 45 ngày và đánh giá năng lực.
- `H05_Warehouse_Supervisor:salary` — S3=rel3: Section chính chứa bảng lương theo công việc, phụ cấp trách nhiệm, phụ cấp ca và ngày/phương thức thanh toán.
- `H05_Warehouse_Supervisor:working_time` — S4=rel3: Section chính quy định ca 12 giờ, lịch ca theo tuần và số ngày làm việc trong tuần.; S5=rel2: Section bổ sung nghỉ giữa ca, thời gian nghỉ tối thiểu giữa hai ca và nghỉ hằng tuần.
- `H05_Warehouse_Supervisor:termination` — S8=rel3: Section chính quy định chấm dứt, thời hạn báo trước và bàn giao kho.
- `H06_Security_Analyst:probation` — S2=rel3: Section mô tả 60 ngày đầu, đánh giá kỹ năng cuối giai đoạn và điều kiện tiếp tục vị trí; nội dung probation trực tiếp dù heading không dùng từ “thử việc”.
- `H06_Security_Analyst:salary` — S3=rel3: Section chính quy định khoản thu nhập cố định hàng tháng và ngày/phương thức thanh toán.
- `H06_Security_Analyst:working_time` — S4=rel3: Section chính quy định 08 giờ/ngày, các ngày làm việc và khung giờ 09:00–18:00.
- `H06_Security_Analyst:termination` — S5=rel1: Section chỉ quy định thu hồi tài khoản và thiết bị khi quan hệ lao động kết thúc; đây là nghĩa vụ liên quan nhưng không phải bằng chứng chính.; S6=rel1: Section chỉ cross-reference việc kết thúc hợp đồng sang Điều 8, không có nội dung substantive.; S8=rel3: Section chính quy định quyền kết thúc quan hệ lao động, thời gian thông báo và nghĩa vụ bàn giao.
- `H07_HR_Specialist:probation` — S2=rel3: Section chính quy định thời gian thử việc, công việc thử việc và mức lương thử việc.; S3=rel2: Section bổ sung tiêu chí đánh giá, thời điểm thông báo kết quả và việc chuyển sang chế độ chính thức sau thử việc.
- `H07_HR_Specialist:salary` — S2=rel1: Section chỉ nêu mức lương thử việc bằng 90% mức lương vị trí; theo guideline probation salary thuộc chủ yếu về probation và thường chỉ relevance 1 cho salary.; S4=rel3: Section chính quy định mức lương theo công việc và ngày thanh toán.
- `H07_HR_Specialist:working_time` — S5=rel3: Section chính quy định 08 giờ/ngày, 05 ngày/tuần và khung giờ 08:30–17:30.
- `H07_HR_Specialist:termination` — S7=rel1: Section chỉ nêu nghĩa vụ bàn giao khi chấm dứt và cross-reference sang Điều 8.; S8=rel3: Section chính quy định các trường hợp chấm dứt và thời hạn báo trước.
- `H08_Sales_Executive:probation` — S2=rel3: Section trực tiếp quy định giai đoạn thử việc 45 ngày và đánh giá kết quả.
- `H08_Sales_Executive:salary` — S3=rel3: Section chính chứa bảng khoản cố định, phụ cấp, hoa hồng và ngày thanh toán.; S5=rel2: Section bổ sung quy định chỉ tiêu không làm thay đổi mức lương cố định nếu không có thỏa thuận khác; đây là thông tin thực chất về điều chỉnh lương nhưng không phải section chính.
- `H08_Sales_Executive:working_time` — S4=rel3: Section chính quy định 08 giờ/ngày, 05 ngày/tuần và cách bố trí lịch gặp khách hàng.
- `H08_Sales_Executive:termination` — S7=rel3: Section chính quy định căn cứ/trình tự kết thúc hợp đồng và thời hạn thông báo.
- `H09_DevOps_Engineer:probation` — S2=rel3: Section mô tả hai tháng đầu, đánh giá của quản lý và dùng kết quả để quyết định tiếp tục bố trí; nội dung trực tiếp tương đương probation.
- `H09_DevOps_Engineer:salary` — S3=rel3: Section chính quy định khoản trả cố định hàng tháng và ngày/phương thức chuyển khoản.
- `H09_DevOps_Engineer:working_time` — S4=rel3: Section chính quy định lịch làm việc 09:00–18:00, 05 ngày/tuần.; S5=rel2: Section bổ sung lịch on-call luân phiên và khoảng nghỉ sau ca trực đêm.
- `H09_DevOps_Engineer:termination` — S7=rel3: Section chính quy định kết thúc quan hệ lao động trước thời hạn, thời gian thông báo và nghĩa vụ bàn giao.
- `H10_Office_Administrator:probation` — Không có section relevance > 0; toàn bộ section được gán 0 theo direct-topical-content rule.
- `H10_Office_Administrator:salary` — S3=rel3: Section chính quy định mức lương theo công việc và ngày/phương thức trả lương.
- `H10_Office_Administrator:working_time` — S4=rel3: Section chính quy định 08 giờ/ngày, 05 ngày/tuần và khung giờ 08:00–17:00.
- `H10_Office_Administrator:termination` — S5=rel1: Section chỉ nêu hoàn trả tài sản/hồ sơ khi hợp đồng chấm dứt; đây là related mention.; S6=rel1: Section chỉ cross-reference các vấn đề chấm dứt hợp đồng sang Điều 8.; S8=rel3: Section chính quy định các trường hợp chấm dứt và thời hạn báo trước.
- `H11_QA_Engineer:probation` — S2=rel3: Section mô tả hai tháng đầu, review chất lượng công việc và quyết định tiếp tục bố trí; nội dung trực tiếp tương đương probation.
- `H11_QA_Engineer:salary` — S3=rel3: Section chính chứa bảng thu nhập cố định, phụ cấp và ngày thanh toán tiền lương.
- `H11_QA_Engineer:working_time` — S4=rel3: Section chính quy định khung giờ làm việc và các ngày làm việc trong tuần.; S5=rel2: Section bổ sung quy định làm thêm và nghỉ bù.
- `H11_QA_Engineer:termination` — S7=rel3: Section chính quy định chấm dứt hợp đồng theo căn cứ và thời hạn báo trước.
- `H12_Finance_Assistant:probation` — S2=rel3: Section trực tiếp quy định thử việc 45 ngày và đánh giá kết quả thử việc.
- `H12_Finance_Assistant:salary` — S3=rel3: Section chính quy định mức lương, phụ cấp và ngày thanh toán.
- `H12_Finance_Assistant:working_time` — S4=rel3: Section chính quy định 08 giờ/ngày, 05 ngày/tuần và lịch 08:30–17:30.
- `H12_Finance_Assistant:termination` — Không có section relevance > 0; toàn bộ section được gán 0 theo direct-topical-content rule.
- `H13_Call_Center_Agent:probation` — S2=rel3: Section mô tả 30 ngày đầu, đào tạo/giám sát và dùng kết quả để quyết định tiếp tục bố trí; nội dung trực tiếp tương đương probation.
- `H13_Call_Center_Agent:salary` — S3=rel3: Section chính quy định khoản thu nhập cố định hàng tháng và ngày chuyển tiền.
- `H13_Call_Center_Agent:working_time` — S4=rel3: Section chính chứa bảng phân ca với khung giờ từng ca và lịch phân ca theo tuần.; S5=rel2: Section bổ sung nghỉ giữa ca và nghỉ hằng tuần.
- `H13_Call_Center_Agent:termination` — S7=rel1: Section chỉ cross-reference quyền kết thúc hợp đồng sang Điều 8.; S8=rel3: Section chính quy định quyền kết thúc quan hệ lao động và thời gian thông báo.
- `H14_Procurement_Specialist:probation` — S2=rel3: Section trực tiếp quy định thử việc 60 ngày và đánh giá kết quả.
- `H14_Procurement_Specialist:salary` — S3=rel3: Section chính quy định mức lương, phụ cấp và ngày trả tiền lương.
- `H14_Procurement_Specialist:working_time` — S4=rel3: Section chính quy định 08 giờ/ngày, 05 ngày/tuần và khung giờ 08:30–17:30.
- `H14_Procurement_Specialist:termination` — S7=rel3: Section chính quy định chấm dứt hợp đồng theo căn cứ và thời hạn báo trước.
- `H15_Data_Engineer:probation` — S2=rel3: Section mô tả 60 ngày đầu, đánh giá kết quả kỹ thuật/phối hợp và điều kiện tiếp tục vị trí; nội dung trực tiếp tương đương probation.
- `H15_Data_Engineer:salary` — Không có section relevance > 0; toàn bộ section được gán 0 theo direct-topical-content rule.
- `H15_Data_Engineer:working_time` — S4=rel3: Section chính quy định lịch công tác 09:00–18:00 từ thứ Hai đến thứ Sáu.; S5=rel2: Section bổ sung lịch trực hệ thống luân phiên và nghỉ bù sau trực ngoài giờ.
- `H15_Data_Engineer:termination` — S6=rel1: Section chỉ nêu hoàn trả thiết bị khi quan hệ lao động kết thúc; đây là related mention.; S7=rel1: Section chỉ cross-reference việc kết thúc quan hệ lao động sang Điều 8.; S8=rel3: Section chính quy định quyền kết thúc quan hệ lao động và thời gian thông báo.
- `H16_Operations_Coordinator:probation` — S2=rel3: Section trực tiếp quy định thử việc 45 ngày và đánh giá kết quả thử việc.
- `H16_Operations_Coordinator:salary` — S3=rel3: Section chính chứa bảng lương theo công việc, phụ cấp/hỗ trợ và ngày thanh toán.
- `H16_Operations_Coordinator:working_time` — Không có section relevance > 0; toàn bộ section được gán 0 theo direct-topical-content rule.
- `H16_Operations_Coordinator:termination` — S7=rel3: Section chính quy định chấm dứt hợp đồng và thời hạn báo trước.

## 7. Validation đã chạy

Validator kiểm tra: đủ 64 query; mỗi query cover toàn bộ section; không duplicate query/section; relevance chỉ thuộc 0..3; `has_relevant/has_primary` khớp; mọi positive judgment có reason; frozen SHA của guideline/sections khớp.

Kết quả: **PASS**.

## 8. File freeze

- Guideline SHA256: `e86979e2d9756cef82d0383a011d72872e583a7ff1039037eb1919b6cc29b2c4`
- Held-out sections SHA256: `7d2d75c2f86046c7ab717de2d0dc70fcc36ff3ce1a55bb7d8f2ff13b79a7eb58`
- Held-out qrels SHA256: `99ada42887f4d52137429650765b55fe93d6478d907655875a265f3efa60805c`

## 9. Cảnh báo bảo mật/project hygiene

- Archive project chứa `.env` và nhiều backup `.env.*`. Không nên commit hoặc chia sẻ các file này. Bản patch bàn giao không chứa chúng.
- `RuleClauseRetriever` và `BM25ClauseRetriever` có term/query phrase khai báo trực tiếp trong source. Đây không phải “hard-code kết quả”, nhưng phải ghi rõ là manual lexical baseline và freeze trước held-out.
- `E5ClauseRetriever` dùng fixed category query. Khi báo cáo phải phân biệt fixed query formulation với supervised/tuned query optimization.

## 10. Việc còn lại trước final held-out benchmark

1. Người làm luận văn review `annotation_decisions_v1.json` và `qrels_gold_v1.json` **mà chưa xem model output held-out**.
2. Nếu đồng ý, đổi status từ `assistant_draft_v1_pending_human_signoff` thành `gold_v1`, tính lại SHA và commit.
3. Sau đó mới chạy Legacy/Rule/BM25/E5/Hybrid trên held-out đúng một lượt và giữ nguyên kết quả kể cả xấu.
4. Báo dev và held-out riêng; không tune theo held-out.
