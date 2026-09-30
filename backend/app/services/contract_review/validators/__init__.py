from __future__ import annotations

from app.schemas.ask import LegalSource
from ..models import CategoryRule
from .probation import analyze_probation
from .salary import analyze_salary
from .termination import analyze_termination
from .working_time import analyze_working_time


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
    markers = " ".join(f"[{source.source_id}]" for source in sources)
    if not excerpt:
        return (
            "attention",
            f"Chưa tìm thấy điều khoản thể hiện rõ nội dung {rule.title.lower()}. "
            f"Các quy định liên quan cần được đối chiếu khi hoàn thiện hợp đồng {markers}.",
            "insufficient_evidence",
        )

    if rule.key == "probation":
        return analyze_probation(rule, excerpt, sources, full_text, markers)

    if rule.key == "working_time":
        return analyze_working_time(rule, excerpt, sources, full_text, markers)

    if rule.key == "termination":
        return analyze_termination(rule, excerpt, sources, full_text, markers)

    if rule.key == "salary":
        return analyze_salary(rule, excerpt, sources, full_text, markers)

    return (
        "attention",
        f"Hợp đồng có nội dung liên quan đến {rule.title.lower()}. Cần đối chiếu điều kiện áp dụng "
        f"với các căn cứ {markers}; hệ thống không thay thế kết luận chuyên môn.",
        "supported",
    )


__all__ = [
    "_analysis",
    "analyze_probation",
    "analyze_salary",
    "analyze_termination",
    "analyze_working_time",
]
