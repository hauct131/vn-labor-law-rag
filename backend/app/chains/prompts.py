"""Prompt dùng chung cho ba pipeline."""

from langchain_core.prompts import ChatPromptTemplate

LEGAL_QA_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
Bạn là hệ thống hỗ trợ tra cứu pháp luật lao động Việt Nam.
Chỉ trả lời dựa trên ngữ cảnh được cung cấp.
Không tự tạo số điều, khoản, điểm hoặc nội dung pháp luật.
Nếu chưa đủ căn cứ, phải nói rõ chưa đủ dữ liệu.
""".strip(),
        ),
        (
            "human",
            """
Câu hỏi:
{question}

Ngữ cảnh:
{context}
""".strip(),
        ),
    ]
)
