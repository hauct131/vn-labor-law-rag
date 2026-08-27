#!/usr/bin/env python3
"""E2E smoke test for real RAG query: dense+sparse retrieval and real OpenRouter generation."""

import json
import sys
import urllib.request
from pathlib import Path


def run_rag_test(base_url: str, output_path: str):
    url = f"{base_url.rstrip('/')}/ask"
    question = "Người lao động có quyền đơn phương chấm dứt hợp đồng lao động không và cần báo trước bao nhiêu ngày?"
    payload = {
        "question": question,
        "method": "hybrid"
    }
    
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    
    print(f"Executing RAG runtime request to {url}...")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"FAIL: RAG request error: {e}")
        sys.exit(1)
        
    answer = data.get("answer", "")
    sources = data.get("sources", [])
    method = data.get("retrieval_method", "")
    
    print(f"Retrieval method: {method}")
    print(f"Sources count: {len(sources)}")
    print(f"Answer snippet: {answer[:150]}...")
    
    assert len(answer) > 20, "Answer content too short"
    assert len(sources) > 0, "No sources returned"
    
    report = {
        "status": "PASS",
        "question": question,
        "retrieval_method": method,
        "sources_count": len(sources),
        "answer_length": len(answer),
        "answer_sample": answer[:200],
        "first_source": sources[0] if sources else None
    }
    
    Path(output_path).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"PASS: Real RAG Runtime E2E verification success! Saved to {output_path}")

if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/api"
    out_file = sys.argv[2] if len(sys.argv) > 2 else "rag-runtime-e2e.json"
    run_rag_test(target_url, out_file)
