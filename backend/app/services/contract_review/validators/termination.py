from __future__ import annotations

from app.schemas.ask import LegalSource
from ..models import CategoryRule
from ..text_utils import _ascii, _number_near_anchors


def analyze_termination(
    rule: CategoryRule,
    excerpt: str,
    sources: list[LegalSource],
    full_text: str,
    markers: str,
) -> tuple[str, str, str]:
    plain_excerpt = _ascii(excerpt)
    notice = _number_near_anchors(
        excerpt,
        anchors=("bao truoc", "thoi han bao truoc", "thong bao"),
        number_pattern=r"\b(?P<value>\d{1,3})\s*ngay\b",
    )
    contract_months = _number_near_anchors(
        full_text,
        anchors=("hop dong xac dinh thoi han", "xac dinh thoi han", "thoi han hop dong"),
        number_pattern=r"\b(?P<value>\d{1,3})\s*thang\b",
        max_distance=120,
    )
    fixed_term = contract_months is not None and 0 < contract_months <= 36
    employer_unilateral = any(
        marker in plain_excerpt
        for marker in (
            "ben a co quyen cham dut",
            "cong ty co quyen cham dut",
            "nguoi su dung lao dong co quyen cham dut",
        )
    )
    broad_employer_reason = any(
        marker in plain_excerpt
        for marker in (
            "xet thay",
            "khong con phu hop",
            "dinh huong kinh doanh",
            "co cau khach hang",
            "nhu cau van hanh",
        )
    )
    incomplete_clause = any(
        marker in plain_excerpt
        for marker in (
            "bo sung noi dung ve cac truong hop cham dut",
            "noi dung cham dut se duoc bo sung",
            "thoi han bao truoc se duoc bo sung",
            "thong nhat dieu khoan cham dut sau",
        )
    )
    if incomplete_clause:
        return (
            "attention",
            f"Hợp đồng mới ghi nhận nội dung chấm dứt và thời hạn báo trước sẽ được bổ sung, "
            f"chưa thể hiện căn cứ, chủ thể và thời hạn cụ thể. Cần hoàn thiện điều khoản theo {markers}.",
            "supported",
        )
    if employer_unilateral and (
        broad_employer_reason or (notice is not None and notice < 30)
    ):
        notice_text = (
            f" và thời hạn báo trước {notice} ngày"
            if notice is not None
            else ""
        )
        return (
            "warning",
            f"Điều khoản trao cho Bên A quyền chấm dứt hợp đồng theo căn cứ rộng"
            f"{notice_text}. Nội dung này có dấu hiệu chưa phân định đúng căn cứ và thời hạn "
            f"đơn phương chấm dứt của người sử dụng lao động, cần ưu tiên kiểm tra theo {markers}.",
            "supported",
        )
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
    shared_clause = any(
        marker in plain_excerpt
        for marker in ("hai ben", "moi ben", "cac ben")
    )
    clause_description = (
        "Điều khoản chấm dứt đang quy định chung cho cả hai bên. "
        if shared_clause
        else "Điều khoản chấm dứt chưa thể hiện đầy đủ căn cứ và thời hạn áp dụng cho từng chủ thể. "
    )
    return (
        "attention",
        f"{notice_text}{clause_description}Cần tách rõ chủ thể, căn cứ "
        f"chấm dứt và thời hạn báo trước tương ứng theo {markers}.",
        "supported",
    )
