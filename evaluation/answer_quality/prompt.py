"""Deterministic prompt and evidence rendering for independent annotators."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evaluation.answer_quality.schema import AnswerQualityQuestion


SYSTEM_PROMPT = """Bạn là người gán nhãn dữ liệu đánh giá pháp luật lao động Việt Nam.

Chỉ sử dụng nội dung trong EVIDENCE PACKET. Không dùng trí nhớ, không duyệt web,
không suy đoán quy định không xuất hiện trong bằng chứng.

Nhiệm vụ:
1. Xác định status: answerable, out_of_scope hoặc insufficient_evidence.
2. Nếu answerable, tách đáp án thành các claim nguyên tử bắt buộc. Mỗi claim
   phải có article code, chunk ID và phải được nội dung chunk hỗ trợ trực tiếp.
3. Ghi các phát biểu dễ gây hiểu sai hoặc mở rộng quá mức vào forbidden_claims.
4. Không xem đây là phê duyệt thẩm quyền pháp lý. Đây chỉ là candidate label.
5. Trả đúng JSON schema, không thêm trường và không thêm văn bản ngoài JSON.

Quy ước claim_id: c1, c2, ... cho required claims và f1, f2, ... cho forbidden
claims. Không gộp nhiều nghĩa vụ, điều kiện hoặc ngoại lệ độc lập vào một claim.
"""


class EvidencePacketError(ValueError):
    """Raised when the corpus cannot reproduce a candidate evidence packet."""


def load_chunk_index(
    path: Path,
    expected_sha256: str,
) -> dict[str, dict[str, Any]]:
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise EvidencePacketError(
            f"corpus SHA-256 mismatch: expected {expected_sha256}, got {actual}"
        )
    chunks: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidencePacketError(
                f"invalid chunk JSON at line {line_number}"
            ) from exc
        chunk_id = str(item.get("chunk_id", "")).strip()
        content = str(item.get("content", "")).strip()
        if not chunk_id or not content or chunk_id in chunks:
            raise EvidencePacketError(
                f"invalid or duplicate chunk at line {line_number}"
            )
        chunks[chunk_id] = item
    return chunks


def render_evidence_packet(
    question: AnswerQualityQuestion,
    chunk_index: dict[str, dict[str, Any]],
) -> str:
    blocks: list[str] = []
    for position, chunk_id in enumerate(question.evidence_chunk_ids, start=1):
        chunk = chunk_index.get(chunk_id)
        if chunk is None:
            raise EvidencePacketError(
                f"{question.id}: missing evidence chunk {chunk_id}"
            )
        article_code = str(chunk.get("article_code", "")).strip()
        if article_code not in question.expected_article_codes:
            raise EvidencePacketError(
                f"{question.id}: unexpected article {article_code} in {chunk_id}"
            )
        blocks.append(
            "\n".join([
                f"--- EVIDENCE {position} ---",
                f"CHUNK_ID: {chunk_id}",
                f"ARTICLE_CODE: {article_code}",
                f"DOCUMENT_NUMBER: {chunk.get('document_number', '')}",
                f"ARTICLE_TITLE: {chunk.get('article_title', '')}",
                "CONTENT:",
                str(chunk["content"]).strip(),
            ])
        )
    return "\n\n".join(blocks)


def build_messages(
    question: AnswerQualityQuestion,
    chunk_index: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    evidence = render_evidence_packet(question, chunk_index)
    user_prompt = "\n".join([
        f"CASE_ID: {question.id}",
        f"QUESTION: {question.question}",
        f"DOCUMENT: {question.document_number} — {question.document_title}",
        "ALLOWED_ARTICLE_CODES: "
        + json.dumps(question.expected_article_codes, ensure_ascii=False),
        "ALLOWED_CHUNK_IDS: "
        + json.dumps(question.evidence_chunk_ids, ensure_ascii=False),
        "",
        "EVIDENCE PACKET:",
        evidence,
    ])
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def prompt_sha256(messages: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
