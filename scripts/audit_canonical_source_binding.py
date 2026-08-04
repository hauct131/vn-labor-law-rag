#!/usr/bin/env python3
"""Fail-closed audit before rebinding the corpus to canonical sources.

The existing unified builder starts from the 16-document VBPL article corpus
and overlays two bespoke DOCX documents. After canonical-source ingestion,
15 of those VBPL documents have an official Gazette Word snapshot instead.
This audit proves (or disproves) that every selected VBPL article is textually
identical to the corresponding Gazette Word article before any provenance is
rewritten. It never builds, indexes, or promotes a release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts import congbao_docx as gazette
    from scripts import vbpl_portal as vbpl
    from scripts.build_official_docx_release import (
        administrative_tail_index,
        extract_docx_display_paragraphs,
        extract_docx_paragraphs,
        locate_main_article_sequence,
        strip_gazette_page_artifacts,
        strip_inline_administrative_tail,
        strip_inter_article_headings,
    )
except ImportError:  # pragma: no cover - direct script execution
    import congbao_docx as gazette  # type: ignore[no-redef]
    import vbpl_portal as vbpl  # type: ignore[no-redef]
    from build_official_docx_release import (  # type: ignore[no-redef]
        administrative_tail_index,
        extract_docx_display_paragraphs,
        extract_docx_paragraphs,
        locate_main_article_sequence,
        strip_gazette_page_artifacts,
        strip_inline_administrative_tail,
        strip_inter_article_headings,
    )


SCHEMA_VERSION = "canonical-source-binding-audit-v1"
EXPECTED_DOCUMENTS = 18
EXPECTED_GAZETTE_DOCUMENTS = 17
EXPECTED_VBPL_FALLBACK_DOCUMENTS = 1


class BindingAuditError(RuntimeError):
    """The supplied inputs cannot support a canonical binding audit."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def comparison_text(value: str) -> str:
    """Normalize representation only; retain words, punctuation and digits."""

    value = unicodedata.normalize("NFC", value)
    value = value.replace("\u00a0", " ").replace("\ufeff", "")
    return " ".join(value.split())


def article_body(article: Mapping[str, Any]) -> str:
    units = article.get("content_units") or []
    return "\n".join(
        str(unit.get("text") or "")
        for unit in units
        if isinstance(unit, Mapping)
    ).strip()


def article_ranges(value: str | int | None) -> set[int]:
    if isinstance(value, int):
        return {value}
    if not isinstance(value, str) or not value.strip():
        raise BindingAuditError(f"Invalid include_articles: {value!r}")
    result: set[int] = set()
    for raw in value.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise BindingAuditError(f"Invalid article range: {part}")
            result.update(range(start, end + 1))
        else:
            result.add(int(part))
    if not result:
        raise BindingAuditError(f"Empty include_articles: {value!r}")
    return result


def _unique_title_sequence(
    paragraphs: Sequence[str],
    expected_titles: Mapping[int, str],
    expected_articles: int,
) -> list[tuple[int, int, str]]:
    """Resolve exactly one complete article chain from stored Word titles.

    Some Gazette packages store ``Điều N.`` as an automatic Word label. Word's
    display counter can drift when the same numbering definition is reused by
    other paragraph lists. The title text itself remains in ``w:t``. This
    fallback therefore accepts a chain only when all titles 1..N from the
    already-validated VBPL corpus occur in exactly one increasing sequence.
    """

    wanted = set(range(1, expected_articles + 1))
    if set(expected_titles) != wanted:
        raise BindingAuditError(
            "Title fallback requires base titles for every main article "
            f"1-{expected_articles}"
        )

    indices_by_title: dict[str, list[int]] = defaultdict(list)
    for index, text in enumerate(paragraphs):
        indices_by_title[comparison_text(text)].append(index)

    candidates: dict[int, list[int]] = {}
    for number in range(1, expected_articles + 1):
        title = comparison_text(expected_titles[number])
        matches = indices_by_title.get(title, [])
        if not matches:
            raise BindingAuditError(
                f"Stored Word title not found for Điều {number}: {title!r}"
            )
        candidates[number] = matches

    # Count increasing paths, capped at two because the audit needs only to
    # distinguish a unique binding from an ambiguous one.
    states: dict[int, tuple[int, int | None]] = {
        index: (1, None) for index in candidates[1]
    }
    history: list[dict[int, tuple[int, int | None]]] = [states]
    for number in range(2, expected_articles + 1):
        next_states: dict[int, tuple[int, int | None]] = {}
        for index in candidates[number]:
            predecessors = [
                (previous, count)
                for previous, (count, _) in states.items()
                if previous < index
            ]
            count = min(2, sum(value for _, value in predecessors))
            if not count:
                continue
            unique_previous = predecessors[0][0] if count == 1 else None
            next_states[index] = (count, unique_previous)
        if not next_states:
            raise BindingAuditError(
                "Stored Word titles do not form a complete increasing main "
                f"article sequence at Điều {number}"
            )
        states = next_states
        history.append(states)

    total_paths = min(2, sum(count for count, _ in states.values()))
    if total_paths != 1:
        raise BindingAuditError(
            "Stored Word titles form more than one complete main article "
            "sequence; refusing an ambiguous canonical binding"
        )

    current = next(index for index, (count, _) in states.items() if count == 1)
    resolved: list[tuple[int, int, str]] = []
    for number in range(expected_articles, 0, -1):
        title = comparison_text(expected_titles[number])
        resolved.append((number, current, title))
        _, previous = history[number - 1][current]
        if number > 1:
            if previous is None:
                raise BindingAuditError(
                    "Internal error while reconstructing the unique title chain"
                )
            current = previous
    resolved.reverse()
    return resolved


def extract_word_articles(
    source_paths: Sequence[Path],
    expected_articles: int,
    expected_titles: Mapping[int, str] | None = None,
) -> dict[int, dict[str, str]]:
    stored_paragraphs: list[str] = []
    for path in source_paths:
        stored_part, _ = extract_docx_paragraphs(path)
        stored_paragraphs.extend(stored_part)
    display_error: Exception | None = None
    raw_error: Exception | None = None
    try:
        display_paragraphs: list[str] = []
        for path in source_paths:
            display_part, _ = extract_docx_display_paragraphs(path)
            display_paragraphs.extend(display_part)
        headings = locate_main_article_sequence(
            display_paragraphs, expected_articles
        )
        paragraphs = display_paragraphs
    except ValueError as exc:
        display_error = exc
        # Many Gazette files store explicit ``Điều N.`` text even when an
        # unrelated Word list definition corrupts the displayed prefix.  The
        # stored text is therefore the safest second parser and also works for
        # corpora that select only a subset of the document's articles.
        try:
            headings = locate_main_article_sequence(
                stored_paragraphs, expected_articles
            )
            paragraphs = stored_paragraphs
        except ValueError as stored_exc:
            raw_error = stored_exc
            if expected_titles is None:
                raise BindingAuditError(
                    "Cannot locate the main article sequence from displayed "
                    f"or stored Word text: display={display_error}; "
                    f"stored={raw_error}"
                ) from stored_exc
            try:
                headings = _unique_title_sequence(
                    stored_paragraphs, expected_titles, expected_articles
                )
                paragraphs = stored_paragraphs
            except Exception as title_exc:
                raise BindingAuditError(
                    "Cannot locate the main article sequence from displayed "
                    f"Word text ({display_error}), stored Word text "
                    f"({raw_error}), or the unique-title fallback "
                    f"({title_exc})"
                ) from title_exc
    result: dict[int, dict[str, str]] = {}
    for position, (number, index, title) in enumerate(headings):
        if position + 1 < len(headings):
            end = headings[position + 1][1]
        else:
            end = administrative_tail_index(
                paragraphs, index + 1, len(paragraphs)
            )
        body_lines = strip_gazette_page_artifacts(
            strip_inter_article_headings(paragraphs[index + 1 : end])
        )
        result[number] = {
            "title": title,
            "body": strip_inline_administrative_tail(
                "\n".join(body_lines).strip()
            ),
        }
    return result


def first_difference(left: str, right: str, radius: int = 100) -> dict[str, Any]:
    limit = min(len(left), len(right))
    index = next((i for i in range(limit) if left[i] != right[i]), limit)
    if index == limit and len(left) == len(right):
        return {}
    start = max(0, index - radius)
    stop = index + radius
    return {
        "offset": index,
        "vbpl_context": left[start:stop],
        "gazette_context": right[start:stop],
    }


def compare_article(
    base: Mapping[str, Any], word: Mapping[str, str]
) -> dict[str, Any]:
    base_title = comparison_text(str(base.get("article_title") or ""))
    word_title = comparison_text(word["title"])
    def comparable_body(value: str) -> str:
        lines = [
            comparison_text(line)
            for line in value.splitlines()
            if comparison_text(line)
        ]
        end = administrative_tail_index(lines, 0, len(lines))
        body_lines = strip_gazette_page_artifacts(
            strip_inter_article_headings(lines[:end])
        )
        return comparison_text(
            strip_inline_administrative_tail("\n".join(body_lines))
        )

    base_body = comparable_body(article_body(base))
    word_body = comparable_body(word["body"])
    title_equal = base_title == word_title
    body_equal = base_body == word_body
    ratio = SequenceMatcher(None, base_body, word_body, autojunk=False).ratio()
    return {
        "article_number": base.get("article_number"),
        "article_id": base.get("article_id"),
        "title_equal": title_equal,
        "body_equal": body_equal,
        "vbpl_body_sha256": hashlib.sha256(base_body.encode()).hexdigest(),
        "gazette_body_sha256": hashlib.sha256(word_body.encode()).hexdigest(),
        "vbpl_characters": len(base_body),
        "gazette_characters": len(word_body),
        "similarity_ratio": round(ratio, 6),
        "first_difference": first_difference(base_body, word_body),
        "status": "PASS" if title_equal and body_equal else "FAIL",
    }


def resolve_repo_path(root: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else root / path


def verify_manifest_shape(manifest: Mapping[str, Any]) -> None:
    if manifest.get("status") != "PASS":
        raise BindingAuditError("Canonical source manifest is not PASS")
    if manifest.get("documents_requested") != EXPECTED_DOCUMENTS:
        raise BindingAuditError("Canonical manifest must request 18 documents")
    if manifest.get("documents_succeeded") != EXPECTED_DOCUMENTS:
        raise BindingAuditError("Canonical manifest must succeed for 18 documents")
    if manifest.get("documents_failed") != 0:
        raise BindingAuditError("Canonical manifest contains failures")
    if manifest.get("official_gazette_documents") != EXPECTED_GAZETTE_DOCUMENTS:
        raise BindingAuditError("Canonical manifest must contain 17 Gazette documents")
    if manifest.get("vbpl_fallback_documents") != EXPECTED_VBPL_FALLBACK_DOCUMENTS:
        raise BindingAuditError("Canonical manifest must contain one VBPL fallback")
    if manifest.get("one_canonical_snapshot_per_document") is not True:
        raise BindingAuditError("Canonical manifest does not prove one snapshot per document")


def audit_document(
    *,
    root: Path,
    number: str,
    item: Mapping[str, Any],
    source: Mapping[str, Any],
    base_articles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit one document without allowing it to abort the remaining set."""

    provider = source.get("canonical_provider")
    snapshot = resolve_repo_path(root, source.get("snapshot_dir"))
    record: dict[str, Any] = {
        "document_number": number,
        "canonical_provider": provider,
        "snapshot_dir": str(snapshot),
        "snapshot_verified": False,
        "selected_articles": 0,
        "articles_passed": 0,
        "articles_failed": 0,
        "article_results": [],
        "status": "FAIL",
    }

    if provider == "vbpl":
        vbpl.verify_snapshot(snapshot, expected_document_number=number)
        expected_reason = (item.get("canonical_source_policy") or {}).get(
            "fallback_reason"
        )
        if source.get("fallback_used") is not True:
            raise BindingAuditError(f"{number}: VBPL result is not marked fallback")
        if source.get("fallback_reason") != expected_reason:
            raise BindingAuditError(f"{number}: unexpected fallback reason")
        record.update(snapshot_verified=True, status="PASS_FALLBACK")
        return record

    if provider != "official_gazette_word":
        raise BindingAuditError(f"{number}: unsupported provider {provider!r}")
    gazette.verify_snapshot(snapshot, expected_document_number=number)
    record["snapshot_verified"] = True

    source_paths = [
        resolve_repo_path(root, value)
        for value in (source.get("source_paths") or [])
    ]
    if not source_paths and source.get("source_path"):
        source_paths = [resolve_repo_path(root, source["source_path"])]
    if not source_paths or any(not path.is_file() for path in source_paths):
        raise BindingAuditError(f"{number}: canonical Word path is missing")
    snapshot_manifest = load_json(snapshot / "manifest.json")
    expected_hashes = (snapshot_manifest.get("content_hashes") or {}).get(
        "normalized_docx_sha256s"
    ) or []
    actual_hashes = [sha256_file(path) for path in source_paths]
    if actual_hashes != expected_hashes:
        raise BindingAuditError(f"{number}: materialized DOCX hash mismatch")
    record["source_paths"] = [str(path) for path in source_paths]
    record["source_sha256s"] = actual_hashes

    # These two documents are parsed directly by the release builder and do
    # not occur in the 16-document VBPL base corpus.
    if not base_articles:
        if item.get("source_file") is None:
            raise BindingAuditError(f"{number}: no base articles to compare")
        record["status"] = "PASS_DIRECT_DOCX"
        return record

    selected = article_ranges(item.get("include_articles"))
    actual_selected = {int(article["article_number"]) for article in base_articles}
    if actual_selected != selected:
        raise BindingAuditError(
            f"{number}: base article selection differs from config: "
            f"{sorted(actual_selected)} != {sorted(selected)}"
        )
    expected_titles = {
        int(article["article_number"]): str(article["article_title"])
        for article in base_articles
    }
    word_articles = extract_word_articles(
        source_paths,
        int(item["expected_articles"]),
        expected_titles=expected_titles,
    )
    comparisons = [
        compare_article(article, word_articles[int(article["article_number"])])
        for article in base_articles
    ]
    passed = sum(row["status"] == "PASS" for row in comparisons)
    record.update(
        selected_articles=len(comparisons),
        articles_passed=passed,
        articles_failed=len(comparisons) - passed,
        article_results=comparisons,
        status="PASS" if passed == len(comparisons) else "FAIL",
    )
    return record


def audit(
    *,
    root: Path,
    config_path: Path,
    canonical_manifest_path: Path,
    base_corpus_path: Path,
) -> dict[str, Any]:
    config = load_json(config_path)
    manifest = load_json(canonical_manifest_path)
    base = load_json(base_corpus_path)
    verify_manifest_shape(manifest)

    config_by_number = {
        str(item["document_number"]): item for item in config.get("documents", [])
    }
    result_by_number = {
        str(item["document_number"]): item for item in manifest.get("results", [])
    }
    if len(config_by_number) != EXPECTED_DOCUMENTS:
        raise BindingAuditError("Config must contain exactly 18 unique documents")
    if set(result_by_number) != set(config_by_number):
        raise BindingAuditError("Canonical results do not exactly match configured documents")

    base_by_number: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for article in base.get("articles", []):
        base_by_number[str(article.get("document_number"))].append(article)

    documents: list[dict[str, Any]] = []
    for number, item in config_by_number.items():
        source = result_by_number[number]
        base_articles = sorted(
            base_by_number.get(number, []),
            key=lambda article: int(article["article_number"]),
        )
        try:
            record = audit_document(
                root=root,
                number=number,
                item=item,
                source=source,
                base_articles=base_articles,
            )
        except Exception as exc:
            record = {
                "document_number": number,
                "canonical_provider": source.get("canonical_provider"),
                "snapshot_dir": str(
                    resolve_repo_path(root, source.get("snapshot_dir"))
                ),
                "snapshot_verified": False,
                "selected_articles": 0,
                "articles_passed": 0,
                "articles_failed": 0,
                "article_results": [],
                "status": "ERROR",
                "error_type": type(exc).__name__,
                "error": f"{number}: {exc}",
            }
        documents.append(record)

    passed_statuses = {"PASS", "PASS_FALLBACK", "PASS_DIRECT_DOCX"}
    failed_documents = [
        row["document_number"]
        for row in documents
        if row["status"] not in passed_statuses
    ]
    errored_documents = [
        row["document_number"] for row in documents if row["status"] == "ERROR"
    ]
    difference_documents = [
        row["document_number"] for row in documents if row["status"] == "FAIL"
    ]
    article_count = sum(int(row["selected_articles"]) for row in documents)
    article_passed = sum(int(row["articles_passed"]) for row in documents)
    if errored_documents:
        status = "ERROR"
    elif failed_documents:
        status = "FAIL"
    else:
        status = "PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_manifest": str(canonical_manifest_path),
        "base_corpus": str(base_corpus_path),
        "documents_audited": len(documents),
        "documents_failed": len(failed_documents),
        "failed_documents": failed_documents,
        "documents_errored": len(errored_documents),
        "errored_documents": errored_documents,
        "documents_with_text_differences": len(difference_documents),
        "text_difference_documents": difference_documents,
        "articles_compared": article_count,
        "articles_passed": article_passed,
        "articles_failed": article_count - article_passed,
        "safe_to_rebind_provenance": not failed_documents,
        "canonical_rebuild_required": bool(difference_documents)
        and not errored_documents,
        "production_promotion_performed": False,
        "documents": documents,
        "status": status,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("config/vbpl_corpus.json"))
    parser.add_argument(
        "--canonical-manifest",
        type=Path,
        default=Path("data/raw/canonical_sources/run_manifest.json"),
    )
    parser.add_argument(
        "--base-corpus",
        type=Path,
        default=Path("data/processed/vbpl_articles_raw.json"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/quality/canonical_source_binding_audit.json"),
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    report_path = resolve_repo_path(root, args.report)
    try:
        report = audit(
            root=root,
            config_path=resolve_repo_path(root, args.config),
            canonical_manifest_path=resolve_repo_path(root, args.canonical_manifest),
            base_corpus_path=resolve_repo_path(root, args.base_corpus),
        )
    except Exception as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "ERROR",
            "documents_audited": 0,
            "documents_failed": 0,
            "failed_documents": [],
            "documents_errored": 0,
            "errored_documents": [],
            "documents_with_text_differences": 0,
            "text_difference_documents": [],
            "articles_compared": 0,
            "articles_passed": 0,
            "articles_failed": 0,
            "safe_to_rebind_provenance": False,
            "canonical_rebuild_required": False,
            "production_promotion_performed": False,
            "documents": [],
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary_keys = (
        "schema_version",
        "status",
        "documents_audited",
        "documents_failed",
        "failed_documents",
        "documents_errored",
        "errored_documents",
        "documents_with_text_differences",
        "text_difference_documents",
        "articles_compared",
        "articles_passed",
        "articles_failed",
        "safe_to_rebind_provenance",
        "canonical_rebuild_required",
        "production_promotion_performed",
        "error_type",
        "error",
    )
    summary = {key: report[key] for key in summary_keys if key in report}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Canonical binding audit: {report_path}")
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
