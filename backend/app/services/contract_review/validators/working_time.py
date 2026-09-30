from __future__ import annotations

from app.schemas.ask import LegalSource
from ..models import CategoryRule
from ..text_utils import _ascii, _unit_numbers


def analyze_working_time(
    rule: CategoryRule,
    excerpt: str,
    sources: list[LegalSource],
    full_text: str,
    markers: str,
) -> tuple[str, str, str]:
    plain_excerpt = _ascii(excerpt)
    daily_values = _unit_numbers(
        (
            r"\b(?P<value>\d{1,2})\s*(?:gio|h)\s*(?:/|moi|mot)?\s*ngay\b",
            r"\b(?:moi|mot)\s+ngay[^.;]{0,40}?\b(?P<value>\d{1,2})\s*(?:gio|h)\b",
            r"\b(?:lam\s+viec\s+theo\s+)?ca\s+(?P<value>\d{1,2})\s*(?:gio|h)\b",
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
    workday_values = _unit_numbers(
        (
            r"\b(?P<value>\d{1,2})\s*ngay\s*(?:/|moi|mot|trong)?\s*tuan\b",
            r"\b(?P<value>\d{1,2})\s*ngay\s+trong\s+tuan\b",
        ),
        excerpt,
    )
    daily = daily_values[0] if daily_values else None
    explicit_weekly = weekly_values[0] if weekly_values else None
    workdays = workday_values[0] if workday_values else None
    five_days = "thu hai den thu sau" in plain_excerpt
    weekly = (
        explicit_weekly
        if explicit_weekly is not None
        else daily * workdays
        if daily is not None and workdays is not None
        else daily * 5
        if daily is not None and five_days
        else None
    )
    schedule = []
    if daily is not None:
        schedule.append(f"{daily} giờ/ngày")
    if weekly is not None:
        schedule.append(f"{weekly} giờ/tuần")
    has_rest_clause = any(
        marker in plain_excerpt
        for marker in (
            "nghi giua gio",
            "nghi trong gio lam viec",
            "thoi gian nghi",
            "nghi trua",
            "nghi hang tuan",
        )
    )
    incomplete_schedule = daily is None and weekly is None and any(
        marker in plain_excerpt
        for marker in (
            "lich lam viec cu the se duoc bo tri",
            "thoi gio lam viec se duoc bo sung",
            "lich lam viec se duoc bo sung",
            "thong nhat lich lam viec sau",
            "chua xac dinh lich lam viec",
        )
    )
    if incomplete_schedule:
        return (
            "attention",
            f"Hợp đồng mới dẫn chiếu việc bố trí lịch làm việc trong tương lai, chưa thể hiện "
            f"số giờ hoặc lịch làm việc cụ thể. Cần hoàn thiện thời giờ làm việc, thời gian nghỉ "
            f"và cơ chế làm thêm để đối chiếu theo {markers}.",
            "supported",
        )
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
        (
            f"Đã tìm thấy lịch làm việc ({', '.join(schedule)}). "
            if schedule
            else "Đã tìm thấy điều khoản thời giờ làm việc. "
        )
        + (
            "Đoạn được nhận diện cũng có nội dung về thời gian nghỉ. "
            if has_rest_clause
            else "Chưa thấy nội dung cụ thể về thời gian nghỉ trong đoạn được nhận diện. "
        )
        + f"Cần kiểm tra thêm cơ chế làm thêm giờ, "
        f"sự đồng ý của người lao động và giới hạn tổng thời gian theo {markers}.",
        "supported",
    )
