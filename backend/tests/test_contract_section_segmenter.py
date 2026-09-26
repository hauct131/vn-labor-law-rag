from app.services.contract_review.segmenter import segment_contract


def test_segment_numbered_contract_sections():
    paragraphs = [
        "HỢP ĐỒNG LAO ĐỘNG",
        "Điều 1. Công việc",
        "Người lao động làm việc tại phòng kỹ thuật.",
        "Điều 2. Thử việc",
        "Thời gian thử việc là 60 ngày.",
        "Mức lương thử việc bằng 90% mức lương chính thức.",
        "Điều 3. Tiền lương",
        "Mức lương là 18.000.000 đồng/tháng.",
    ]

    sections = segment_contract(paragraphs)

    assert len(sections) == 4

    assert sections[0].heading is None
    assert "HỢP ĐỒNG LAO ĐỘNG" in sections[0].text

    assert sections[1].heading == "Điều 1. Công việc"

    assert sections[2].heading == "Điều 2. Thử việc"
    assert "60 ngày" in sections[2].text
    assert "90%" in sections[2].text

    assert sections[3].heading == "Điều 3. Tiền lương"

def test_section_keeps_sibling_rows_until_next_heading():
    paragraphs = [
        "Điều 4. Tiền lương, phụ cấp và phương thức thanh toán",
        "Nội dung | Thỏa thuận",
        "Lương theo công việc | 18.500.000 đồng/tháng",
        "Phụ cấp điện thoại | 500.000 đồng/tháng",
        "Hình thức trả | Chuyển khoản vào ngày 05",
        "Điều 5. Thời giờ làm việc và nghỉ ngơi",
        "08 giờ/ngày, 05 ngày/tuần.",
    ]

    sections = segment_contract(paragraphs)

    assert len(sections) == 2

    salary = sections[0]

    assert "18.500.000 đồng/tháng" in salary.text
    assert "500.000 đồng/tháng" in salary.text
    assert "ngày 05" in salary.text
    assert "08 giờ/ngày" not in salary.text


def test_segmenter_rejects_subarticle_as_contract_heading():
    paragraphs = [
        "Điều 2. Thử việc",
        "Nội dung thử việc.",
        "Điều 2.1. Chi tiết",
        "Nội dung chi tiết.",
    ]

    sections = segment_contract(paragraphs)

    assert len(sections) == 1
    assert "Điều 2.1. Chi tiết" in sections[0].text
