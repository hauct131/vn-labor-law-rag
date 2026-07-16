from __future__ import annotations

import argparse
import json
import logging
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

# Bootstrap path so the script can run from repository root
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.ingestion.legal_parser import (
    PARSER_VERSION,
    build_inspection_report,
    parse_legal_document,
    validate_corpus,
)


def markdown_escape(text: str | None) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    metadata = report["metadata"]
    class_counts = report["class_counts"]
    structure = report["structure"]
    article_stats = report["article_statistics"]
    citation_stats = report["citation_statistics"]
    validation = report["validation"]

    lines = [
        f"# Cấu trúc HTML — Đề mục {metadata['topic_code']} "
        f"{metadata['topic_name']}",
        "",
        "## 1. File được khảo sát",
        "",
        f"- File: `{metadata['source_file']}`",
        f"- Encoding phát hiện: `{metadata['detected_encoding']}`",
        f"- SHA-256: `{metadata['source_sha256']}`",
        f"- Phiên bản parser: `{metadata['parser_version']}`",
        "- Nguồn là HTML có cấu trúc, vì vậy **không sử dụng OCR**.",
        "",
        "## 2. Thống kê tổng quan",
        "",
        f"- Tổng số điều: **{article_stats['total']}**",
        f"- Chương: **{structure['chapter_count']}**",
        f"- Mục: **{structure['section_count']}**",
        f"- Bảng nằm trong nội dung điều: **{article_stats['table_count']}**",
        f"- Tệp/phụ lục đính kèm: **{article_stats['attachment_count']}**",
        f"- Anchor điều bị thiếu: **{article_stats['missing_anchor_ids']}**",
        f"- Anchor điều bị trùng: **{article_stats['duplicate_anchor_ids']}**",
        f"- Điều rỗng: **{article_stats['empty_article_count']}**",
        f"- Điều không xác định được chương: "
        f"**{article_stats['articles_without_chapter_count']}**",
        "",
        "### Phân bố theo nguồn",
        "",
        "| Mã nguồn | Loại nguồn | Số điều |",
        "|---|---|---:|",
        f"| `LQ` | Bộ luật/Luật | "
        f"{article_stats['by_source_type'].get('LQ', 0)} |",
        f"| `NĐ` | Nghị định | "
        f"{article_stats['by_source_type'].get('NĐ', 0)} |",
        f"| `TT` | Thông tư | "
        f"{article_stats['by_source_type'].get('TT', 0)} |",
        "",
        "## 3. Các class HTML chính",
        "",
        "| Class | Số lượng | Vai trò thực tế |",
        "|---|---:|---|",
        f"| `pChuong` | {class_counts.get('pChuong', 0)} | "
        "Dùng chung cho mã chương, tên chương, mã mục và tên mục |",
        f"| `pDieu` | {class_counts.get('pDieu', 0)} | "
        "Tiêu đề điều pháp điển và anchor ID |",
        f"| `pGhiChu` | {class_counts.get('pGhiChu', 0)} | "
        "Ghi chú nguồn, số văn bản, hiệu lực và URL nguồn |",
        f"| `pNoiDung` | {class_counts.get('pNoiDung', 0)} | "
        f"Marker bắt đầu nội dung; rỗng "
        f"{article_stats['empty_p_noi_dung_count']}/"
        f"{article_stats['p_noi_dung_count']} thẻ |",
        f"| `pChiDan` | {class_counts.get('pChiDan', 0)} | "
        "Chỉ dẫn/quan hệ liên quan giữa điều, mục hoặc chương |",
        "",
        "## 4. Cấu trúc chương và mục",
        "",
        "File không có class riêng `pMuc`. Cả chương và mục đều dùng "
        "`pChuong` theo cặp marker–tiêu đề.",
        "",
        "Quy tắc nhận diện:",
        "",
        "- Text bắt đầu bằng `Chương` → marker chương.",
        "- Text bắt đầu bằng `Mục` → marker mục.",
        "- `pChuong` tiếp theo không có marker → tên của cấu trúc vừa gặp.",
        "- Khi bắt đầu Chương mới, Mục hiện hành được đặt lại về `null`.",
        "- Mỗi điều được gắn bản sao metadata Chương/Mục hiện hành.",
        "",
        "## 5. Ranh giới và nội dung điều",
        "",
        "- Bắt đầu tại `pDieu`.",
        "- Duyệt các sibling kế tiếp.",
        "- Dừng khi gặp `pDieu` hoặc `pChuong` mới.",
        "- `pNoiDung` là marker rỗng; nội dung thật nằm ở các sibling sau nó.",
        "- Giữ cả `content_text_raw` và `content_text` đã chuẩn hóa.",
        "- Nhận diện `content_units`: preamble, clause, point, "
        "clause_continuation và table.",
        "",
        "## 6. Mã pháp điển và ID",
        "",
        "- Giữ nguyên toàn bộ mã dưới trường `codification_code`; "
        "không suy diễn số điều gốc từ phần tử cuối của mã.",
        "- `article_id` lấy từ `<a name>` và luôn lưu dưới dạng chuỗi.",
        "- `source_sha256` và `parser_version` được lưu để tái lập thí nghiệm.",
        "",
        "## 7. Quan hệ chỉ dẫn",
        "",
        f"- Tổng đoạn `pChiDan`: **{citation_stats['paragraph_count']}**",
        f"- Gắn với điều: **{citation_stats['article_paragraph_count']}**",
        f"- Gắn với chương/mục: "
        f"**{citation_stats['structure_paragraph_count']}**",
        f"- Liên kết nội bộ: **{citation_stats['internal_link_count']}**",
        f"- Cùng Đề mục {metadata['topic_code']}: "
        f"**{citation_stats['internal_same_topic']}**",
        f"- Sang đề mục khác: **{citation_stats['internal_other_topic']}**",
        f"- Liên kết ngoài: **{citation_stats['external_link_count']}**",
        f"- Quan hệ cấp điều sau loại trùng: "
        f"**{citation_stats['article_relation_count_after_dedup']}**",
        f"- Quan hệ cấp cấu trúc sau loại trùng: "
        f"**{citation_stats['structure_relation_count_after_dedup']}**",
        "",
        "Quan hệ được làm phẳng thành `RELATED_TO`, "
        "`EXTERNAL_REFERENCE` hoặc `UNKNOWN`. `target_in_corpus` được xác định "
        "bằng tập `article_id` thực tế, không chỉ dựa vào mã đề mục.",
        "",
        "## 8. Bảng và attachment",
        "",
        "- Bảng được lưu thành `headers`, `rows` và `text`.",
        "- Attachment lưu tên, URL, phần mở rộng và `downloaded = false`.",
        "- Không tải, OCR hoặc nhúng nội dung attachment trong giai đoạn này.",
        "",
        "## 9. Kết quả parse thử",
        "",
        "| STT | Mã điều | Loại | Tên điều | Chương | Mục | "
        "Số unit | Chỉ dẫn |",
        "|---:|---|---|---|---|---|---:|---:|",
    ]

    for article in report["sample_articles"]:
        chapter = article.get("chapter") or {}
        section = article.get("section") or {}
        lines.append(
            "| {index} | `{code}` | `{source}` | {title} | {chapter} | "
            "{section} | {units} | {relations} |".format(
                index=article["index"],
                code=article["article_code"],
                source=article["source_type"],
                title=markdown_escape(article["article_title"]),
                chapter=markdown_escape(chapter.get("number")),
                section=markdown_escape(section.get("number")),
                units=len(article["content_units"]),
                relations=len(article["relations"]),
            )
        )

    lines.extend(
        [
            "",
            "## 10. Validation",
            "",
            f"- Trạng thái: **{'ĐẠT' if validation['is_valid'] else 'CHƯA ĐẠT'}**",
            f"- Lỗi: **{len(validation['errors'])}**",
            f"- Cảnh báo: **{len(validation['warnings'])}**",
        ]
    )

    if validation["errors"]:
        lines.extend(["", "### Lỗi"])
        lines.extend(f"- {item}" for item in validation["errors"])

    if validation["warnings"]:
        lines.extend(["", "### Cảnh báo"])
        lines.extend(f"- {item}" for item in validation["warnings"])

    lines.extend(
        [
            "",
            "## 11. Kết luận",
            "",
            "Dữ liệu đủ nhất quán để khóa làm corpus canonical trước khi "
            "thực hiện structural parent–child chunking. Parser bảo toàn cấu "
            "trúc Chương/Mục/Điều, nội dung thô và chuẩn hóa, khoản/điểm, bảng, "
            "attachment, provenance và quan hệ graph.",
        ]
    )

    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Khảo sát và chuẩn hóa HTML một đề mục trong Bộ pháp điển."
        )
    )
    parser.add_argument("html_path", type=Path, help="Đường dẫn file HTML")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Số điều đưa vào báo cáo mẫu",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Trả mã lỗi nếu validation không đạt",
    )
    parser.add_argument(
        "--topic-code",
        type=str,
        default=None,
        help="Ghi đè mã đề mục nếu HTML không nhận diện được",
    )
    parser.add_argument(
        "--topic-name",
        type=str,
        default=None,
        help="Ghi đè tên đề mục nếu HTML không nhận diện được",
    )
    parser.add_argument(
        "--document-id",
        type=str,
        default=None,
        help="Ghi đè document ID; mặc định phap-dien:<topic_code>",
    )
    parser.add_argument(
        "--expected-article-count",
        type=int,
        default=None,
        help="Số điều kỳ vọng để validation nghiêm ngặt",
    )
    parser.add_argument(
        "--expected-source-counts",
        type=str,
        default=None,
        help='JSON số lượng nguồn, ví dụ {"LQ":220,"NĐ":174,"TT":83}',
    )
    parser.add_argument(
        "--expected-table-count",
        type=int,
        default=None,
        help="Số bảng kỳ vọng",
    )
    parser.add_argument(
        "--expected-attachment-count",
        type=int,
        default=None,
        help="Số attachment kỳ vọng",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("data/processed/html_inspection.json"),
        help="Báo cáo khảo sát dạng JSON",
    )
    parser.add_argument(
        "--canonical-output",
        "--articles-output",
        type=Path,
        dest="canonical_output",
        default=Path("data/processed/articles_raw.json"),
        help="Toàn bộ corpus canonical gồm 477 điều",
    )
    parser.add_argument(
        "--validation-output",
        type=Path,
        default=Path("data/processed/parser_report.json"),
        help="Báo cáo validation riêng",
    )
    parser.add_argument(
        "--md-output",
        type=Path,
        default=Path("docs/design/html_structure.md"),
        help="Tài liệu cấu trúc HTML",
    )
    return parser.parse_args()


def parse_expected_source_counts(value: str | None) -> dict[str, int] | None:
    if value is None:
        return None

    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "--expected-source-counts phải là JSON hợp lệ."
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError("--expected-source-counts phải là một JSON object.")

    result: dict[str, int] = {}
    for key, count in payload.items():
        if not isinstance(key, str) or not isinstance(count, int):
            raise ValueError(
                "--expected-source-counts phải có dạng mã nguồn -> số nguyên."
            )
        result[key.upper()] = count
    return result


def main() -> None:
    args = parse_args()

    # Configure base logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if not args.html_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file HTML: {args.html_path}")
    if args.limit < 1:
        raise ValueError("--limit phải lớn hơn hoặc bằng 1")

    # Run core parser
    canonical = parse_legal_document(
        html_path=args.html_path,
        topic_code_override=args.topic_code,
        topic_name_override=args.topic_name,
        document_id_override=args.document_id,
    )

    expected_source_counts = parse_expected_source_counts(
        args.expected_source_counts
    )

    report = build_inspection_report(
        canonical,
        sample_limit=args.limit,
        expected_article_count=args.expected_article_count,
        expected_source_counts=expected_source_counts,
        expected_table_count=args.expected_table_count,
        expected_attachment_count=args.expected_attachment_count,
    )

    validation = report["validation"]

    # Write html_inspection.json
    write_json(args.json_output, report)

    # Write canonical corpus (remove class_counts & citation_statistics to maintain schema compatibility)
    canonical_to_write = deepcopy(canonical)
    canonical_to_write["metadata"].pop("class_counts", None)
    canonical_to_write["metadata"].pop("citation_statistics", None)
    write_json(args.canonical_output, canonical_to_write)

    # Write validation parser_report.json
    write_json(args.validation_output, validation)

    # Write docs/design/html_structure.md
    args.md_output.parent.mkdir(parents=True, exist_ok=True)
    args.md_output.write_text(
        render_markdown(report),
        encoding="utf-8",
    )

    stats = report["article_statistics"]
    citations = report["citation_statistics"]

    print("HTML inspection and canonical parsing completed")
    print(f"File: {args.html_path}")
    print(
        f"Topic: {report['metadata']['topic_code']} - "
        f"{report['metadata']['topic_name']}"
    )
    print(f"Document ID: {report['metadata']['document_id']}")
    print(f"Parser version: {report['metadata']['parser_version']}")
    print(f"Articles: {stats['total']}")
    print(f"Sources: {stats['by_source_type']}")
    print(f"Chapters: {report['structure']['chapter_count']}")
    print(f"Sections: {report['structure']['section_count']}")
    print(f"Tables: {stats['table_count']}")
    print(f"Attachments: {stats['attachment_count']}")
    print(
        "Relations: "
        f"{citations['article_relation_count_after_dedup']} article + "
        f"{citations['structure_relation_count_after_dedup']} structure"
    )
    print(f"Validation: {'PASS' if validation['is_valid'] else 'FAIL'}")
    print(f"Inspection JSON: {args.json_output}")
    print(f"Canonical articles: {args.canonical_output}")
    print(f"Validation report: {args.validation_output}")
    print(f"Markdown: {args.md_output}")

    if args.strict and not validation["is_valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()