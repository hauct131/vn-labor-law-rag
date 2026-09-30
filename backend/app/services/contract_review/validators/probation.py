from __future__ import annotations

from app.schemas.ask import LegalSource
from ..models import CategoryRule
from ..text_utils import _ascii, _is_negated_probation_mention, _number_near_anchors, _plain_text


def analyze_probation(
    rule: CategoryRule,
    excerpt: str,
    sources: list[LegalSource],
    full_text: str,
    markers: str,
) -> tuple[str, str, str]:
    plain_excerpt = _ascii(excerpt)
    if _is_negated_probation_mention(plain_excerpt):
        return (
            "info",
            f"Hợp đồng xác nhận không áp dụng thử việc. Không phát hiện thời hạn "
            f"hoặc mức lương thử việc cần đối chiếu theo {markers}.",
            "supported",
        )

    plain_full_text = _plain_text(full_text)
    enterprise_manager = any(
        marker in plain_full_text
        for marker in (
            "nguoi quan ly doanh nghiep",
            "tong giam doc",
            "giam doc doanh nghiep",
        )
    )
    value = _number_near_anchors(
        excerpt,
        anchors=("thu viec", "thoi gian thu"),
        number_pattern=r"\b(?P<value>\d{1,3})\s*ngay\b",
    )
    months = _number_near_anchors(
        excerpt,
        anchors=("thu viec", "thoi gian thu"),
        number_pattern=r"\b(?P<value>\d{1,2})\s*thang\b",
    )
    salary_percent = _number_near_anchors(
        excerpt,
        anchors=("luong thu viec", "luong trong thoi gian thu viec", "muc luong"),
        number_pattern=r"\b(?P<value>\d{1,3})\s*(?:%|phan\s+tram\b)",
        max_distance=120,
    )
    duration_warning = (
        (value is not None and value > 60)
        or (months is not None and months >= 3)
    ) and not enterprise_manager
    if salary_percent is not None and salary_percent < 85:
        duration_text = (
            f"thời gian thử việc {value} ngày và "
            if value is not None
            else f"thời gian thử việc {months} tháng và "
            if months is not None
            else ""
        )
        duration_note = (
            " Thời lượng thử việc cũng có dấu hiệu vượt giới hạn thường áp dụng "
            "cho vị trí không phải người quản lý doanh nghiệp."
            if duration_warning
            else ""
        )
        return (
            "warning",
            f"Điều khoản ghi {duration_text}mức lương thử việc bằng {salary_percent}% "
            f"lương theo công việc, thấp hơn mức 85% cần đối chiếu."
            f"{duration_note} Cần ưu tiên kiểm tra theo {markers}.",
            "supported",
        )
    if value is None and months is None:
        return (
            "attention",
            f"Hợp đồng có điều khoản thử việc nhưng chưa thể xác định chắc chắn thời lượng. "
            f"Cần đối chiếu nhóm công việc và mức lương thử việc với {markers}.",
            "supported",
        )
    if value is None and months is not None:
        requires_degree = any(
            marker in plain_full_text
            for marker in (
                "tot nghiep dai hoc",
                "trinh do dai hoc",
                "trinh do cao dang",
                "tu cao dang tro len",
            )
        )
        if months > 6:
            return (
                "warning",
                f"Điều khoản ghi thời gian thử việc {months} tháng, dài hơn ngưỡng tối đa "
                f"thường áp dụng kể cả với người quản lý doanh nghiệp. Cần ưu tiên kiểm tra theo {markers}.",
                "supported",
            )
        if months >= 3 and requires_degree and not enterprise_manager:
            return (
                "warning",
                f"Điều khoản ghi thời gian thử việc {months} tháng cho vị trí yêu cầu trình độ "
                f"cao đẳng hoặc đại học. Thời lượng này có dấu hiệu vượt giới hạn 60 ngày và "
                f"cần ưu tiên kiểm tra theo {markers}.",
                "supported",
            )
        if months >= 3 and not enterprise_manager:
            return (
                "warning",
                f"Điều khoản ghi thời gian thử việc {months} tháng cho vị trí không được xác định "
                f"là người quản lý doanh nghiệp. Thời lượng này có dấu hiệu vượt giới hạn thường áp dụng "
                f"và cần ưu tiên kiểm tra theo {markers}.",
                "supported",
            )
        return (
            "info",
            f"Điều khoản ghi thời gian thử việc {months} tháng. Cần xác định nhóm công việc cụ thể "
            f"và đối chiếu mức lương thử việc theo {markers}.",
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
        if not enterprise_manager:
            return (
                "warning",
                f"Điều khoản ghi thời gian thử việc {value} ngày cho vị trí không được xác định "
                f"là người quản lý doanh nghiệp. Thời lượng này có dấu hiệu vượt giới hạn 60 ngày "
                f"và cần ưu tiên kiểm tra theo {markers}.",
                "supported",
            )
        return (
            "info",
            f"Điều khoản ghi thời gian thử việc {value} ngày. Mức này dài hơn giới hạn 60 ngày "
            f"thường áp dụng cho công việc chuyên môn nhưng hợp đồng xác định vị trí quản lý doanh nghiệp; "
            f"cần kiểm tra điều kiện áp dụng theo {markers}.",
            "supported",
        )
    return (
        "info",
        f"Điều khoản ghi thời gian thử việc {value} ngày. Cần xác định nhóm công việc cụ thể và "
        f"đối chiếu mức lương thử việc theo {markers}.",
        "supported",
    )
