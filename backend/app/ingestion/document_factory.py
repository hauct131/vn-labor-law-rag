"""Chuyển legal chunk sang LangChain Document."""

import json
from langchain_core.documents import Document


def chunk_to_document(chunk: dict) -> Document:
    """Convert a legal chunk dict into a LangChain Document.
    
    Verifies that the chunk has the required fields 'content' and 'chunk_id'.
    Ensures that the document metadata is JSON serializable and contains
    all fields except 'content' itself.
    """
    if not isinstance(chunk, dict):
        raise TypeError("Chunk must be a dictionary")
    if "content" not in chunk:
        raise ValueError("Chunk is missing required field: content")
    if "chunk_id" not in chunk:
        raise ValueError("Chunk is missing required field: chunk_id")

    # Keep metadata JSON-serializable
    metadata = {}
    for key, val in chunk.items():
        if key == "content":
            continue
        # Test JSON serialization of values; if not serializable, skip or raise error
        try:
            json.dumps(val, ensure_ascii=False)
            metadata[key] = val
        except (TypeError, OverflowError) as e:
            raise ValueError(f"Field '{key}' is not JSON serializable: {e}")

    # Return Document with page_content, metadata, and id
    return Document(
        page_content=chunk["content"],
        metadata=metadata,
        id=chunk["chunk_id"]
    )


def chunks_to_documents(chunks: list[dict]) -> list[Document]:
    """Convert a list of legal chunk dicts to a list of LangChain Documents."""
    if not isinstance(chunks, list):
        raise TypeError("Chunks must be a list of dictionaries")
    return [chunk_to_document(c) for c in chunks]
