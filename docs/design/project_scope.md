Phạm vi project: Hệ thống tập trung hỗ trợ hỏi đáp và tra cứu các quy định thuộc Bộ luật Lao động Việt Nam, đồng thời sử dụng một số nghị định và thông tư hướng dẫn trực tiếp để bổ sung căn cứ trả lời.

Tên đề tài: Xây dựng hệ thống hỏi đáp pháp luật lao động Việt Nam sử dụng Retrieval-Augmented Generation: So sánh Sparse RAG, Hybrid RAG và Graph-enhanced RAG.
Kiến trúc tổng thể:

user -> UI (React) -> API Gateway (Fastapi) -> Langchain orchestration -> [3 pipeline RAG] -> LLM -> UI
Trong đó [3 pipeline RAG] bao gồm:
1. Sparse RAG
2. Hybrid RAG
3. Graph-enhanced RAG

Chức năng mvp: 
Nguồn chính:
- Bộ luật Lao động số 45/2019/QH14.

Nguồn bổ sung:
- Các nghị định và thông tư hướng dẫn trực tiếp có trong Đề mục Lao động của Bộ pháp điển.

Không bao gồm:
- Tư vấn pháp lý mang tính kết luận.
- Dự đoán kết quả tranh chấp.
- Tự động cập nhật văn bản mới.
- Toàn bộ pháp luật Việt Nam.