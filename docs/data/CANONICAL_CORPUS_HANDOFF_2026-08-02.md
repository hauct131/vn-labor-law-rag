# Bàn giao canonical corpus luật lao động

## Kết luận

Project hiện có một **canonical technical candidate tái lập được**, không phải production release:

- Pháp điển 20.2 chỉ dùng làm bộ đối chiếu phạm vi, không index vào runtime.
- 18 nguồn chính thức là nền runtime; 16 snapshot VBPL và 2 DOCX đều đã kiểm tra hash.
- Các phần hết hiệu lực đã biết trong Nghị định 135/2020, Nghị định 145/2020 và Thông tư 09/2020 đã được lọc hoặc thay nội dung ở cấp đơn vị.
- Bộ hồi quy 45 câu đã khóa, giữ đủ 175/175 bằng chứng.
- Qdrant alias chưa chuyển vì strict gate còn chặn đúng ba nhóm việc bên ngoài bản build này.

Release:

```text
data/releases/labor-law-canonical-20260727-candidate
```

Ngày khóa pháp luật vẫn là `2026-07-27`. Ngày sinh release không được dùng thay cho ngày khóa pháp luật.

## Các lớp dữ liệu

| Lớp | Số lượng | Vai trò |
|---|---:|---|
| Pháp điển 20.2 | 477 điều, 1.382 chunk cũ | Đối chiếu phạm vi và phát hiện khoảng trống |
| Unified source candidate | 18 văn bản, 513 container, 833 chunk | Nguồn kỹ thuật trước review hiệu lực |
| Canonical technical candidate | 18 văn bản, 510 container, 834 chunk | Corpus đã áp dụng 50 biến đổi hiệu lực |

Không cộng hoặc gộp trực tiếp các số lượng của Pháp điển với corpus runtime.

## Kết quả đối chiếu Pháp điển

`data/governance/phapdien_20_2_coverage_report.json` phân loại 477 điều:

- 233 điều khớp cả văn bản và điều trong phạm vi 18 nguồn;
- 23 điều thuộc văn bản có trong runtime nhưng nằm ngoài phạm vi điều đã chọn;
- 221 điều thuộc văn bản không nằm trong bộ 18 nguồn.

Hai nhóm sau là review lead. Chúng không tự động trở thành nguồn cần index vì có thể là quy định ngoài phạm vi, lịch sử, văn bản sửa đổi hoặc bằng chứng đã được thay thế.

## Quyết định hiệu lực đã mã hóa

Cấu hình nguồn sự thật:

```text
config/legal_effect_decisions_20260727.json
```

Các quyết định chính:

- Nghị định 135/2020/NĐ-CP: loại khoản 2 Điều 3; khoản 1 và khoản 3 Điều 7; khoản 2 Điều 8. Phụ lục III không có container riêng trong release.
- Nghị định 152/2020/NĐ-CP: giữ Điều 22-28 vì phần này điều chỉnh lao động Việt Nam làm việc cho tổ chức, cá nhân nước ngoài; phần lao động nước ngoài tại Việt Nam đã không được chọn.
- Nghị định 128/2025/NĐ-CP: chỉ Điều 7 được chọn; Điều 8 và Mục 2 Phụ lục II bị Nghị định 219/2025 làm hết hiệu lực nhưng không có trong runtime.
- Nghị định 145/2020/NĐ-CP: loại các đơn vị thẩm quyền cũ đã được Điều 71-79 Nghị định 129/2025 quy định tương ứng trong giai đoạn tạm thời.
- Thông tư 09/2020/TT-BLĐTBXH: bỏ yêu cầu bản sao sổ hộ khẩu hoặc giấy tạm trú tại khoản 2 Điều 6.

Đây là review kỹ thuật dựa trên nguồn chính thức, không thay thế ý kiến pháp lý của người có thẩm quyền.

## Ba gate còn chặn production

1. **Bốn khoảng trống nội dung sửa đổi Nghị định 145/2020**: khoản 2 Điều 31 theo Nghị định 35/2022; khoản 4 Điều 4, khoản 5 Điều 31 và khoản 4 Điều 62 theo Nghị định 10/2024. Cần tải, hash-bind và parse hai văn bản sửa đổi từ nguồn chính thức.
2. **Exact E5 audit**: 802 chunk không đổi kế thừa audit chính xác; 32 chunk biến đổi đã kiểm tra cấu trúc bằng `cl100k_base`, tối đa 315/320 token. Toàn file 834 chunk vẫn phải chạy lại tokenizer `intfloat/multilingual-e5-large` và bind kết quả với SHA-256 mới.
3. **Authority approval**: người có thẩm quyền phải duyệt `legal_effect_review.json` và ký theo SHA-256 của manifest cuối.

Do ba gate này, `production_publishable=false` là kết quả đúng. Không được sửa tay thành `true`.

## Đánh giá

Golden regression:

```text
45 câu
44 câu benchmark_enabled
175/175 evidence tồn tại
0 evidence thiếu
```

BM25 trên canonical candidate:

| K | Any article hit | Article recall | MRR |
|---:|---:|---:|---:|
| 1 | 0,6591 | 0,4856 | 0,6591 |
| 3 | 0,7727 | 0,5973 | 0,7121 |
| 5 | 0,8409 | 0,6580 | 0,7292 |
| 10 | 0,9318 | 0,7763 | 0,7425 |

Kết quả hit/recall tại K=5 và K=10 không giảm so với baseline 833 chunk. Không dùng 45 câu này để tuning thêm. Tập 20 câu mới hiện là `human_label_review_required` và chỉ được dùng làm held-out sau khi review nhãn.

## Qdrant blue-green

Dry-run của corpus 833 chunk với audit E5 cũ đã thành công. Dry-run của canonical 834 chunk bị chặn vì SHA-256 audit không khớp — đúng thiết kế.

Sau khi ba gate trên đạt:

1. Tạo collection mới, không ghi đè collection đang chạy.
2. Index đúng số point bằng số canonical chunk.
3. Kiểm tra payload, citation và golden regression.
4. Chuyển alias runtime sang collection mới.
5. Giữ collection cũ để rollback.

## Tái lập

```bash
cd /media/hao/Data/vn-labor-law-rag
bash scripts/run_canonical_completion.sh
```

Script sẽ build lại corpus, audit golden, chạy BM25, kiểm tra Qdrant preflight fail-closed, chạy toàn bộ test không phụ thuộc model/network, rồi tạo lại checksum.

Kết quả kiểm thử tại thời điểm bàn giao:

```text
427 passed, 15 skipped, 2 deselected
```

Các test `integration` bị loại ở lượt này vì cần model tokenizer/embedding hoặc dịch vụ ngoài môi trường. Exact E5 là một gate riêng, không bị coi nhầm là đã đạt.

## Nguồn bằng chứng chính thức

- Nghị định 158/2025/NĐ-CP: https://xaydungchinhsach.chinhphu.vn/toan-van-nghi-dinh-158-2025-nd-cp-quy-dinh-ve-bao-hiem-xa-hoi-bat-buoc-119250629171336803.htm
- Nghị định 219/2025/NĐ-CP: https://vanban.chinhphu.vn/?docid=214840&pageid=27160
- Nghị định 129/2025/NĐ-CP: https://vanban.chinhphu.vn/?docid=213920&pageid=27160
- Thông tư 08/2023/TT-BLĐTBXH: https://vanban.chinhphu.vn/?docid=208729&pageid=27160
- Nghị định 35/2022/NĐ-CP: https://vanban.chinhphu.vn/?docid=205861&pageid=27160
- Nghị định 10/2024/NĐ-CP: https://vanban.chinhphu.vn/?docid=209657&pageid=27160
