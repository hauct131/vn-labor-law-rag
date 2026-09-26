# Contract Clause Retrieval Annotation Guideline v1

## 1. Task

Given one Vietnamese employment contract and one review category,
judge how relevant each contract section is to that category.

This dataset evaluates **clause retrieval only**.

It does NOT evaluate whether a clause is:
- legally compliant,
- illegal,
- valid,
- invalid,
- fair,
- unfair.

A relevant section may describe:
- a compliant arrangement,
- a potentially problematic arrangement,
- an explicit absence of an arrangement,
- or incomplete information.

The annotation question is:

> "How relevant is this contract section to the given review category?"

It is NOT:

> "Does this section violate labor law?"

---

## 2. Categories

### probation

Includes information about:
- whether probation applies,
- probation duration,
- probation work or position,
- probation salary,
- probation assessment,
- probation result,
- completion or termination of probation.

Examples:
- "Thời gian thử việc là 60 ngày."
- "Không áp dụng thử việc."
- "Lương thử việc bằng 85% mức lương theo công việc."
- "Kết quả thử việc được đánh giá..."
- "Sau thời gian thử việc..."

A section that only contains contextual information about the employee's
job, education, or qualification is NOT probation-relevant unless it
also directly discusses probation.

---

### salary

Includes information about:
- official salary for the job,
- allowances,
- additional payments,
- salary payment date,
- salary payment period,
- salary payment method,
- delayed salary payment,
- salary deductions,
- salary adjustment,
- salary-related payment obligations.

Important:

Probation salary belongs primarily to `probation`.

A section that discusses only probation salary is normally NOT a
primary salary section.

Examples:
- "Mức lương theo công việc là 15.000.000 đồng/tháng."
- "Tiền lương được thanh toán vào ngày 05 hằng tháng."
- "Phụ cấp trách nhiệm là..."
- "Công ty được lùi thời điểm trả lương..."
- "Thanh toán lương đúng hạn."

---

### working_time

Includes information about:
- working hours per day,
- working hours per week,
- work schedules,
- shifts,
- shift rotation,
- overtime,
- breaks,
- rest between shifts,
- weekly rest.

Examples:
- "08 giờ/ngày, 05 ngày/tuần."
- "Làm việc theo ca 12 giờ."
- "Ca A từ 06:00 đến 14:00."
- "Khoảng nghỉ giữa hai ca..."
- "Nghỉ hằng tuần..."
- "Làm thêm giờ..."

Annual leave alone is NOT sufficient for working_time relevance in this
benchmark unless the section also contains working-time or rest-schedule
information.

---

### termination

Includes information about:
- termination grounds,
- expiry of an employment contract,
- early termination,
- unilateral termination,
- notice period,
- procedures for termination,
- obligations directly connected with termination,
- handover or settlement obligations directly tied to termination.

Examples:
- "Hợp đồng chấm dứt khi hết thời hạn."
- "Bên A có quyền chấm dứt hợp đồng trước thời hạn..."
- "Thông báo trước 30 ngày."
- "Đơn phương chấm dứt hợp đồng."
- "Khi chấm dứt hợp đồng, hai bên thực hiện bàn giao..."

A section stating only the duration of a fixed-term contract is NOT
termination-relevant unless it explicitly discusses expiry, termination,
or consequences of expiry.

---

## 3. Relevance labels

### 0 — Not relevant

The section does not provide useful information for reviewing the
category.

Incidental words alone are not sufficient.

Examples:
- A confidentiality section mentioning "when the contract ends" only as
  background may still be 0 if it does not provide substantive
  termination information.
- A job-description section requiring a university degree is 0 for
  `probation` if it does not discuss probation.

---

### 1 — Related mention

The section mentions or cross-references the category, but does not
contain substantive information that should normally be used as the
main retrieval evidence.

Typical cases:
- cross-reference to another section,
- incidental mention,
- secondary phrase without enough content to review the category.

Examples:
- A probation section saying:
  "Mức lương thử việc được tính theo mức lương tại Điều 4."
  For `salary`, this may be relevance 1.
- A section mentioning "bàn giao khi chấm dứt hợp đồng" without
  providing termination grounds or notice conditions may be relevance 1.

---

### 2 — Relevant / Secondary

The section contains substantive information useful for reviewing the
category, but it is:
- secondary,
- incomplete,
- supplementary,
- or covers only part of the category.

Examples:
- One section contains working hours, while another contains weekly rest.
  The main working-hours section may be 3 and the rest section may be 2.
- A general employer-obligation section requiring salary to be paid on
  time may be 2 when another section contains the complete salary terms.

---

### 3 — Primary

The section directly contains the principal information needed to review
the category.

This is the section that a retrieval system should ideally rank first
for that category.

Examples:
- "Điều 3. Tiền lương..." containing salary amount, payment date and
  payment method.
- "Điều 2. Thử việc..." containing probation duration and probation
  salary.
- "Điều 4. Tổ chức ca làm việc..." containing the shift schedule.
- "Điều 7. Chấm dứt hợp đồng..." containing termination grounds and
  notice period.

---

## 4. Annotation rules

1. Judge the complete section, not only its heading.

2. Relevance does NOT mean legal violation.

3. Explicit absence is still relevant.

   Example:

   "Hai bên xác nhận không áp dụng thử việc."

   This is primary information for `probation` and should normally
   receive relevance 3.

4. One section may be relevant to more than one category.

5. Multiple sections may receive relevance 2 or 3 when the contract
   genuinely distributes the relevant information across sections.

6. Do not assign positive relevance merely because a generic keyword
   occurs in the section.

7. Cross-references without substantive information normally receive
   relevance 1.

8. When one section contains the principal content and another contains
   supplementary content:

   - principal section: relevance 3
   - supplementary section: relevance 2

9. Relevance is judged by DIRECT TOPICAL CONTENT.

   A section is not relevant merely because it contains contextual facts
   that may later be useful for downstream legal analysis.

   Examples:

   - A job-description section requiring a university degree is NOT
     probation-relevant unless it also discusses probation.

   - A section stating only the contract duration is NOT
     termination-relevant unless it explicitly discusses expiry,
     termination, or consequences of expiry.

   - Annual leave alone is NOT working_time relevance for this
     benchmark.

10. This benchmark evaluates clause/topic retrieval, not retrieval of
    every contextual fact required by downstream legal validation.

11. A category may legitimately have no relevant section.

    In that case, every section receives relevance 0.

12. For `termination`, a fixed-term expiry section is relevant only when
    it explicitly states that the contract ends or terminates upon
    expiry.

13. For `salary`, a section discussing only probation salary normally
    receives relevance 1 because probation compensation belongs
    primarily to the `probation` category.

14. If a section contains both official salary information and probation
    salary information, evaluate the complete content:

    - if official salary terms are the main content, it may receive
      salary relevance 3;
    - if it only mentions probation salary, it should normally receive
      salary relevance 1.

15. If a section contains several categories because the contract is a
    draft or incomplete document, each category must be judged
    independently.

    Example:

    A section saying:
    - complete probation information,
    - add salary payment date,
    - confirm working schedule,
    - add termination notice,

    may legitimately receive positive relevance for all four categories.

16. Do not use legal-compliance severity to decide retrieval relevance.

    Example:

    A clearly lawful salary clause and a potentially problematic salary
    clause can both be relevance 3 for `salary`.

17. Do not change annotations after seeing retrieval results from:
    - Legacy V1,
    - lexical retrieval,
    - BM25,
    - E5,
    - Hybrid,
    - Cross-Encoder,
    - or any other retrieval model.

18. Any ambiguous case must include a written annotation note explaining
    why the selected relevance level was chosen.

---

## 5. Special-case guidance

### 5.1 Explicit absence

Statements such as:

- "Không áp dụng thử việc."
- "Không bố trí làm thêm giờ."
- "Không áp dụng phụ cấp..."

can still be highly relevant because they directly answer the review
question for that category.

Explicit absence is NOT the same as missing information.

---

### 5.2 Missing information

A section explicitly stating that information still needs to be added
can be relevant.

Example:

"Thông tin về thử việc sẽ được bổ sung trước khi ký chính thức."

This is directly relevant to `probation`, even though the substantive
probation terms are missing.

The score depends on how central the section is:

- primary missing-information section: 3
- secondary reminder/cross-reference: 1 or 2

---

### 5.3 Cross-references

Example:

"Lương thử việc bằng 85% mức lương theo công việc nêu tại Điều 4."

For `probation`:
- this is substantive probation information and may be 3.

For `salary`:
- if the section only references the official salary elsewhere,
  it is normally 1.

The referenced salary section itself may be 3 for `salary`.

---

### 5.4 Contract duration vs termination

Example:

"Hợp đồng có thời hạn 24 tháng từ ngày X đến ngày Y."

This alone is NOT termination relevance.

Normally:

termination = 0

Example:

"Hợp đồng chấm dứt khi hết thời hạn nêu tại Điều 2."

This directly discusses termination by expiry.

Normally:

termination = 2 or 3 depending on whether it is the primary termination
section.

---

### 5.5 Working time vs annual leave

A section containing only:
- annual leave,
- public holidays,
- insurance benefits,

is normally relevance 0 for `working_time`.

A section containing:
- weekly rest,
- breaks between shifts,
- rest schedule,

is working_time-relevant.

---

### 5.6 Termination-related handover

A section that only says:

"Người lao động phải hoàn trả tài sản khi chấm dứt hợp đồng."

usually receives:
- termination relevance 1

because termination is only the context for another obligation.

A section saying:

"When terminating the contract, the parties must perform handover,
final payment and other termination obligations."

may receive:
- termination relevance 2

if these are substantive post-termination duties.

---

## 6. Annotation procedure

For each document:

1. Verify that the source DOCX is the frozen source version.

2. Verify extracted content against the original DOCX.

3. Verify every section boundary.

4. Read the complete section.

5. For each of the four categories:
   - probation
   - salary
   - working_time
   - termination

   assign relevance:
   - 0
   - 1
   - 2
   - 3

6. Add a reason for:
   - every relevance score greater than 0,
   - every ambiguous case.

7. Review the document again for consistency.

8. Do not inspect retrieval-model output while annotating.

---

## 7. Recommended annotation workflow

The preferred process is:

Source DOCX
    ↓
SHA-256 source lock
    ↓
manual verification of extraction
    ↓
manual verification of section boundaries
    ↓
freeze annotation guideline
    ↓
Annotator A labels independently
    ↓
Annotator B labels independently
    ↓
compare disagreements
    ↓
adjudication
    ↓
final qrels
    ↓
SHA-256 lock
    ↓
retrieval evaluation

If only one annotator is available:

1. perform the first annotation pass;
2. wait before reviewing;
3. perform a second independent review without looking at model output;
4. resolve inconsistencies;
5. then freeze the qrels.

---

## 8. Dataset integrity

Source DOCX files must be frozen using SHA-256 before annotation.

The annotation guideline must be frozen before retrieval evaluation.

Canonical section boundaries must be manually reviewed before relevance
judgments are finalized.

The final qrels must:
- contain no null relevance values,
- contain only values 0, 1, 2, or 3,
- include reasons for positive judgments,
- be versioned,
- be hashed after final review.

After the gold set is frozen, labels must not be changed because a
retrieval method performs poorly.

If a genuine annotation error is discovered later:
- create a new dataset version,
- document the correction,
- regenerate the hash,
- rerun all compared methods.

---

## 9. Evaluation interpretation

Recommended interpretation:

- relevance 3:
  primary / ideal retrieval target

- relevance 2:
  substantively relevant secondary target

- relevance 1:
  related mention only

- relevance 0:
  not relevant

Recommended metrics:

### Strict Hit@1

Top-1 result must have relevance 3.

### Recall@k

Relevant sections are those with relevance >= 2.

### MRR

Use the rank of the first relevance-3 section.

### nDCG@k

Use the full graded relevance scale 0–3.

### Latency

Measure retrieval latency separately from relevance quality.

---

## 10. Important limitation

This golden set is a gold standard for:

> contract-clause retrieval relevance

It is NOT a gold standard for:

> legal compliance correctness

It does not determine whether a contract clause violates Vietnamese
labor law.

Legal-rule validation, severity classification, and legal conclusion
accuracy must be evaluated separately.