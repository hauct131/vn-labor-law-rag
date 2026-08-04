# Chính sách nguồn DOCX Công báo

## Quyết định

Công báo điện tử Chính phủ **đủ làm nguồn nội dung canonical** khi trang văn
bản cung cấp DOCX thật. Công báo **không phải nguồn duy nhất** cho toàn bộ
metadata pháp lý.

Pipeline dùng vai trò nguồn như sau:

| Dữ liệu | Nguồn ưu tiên | Vai trò |
|---|---|---|
| Nội dung Điều, khoản, phụ lục, bảng | DOCX Công báo | Canonical để parse/chunk/index |
| Số hiệu, tiêu đề, loại văn bản, cơ quan, người ký, ngày ban hành | Cổng TTĐT Chính phủ + trang Công báo | Đối chiếu metadata nhận dạng |
| Số Công báo, URL DOCX/PDF | Trang Công báo | Provenance xuất bản |
| Trạng thái hiệu lực hiện tại, hết hiệu lực, sửa đổi/bãi bỏ, quan hệ văn bản | VBPL đã xác minh hoặc legal-effect review | Không tự suy diễn từ DOCX |
| PDF ký | Cổng TTĐT Chính phủ/Công báo | QA ngoài pipeline; không tạo bộ chunk thứ hai |

Không index đồng thời text DOCX và text PDF/OCR của cùng một văn bản. Làm vậy
sẽ tạo bằng chứng trùng và có thể đưa các phiên bản text lệch nhau vào cùng
collection.

## Hai trường hợp DOCX hiện tại

### 18/VBHN-VPQH

- Công báo số 131, ban hành ngày 12/02/2026.
- Trang Công báo không công bố ngày hiệu lực cho bản hợp nhất này.
- Ngày `2021-01-01` trong release là hiệu lực của Bộ luật nền, không phải một
  trường được crawler đọc từ trang Công báo. Trường này phải tiếp tục được
  quản lý bởi `legal_effect_review`.

### 66.18/2026/NQ-CP

- Công báo số 301, ban hành ngày 18/05/2026, hiệu lực ngày 01/07/2026.
- Cổng TTĐT Chính phủ bổ sung cơ quan ban hành và người ký.
- `effective_to=2027-02-28` và trạng thái `effective_temporarily` là kết quả
  review điều khoản hiệu lực, không phải metadata do crawler Công báo tự sinh.

## Luồng dữ liệu

```text
Trang Công báo
  -> chọn đúng một link DOCX theo số hiệu
  -> tải DOCX, từ chối PDF/HTML
  -> kiểm tra ZIP + word/document.xml + số hiệu trong thân văn bản
  -> đối chiếu trang metadata Cổng TTĐT Chính phủ
  -> snapshot bất biến + SHA256SUMS.txt + manifest field-level provenance
  -> materialize data/sources/official_docx/<source_file>
  -> build release candidate mới
  -> token audit + golden regression + approval
  -> blue/green index; chỉ đổi alias sau khi tất cả gate đạt
```

Crawler mới tái sử dụng từ `scripts/vbpl_portal.py`:

- retry/backoff có giới hạn;
- chuẩn hóa và so khớp chính xác số hiệu;
- khóa theo văn bản để tránh chạy đồng thời;
- atomic write;
- snapshot bất biến;
- SHA-256 manifest và kiểm tra resume.

Crawler không tái sử dụng SOAP/gateway/Playwright của VBPL và không tải PDF.

## Metadata snapshot

Mỗi snapshot dùng schema `congbao-docx-source-snapshot-v2` và ghi:

- `document`: các giá trị metadata đã hợp nhất;
- `observed_metadata`: giá trị quan sát riêng từ từng trang;
- `field_provenance`: nguồn và trạng thái của từng nhóm trường;
- `source`: URL trang, URL DOCX, link PDF chỉ để QA, các operation đã chạy;
- `content_hashes`: SHA-256 DOCX, text nhìn thấy và hai trang HTML;
- `package`: số member OOXML và số member dùng đường dẫn `\` bất thường;
- `gates`: toàn bộ điều kiện fail-closed;
- `limitations`: những điều crawler không tuyên bố.

Ba trường sau phải để `null` ở snapshot crawler nếu chưa có review:

```json
{
  "effective_to": null,
  "legal_status": null,
  "relations": null
}
```

## Lệnh vận hành

Crawl hai DOCX đã cấu hình:

```bash
make official-docx-crawl
```

Kiểm tra run manifest:

```bash
python3 -m json.tool data/raw/official_docx/run_manifest.json
```

Chỉ khi bản nguồn trên mạng đã thay đổi và đã được review mới cho phép thay
file materialized:

```bash
.venv/bin/python3 scripts/congbao_docx.py \
  --replace-source \
  fetch '66.18/2026/NQ-CP'
```

Tạo release candidate mới sau khi crawl:

```bash
UNIFIED_RELEASE_DIR=data/releases/<release-id-moi> \
  make official-docx-refresh-and-release
```

Không trỏ alias Qdrant hiện tại sang release mới trong cùng bước crawl.
