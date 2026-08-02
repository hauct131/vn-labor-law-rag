from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_validator():
    path = ROOT / "scripts" / "validate_source_registry.py"
    spec = importlib.util.spec_from_file_location("validate_source_registry", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_registry_when_present() -> None:
    registry_path = ROOT / "data" / "governance" / "source_registry.draft.json"
    if not registry_path.exists():
        return
    validator = load_validator()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    report = validator.validate(ROOT, registry)
    assert report["passed"], report["errors"]
    assert report["document_count"] == 18
    assert report["included_unit_count"] == 513
    assert report["integrity_failures"] == 0
