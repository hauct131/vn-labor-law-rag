"""Tests for the read-only retrieval-core smoke evaluation command."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_retrieval_core import build_parser, run


class RetrievalCoreEvaluationTests(unittest.TestCase):
    def test_dry_run_validates_inputs_without_creating_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chunks_path = root / "chunks.jsonl"
            questions_path = root / "questions.json"
            output_path = root / "report.json"
            model_dir = root / "vncorenlp"
            (model_dir / "models").mkdir(parents=True)
            (model_dir / "VnCoreNLP-1.2.jar").write_bytes(b"test")

            raw_chunks = json.dumps({
                "chunk_id": "one",
                "content": "Nội dung Điều 1",
                "article_code": "20.2.LQ.1",
            }, ensure_ascii=False) + "\n"
            chunks_path.write_text(raw_chunks, encoding="utf-8")
            questions_path.write_text(json.dumps([
                {
                    "id": "q1",
                    "category": "exact",
                    "question": "Điều nào?",
                    "expected_article_codes": ["20.2.LQ.1"],
                }
            ], ensure_ascii=False), encoding="utf-8")
            expected_sha = hashlib.sha256(
                raw_chunks.encode("utf-8")
            ).hexdigest()

            args = build_parser().parse_args([
                "--chunks", str(chunks_path),
                "--questions", str(questions_path),
                "--output", str(output_path),
                "--vncorenlp-model-dir", str(model_dir),
                "--expected-chunks", "1",
                "--expected-sha256", expected_sha,
                "--dry-run",
            ])
            report = run(args)

            self.assertEqual(report["status"], "dry_run_pass")
            self.assertEqual(report["config"]["chunk_count"], 1)
            self.assertFalse(report["config"]["writes_qdrant"])
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
