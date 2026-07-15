"""Custom LangChain BaseRetriever cho Graph-enhanced RAG."""

from langchain_core.retrievers import BaseRetriever


class LegalGraphRetriever(BaseRetriever):
    def _get_relevant_documents(self, query: str, *, run_manager=None):
        raise NotImplementedError("Sẽ triển khai ở Ngày 13.")
