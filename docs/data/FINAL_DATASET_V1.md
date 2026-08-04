# Bộ dữ liệu canonical cuối của đồ án

## Định danh

- Tên phát hành: `labor-law-canonical-word-v1.0-academic-final`
- Release bất biến: `data/releases/labor-law-canonical-word-20260804-164432-candidate`
- Commit sửa chunker: `729ca92b2bcf4f4656c4d722a9d7ea386a000e63`
- Số chunk: `804`
- SHA-256 của `canonical_chunks.jsonl`:
  `fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307`

## Chính sách nguồn

Bộ dữ liệu runtime gồm 18 văn bản:

- 17 văn bản lấy nội dung từ Word Công báo điện tử Chính phủ.
- 1 văn bản, `10/2020/TT-BLĐTBXH`, sử dụng VBPL làm nguồn
  fallback được khai báo rõ.

Pháp điển chỉ dùng để đối chiếu phạm vi và kiểm tra hồi quy,
không gộp vào corpus runtime.

## Kết quả kiểm chứng

- 513 source article container.
- 510 canonical article container.
- 52/52 khác biệt Word/VBPL đã được migrate.
- 56 biến đổi hiệu lực pháp lý.
- 804 chunk.
- 0 chunk vượt giới hạn tokenizer E5.
- Golden evidence đã bind vào đúng SHA-256 của corpus.
- Qdrant mới chỉ dry-run; production alias chưa được chuyển.

## Phạm vi tuyên bố

Đây là bộ dữ liệu cuối phục vụ đồ án, thực nghiệm và demo.

Bộ dữ liệu không phải văn bản hợp nhất chính thức, không thay thế
ý kiến pháp lý và chưa được tuyên bố là bản phát hành pháp lý chính thức.
