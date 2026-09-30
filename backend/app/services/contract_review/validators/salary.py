from __future__ import annotations

import re

from app.schemas.ask import LegalSource
from ..models import CategoryRule
from ..text_utils import _ascii, _has_pay_date


def analyze_salary(
    rule: CategoryRule,
    excerpt: str,
    sources: list[LegalSource],
    full_text: str,
    markers: str,
) -> tuple[str, str, str]:
    plain_excerpt = _ascii(excerpt)
    has_amount = bool(re.search(r"\d[\d. ,]{3,}\s*(?:dong|vnd)", plain_excerpt))
    has_pay_date = _has_pay_date(excerpt)
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
