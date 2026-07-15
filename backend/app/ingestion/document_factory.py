"""Chuyển legal chunk sang LangChain Document."""

from langchain_core.documents import Document


def chunk_to_document(chunk: dict) -> Document:
    return Document(
        page_content=chunk["content"],
        metadata={
            key: value
            for key, value in chunk.items()
            if key != "content"
        },
    )
