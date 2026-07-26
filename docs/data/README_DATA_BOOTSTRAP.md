# Data bootstrap for vn-labor-law-rag

This bundle restores the versioned golden datasets and adds reproducible data
audit/rebase tooling.

## Included files

- `data/evaluation/golden_questions_v1_legacy.json`
  - Original 45-question verified legacy set.
  - Bound to the old 1,395-chunk Pháp điển corpus.
  - Preserve unchanged.

- `data/evaluation/golden_questions_v2_current_law.json`
  - Recovered current-law migration from 2026-07-23.
  - Bound to the separate 687-chunk verified-scope snapshot.
  - Keep as historical/current-law labeling evidence; do not point the active
    evaluator at it until its snapshot is restored or evidence is remapped.

- `data/evaluation/legacy_evidence_catalog.json`
  - Compact catalog for the 102 legacy evidence references.
  - Allows deterministic remapping without committing the old 1,395-chunk
    corpus.

- `scripts/audit_golden_dataset.py`
  - Audits question IDs, evidence IDs, article coverage and corpus hash.

- `scripts/rebase_golden_evidence.py`
  - Replaces only missing evidence IDs against the current 1,382-chunk corpus.
  - Preserves all legal labels and required points.
  - Does not overwrite v1.

- `scripts/build_data_manifests.py`
  - Creates Pháp điển/VBPL manifests with SHA-256, counts and quality metadata.

## Install into the repository

From the repository root:

```bash
unzip -o vn_labor_law_data_bootstrap_2026-07-26.zip
chmod +x scripts/audit_golden_dataset.py
chmod +x scripts/rebase_golden_evidence.py
chmod +x scripts/build_data_manifests.py
```

Review `docs/data/gitignore_data_snippet.txt` and merge the relevant rules into
the repository `.gitignore`.

## Step 1 — reproduce the known legacy mismatch

```bash
PYTHONPATH=.:backend .venv/bin/python3 scripts/audit_golden_dataset.py \
  --golden data/evaluation/golden_questions_v1_legacy.json \
  --chunks data/processed/legal_chunks.jsonl \
  --report data/evaluation/golden_v1_legacy_audit.json
```

Expected with the current Pháp điển corpus:

```text
questions = 45
chunks = 1382
evidence references = 102
evidence found = 100
evidence missing = 2
affected questions = r2ai_gold_022, r2ai_gold_037
```

## Step 2 — rebase the two missing evidence IDs

```bash
PYTHONPATH=.:backend .venv/bin/python3 scripts/rebase_golden_evidence.py \
  --golden data/evaluation/golden_questions_v1_legacy.json \
  --chunks data/processed/legal_chunks.jsonl \
  --legacy-catalog data/evaluation/legacy_evidence_catalog.json \
  --output data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
  --report data/evaluation/golden_rebase_report.json \
  --strict
```

Inspect the report:

```bash
python3 -m json.tool data/evaluation/golden_rebase_report.json
```

The report should contain exactly the old/new chunk IDs, article codes and
similarity scores. A score below 0.80 is intentionally surfaced for manual
review.

Important: the output is a **rebased legacy benchmark**, not the final
current-law benchmark.

## Step 3 — strict audit of the rebased file

```bash
PYTHONPATH=.:backend .venv/bin/python3 scripts/audit_golden_dataset.py \
  --golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
  --chunks data/processed/legal_chunks.jsonl \
  --report data/evaluation/golden_v1_rebased_audit.json \
  --strict
```

Expected:

```text
evidence_missing_count = 0
affected_question_count = 0
status = PASS
```

## Step 4 — generate data manifests

```bash
PYTHONPATH=.:backend .venv/bin/python3 scripts/build_data_manifests.py
```

Outputs:

```text
data/manifests/phapdien_manifest.json
data/manifests/vbpl_manifest.json
data/manifests/data_inventory.json
```

## Versioning policy

Commit these files:

```text
data/raw/DeMuc_20.2_Lao_Dong.html
data/raw/vbpl/**
data/processed/articles_raw.json
data/processed/legal_chunks.jsonl
data/processed/chunking_summary.json
data/processed/parser_report.json
data/processed/vbpl_articles_raw.json
data/processed/vbpl_legal_chunks.jsonl
data/processed/vbpl_chunking_summary.json
data/evaluation/**
data/manifests/**
data/quality/vbpl_article_build_report.json
data/quality/vbpl_chunk_quality_report.json
```

Do not commit Qdrant storage, model caches, logs, cookies, sessions or secrets.

## Golden dataset lifecycle

1. `v1_legacy`
   - immutable historical labels tied to 1,395 chunks.

2. `v1_rebased_current_phapdien`
   - same legacy legal labels, evidence remapped to 1,382 chunks.
   - useful immediately for evaluator regression.

3. `v2_current_law`
   - recovered 2026-07-23 current-law labels tied to a 687-chunk snapshot.
   - preserve, but do not make the active alias yet.

4. `v3_unified`
   - create after Pháp điển + VBPL canonical merger.
   - use stable canonical article IDs as the primary labels.
   - this should eventually become `golden_questions.json`.

Do not create or overwrite `data/evaluation/golden_questions.json` until the
unified corpus and v3 evidence mapping are ready.
