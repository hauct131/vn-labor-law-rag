"""Safe text extraction for uploaded PDF and DOCX contracts."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_CHARACTERS = 200_000
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 250
SUPPORTED_SUFFIXES = {".pdf", ".docx"}


class ContractFileError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedContract:
    filename: str
    mime_type: str
    text: str


def safe_filename(value: str | None) -> str:
    raw = Path(value or "contract").name
    cleaned = re.sub(r"[^0-9A-Za-zÀ-ỹ._() -]+", "_", raw).strip(" .")
    return cleaned[:255] or "contract"


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) > MAX_EXTRACTED_CHARACTERS:
        raise ContractFileError(
            "Hợp đồng có quá nhiều nội dung để xử lý trong phiên bản hiện tại."
        )
    if len(text) < 80:
        raise ContractFileError("Không trích xuất được đủ nội dung văn bản từ hợp đồng.")
    return text


def _extract_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            expanded_size = sum(item.file_size for item in archive.infolist())
            if expanded_size > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ContractFileError(
                    "Tệp DOCX có kích thước giải nén vượt giới hạn an toàn."
                )
        document = Document(io.BytesIO(data))
    except ContractFileError:
        raise
    except Exception as exc:
        raise ContractFileError("Tệp DOCX bị hỏng hoặc không đúng định dạng.") from exc

    blocks: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, document).text.strip()
            if text:
                blocks.append(text)
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            for row in table.rows:
                row_text = " | ".join(
                    cell.text.strip() for cell in row.cells if cell.text.strip()
                )
                if row_text:
                    blocks.append(row_text)
    return _normalize_text("\n".join(blocks))


def _extract_pdf(data: bytes) -> str:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ContractFileError("Tệp PDF bị hỏng hoặc không đúng định dạng.") from exc
    try:
        if document.page_count > MAX_PDF_PAGES:
            raise ContractFileError(
                f"PDF vượt quá giới hạn {MAX_PDF_PAGES} trang của phiên bản hiện tại."
            )
        text = "\n".join(page.get_text("text") for page in document)
    finally:
        document.close()
    if not text.strip():
        raise ContractFileError(
            "PDF không có lớp văn bản. Phiên bản hiện tại chưa hỗ trợ OCR cho PDF scan."
        )
    return _normalize_text(text)


def extract_contract(filename: str | None, content_type: str | None, data: bytes) -> ExtractedContract:
    if not data:
        raise ContractFileError("Tệp tải lên đang trống.")
    if len(data) > MAX_FILE_BYTES:
        raise ContractFileError("Tệp vượt quá giới hạn 10 MB.")

    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ContractFileError("Chỉ hỗ trợ tệp PDF hoặc DOCX.")

    if suffix == ".docx":
        text = _extract_docx(data)
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        text = _extract_pdf(data)
        mime = "application/pdf"

    if content_type and content_type not in {
        mime,
        "application/octet-stream",
        "application/zip",
    }:
        raise ContractFileError("MIME type của tệp không phù hợp với phần mở rộng.")
    return ExtractedContract(filename=name, mime_type=mime, text=text)
