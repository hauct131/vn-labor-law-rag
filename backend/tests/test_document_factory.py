import pytest
import json
from langchain_core.documents import Document
from backend.app.ingestion.document_factory import chunk_to_document, chunks_to_documents


def test_chunk_to_document_returns_document():
    chunk = {
        "chunk_id": "test-id-123",
        "parent_article_id": "art-1",
        "content": "Đây là nội dung.",
        "body_text": "nội dung chính",
        "custom_meta": 42
    }
    doc = chunk_to_document(chunk)
    assert isinstance(doc, Document)
    assert doc.page_content == "Đây là nội dung."
    assert doc.id == "test-id-123"
    assert doc.metadata["chunk_id"] == "test-id-123"
    assert doc.metadata["parent_article_id"] == "art-1"
    assert doc.metadata["body_text"] == "nội dung chính"
    assert doc.metadata["custom_meta"] == 42
    assert "content" not in doc.metadata


def test_chunk_not_mutated():
    chunk = {
        "chunk_id": "test-id-123",
        "content": "Nội dung",
        "body_text": "nội dung chính"
    }
    snapshot = json.dumps(chunk)
    chunk_to_document(chunk)
    assert json.dumps(chunk) == snapshot


def test_metadata_json_serializable():
    chunk = {
        "chunk_id": "test-id-123",
        "content": "Nội dung",
        "non_serializable": object()  # Not JSON serializable
    }
    with pytest.raises(ValueError):
        chunk_to_document(chunk)


def test_chunks_to_documents_preserves_order():
    chunks = [
        {"chunk_id": "id1", "content": "Nội dung 1"},
        {"chunk_id": "id2", "content": "Nội dung 2"}
    ]
    docs = chunks_to_documents(chunks)
    assert len(docs) == 2
    assert docs[0].id == "id1"
    assert docs[0].page_content == "Nội dung 1"
    assert docs[1].id == "id2"
    assert docs[1].page_content == "Nội dung 2"


def test_chunks_to_documents_empty_returns_empty():
    assert chunks_to_documents([]) == []


def test_invalid_chunk_missing_content_rejected():
    chunk = {"chunk_id": "id1"}
    with pytest.raises(ValueError) as excinfo:
        chunk_to_document(chunk)
    assert "missing required field: content" in str(excinfo.value)


def test_invalid_chunk_missing_chunk_id_rejected():
    chunk = {"content": "Nội dung"}
    with pytest.raises(ValueError) as excinfo:
        chunk_to_document(chunk)
    assert "missing required field: chunk_id" in str(excinfo.value)


def test_chunk_to_document_invalid_type_rejected():
    with pytest.raises(TypeError):
        chunk_to_document("not a dict")  # type: ignore


def test_chunks_to_documents_invalid_type_rejected():
    with pytest.raises(TypeError):
        chunks_to_documents("not a list")  # type: ignore
