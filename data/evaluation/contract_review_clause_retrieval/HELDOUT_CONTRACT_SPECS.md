# Held-out Contract Specifications

## Mục đích

Tài liệu này cố định nội dung và cấu trúc dự kiến của 16 hợp đồng held-out
trước khi tạo DOCX, segmentation, annotation và chạy retrieval.

Các specification này được thiết kế dựa trên các challenge dimension đã khóa
trong `HELDOUT_DESIGN.md` và `HELDOUT_CONTRACT_MATRIX.md`.

Không được sửa specification dựa trên kết quả của:

- Legacy V1;
- RuleClauseRetriever;
- BM25ClauseRetriever;
- E5ClauseRetriever;
- HybridClauseRetriever.

## Quy tắc chung

Mỗi hợp đồng:

- có khoảng 8–10 section chính;
- có preamble thông thường;
- dùng cấu trúc và cách diễn đạt tự nhiên của hợp đồng lao động;
- không cố tình chèn từ khóa chỉ để hỗ trợ hoặc phá một retriever;
- các con số về lương, thời hạn và giờ làm chỉ dùng để tạo dữ liệu synthetic;
- không dùng kết quả retrieval để sửa nội dung sau khi DOCX đã freeze.

Các heading có thể là:

- heading trực tiếp;
- heading chung;
- heading mô tả chức năng.

Không bắt buộc mọi hợp đồng phải dùng cùng một template.

---

# H01 — Backend Developer

## Challenge

- H1 Direct terminology
- Basic regression case

## Cấu trúc

### S0 — Preamble

Thông tin hai bên, chức danh Backend Developer, nơi làm việc.

### S1 — Điều 1. Công việc và địa điểm làm việc

- phát triển API;
- bảo trì backend;
- phối hợp frontend;
- địa điểm làm việc.

### S2 — Điều 2. Thử việc

- dùng trực tiếp thuật ngữ `thử việc`;
- thời gian thử việc 60 ngày;
- công việc trong thời gian thử việc giống vị trí Backend Developer;
- có đánh giá kết quả cuối kỳ.

### S3 — Điều 3. Thời hạn hợp đồng

- hợp đồng xác định thời hạn 24 tháng;
- ngày bắt đầu và ngày kết thúc.

Không mô tả termination tại section này ngoài thời hạn hợp đồng.

### S4 — Điều 4. Tiền lương

- mức lương theo công việc;
- phụ cấp;
- ngày trả lương;
- chuyển khoản ngân hàng.

### S5 — Điều 5. Thời giờ làm việc và nghỉ ngơi

- 08 giờ/ngày;
- 05 ngày/tuần;
- thứ Hai đến thứ Sáu;
- nghỉ trưa;
- nghỉ hằng tuần.

### S6 — Điều 6. Bảo hiểm và nghỉ phép

- BHXH;
- BHYT;
- nghỉ phép năm.

### S7 — Điều 7. Bảo mật

Không chứa nội dung substantive của bốn category retrieval.

### S8 — Điều 8. Chấm dứt hợp đồng

- dùng trực tiếp `chấm dứt hợp đồng`;
- thời hạn báo trước;
- nghĩa vụ bàn giao.

---

# H02 — UI/UX Designer

## Challenge

- H2 Paraphrase
- H3 Generic heading

## Cấu trúc

### S0 — Preamble

Thông tin hai bên và vị trí UI/UX Designer.

### S1 — Điều 1. Phạm vi công việc

- thiết kế giao diện;
- prototype;
- design system.

### S2 — Điều 2. Giai đoạn ban đầu

Không dùng heading `Thử việc`.

Nội dung:

- trong 60 ngày đầu kể từ ngày nhận việc;
- người lao động thực hiện đầy đủ nhiệm vụ của vị trí;
- cuối giai đoạn sẽ đánh giá khả năng đáp ứng yêu cầu;
- nếu đạt yêu cầu tiếp tục thực hiện hợp đồng theo điều khoản đã thỏa thuận.

Không cần dùng cụm `thử việc` trong section này.

### S3 — Điều 3. Thời hạn

- hợp đồng 18 tháng.

### S4 — Điều 4. Quyền lợi

Generic heading.

Nội dung salary substantive:

- khoản thanh toán cố định mỗi tháng;
- số tiền cụ thể;
- phụ cấp thiết bị;
- chuyển vào tài khoản vào ngày 05.

Có thể không dùng heading `Tiền lương`, nhưng nội dung phải rõ về khoản tiền trả
cho công việc.

### S5 — Điều 5. Tổ chức công việc

Generic heading.

- làm việc 09:00–18:00;
- thứ Hai đến thứ Sáu;
- nghỉ trưa một giờ.

Không bắt buộc dùng cụm `thời giờ làm việc`.

### S6 — Điều 6. Sở hữu trí tuệ

Không relevant.

### S7 — Điều 7. Khi quan hệ lao động kết thúc

Heading không bắt buộc có từ `chấm dứt`.

- quy định việc một bên kết thúc quan hệ lao động trước thời hạn;
- thời gian thông báo;
- bàn giao hồ sơ.

---

# H03 — Network Engineer

## Challenge

- H1 Direct terminology
- H4 Multiple relevant sections
- H6 Table

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

Network monitoring, configuration, incident response.

### S2 — Điều 2. Thử việc

- 60 ngày;
- đánh giá kết quả.

### S3 — Điều 3. Tiền lương

- mức lương;
- phụ cấp trực;
- ngày thanh toán.

### S4 — Điều 4. Thời giờ làm việc

Primary working-time section:

- 08 giờ/ngày;
- 05 ngày/tuần;
- giờ hành chính.

### S5 — Điều 5. Lịch trực hệ thống

Supplementary working-time section.

Có DOCX table:

| Ca | Khung giờ | Ghi chú |
|---|---|---|
| A | 06:00–14:00 | trực sự cố |
| B | 14:00–22:00 | trực sự cố |
| C | 22:00–06:00 | trực đêm |

- lịch trực được phân công theo tuần;
- quy định khoảng nghỉ trước khi chuyển sang ca tiếp theo.

### S6 — Điều 6. Nghỉ phép và bảo hiểm

### S7 — Điều 7. Thiết bị

Không substantive termination.

### S8 — Điều 8. Chấm dứt hợp đồng

- termination;
- notice;
- handover.

---

# H04 — Customer Support

## Challenge

- H2 Paraphrase
- H5 Distractor mention

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

Customer support qua chat/email/điện thoại.

### S2 — Điều 2. Giai đoạn làm quen công việc

Paraphrase probation:

- 30 ngày đầu;
- được hướng dẫn và đánh giá khả năng xử lý yêu cầu;
- kết quả dùng để quyết định tiếp tục bố trí vào vị trí.

Không nhất thiết dùng từ `thử việc`.

### S3 — Điều 3. Thu nhập

- số tiền hàng tháng;
- phụ cấp;
- ngày chuyển tiền.

### S4 — Điều 4. Lịch phục vụ khách hàng

Paraphrase working time:

- ca sáng 07:00–15:00;
- ca chiều 15:00–23:00;
- lịch luân phiên;
- nghỉ giữa ca.

### S5 — Điều 5. Thiết bị

Distractor termination mention:

- tai nghe/laptop phải hoàn trả khi hợp đồng chấm dứt.

Section này không quy định grounds/notice/procedure.

### S6 — Điều 6. Bảo mật

### S7 — Điều 7. Kết thúc hợp đồng

Substantive termination:

- grounds;
- notice;
- bàn giao.

---

# H05 — Warehouse Supervisor

## Challenge

- H1 Direct terminology
- H3 Generic heading
- H4 Multiple relevant sections
- H6 Table

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Trách nhiệm

Quản lý kho và nhân sự ca.

### S2 — Điều 2. Thử việc

Direct probation.

### S3 — Điều 3. Chế độ

Generic heading, salary substantive.

DOCX table:

| Khoản | Mức |
|---|---:|
| Lương theo công việc | ... |
| Phụ cấp trách nhiệm | ... |
| Phụ cấp ca | ... |

Bên dưới bảng:

- thanh toán ngày 07;
- chuyển khoản.

### S4 — Điều 4. Tổ chức vận hành kho

Primary working-time section:

- 12 giờ/ca;
- lịch ca;
- số ngày làm trong tuần.

### S5 — Điều 5. Nghỉ và chuyển ca

Secondary working-time:

- nghỉ giữa ca;
- thời gian nghỉ tối thiểu giữa hai ca;
- nghỉ hằng tuần.

### S6 — Điều 6. An toàn lao động

### S7 — Điều 7. Nghĩa vụ quản lý

### S8 — Điều 8. Chấm dứt

Direct termination.

---

# H06 — Security Analyst

## Challenge

- H2 Paraphrase
- H5 Distractor
- H7 Cross-reference

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Nhiệm vụ

SOC monitoring, incident response.

### S2 — Điều 2. Thời gian đánh giá ban đầu

Paraphrase probation:

- 60 ngày đầu;
- đánh giá kỹ năng;
- nếu đáp ứng thì tiếp tục vị trí.

### S3 — Điều 3. Thu nhập

Salary substantive.

### S4 — Điều 4. Lịch công tác

Working time substantive.

### S5 — Điều 5. Quản lý tài khoản và thiết bị

Distractor termination:

- thu hồi account và thiết bị khi quan hệ lao động kết thúc.

Không có grounds/notice.

### S6 — Điều 6. Điều khoản áp dụng

Cross-reference:

- việc kết thúc hợp đồng thực hiện theo Điều 8.

Không substantive termination ngoài dẫn chiếu.

### S7 — Điều 7. Bảo mật

### S8 — Điều 8. Quyền kết thúc quan hệ lao động

Termination substantive.

---

# H07 — HR Specialist

## Challenge

- H1 Direct terminology
- H4 Multiple relevant sections
- H7 Cross-reference

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Thử việc

Primary probation:

- thời hạn;
- công việc;
- mức lương thử việc.

### S3 — Điều 3. Đánh giá sau thử việc

Secondary probation:

- tiêu chí đánh giá;
- thời điểm thông báo kết quả;
- kết quả đạt thì chuyển sang chế độ chính thức.

### S4 — Điều 4. Tiền lương

Primary salary.

### S5 — Điều 5. Thời giờ làm việc

Primary working time.

### S6 — Điều 6. Nghỉ phép

### S7 — Điều 7. Nghĩa vụ khi kết thúc

Cross-reference termination:

- bàn giao theo Điều 8 khi chấm dứt;
- không nêu grounds hoặc notice.

### S8 — Điều 8. Chấm dứt hợp đồng

Primary termination.

---

# H08 — Sales Executive

## Challenge

- H3 Generic heading
- H5 Distractor
- H6 Table

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Giai đoạn thử việc

Probation direct.

### S3 — Điều 3. Quyền lợi

Generic heading.

Salary primary nằm trong table:

| Nội dung | Mức |
|---|---:|
| Khoản cố định | ... |
| Phụ cấp điện thoại | ... |
| Hoa hồng | theo chính sách |

- thanh toán ngày 05.

### S4 — Điều 4. Tổ chức công việc

Working time substantive.

### S5 — Điều 5. Chỉ tiêu kinh doanh

Có từ `lương` trong ngữ cảnh:

- chỉ tiêu không làm thay đổi mức lương cố định nếu không có thỏa thuận khác.

Đây chỉ là distractor/secondary salary mention, không phải salary section chính.

### S6 — Điều 6. Tài sản

### S7 — Điều 7. Kết thúc hợp đồng

Termination substantive.

---

# H09 — DevOps Engineer

## Challenge

- H2 Paraphrase
- H3 Generic heading
- H4 Multiple relevant sections

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Giai đoạn đầu

Paraphrase probation.

### S3 — Điều 3. Chế độ

Generic heading containing salary primary.

### S4 — Điều 4. Lịch làm việc

Working-time primary:

- giờ hành chính;
- 5 ngày/tuần.

### S5 — Điều 5. Trực ngoài giờ

Working-time secondary:

- on-call;
- lịch luân phiên;
- khoảng nghỉ sau ca trực.

### S6 — Điều 6. Bảo mật và quyền truy cập

### S7 — Điều 7. Điều khoản khác

Generic heading containing substantive termination.

### S8 — Điều 8. Bảo hiểm

---

# H10 — Office Administrator

## Challenge

- H1 Direct terminology
- H5 Distractor
- H7 Cross-reference
- H8 probation missing

## Missing category

`probation`

Không được có:

- từ `thử việc`;
- mô tả giai đoạn đầu mang tính probation;
- mức lương thử việc;
- evaluation period có chức năng tương đương probation.

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Thời hạn hợp đồng

### S3 — Điều 3. Tiền lương

Salary primary.

### S4 — Điều 4. Thời giờ làm việc

Working-time primary.

### S5 — Điều 5. Tài sản

Distractor thuộc termination:

- hoàn trả tài sản khi hợp đồng chấm dứt.

### S6 — Điều 6. Điều khoản áp dụng

Cross-reference termination tới Điều 8.

### S7 — Điều 7. Bảo hiểm và nghỉ phép

### S8 — Điều 8. Chấm dứt hợp đồng

Termination primary.

---

# H11 — QA Engineer

## Challenge

- H2 Paraphrase
- H4 Multiple relevant sections
- H6 Table

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Giai đoạn đánh giá ban đầu

Paraphrase probation:

- hai tháng đầu;
- thực hiện công việc QA;
- review chất lượng công việc;
- quyết định tiếp tục bố trí.

### S3 — Điều 3. Tiền lương

Salary primary.

Có table nhỏ:

| Khoản | Giá trị |
|---|---:|
| Thu nhập cố định | ... |
| Phụ cấp | ... |

### S4 — Điều 4. Lịch làm việc

Working-time primary.

### S5 — Điều 5. Làm thêm và nghỉ bù

Working-time secondary.

### S6 — Điều 6. Thiết bị

### S7 — Điều 7. Chấm dứt hợp đồng

Termination primary.

---

# H12 — Finance Assistant

## Challenge

- H1 Direct terminology
- H3 Generic heading
- H5 Distractor
- H8 termination missing

## Missing category

`termination`

Điều kiện rất quan trọng:

- không có `chấm dứt`;
- không có `nghỉ việc`;
- không có `báo trước`;
- không có asset-return-on-termination;
- không có cross-reference termination.

Distractor của hợp đồng này phải thuộc category khác, không được thuộc termination.

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Thử việc

Probation direct.

### S3 — Điều 3. Chế độ

Generic heading, salary primary.

### S4 — Điều 4. Thời giờ làm việc

Working time primary.

### S5 — Điều 5. Đánh giá hiệu quả

Distractor probation-like wording:

- đánh giá KPI định kỳ hằng quý;
- đây là performance review sau khi đã làm việc chính thức.

Không mô tả probation.

### S6 — Điều 6. Bảo hiểm

### S7 — Điều 7. Bảo mật

### S8 — Điều 8. Điều khoản chung

Không termination content.

---

# H13 — Call Center Agent

## Challenge

- H2 Paraphrase
- H4 Multiple relevant sections
- H6 Table
- H7 Cross-reference

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Giai đoạn đầu

Paraphrase probation.

### S3 — Điều 3. Thu nhập

Salary substantive.

### S4 — Điều 4. Phân ca

Working-time primary.

DOCX table:

| Ca | Giờ |
|---|---|
| A | 06:00–14:00 |
| B | 14:00–22:00 |
| C | 22:00–06:00 |

### S5 — Điều 5. Nghỉ giữa ca và nghỉ hằng tuần

Working-time secondary.

### S6 — Điều 6. Tài sản

### S7 — Điều 7. Điều khoản liên quan

Cross-reference:

- quyền kết thúc hợp đồng thực hiện theo Điều 8.

### S8 — Điều 8. Kết thúc quan hệ lao động

Termination primary.

---

# H14 — Procurement Specialist

## Challenge

- H1 Direct terminology
- H3 Generic heading
- H5 Distractor

## Cấu trúc

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Thử việc

Probation direct.

### S3 — Điều 3. Quyền lợi người lao động

Generic heading, salary substantive.

### S4 — Điều 4. Tổ chức làm việc

Generic heading, working-time substantive.

### S5 — Điều 5. Quy trình mua hàng

Có từ `thanh toán` nhiều lần nhưng đều là thanh toán cho nhà cung cấp.

Đây là distractor lexical đối với salary nhưng không phải tiền lương người lao động.

### S6 — Điều 6. Bảo mật

### S7 — Điều 7. Chấm dứt hợp đồng

Termination primary.

---

# H15 — Data Engineer

## Challenge

- H2 Paraphrase
- H4 Multiple relevant sections
- H5 Distractor
- H7 Cross-reference
- H8 salary missing

## Missing category

`salary`

Không được có:

- mức lương;
- tiền lương;
- phụ cấp;
- ngày trả lương;
- probation salary;
- khoản tiền trả cho công việc.

Distractor phải thuộc category khác.

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Giai đoạn đánh giá ban đầu

Paraphrase probation nhưng KHÔNG nhắc probation salary.

### S3 — Điều 3. Thời hạn hợp đồng

### S4 — Điều 4. Lịch công tác

Working-time primary.

### S5 — Điều 5. Trực hệ thống và nghỉ bù

Working-time secondary.

### S6 — Điều 6. Thiết bị và dữ liệu

Distractor termination:

- thiết bị phải hoàn trả khi quan hệ lao động kết thúc.

### S7 — Điều 7. Điều khoản áp dụng

Cross-reference tới termination section.

### S8 — Điều 8. Kết thúc quan hệ lao động

Termination primary.

---

# H16 — Operations Coordinator

## Challenge

- H1 Direct terminology
- H3 Generic heading
- H6 Table
- H8 working_time missing

## Missing category

`working_time`

Không được có:

- giờ bắt đầu/kết thúc;
- số giờ/ngày;
- số ngày/tuần;
- shift/ca làm việc;
- nghỉ giữa ca;
- nghỉ hằng tuần;
- overtime.

Table của hợp đồng này phải thuộc salary, không phải working time.

### S0 — Preamble

### S1 — Điều 1. Công việc

### S2 — Điều 2. Thử việc

Probation direct.

### S3 — Điều 3. Quyền lợi

Generic heading.

Salary primary trong DOCX table:

| Khoản | Mức |
|---|---:|
| Lương theo công việc | ... |
| Phụ cấp trách nhiệm | ... |
| Hỗ trợ điện thoại | ... |

Bên dưới có ngày thanh toán.

### S4 — Điều 4. Thời hạn hợp đồng

Không mô tả work schedule.

### S5 — Điều 5. Bảo hiểm và nghỉ phép năm

Chỉ annual leave/insurance.

Không mô tả weekly rest hoặc working schedule.

### S6 — Điều 6. Tài sản

### S7 — Điều 7. Chấm dứt hợp đồng

Termination primary.

### S8 — Điều 8. Điều khoản chung

Không working-time content.

---

# Missing-category freeze

Final distribution:

| Contract | Missing category |
|---|---|
| H10 | probation |
| H12 | termination |
| H15 | salary |
| H16 | working_time |

Mỗi missing category phải có toàn bộ section judgment bằng relevance `0`
cho category tương ứng.

Không được chèn distractor relevance `1` vào chính category được đánh dấu missing.

---

# Generation rules

Khi tạo DOCX:

1. Không thay challenge assignment.
2. Không thêm category mới vào missing-category contracts.
3. Có thể điều chỉnh câu chữ để hợp đồng tự nhiên hơn nhưng không thay semantic content.
4. Không xem retrieval result trong quá trình tạo.
5. Mỗi DOCX sau khi hoàn tất phải được SHA-256 freeze.
6. Sau khi freeze DOCX mới được export canonical sections.
7. Không sửa DOCX sau khi annotation bắt đầu, trừ khi tạo version mới.

---

# Evaluation firewall

Trước khi hoàn thành các bước sau, tuyệt đối không chạy retriever trên held-out:

```text
16 DOCX complete
        ↓
SOURCE_SHA256SUMS frozen
        ↓
canonical sections exported
        ↓
section boundaries reviewed
        ↓
manual annotation complete
        ↓
qrels materialized
        ↓
qrels SHA-256 frozen
        ↓
ONLY THEN run retrieval