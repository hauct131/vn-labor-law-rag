# Held-out Contract Matrix

## Mục tiêu

Thiết kế 16 hợp đồng held-out cho bài toán contract clause retrieval.

Các hợp đồng phải đa dạng về cấu trúc và cách diễn đạt, nhưng không được xây dựng nhằm làm thất bại riêng một retriever cụ thể.

## Contract Matrix

| ID | Vai trò | H1 Direct | H2 Paraphrase | H3 Generic heading | H4 Multi-section | H5 Distractor | H6 Table | H7 Cross-ref | H8 Missing |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H01 | Backend Developer | ✓ |  |  |  |  |  |  |  |
| H02 | UI/UX Designer |  | ✓ | ✓ |  |  |  |  |  |
| H03 | Network Engineer | ✓ |  |  | ✓ |  | ✓ |  |  |
| H04 | Customer Support |  | ✓ |  |  | ✓ |  |  |  |
| H05 | Warehouse Supervisor | ✓ |  | ✓ | ✓ |  | ✓ |  |  |
| H06 | Security Analyst |  | ✓ |  |  | ✓ |  | ✓ |  |
| H07 | HR Specialist | ✓ |  |  | ✓ |  |  | ✓ |  |
| H08 | Sales Executive |  |  | ✓ |  | ✓ | ✓ |  |  |
| H09 | DevOps Engineer |  | ✓ | ✓ | ✓ |  |  |  |  |
| H10 | Office Administrator | ✓ |  |  |  | ✓ |  | ✓ | ✓ |
| H11 | QA Engineer |  | ✓ |  | ✓ |  | ✓ |  |  |
| H12 | Finance Assistant | ✓ |  | ✓ |  | ✓ |  |  | ✓ |
| H13 | Call Center Agent |  | ✓ |  | ✓ |  | ✓ | ✓ |  |
| H14 | Procurement Specialist | ✓ |  | ✓ |  | ✓ |  |  |  |
| H15 | Data Engineer |  | ✓ |  | ✓ | ✓ |  | ✓ | ✓ |
| H16 | Operations Coordinator | ✓ |  | ✓ |  |  | ✓ |  | ✓ |

## Coverage Summary

- H1 Direct terminology: 8 contracts
- H2 Paraphrase: 8 contracts
- H3 Generic headings: 8 contracts
- H4 Multiple relevant sections: 7 contracts
- H5 Distractor mentions: 7 contracts
- H6 Tables: 7 contracts
- H7 Cross references: 5 contracts
- H8 Missing category: 4 contracts

## Missing-category distribution

Missing-category cases phải phân tán:

- H10: probation missing
- H12: termination missing
- H15: salary missing
- H16: working_time missing

Không được thay đổi distribution này sau khi xem retrieval output.

## Per-contract content constraints

### H01 - Backend Developer

- Direct terminology.
- probation: explicit section.
- salary: explicit salary section.
- working_time: explicit schedule.
- termination: explicit termination section.
- Đây là regression/basic case.

### H02 - UI/UX Designer

- Paraphrase probation.
- Generic headings.
- Không dùng heading “Thử việc”.
- Nội dung probation phải vẫn đủ rõ để con người xác định.

### H03 - Network Engineer

- working_time phân tán ở nhiều section.
- Có bảng lịch trực.
- Một section chính, một section supplementary.

### H04 - Customer Support

- Paraphrase working time.
- Có distractor termination mention trong phần bàn giao tài sản.
- Termination thật ở section khác.

### H05 - Warehouse Supervisor

- Generic headings.
- working_time nằm ở nhiều section.
- Salary hoặc shift schedule nằm trong table.

### H06 - Security Analyst

- Paraphrase probation hoặc termination.
- Có distractor keyword.
- Có cross-reference không chứa substantive content.

### H07 - HR Specialist

- Direct terminology.
- probation hoặc termination được phân tán qua hai section.
- Có cross-reference relevance 1.

### H08 - Sales Executive

- Generic heading.
- Có distractor salary mention.
- Salary chính nằm trong table.

### H09 - DevOps Engineer

- Paraphrase.
- Generic heading.
- working_time chia giữa schedule và on-call/rest.

### H10 - Office Administrator

- probation missing.
- Các category khác explicit.
- Có distractor keyword và cross-reference.

### H11 - QA Engineer

- Paraphrase probation.
- Multiple relevant sections.
- Có table về salary hoặc schedule.

### H12 - Finance Assistant

- termination missing.
- Generic headings.
- Có distractor termination-related wording nhưng không substantive.

### H13 - Call Center Agent

- Paraphrase working time.
- Multiple sections.
- Shift table.
- Cross-reference.

### H14 - Procurement Specialist

- Direct terminology.
- Generic headings.
- Distractor keyword.

### H15 - Data Engineer

- salary missing.
- Paraphrase.
- Multiple relevant sections.
- Distractor.
- Cross-reference.

### H16 - Operations Coordinator

- working_time missing.
- Generic headings.
- Table.
- Các category còn lại có substantive content.

## Freeze rule

Sau khi file này được commit:

- không thay challenge assignment;
- không thay missing-category distribution;
- không sửa contract specification dựa trên retrieval output;
- nếu cần sửa vì lỗi dữ liệu thật, tạo version mới.