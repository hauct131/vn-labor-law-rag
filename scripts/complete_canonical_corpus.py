#!/usr/bin/env python3
"""Build a governed canonical labour-law candidate from the audited 18-source release.

The script is deliberately fail-closed.  It may produce a technically valid
candidate while keeping ``production_publishable`` false when exact E5 audit,
source completeness, or authority approval is still missing.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.ingestion.legal_chunker import (  # noqa: E402
    ChunkingConfig,
    TiktokenTokenCounter,
    build_legal_chunks,
    validate_chunks,
)


DOC_NUMBER_RE = re.compile(
    r"(?:số\s*)?([0-9]+(?:\.[0-9]+)?/[0-9]{4}/[A-ZĐ-]+)",
    re.IGNORECASE,
)
SOURCE_ARTICLE_RE = re.compile(r"\bĐiều\s+([0-9]+)\b", re.IGNORECASE)
DOCUMENT_ALIASES = {"45/2019/QH14": "18/VBHN-VPQH"}
EXPECTED_E5_MODEL = "intfloat/multilingual-e5-large"
EXPECTED_E5_MODEL_MAX_TOKENS = 512
EXPECTED_E5_PREFIX = "passage: "


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def verify_exact_e5_audit(
    root: Path,
    audit_path: Path,
    chunks_path: Path,
    expected_chunk_count: int,
) -> tuple[bool, dict[str, Any]]:
    """Bind an exact E5 audit to the canonical bytes, failing closed.

    A missing, malformed, stale, or incompatible summary is represented as a
    failed external gate rather than a successful technical build.  The
    returned record is safe to persist in the release; it contains hashes and
    validation facts, not model or service credentials.
    """
    binding: dict[str, Any] = {
        "schema_version": "canonical-e5-audit-binding-v1",
        "audit_summary_path": relative(audit_path, root),
        "canonical_chunks_path": relative(chunks_path, root),
        "canonical_chunks_sha256": sha256(chunks_path),
        "canonical_chunk_count": expected_chunk_count,
        "expected_model_name": EXPECTED_E5_MODEL,
        "expected_model_max_tokens": EXPECTED_E5_MODEL_MAX_TOKENS,
        "expected_prefix_string": EXPECTED_E5_PREFIX,
        "verified": False,
        "errors": [],
    }
    errors: list[str] = binding["errors"]
    if not audit_path.is_file():
        errors.append("exact E5 audit summary is missing")
        return False, binding

    binding["audit_summary_sha256"] = sha256(audit_path)
    try:
        summary = load_json(audit_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"exact E5 audit summary is unreadable: {type(exc).__name__}")
        return False, binding
    if not isinstance(summary, dict):
        errors.append("exact E5 audit summary must be an object")
        return False, binding

    input_path_value = summary.get("input_path")
    if isinstance(input_path_value, str) and input_path_value:
        declared_input = Path(input_path_value)
        if not declared_input.is_absolute():
            declared_input = root / declared_input
        if declared_input.resolve() != chunks_path.resolve():
            errors.append("audit input_path does not resolve to canonical_chunks.jsonl")
    else:
        errors.append("audit input_path is missing")

    expected_values = {
        "status": "completed",
        "input_sha256": binding["canonical_chunks_sha256"],
        "chunk_count": expected_chunk_count,
        "model_name": EXPECTED_E5_MODEL,
        "actual_model_max_tokens": EXPECTED_E5_MODEL_MAX_TOKENS,
        "prefix_string": EXPECTED_E5_PREFIX,
        "exact_measurement": True,
    }
    for field, expected in expected_values.items():
        if summary.get(field) != expected:
            errors.append(
                f"audit {field} mismatch: expected {expected!r}, got {summary.get(field)!r}"
            )

    risk_counts = summary.get("risk_counts")
    if not isinstance(risk_counts, dict):
        errors.append("audit risk_counts is missing")
    else:
        if risk_counts.get("strictly_over_model_limit_count") != 0:
            errors.append("audit contains chunks strictly over the model limit")
        if risk_counts.get("at_model_limit_count") != 0:
            errors.append("audit contains chunks at the model limit")

    validation = summary.get("validation")
    if not isinstance(validation, dict):
        errors.append("audit validation is missing")
    else:
        if validation.get("is_valid") is not True:
            errors.append("audit validation is not valid")
        if validation.get("error_count") != 0:
            errors.append("audit validation contains errors")

    binding["measured_max_tokens"] = (
        summary.get("token_statistics", {}).get("max")
        if isinstance(summary.get("token_statistics"), dict)
        else None
    )
    binding["near_or_above_count"] = (
        risk_counts.get("near_or_above_count") if isinstance(risk_counts, dict) else None
    )
    binding["fastembed_version"] = summary.get("fastembed_version")
    binding["verified"] = not errors
    return binding["verified"], binding


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            require(isinstance(row, dict), f"{path}:{line_number} is not an object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def group_articles(articles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        grouped[article["document_number"]].append(article)
    return grouped


def build_source_registry(
    root: Path,
    source_release: Path,
    articles: list[dict[str, Any]],
    decisions: dict[str, Any],
) -> dict[str, Any]:
    inventory = load_json(source_release / "source_inventory.json")
    release_manifest = load_json(source_release / "manifest.json")
    run_manifest = load_json(root / "data/raw/vbpl/run_manifest.json")
    grouped = group_articles(articles)
    audit_by_number = {
        row["document_number"]: row
        for row in inventory["base_corpus"]["snapshot_audit"]["records"]
    }
    documents: list[dict[str, Any]] = []

    for run_row in run_manifest["results"]:
        number = run_row["document_number"]
        snapshot_dir = root / run_row["snapshot_dir"]
        snapshot_manifest_path = snapshot_dir / "manifest.json"
        full_text_path = snapshot_dir / "full_text.txt"
        snapshot = load_json(snapshot_manifest_path)
        declared_hash = snapshot["content_hashes"]["full_text_text_sha256"]
        actual_hash = sha256(full_text_path)
        require(actual_hash == declared_hash, f"{number}: snapshot content hash mismatch")
        require(audit_by_number[number]["technical_verification_passed"] is True, f"{number}: snapshot audit failed")
        first = grouped[number][0]
        documents.append(
            {
                "document_number": number,
                "title": first.get("document_title") or snapshot["document"].get("title"),
                "source_kind": "vbpl_snapshot",
                "provider": snapshot["source"].get("provider"),
                "source_item_id": str(snapshot["document"].get("item_id")),
                "acquisition": {
                    "detail_url": snapshot["source"].get("detail_url"),
                    "gateway_url": snapshot["source"].get("gateway_url"),
                    "retrieved_at": snapshot.get("retrieved_at"),
                    "snapshot_path": relative(snapshot_dir, root),
                    "primary_file_path": relative(full_text_path, root),
                },
                "integrity": {
                    "content_sha256": declared_hash,
                    "content_hash_verified": True,
                    "snapshot_manifest_sha256": sha256(snapshot_manifest_path),
                },
                "observed_legal_metadata": {
                    "issued_at": snapshot["document"].get("issued_at"),
                    "effective_from": snapshot["document"].get("effective_from"),
                    "effective_to": snapshot["document"].get("effective_to"),
                    "portal_status": snapshot["document"].get("legal_status"),
                },
                "corpus_scope": {"included_container_count": len(grouped[number])},
                "technical_legal_review": decisions["documents"][number],
                "authority_review_passed": False,
            }
        )

    raw_by_number = {row["document_number"]: row for row in inventory["raw_snapshots"]}
    for item in inventory["official_docx_sources"]:
        number = item["document_number"]
        source_path = source_release / item["release_path"]
        require(source_path.is_file(), f"{number}: missing release DOCX")
        require(sha256(source_path) == item["sha256"], f"{number}: DOCX hash mismatch")
        documents.append(
            {
                "document_number": number,
                "title": grouped[number][0].get("document_title"),
                "source_kind": "official_docx_supplied_to_pipeline",
                "provider": item.get("provider"),
                "source_item_id": item.get("source_item_id"),
                "official_identity_url": item.get("official_page_url"),
                "acquisition": {
                    "source_file_path": relative(source_path, root),
                    "direct_download_verified": False,
                    "raw_snapshot_path": raw_by_number.get(number, {}).get("snapshot_dir"),
                },
                "integrity": {
                    "source_file_sha256": item["sha256"],
                    "source_file_hash_verified": True,
                    "digital_signature_parts_present": item.get("package", {}).get("digital_signature_parts_present"),
                    "cryptographic_signature_validation": item.get("package", {}).get("cryptographic_signature_validation"),
                },
                "observed_legal_metadata": {
                    "issued_at": grouped[number][0].get("issued_at"),
                    "effective_from": grouped[number][0].get("effective_from"),
                    "effective_to": grouped[number][0].get("effective_to"),
                    "portal_status": None,
                },
                "corpus_scope": {"included_container_count": len(grouped[number])},
                "technical_legal_review": decisions["documents"][number],
                "authority_review_passed": False,
            }
        )

    documents.sort(key=lambda row: row["document_number"])
    kinds = Counter(row["source_kind"] for row in documents)
    require(len(documents) == 18, "source registry must contain 18 documents")
    require(kinds["vbpl_snapshot"] == 16, "source registry must contain 16 VBPL snapshots")
    require(kinds["official_docx_supplied_to_pipeline"] == 2, "source registry must contain 2 DOCX sources")
    require(sum(row["corpus_scope"]["included_container_count"] for row in documents) == 513, "source registry container count mismatch")
    return {
        "schema_version": "labor-law-source-registry-v2",
        "registry_status": "technical_review_complete_pending_authority_approval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_release": {
            "release_id": release_manifest["release_id"],
            "law_as_of": release_manifest["law_as_of"],
            "manifest_sha256": sha256(source_release / "manifest.json"),
        },
        "summary": {
            "document_count": 18,
            "vbpl_snapshot_count": 16,
            "official_docx_count": 2,
            "included_container_count": 513,
            "integrity_failures": 0,
            "technical_unresolved_count": 0,
            "authority_review_pending": 18,
        },
        "documents": documents,
    }


def compare_phapdien(
    legacy_articles: list[dict[str, Any]],
    candidate_articles: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate_by_doc: dict[str, set[int]] = defaultdict(set)
    for article in candidate_articles:
        number = article["document_number"]
        try:
            candidate_by_doc[number].add(int(article["article_number"]))
        except (TypeError, ValueError):
            continue

    rows: list[dict[str, Any]] = []
    for article in legacy_articles:
        note = article.get("source_note_text") or ""
        doc_match = DOC_NUMBER_RE.search(note)
        article_match = SOURCE_ARTICLE_RE.search(note)
        source_number = doc_match.group(1).upper() if doc_match else None
        candidate_number = DOCUMENT_ALIASES.get(source_number, source_number)
        source_article = int(article_match.group(1)) if article_match else None
        if candidate_number is None or source_article is None:
            classification = "unparseable_source_note"
        elif candidate_number not in candidate_by_doc:
            classification = "source_document_absent_from_runtime_scope"
        elif source_article not in candidate_by_doc[candidate_number]:
            classification = "source_article_outside_selected_runtime_scope"
        else:
            classification = "covered_by_document_and_article"
        rows.append(
            {
                "phapdien_article_code": article.get("article_code"),
                "phapdien_heading": article.get("heading"),
                "source_document_number": source_number,
                "runtime_document_number": candidate_number,
                "source_article_number": source_article,
                "classification": classification,
                "source_note": note,
            }
        )

    counts = Counter(row["classification"] for row in rows)
    source_doc_counts = Counter(row["runtime_document_number"] or "<unparseable>" for row in rows)
    report = {
        "schema_version": "phapdien-runtime-coverage-v1",
        "role_policy": {
            "phapdien_20_2": "coverage_reference_only",
            "official_18_sources": "runtime_candidate",
            "direct_merge_allowed": False,
        },
        "summary": {
            "phapdien_article_count": len(legacy_articles),
            "runtime_document_count": len(candidate_by_doc),
            "runtime_container_count": len(candidate_articles),
            "classification_counts": dict(sorted(counts.items())),
        },
        "phapdien_source_document_counts": dict(sorted(source_doc_counts.items())),
        "interpretation": (
            "A missing Pháp điển row is a review lead, not automatic evidence that the runtime corpus is wrong. "
            "The runtime scope remains the 18 hash-bound official sources."
        ),
    }
    return report, rows


def unit_id_matches_rule(unit_id: str, rule: str) -> bool:
    """Match one unit or its descendants without matching sibling numbers.

    A rule ending in ``|`` intentionally selects every unit in an article.  A
    provision rule such as ``|clause=1`` selects that clause and its points, but
    must not select ``|clause=10``.
    """
    if rule.endswith("|"):
        return unit_id.startswith(rule)
    return unit_id == rule or unit_id.startswith(f"{rule}|")


def verify_effect_evidence(
    root: Path,
    decisions: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Verify every declared local legal-effect snapshot, failing closed."""
    evidence_rows = decisions.get("evidence", [])
    evidence_by_id = {row["id"]: row for row in evidence_rows}
    require(len(evidence_by_id) == len(evidence_rows), "duplicate legal-effect evidence ID")
    verification: dict[str, dict[str, Any]] = {}

    for evidence_id, evidence in evidence_by_id.items():
        snapshot = evidence.get("local_snapshot")
        expected_hash = evidence.get("sha256")
        hash_binding_required = evidence.get("hash_binding_required", False)
        if hash_binding_required:
            require(snapshot and expected_hash, f"{evidence_id}: required hash binding is missing")
        if expected_hash:
            require(snapshot, f"{evidence_id}: sha256 requires local_snapshot")
        if not expected_hash:
            continue

        snapshot_path = root / snapshot
        require(snapshot_path.is_file(), f"{evidence_id}: missing evidence snapshot: {snapshot}")
        actual_hash = sha256(snapshot_path)
        require(actual_hash == expected_hash, f"{evidence_id}: evidence snapshot hash mismatch")
        verification[evidence_id] = {
            "local_snapshot": snapshot,
            "sha256": actual_hash,
            "hash_verified": True,
        }

    return verification


def transformation_provenance(
    evidence_id: str,
    evidence_by_id: dict[str, dict[str, Any]],
    evidence_verification: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    require(evidence_id in evidence_by_id, f"unknown transformation evidence: {evidence_id}")
    evidence = evidence_by_id[evidence_id]
    if evidence.get("hash_binding_required"):
        require(evidence_id in evidence_verification, f"{evidence_id}: required evidence was not verified")
    provenance: dict[str, Any] = {"evidence_id": evidence_id}
    if evidence_id in evidence_verification:
        provenance["evidence_snapshot"] = evidence_verification[evidence_id]
    return provenance


def apply_effect_decisions(
    corpus: dict[str, Any],
    decisions: dict[str, Any],
    evidence_verification: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    output = copy.deepcopy(corpus)
    prefixes = tuple(decisions["exclude_unit_prefixes"])
    replacements = decisions["replace_unit_text"]
    insertions = decisions.get("insert_content_units", [])
    evidence_by_id = {row["id"]: row for row in decisions.get("evidence", [])}
    evidence_verification = evidence_verification or {}
    kept_articles: list[dict[str, Any]] = []
    transformations: list[dict[str, Any]] = []
    changed_article_ids: set[str] = set()

    insertions_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for insertion in insertions:
        insertions_by_article[insertion["article_id"]].append(insertion)
    known_article_ids = {article["article_id"] for article in output["articles"]}
    missing_article_ids = sorted(set(insertions_by_article) - known_article_ids)
    require(not missing_article_ids, f"insertion target articles not found: {missing_article_ids}")

    for article in output["articles"]:
        original_units = article.get("content_units", [])
        new_units: list[dict[str, Any]] = []
        article_changes: list[dict[str, Any]] = []
        for unit in original_units:
            unit_id = unit["unit_id"]
            matching_prefix = next(
                (prefix for prefix in prefixes if unit_id_matches_rule(unit_id, prefix)),
                None,
            )
            if matching_prefix:
                article_changes.append({"action": "exclude", "unit_id": unit_id, "matched_rule": matching_prefix})
                continue
            if unit_id in replacements:
                replacement = replacements[unit_id]
                require(isinstance(replacement, dict), f"{unit_id}: replacement must be an object")
                replacement_text = replacement["text"]
                provenance = transformation_provenance(
                    replacement["evidence_id"], evidence_by_id, evidence_verification
                )
                before_hash = hashlib.sha256(unit.get("text", "").encode("utf-8")).hexdigest()
                unit["text"] = replacement_text
                if "raw_text" in unit:
                    unit["raw_text"] = replacement_text
                article_changes.append(
                    {
                        "action": "replace_text",
                        "unit_id": unit_id,
                        "before_sha256": before_hash,
                        "after_sha256": hashlib.sha256(replacement_text.encode("utf-8")).hexdigest(),
                        **provenance,
                    }
                )
            new_units.append(unit)

        existing_unit_ids = {unit["unit_id"] for unit in new_units}
        for insertion in insertions_by_article.get(article["article_id"], []):
            after_unit_id = insertion["after_unit_id"]
            require(after_unit_id in existing_unit_ids, f"insertion anchor not found: {after_unit_id}")
            provenance = transformation_provenance(
                insertion["evidence_id"], evidence_by_id, evidence_verification
            )
            inserted_units = copy.deepcopy(insertion["units"])
            inserted_ids = [unit["unit_id"] for unit in inserted_units]
            require(len(inserted_ids) == len(set(inserted_ids)), "duplicate unit IDs inside insertion")
            require(not (existing_unit_ids & set(inserted_ids)), f"insertion would duplicate units: {inserted_ids}")
            for inserted_unit in inserted_units:
                require(inserted_unit.get("text"), f"{inserted_unit['unit_id']}: inserted unit has no text")
            anchor_index = next(
                index for index, unit in enumerate(new_units) if unit["unit_id"] == after_unit_id
            )
            new_units[anchor_index + 1 : anchor_index + 1] = inserted_units
            existing_unit_ids.update(inserted_ids)
            article_changes.extend(
                {
                    "action": "insert_unit",
                    "unit_id": inserted_unit["unit_id"],
                    "after_unit_id": after_unit_id,
                    "after_sha256": hashlib.sha256(inserted_unit["text"].encode("utf-8")).hexdigest(),
                    **provenance,
                }
                for inserted_unit in inserted_units
            )
        if article_changes:
            changed_article_ids.add(article["article_id"])
            transformations.extend(
                {
                    "document_number": article["document_number"],
                    "article_number": article["article_number"],
                    "article_id": article["article_id"],
                    **change,
                }
                for change in article_changes
            )
            article["content_units"] = new_units
            article["legal_effect_transformations"] = article_changes
        if new_units or not original_units:
            kept_articles.append(article)
        else:
            transformations.append(
                {
                    "document_number": article["document_number"],
                    "article_number": article["article_number"],
                    "article_id": article["article_id"],
                    "action": "exclude_empty_article_after_provision_filter",
                }
            )

    output["articles"] = kept_articles
    output["metadata"].update(
        {
            "schema_version": "canonical-labor-corpus-v1",
            "builder_version": "complete-canonical-corpus-v1",
            "law_as_of": decisions["law_as_of"],
            "release_status": "canonical_candidate_pending_external_gates",
            "source_article_container_count": len(corpus["articles"]),
            "article_container_count": len(kept_articles),
            "effect_transformation_count": len(transformations),
            "authority_review_required": True,
            "authority_review_status": "pending",
        }
    )
    return output, transformations, changed_article_ids


def rebuild_changed_chunks(
    original_chunks: list[dict[str, Any]],
    canonical_corpus: dict[str, Any],
    changed_article_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    counter = TiktokenTokenCounter("cl100k_base")
    config = ChunkingConfig(target_tokens=240, max_tokens=320, fallback_overlap=40)
    changed_articles = [row for row in canonical_corpus["articles"] if row["article_id"] in changed_article_ids]
    changed_corpus = {
        "metadata": canonical_corpus["metadata"],
        "articles": changed_articles,
        "attachments": [],
    }
    rebuilt = build_legal_chunks(changed_corpus, config=config, token_counter=counter) if changed_articles else []
    validation = validate_chunks(changed_corpus, rebuilt, config=config, token_counter=counter) if changed_articles else {"is_valid": True, "errors": []}
    require(validation["is_valid"], f"changed chunk validation failed: {validation['errors']}")
    rebuilt_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in rebuilt:
        chunk["canonicalization"] = {
            "status": "rebuilt_after_legal_effect_transform",
            "exact_e5_audit": "pending",
        }
        rebuilt_by_article[chunk["parent_article_id"]].append(chunk)

    old_by_article: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in original_chunks:
        old_by_article[chunk["parent_article_id"]].append(chunk)

    merged: list[dict[str, Any]] = []
    for article in canonical_corpus["articles"]:
        article_id = article["article_id"]
        merged.extend(rebuilt_by_article[article_id] if article_id in changed_article_ids else old_by_article[article_id])

    require(len({row["chunk_id"] for row in merged}) == len(merged), "duplicate chunk IDs after canonical rebuild")
    audit = {
        "schema_version": "canonical-token-transition-v1",
        "unchanged_chunk_count_exact_e5_inherited": len(merged) - len(rebuilt),
        "changed_chunk_count_tiktoken_validated": len(rebuilt),
        "changed_chunk_max_cl100k_tokens": max((row["token_count"] for row in rebuilt), default=0),
        "changed_chunk_limit": config.max_tokens,
        "exact_e5_audit_complete_for_entire_corpus": False,
        "required_next_command": (
            "python scripts/audit_e5_token_lengths.py --input <release>/canonical_chunks.jsonl "
            "--output-dir <release>/e5_audit"
        ),
    }
    return merged, audit


def rebase_golden(
    golden: dict[str, Any],
    chunks: list[dict[str, Any]],
    release_id: str,
    release_dir: Path,
    exact_e5_audit_passed: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    output = copy.deepcopy(golden)
    chunks_by_id = {row["chunk_id"]: row for row in chunks}
    chunks_by_article_code: dict[str, list[str]] = defaultdict(list)
    for row in chunks:
        if row.get("article_code"):
            chunks_by_article_code[row["article_code"]].append(row["chunk_id"])
    missing: dict[str, list[str]] = {}
    changed_questions = 0
    evidence_count = 0
    for question in output["questions"]:
        original = question.get("evidence_chunk_ids", [])
        retained = [chunk_id for chunk_id in original if chunk_id in chunks_by_id]
        if len(retained) != len(original):
            rebound: list[str] = []
            for code in question.get("expected_article_codes", []):
                rebound.extend(chunks_by_article_code.get(code, []))
            retained = sorted(set(rebound))
            changed_questions += 1
        question["evidence_chunk_ids"] = retained
        question["corpus_verification"] = "rebound_to_canonical_candidate"
        question["law_as_of"] = output.get("law_as_of")
        evidence_count += len(retained)
        if question.get("answerable") and not retained:
            missing[question["id"]] = question.get("expected_article_codes", [])
    output["locked"] = True
    output["dataset_status"] = "locked_internal_regression_pending_release_gates"
    output["corpus"] = {
        "release_id": release_id,
        "release_status": "canonical_candidate_pending_external_gates",
        "path": relative(release_dir / "canonical_chunks.jsonl", REPO_ROOT),
        "article_path": relative(release_dir / "canonical_articles.json", REPO_ROOT),
        "chunk_count": len(chunks),
        "sha256": sha256(release_dir / "canonical_chunks.jsonl"),
        "tokenizer": (
            "fastembed-tokenizers:intfloat/multilingual-e5-large:document; exact release audit bound"
            if exact_e5_audit_passed
            else "mixed: inherited exact E5 plus tiktoken for transformed articles; exact E5 re-audit pending"
        ),
    }
    audit = {
        "schema_version": "golden-canonical-audit-v1",
        "question_count": len(output["questions"]),
        "locked": True,
        "evidence_reference_count": evidence_count,
        "evidence_missing_count": len(missing),
        "questions_with_changed_evidence_ids": changed_questions,
        "missing_evidence_by_question": missing,
        "status": "PASS" if not missing else "FAIL",
    }
    return output, audit


def build_heldout_candidate(source: dict[str, Any], regression_ids: set[str]) -> dict[str, Any]:
    candidates = [row for row in source.get("questions", []) if row.get("id") not in regression_ids][:20]
    return {
        "schema_version": "heldout-evaluation-candidate-v1",
        "dataset_status": "human_label_review_required",
        "locked": False,
        "target_use": "held_out_evaluation_after_configuration_lock",
        "question_count": len(candidates),
        "questions": candidates,
    }


def write_coverage_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--source-release",
        default="data/releases/labor-law-2026-07-28-provenance-rebuild-20260728T095702Z",
    )
    parser.add_argument("--decisions", default="config/legal_effect_decisions_20260727.json")
    parser.add_argument("--legacy-articles", default="data/legacy/phapdien/processed/articles_raw.json")
    parser.add_argument("--golden", default="data/evaluation/golden_questions_v3_unified_candidate.json")
    parser.add_argument("--heldout-source")
    parser.add_argument("--release-id", default="labor-law-canonical-20260727-candidate")
    parser.add_argument(
        "--e5-audit-summary",
        help=(
            "Exact multilingual-E5 audit summary. Defaults to "
            "<release>/e5_audit/summary.json."
        ),
    )
    args = parser.parse_args()

    root = args.repo_root.resolve()
    source_release = root / args.source_release
    release_dir = root / "data/releases" / args.release_id
    decisions = load_json(root / args.decisions)
    effect_evidence_verification = verify_effect_evidence(root, decisions)
    source_manifest = load_json(source_release / "manifest.json")
    require(source_manifest["gates"]["source_hashes_verified"] is True, "source release hash gate failed")
    require(source_manifest["gates"]["base_vbpl_snapshot_verification_passed"] is True, "source snapshot gate failed")
    require(len(decisions["documents"]) == 18, "legal decisions must cover 18 documents")

    source_corpus = load_json(source_release / "articles.json")
    source_chunks = load_jsonl(source_release / "chunks.jsonl")
    registry = build_source_registry(root, source_release, source_corpus["articles"], decisions)
    governance_dir = root / "data/governance"
    write_json(governance_dir / "source_registry.json", registry)

    coverage_report, coverage_rows = compare_phapdien(
        load_json(root / args.legacy_articles)["articles"],
        source_corpus["articles"],
    )
    write_json(governance_dir / "phapdien_20_2_coverage_report.json", coverage_report)
    write_coverage_csv(governance_dir / "phapdien_20_2_coverage_map.csv", coverage_rows)

    canonical_corpus, transformations, changed_article_ids = apply_effect_decisions(
        source_corpus,
        decisions,
        effect_evidence_verification,
    )
    canonical_corpus["metadata"]["release_id"] = args.release_id
    release_dir.mkdir(parents=True, exist_ok=True)
    write_json(release_dir / "canonical_articles.json", canonical_corpus)
    canonical_chunks, token_transition = rebuild_changed_chunks(source_chunks, canonical_corpus, changed_article_ids)
    write_jsonl(release_dir / "canonical_chunks.jsonl", canonical_chunks)
    canonical_chunks_path = release_dir / "canonical_chunks.jsonl"
    e5_audit_path = (
        root / args.e5_audit_summary
        if args.e5_audit_summary
        else release_dir / "e5_audit/summary.json"
    )
    exact_e5_audit_passed, e5_audit_binding = verify_exact_e5_audit(
        root,
        e5_audit_path,
        canonical_chunks_path,
        len(canonical_chunks),
    )
    token_transition["exact_e5_audit_complete_for_entire_corpus"] = exact_e5_audit_passed
    token_transition["exact_e5_audit_binding"] = e5_audit_binding
    if exact_e5_audit_passed:
        token_transition.pop("required_next_command", None)
    write_json(release_dir / "token_audit_transition.json", token_transition)
    write_json(release_dir / "e5_audit_binding.json", e5_audit_binding)

    legal_review = {
        "schema_version": "legal-effect-review-v2",
        "release_id": args.release_id,
        "law_as_of": decisions["law_as_of"],
        "review_status": decisions["review_status"],
        "authority_review_status": "pending",
        "document_decisions": decisions["documents"],
        "evidence": decisions["evidence"],
        "effect_evidence_verification": effect_evidence_verification,
        "transformations": transformations,
        "replacement_documents": decisions["replacement_documents"],
        "known_source_gaps": decisions["known_source_gaps"],
        "technical_unresolved_count": 0,
        "authority_approval_required": True,
        "not_legal_advice": True,
    }
    write_json(release_dir / "legal_effect_review.json", legal_review)
    write_json(release_dir / "source_registry.json", registry)

    golden, golden_audit = rebase_golden(
        load_json(root / args.golden),
        canonical_chunks,
        args.release_id,
        release_dir,
        exact_e5_audit_passed,
    )
    golden_path = root / "data/evaluation/golden_questions_v3_canonical_20260727.json"
    write_json(golden_path, golden)
    write_json(release_dir / "golden_regression_audit.json", golden_audit)
    if args.heldout_source:
        heldout = build_heldout_candidate(
            load_json(Path(args.heldout_source)),
            {row["id"] for row in golden["questions"]},
        )
        write_json(root / "data/evaluation/heldout_20_review_candidate.json", heldout)

    article_ids = {row["article_id"] for row in canonical_corpus["articles"]}
    unit_ids = {
        unit["unit_id"]
        for article in canonical_corpus["articles"]
        for unit in article.get("content_units", [])
    }
    chunk_trace_errors = []
    for chunk in canonical_chunks:
        if chunk["parent_article_id"] not in article_ids:
            chunk_trace_errors.append(f"{chunk['chunk_id']}: unknown article")
        missing_units = [unit_id for unit_id in chunk.get("source_unit_ids", []) if unit_id not in unit_ids]
        if missing_units:
            chunk_trace_errors.append(f"{chunk['chunk_id']}: missing units {missing_units}")
    excluded_still_present = [
        unit_id
        for unit_id in unit_ids
        if any(unit_id_matches_rule(unit_id, prefix) for prefix in decisions["exclude_unit_prefixes"])
    ]
    source_gaps = decisions["known_source_gaps"]
    required_hash_evidence_ids = {
        row["id"]
        for row in decisions["evidence"]
        if row.get("hash_binding_required")
    }
    gates = {
        "source_registry_integrity_passed": registry["summary"]["integrity_failures"] == 0,
        "all_18_documents_have_technical_decisions": len(decisions["documents"]) == 18,
        "technical_unresolved_count_is_zero": legal_review["technical_unresolved_count"] == 0,
        "excluded_provisions_absent": not excluded_still_present,
        "chunk_traceability_passed": not chunk_trace_errors,
        "golden_regression_evidence_complete": golden_audit["status"] == "PASS",
        "changed_chunks_structurally_validated": True,
        "current_text_source_gaps_resolved": len(source_gaps) == 0,
        "effect_evidence_hashes_verified": (
            required_hash_evidence_ids == set(effect_evidence_verification)
        ),
        "exact_e5_audit_passed": exact_e5_audit_passed,
        "authority_review_passed": False,
    }
    technical_candidate_passed = all(
        gates[key]
        for key in (
            "source_registry_integrity_passed",
            "all_18_documents_have_technical_decisions",
            "technical_unresolved_count_is_zero",
            "excluded_provisions_absent",
            "chunk_traceability_passed",
            "golden_regression_evidence_complete",
            "changed_chunks_structurally_validated",
            "current_text_source_gaps_resolved",
            "effect_evidence_hashes_verified",
        )
    )
    production_publishable = all(gates.values())
    blockers = []
    if not exact_e5_audit_passed:
        blockers.append("Bind exact multilingual-e5-large audit to canonical_chunks.jsonl.")
    blockers.append("Obtain authorized legal-effect approval bound to manifest SHA-256.")
    strict_report = {
        "schema_version": "canonical-strict-gate-v1",
        "release_id": args.release_id,
        "counts": {
            "documents": len({row["document_number"] for row in canonical_corpus["articles"]}),
            "source_containers": len(source_corpus["articles"]),
            "canonical_containers": len(canonical_corpus["articles"]),
            "canonical_chunks": len(canonical_chunks),
            "changed_articles": len(changed_article_ids),
            "transformations": len(transformations),
            "known_source_gaps": len(source_gaps),
            "chunk_trace_errors": len(chunk_trace_errors),
            "golden_missing_evidence": golden_audit["evidence_missing_count"],
        },
        "gates": gates,
        "technical_candidate_passed": technical_candidate_passed,
        "production_publishable": production_publishable,
        "gate_details": {"exact_e5_audit": e5_audit_binding},
        "blockers": blockers,
        "errors": chunk_trace_errors,
    }
    write_json(release_dir / "STRICT_GATE_REPORT.json", strict_report)

    build_report = {
        "schema_version": "canonical-build-report-v1",
        "release_id": args.release_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_release_id": source_manifest["release_id"],
        "source_release_manifest_sha256": sha256(source_release / "manifest.json"),
        "technical_candidate_passed": technical_candidate_passed,
        "production_publishable": production_publishable,
        "counts": strict_report["counts"],
    }
    write_json(release_dir / "BUILD_REPORT.json", build_report)

    manifest = {
        "schema_version": "canonical-release-manifest-v1",
        "release_id": args.release_id,
        "release_status": "canonical_candidate_pending_external_gates",
        "source_fetched_through": "2026-07-28",
        "law_as_of": decisions["law_as_of"],
        "release_generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": strict_report["counts"],
        "gates": gates,
        "e5_audit_binding": e5_audit_binding,
        "technical_candidate_passed": technical_candidate_passed,
        "production_publishable": production_publishable,
        "runtime_policy": {
            "phapdien_20_2_indexed": False,
            "blue_green_alias_switch_allowed": production_publishable,
            "old_collection_retained_for_rollback": True,
        },
    }
    write_json(release_dir / "manifest.json", manifest)

    checksum_files = sorted(
        path for path in release_dir.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    (release_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksum_files),
        encoding="utf-8",
    )
    print(json.dumps(strict_report, ensure_ascii=False, indent=2))
    return 0 if technical_candidate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
