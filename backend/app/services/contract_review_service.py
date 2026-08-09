"""Grounded, deterministic labor contract review orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.paths import resolve_project_path
from app.schemas.ask import LegalSource, RetrievalMethod
from app.services.legal_citation import build_citation_metadata
from app.services.official_sources import resolved_source_url


@dataclass(frozen=True)
class FindingDraft:
    category: str
    title: str
    severity: str
    contract_excerpt: str
    analysis: str
    recommendation: str
    evidence_status: str
    sources: list[LegalSource]


@dataclass(frozen=True)
class ReviewDraft:
    summary: str
    findings: list[FindingDraft]


@dataclass(frozen=True)
class CategoryRule:
    key: str
    title: str
    query: str
    terms: tuple[str, ...]
    preferred_article_codes: tuple[str, ...]
    recommendation: str


CATEGORIES = (
    CategoryRule(
        "probation",
        "Thử việc",
        "quy định thời gian thử việc tiền lương thử việc kết thúc thử việc",
        ("thử việc", "thời gian thử", "lương thử việc"),
        ("20.2.LQ.25", "20.2.LQ.26", "20.2.LQ.27"),
        "Đối chiếu thời gian, mức lương và cách kết thúc thử việc với nhóm công việc thực tế.",
    ),
    CategoryRule(
        "salary",
        "Tiền lương và phương thức trả lương",
        "quy định tiền lương kỳ hạn trả lương hình thức trả lương chậm trả lương",
        ("tiền lương", "mức lương", "lương cơ bản", "trả lương", "ngày trả lương"),
        ("20.2.LQ.90", "20.2.LQ.94", "20.2.LQ.95", "20.2.LQ.96", "20.2.LQ.97"),
        "Làm rõ mức lương, phụ cấp, kỳ hạn, hình thức trả và các khoản khấu trừ trong hợp đồng.",
    ),
    CategoryRule(
        "working_time",
        "Thời giờ làm việc và nghỉ ngơi",
        "quy định thời giờ làm việc bình thường nghỉ giữa giờ nghỉ hằng tuần làm thêm giờ",
        ("thời giờ làm việc", "giờ làm việc", "nghỉ giữa giờ", "nghỉ hằng tuần", "làm thêm"),
        ("20.2.LQ.105", "20.2.LQ.107", "20.2.LQ.109", "20.2.LQ.111"),
        "Kiểm tra lịch làm việc, thời gian nghỉ và cơ chế làm thêm với thực tế bố trí lao động.",
    ),
    CategoryRule(
        "termination",
        "Chấm dứt hợp đồng và báo trước",
        "quy định đơn phương chấm dứt hợp đồng lao động thời hạn báo trước",
        ("chấm dứt", "báo trước", "đơn phương", "thôi việc"),
        ("20.2.LQ.34", "20.2.LQ.35", "20.2.LQ.36", "20.2.NĐ.3.7"),
        "Tách rõ từng căn cứ chấm dứt, chủ thể thực hiện và thời hạn báo trước tương ứng.",
    ),
)


def _ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _ascii(value))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


class CanonicalEvidenceRetriever:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.docs = [Counter(_tokens(str(chunk["content"]))) for chunk in chunks]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average_length = sum(self.lengths) / max(len(self.lengths), 1)
        self.document_frequency: Counter[str] = Counter()
        for doc in self.docs:
            self.document_frequency.update(doc.keys())

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
        preferred_article_codes: tuple[str, ...] = (),
    ) -> list[LegalSource]:
        query_terms = Counter(_tokens(query))
        scored: list[tuple[float, float, int]] = []
        total = len(self.docs)
        for index, doc in enumerate(self.docs):
            score = 0.0
            length = self.lengths[index] or 1
            for term, query_count in query_terms.items():
                frequency = doc.get(term, 0)
                if not frequency:
                    continue
                df = self.document_frequency[term]
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * length / self.average_length)
                score += query_count * idf * frequency * 2.2 / denominator
            raw_score = score
            article_code = str(self.chunks[index].get("article_code") or "")
            ranking_score = raw_score
            if article_code in preferred_article_codes:
                priority = preferred_article_codes.index(article_code)
                ranking_score += 1000.0 - priority * 10.0
            if ranking_score > 0:
                scored.append((ranking_score, raw_score, index))
        scored.sort(key=lambda item: (-item[0], str(self.chunks[item[2]]["chunk_id"])))
        result: list[LegalSource] = []
        for rank, (_ranking_score, raw_score, index) in enumerate(scored[:top_k], 1):
            payload = self.chunks[index]
            citation = build_citation_metadata(payload)
            result.append(LegalSource(
                source_id=f"S{rank}",
                chunk_id=str(payload["chunk_id"]),
                article_code=str(payload.get("article_code") or payload.get("codification_code") or "") or None,
                article_number=citation.article_number,
                article_title=str(payload.get("article_title") or "") or None,
                document_title=citation.document_title,
                document_number=citation.document_number,
                citation_label=citation.label,
                clause_number=str(payload.get("clause_number") or "") or None,
                point_labels=_string_list(payload.get("point_labels")),
                content=str(payload["content"]),
                score=round(raw_score, 6),
                rank=rank,
                retrieval_origin="contract_canonical_lexical_v1",
                source_type=str(payload.get("source_type") or "") or None,
                source_url=resolved_source_url(payload),
                component_ranks={"contract_lexical": rank},
            ))
        return result


@lru_cache(maxsize=1)
def evidence_retriever() -> CanonicalEvidenceRetriever:
    path = resolve_project_path(settings.legal_chunks_path)
    digest_builder = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest_builder.update(block)
    digest = digest_builder.hexdigest()
    if digest != settings.retrieval_corpus_sha256:
        raise RuntimeError("Canonical corpus SHA-256 mismatch.")
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(chunks) != settings.retrieval_expected_chunks:
        raise RuntimeError("Canonical corpus chunk count mismatch.")
    return CanonicalEvidenceRetriever(chunks)


def _paragraphs(text: str) -> list[str]:
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    return [part for part in paragraphs if len(part) >= 12]


def _excerpt_for(rule: CategoryRule, paragraphs: list[str]) -> str:
    scored: list[tuple[int, int]] = []
    for index, paragraph in enumerate(paragraphs):
        plain = _ascii(paragraph)
        paragraph_tokens = set(_tokens(paragraph))
        score = sum(4 for term in rule.terms if _ascii(term) in plain)
        score += sum(
            1 for token in set(_tokens(rule.title)) if token in paragraph_tokens
        )
        if rule.key == "salary":
            if re.search(r"\d[\d. ,]{3,}\s*(?:dong|vnd)\b", plain):
                score += 8
            if re.search(r"\btra\b.{0,40}\bngay\b|\bngay\b.{0,40}\btra\b", plain):
                score += 4
            if "thu viec" in plain:
                score -= 4
        if re.search(r"\d", paragraph):
            score += 2
        if len(paragraph) > 80:
            score += 1
        if score:
            scored.append((score, index))
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], item[1]))
    index = scored[0][1]
    parts = [paragraphs[index]]
    plain_heading = _ascii(paragraphs[index])
    is_article_heading = (
        bool(re.match(r"^dieu\s+\d+[.:]", plain_heading))
        and len(re.findall(r"\d+", plain_heading)) == 1
        and len(paragraphs[index]) < 120
    )
    if is_article_heading and index + 1 < len(paragraphs):
        parts.append(paragraphs[index + 1])
    return "\n".join(parts)[:1600]


def _plain_text(value: str) -> str:
    """Normalize accents and whitespace while preserving semantic distance."""
    return re.sub(r"\s+", " ", _ascii(value)).strip()


def _unit_numbers(patterns: tuple[str, ...], value: str) -> list[int]:
    plain = _plain_text(value)
    numbers: list[int] = []
    for pattern in patterns:
        numbers.extend(
            int(match.group("value"))
            for match in re.finditer(pattern, plain)
        )
    return numbers


def _number_near_anchors(
    value: str,
    *,
    anchors: tuple[str, ...],
    number_pattern: str,
    max_distance: int = 90,
) -> int | None:
    """Return the unit-bearing number nearest to a relevant legal phrase."""
    plain = _plain_text(value)
    anchor_spans = [
        match.span()
        for anchor in anchors
        for match in re.finditer(re.escape(anchor), plain)
    ]
    if not anchor_spans:
        return None

    candidates: list[tuple[int, int, int]] = []
    for match in re.finditer(number_pattern, plain):
        number_center = (match.start() + match.end()) // 2
        distance = min(
            abs(number_center - ((start + end) // 2))
            for start, end in anchor_spans
        )
        if distance <= max_distance:
            candidates.append((distance, match.start(), int(match.group("value"))))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _analysis(
    rule: CategoryRule,
    excerpt: str,
    sources: list[LegalSource],
    full_text: str,
) -> tuple[str, str, str]:
    if not sources:
        return (
            "insufficient_evidence",
            "Không đủ căn cứ pháp luật trong corpus để đưa ra nhận xét cho nhóm này.",
            "insufficient_evidence",
        )
    markers = " ".join(f"[{source.source_id}]" for source in sources[:2])
    if not excerpt:
        return (
            "attention",
            f"Chưa tìm thấy điều khoản thể hiện rõ nội dung {rule.title.lower()}. "
            f"Các quy định liên quan cần được đối chiếu khi hoàn thiện hợp đồng {markers}.",
            "supported",
        )

    plain_excerpt = _ascii(excerpt)
    if rule.key == "probation":
        value = _number_near_anchors(
            excerpt,
            anchors=("thu viec", "thoi gian thu"),
            number_pattern=r"\b(?P<value>\d{1,3})\s*ngay\b",
        )
        if value is None:
            return (
                "attention",
                f"Hợp đồng có điều khoản thử việc nhưng chưa thể xác định chắc chắn thời lượng. "
                f"Cần đối chiếu nhóm công việc và mức lương thử việc với {markers}.",
                "supported",
            )
        if value > 180:
            return (
                "warning",
                f"Điều khoản ghi thời gian thử việc {value} ngày, vượt cả ngưỡng dài nhất thường được "
                f"quy định cho người quản lý doanh nghiệp. Đây là dấu hiệu cần ưu tiên kiểm tra theo {markers}.",
                "supported",
            )
        if value > 60:
            return (
                "attention",
                f"Điều khoản ghi thời gian thử việc {value} ngày. Mức này dài hơn giới hạn 60 ngày "
                f"thường áp dụng cho công việc cần trình độ chuyên môn, kỹ thuật từ cao đẳng trở lên, "
                f"nhưng có thể có ngoại lệ đối với người quản lý doanh nghiệp. Cần xác định đúng chức danh theo {markers}.",
                "supported",
            )
        return (
            "info",
            f"Điều khoản ghi thời gian thử việc {value} ngày. Cần xác định nhóm công việc cụ thể và "
            f"đối chiếu mức lương thử việc theo {markers}.",
            "supported",
        )

    if rule.key == "working_time":
        daily_values = _unit_numbers(
            (
                r"\b(?P<value>\d{1,2})\s*(?:gio|h)\s*(?:/|moi|mot)?\s*ngay\b",
                r"\b(?:moi|mot)\s+ngay[^.;]{0,40}?\b(?P<value>\d{1,2})\s*(?:gio|h)\b",
            ),
            excerpt,
        )
        weekly_values = _unit_numbers(
            (
                r"\b(?P<value>\d{1,3})\s*(?:gio|h)\s*(?:/|moi|mot)?\s*tuan\b",
                r"\b(?:moi|mot)\s+tuan[^.;]{0,40}?\b(?P<value>\d{1,3})\s*(?:gio|h)\b",
            ),
            excerpt,
        )
        daily = daily_values[0] if daily_values else None
        explicit_weekly = weekly_values[0] if weekly_values else None
        five_days = "thu hai den thu sau" in plain_excerpt
        weekly = (
            explicit_weekly
            if explicit_weekly is not None
            else daily * 5 if daily is not None and five_days else None
        )
        schedule = []
        if daily is not None:
            schedule.append(f"{daily} giờ/ngày")
        if weekly is not None:
            schedule.append(f"{weekly} giờ/tuần")
        if (daily is not None and daily > 10) or (weekly is not None and weekly > 48):
            return (
                "warning",
                f"Điều khoản thể hiện {', '.join(schedule)}. "
                f"Số giờ này có dấu hiệu vượt giới hạn làm việc bình thường và cần ưu tiên kiểm tra theo {markers}.",
                "supported",
            )
        if daily is not None and daily > 8:
            return (
                "attention",
                f"Điều khoản thể hiện {daily} giờ/ngày"
                f"{f', ước tính {weekly} giờ/tuần' if weekly is not None else ''}. "
                f"Nếu bố trí theo tuần thì pháp luật có thể cho phép tối đa 10 giờ/ngày nhưng vẫn không quá "
                f"48 giờ/tuần; hợp đồng nên ghi rõ cách bố trí và thời gian nghỉ theo {markers}.",
                "supported",
            )
        return (
            "info",
            f"Đã tìm thấy lịch làm việc"
            f"{f' ({', '.join(schedule)})' if schedule else ''} và nghỉ ngơi. "
            f"Cần kiểm tra thêm cơ chế làm thêm giờ, "
            f"sự đồng ý của người lao động và giới hạn tổng thời gian theo {markers}.",
            "supported",
        )

    if rule.key == "termination":
        notice = _number_near_anchors(
            excerpt,
            anchors=("bao truoc", "thoi han bao truoc"),
            number_pattern=r"\b(?P<value>\d{1,3})\s*ngay\b",
        )
        contract_months = _number_near_anchors(
            full_text,
            anchors=("hop dong xac dinh thoi han", "xac dinh thoi han", "thoi han hop dong"),
            number_pattern=r"\b(?P<value>\d{1,3})\s*thang\b",
            max_distance=120,
        )
        fixed_term = contract_months is not None and 12 <= contract_months <= 36
        if notice is not None and notice < 30 and fixed_term:
            return (
                "warning",
                f"Hợp đồng xác định thời hạn {contract_months} tháng nhưng điều khoản dùng chung thời hạn báo trước "
                f"{notice} ngày cho mỗi bên. Quyền, căn cứ và thời hạn báo trước của người lao động và "
                f"người sử dụng lao động không hoàn toàn giống nhau; điều khoản này cần được tách và "
                f"đối chiếu ưu tiên theo {markers}.",
                "supported",
            )
        notice_text = (
            f"Điều khoản thể hiện thời hạn báo trước {notice} ngày. "
            if notice is not None
            else ""
        )
        return (
            "attention",
            f"{notice_text}Điều khoản chấm dứt đang quy định chung cho cả hai bên. "
            f"Cần tách rõ chủ thể, căn cứ "
            f"chấm dứt và thời hạn báo trước tương ứng theo {markers}.",
            "supported",
        )

    if rule.key == "salary":
        has_amount = bool(re.search(r"\d[\d. ,]{3,}\s*(?:dong|vnd)", plain_excerpt))
        has_pay_date = bool(
            re.search(
                r"\btra\b.{0,40}\bngay\b|\bngay\b.{0,40}\btra\b",
                plain_excerpt,
            )
        )
        if has_amount and has_pay_date:
            return (
                "info",
                f"Hợp đồng đã thể hiện mức lương và thời điểm trả lương. Cần kiểm tra thêm kỳ trả, "
                f"phụ cấp, khấu trừ và xử lý khi trả chậm theo {markers}.",
                "supported",
            )
        return (
            "attention",
            f"Điều khoản tiền lương chưa thể hiện đầy đủ mức lương hoặc kỳ hạn trả lương. "
            f"Cần bổ sung và đối chiếu theo {markers}.",
            "supported",
        )

    return (
        "attention",
        f"Hợp đồng có nội dung liên quan đến {rule.title.lower()}. Cần đối chiếu điều kiện áp dụng "
        f"với các căn cứ {markers}; hệ thống không thay thế kết luận chuyên môn.",
        "supported",
    )


def review_contract(text: str, method: RetrievalMethod) -> ReviewDraft:
    paragraphs = _paragraphs(text)
    retriever = evidence_retriever()
    findings: list[FindingDraft] = []
    for rule in CATEGORIES:
        excerpt = _excerpt_for(rule, paragraphs)
        query = rule.query + (f" Nội dung hợp đồng: {excerpt[:500]}" if excerpt else "")
        sources = retriever.retrieve(
            query,
            top_k=3,
            preferred_article_codes=rule.preferred_article_codes,
        )
        severity, analysis, evidence_status = _analysis(
            rule, excerpt, sources, text
        )
        findings.append(FindingDraft(
            category=rule.key,
            title=rule.title,
            severity=severity,
            contract_excerpt=excerpt or "Chưa tìm thấy điều khoản liên quan trong nội dung được trích xuất.",
            analysis=analysis,
            recommendation=rule.recommendation,
            evidence_status=evidence_status,
            sources=sources,
        ))
    warnings = sum(item.severity == "warning" for item in findings)
    missing = sum("Chưa tìm thấy" in item.contract_excerpt for item in findings)
    summary = (
        f"Đã rà soát 4 nhóm điều khoản. Có {warnings} nhóm có dấu hiệu cần ưu tiên kiểm tra "
        f"và {missing} nhóm chưa tìm thấy nội dung thể hiện rõ trong hợp đồng."
    )
    return ReviewDraft(summary=summary, findings=findings)
