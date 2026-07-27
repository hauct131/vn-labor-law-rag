# Nguồn gốc dữ liệu — candidate 2026-07-27

## Phạm vi release

Release kỹ thuật hiện tại:

```text
data/releases/labor-law-2026-07-27-candidate/
```

Release gồm 18 văn bản, 513 đơn vị truy hồi và 778 chunk:

- 16 văn bản đã thu thập từ cổng VBPL;
- 220 Điều Bộ luật Lao động từ `18/VBHN-VPQH`;
- Điều 4, Điều 6 và sáu đơn vị bằng chứng thuộc Phụ lục I.4 của
  `66.18/2026/NQ-CP`.

Sáu đơn vị phụ lục là:

```text
NQ66.18.PL-I.4.C.I
NQ66.18.PL-I.4.C.III
NQ66.18.PL-I.4.C.V
NQ66.18.PL-I.4.C.VII
NQ66.18.PL-I.4.C.VIII
NQ66.18.PL-I.4.C.IX
```

Chúng là các mục trong Phụ lục I.4, không phải các Điều độc lập.

### Giới hạn của 16 snapshot VBPL

Builder đã tính lại `full_text.txt` và xác nhận 16/16 hash khớp giá trị trong
manifest. Tuy nhiên archive được cung cấp thiếu cả:

```text
SHA256SUMS.txt
portal/api_responses.json
```

ở cả 16 snapshot và các manifest vẫn ở trạng thái `staged_unapproved`. Vì vậy
release ghi:

```text
source_hashes_verified = false
base_vbpl_snapshot_verification_passed = false
```

Hai DOCX mới có provenance đầy đủ hơn, nhưng không thể dùng chúng để che lấp
khoảng trống của 16 nguồn VBPL.

## Hai nguồn DOCX chính thức

| Văn bản | Trang nguồn | SHA-256 DOCX |
|---|---|---|
| `18/VBHN-VPQH` | `https://vanban.chinhphu.vn/?docid=217002&pageid=27160` | `1386441b1f513defdd55186d7e65b8432dcac87c2e0d78676de25facb9c5e6ff` |
| `66.18/2026/NQ-CP` | `https://vanban.chinhphu.vn/?docid=218181&pageid=27160` | `c65df9bd52c10589ea51cc50ea7a36b8ba64d14470343e5d2e309c806b3f130f` |

Hai file đều có phần chữ ký OOXML và chứng thư mang chủ thể:

```text
CN=CÔNG BÁO NƯỚC CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM,
O=VĂN PHÒNG CHÍNH PHỦ,L=Hà Nội,C=VN
```

Pipeline chỉ xác nhận sự hiện diện của chữ ký và đọc chứng thư. Pipeline chưa
thực hiện xác minh mật mã toàn bộ XML signature, vì vậy manifest ghi rõ
`cryptographic_signature_validation = not_performed`.

DOCX của Nghị quyết dùng dấu `\` trong tên member ZIP thay vì `/`. File gốc
không bị sửa. Builder chỉ chuẩn hóa member name trong bộ nhớ khi đọc
`word/document.xml`; SHA-256 của file gốc được giữ nguyên.

## Chuỗi provenance

Mỗi nguồn được giữ theo chuỗi:

```text
DOCX gốc
→ SHA-256
→ raw snapshot + manifest + SHA256SUMS
→ Điều/đơn vị phụ lục
→ canonical article_code
→ chunk_id
→ evidence_chunk_ids trong golden v3
```

Các file kiểm chứng chính:

```text
data/raw/official_docx/
data/releases/labor-law-2026-07-27-candidate/source_inventory.json
data/releases/labor-law-2026-07-27-candidate/manifest.json
data/releases/labor-law-2026-07-27-candidate/SHA256SUMS.txt
data/quality/unified_release_validation.json
data/evaluation/golden_questions_v3_unified_candidate.json
```

## Mốc thời gian pháp lý

- `retrieved_at`: thời điểm pipeline tạo snapshot.
- `issued_at`: ngày văn bản được ban hành/xác thực.
- `effective_from`, `effective_to`: khoảng hiệu lực ghi trong nguồn.
- `law_as_of`: ngày corpus tuyên bố phản ánh pháp luật, hiện là
  `2026-07-27`.

`retrieved_at` không được dùng để suy ra hiệu lực pháp lý.

`66.18/2026/NQ-CP` có hiệu lực chung từ `2026-07-01` đến hết `2027-02-28`.
Khoản 4 Điều 7 còn cho phép quy định tương ứng hết hiệu lực sớm khi văn bản
mới có hiệu lực. Vì vậy release vẫn ở trạng thái:

```text
technical_candidate_pending_authority_review
```

Đây là corpus kỹ thuật phục vụ nghiên cứu, không thay thế văn bản gốc hoặc tư
vấn pháp lý.
