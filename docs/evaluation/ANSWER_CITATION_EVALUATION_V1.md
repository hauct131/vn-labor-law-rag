# Answer and Citation Evaluation V1

## Scope

This layer evaluates generated answers separately from the existing locked
retrieval benchmark. It does not modify or unlock the 13-question retrieval
test split.

All implementation code lives under `evaluation/answer_quality`. It has its
own dependency file and CI job, is not copied into the backend container, and
does not participate in the user-facing request path. A later live evaluator
will communicate with the product only through its public HTTP API.

The first implementation stage provides:

- a strict dataset schema;
- atomic required and forbidden claims;
- structural citation and article-level metrics;
- a ten-point human-review rubric;
- explicit technical pass thresholds;
- state rules that prevent multi-LLM consensus from being reported as legal
  authority approval.

The 20 existing held-out questions are migrated deterministically into a draft
candidate file. The external panel can call explicitly selected OpenRouter
models and create candidate labels, but it never enables benchmarking or locks
a golden dataset.

## Dataset states

```text
draft_candidate
  -> multi_llm_reviewed
  -> human_adjudicated
  -> locked
```

A `locked` dataset requires every question to have
`label_status=human_adjudicated`. An answerable question cannot be enabled for
benchmarking until it has at least one atomic required claim and a named human
reviewer.

`authority_review_status` remains `pending` unless a qualified reviewer makes
an explicit decision. Multi-model agreement alone cannot change that status.

## Technical metrics

| Metric | Meaning | Default gate |
|---|---|---:|
| Status accuracy | Correct answerable/out-of-scope/insufficient status | >= 0.90 |
| Citation ID validity | Declared source IDs exist in supplied context | 1.00 |
| Inline/declared match | Inline citations exactly match declared IDs | 1.00 |
| Citation precision | Cited expected articles / all cited articles | >= 0.90 |
| Citation completeness | Cited expected articles / expected articles | >= 0.90 |
| Required-claim recall | Satisfied atomic claims / required claims | >= 0.90 |
| Unsupported material claims | Material unsupported assertions | 0 |
| Runtime success rate | Calls completed without generation failure | >= 0.95 |

The gate fails closed when citation or required-claim dimensions have not been
scored. These thresholds are internal technical criteria, not legal standards.

## Human rubric

| Criterion | Points |
|---|---:|
| Legal correctness under supplied authority | 0-4 |
| Groundedness in supplied evidence | 0-2 |
| Article/clause/point citation accuracy | 0-2 |
| Material conditions and exceptions covered | 0-1 |
| No unsupported material information | 0-1 |

Default rubric gate: average at least 8/10 and no reviewed answer below 6/10.

## Validate a dataset

From the repository root:

```bash
python3 -m evaluation.answer_quality.validate_dataset \
  path/to/answer-quality-dataset.json
```

Validation performs no network call, retrieval, generation, or write to the
locked retrieval benchmark.

Rebuild and verify the migrated 20-question candidate file:

```bash
python3 -m evaluation.answer_quality.migrate_candidates
python3 -m evaluation.answer_quality.migrate_candidates --check
```

The migration fails instead of guessing when a source label cannot be mapped,
when an evidence chunk is missing, or when the source/corpus hash changes.

## Select current panel models

No model version is hard-coded. Query OpenRouter's current model metadata and
select at least three exact model IDs from distinct organizations. Only models
reporting structured-output support and enough context are returned:

```bash
.venv-evaluation/bin/python -m \
  evaluation.answer_quality.list_models \
  --minimum-context 20000
```

Add `--free-only` only when all three selected organizations have a compatible
free model. Availability is checked at run time and is not assumed.

## Validate the multi-model plan without network generation

Replace the values below with exact IDs returned by the live model query:

```bash
MODEL_A='organization-a/exact-model-id'
MODEL_B='organization-b/exact-model-id'
MODEL_C='organization-c/exact-model-id'

.venv-evaluation/bin/python -m evaluation.answer_quality.panel \
  --dataset data/evaluation/answer-quality-v1/candidate_questions.json \
  --models "$MODEL_A" "$MODEL_B" "$MODEL_C" \
  --dry-run
```

The expected result for the current candidate set is 20 questions and 60
planned requests. Dry-run performs zero provider calls.

## Run a real three-model smoke annotation

Export the API key without printing or writing it to reports. Use a fresh
artifact directory outside the repository:

```bash
export OPENROUTER_API_KEY='set-this-in-your-shell'

SMOKE_DIR="$HOME/Downloads/answer-quality-panel-smoke"

.venv-evaluation/bin/python -m evaluation.answer_quality.panel \
  --dataset data/evaluation/answer-quality-v1/candidate_questions.json \
  --models "$MODEL_A" "$MODEL_B" "$MODEL_C" \
  --case-id labor_candidate_046 \
  --workers 3 \
  --output-dir "$SMOKE_DIR"
```

This makes three real requests. Inspect `manifest.json`, the three files under
`records/labor_candidate_046/`, and `consensus-report.json`. Authorization
headers and the API key are never persisted.

## Run the full panel

Only after the smoke run succeeds, use a separate output directory:

```bash
PANEL_DIR="$HOME/Downloads/answer-quality-panel-full"

.venv-evaluation/bin/python -m evaluation.answer_quality.panel \
  --dataset data/evaluation/answer-quality-v1/candidate_questions.json \
  --models "$MODEL_A" "$MODEL_B" "$MODEL_C" \
  --workers 3 \
  --output-dir "$PANEL_DIR"
```

The runner stores one atomic record per case/model. Interrupted runs can be
continued only with the same dataset, cases and model list:

```bash
.venv-evaluation/bin/python -m evaluation.answer_quality.panel \
  --dataset data/evaluation/answer-quality-v1/candidate_questions.json \
  --models "$MODEL_A" "$MODEL_B" "$MODEL_C" \
  --workers 3 \
  --output-dir "$PANEL_DIR" \
  --resume
```

Provider failures, missing records, reference violations, or non-identical
claim sets produce `needs_human_adjudication`. Even exact model agreement is
only `multi_llm_candidate`; authority review remains `pending` and
`golden_locked` remains `false`.

## Apply the reviewed multi-model candidate

The external adjudication artifact is tracked at:

```text
data/evaluation/answer-quality-v1/adjudications/
multi_llm_adjudicated_20.json
```

Apply it deterministically to the draft questions:

```bash
.venv-evaluation/bin/python -m \
  evaluation.answer_quality.apply_adjudication

.venv-evaluation/bin/python -m \
  evaluation.answer_quality.apply_adjudication --check

.venv-evaluation/bin/python -m \
  evaluation.answer_quality.validate_dataset \
  data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json
```

The generated dataset has `dataset_status=multi_llm_reviewed`, keeps
`benchmark_enabled=false` for every case, has no named human reviewer, and
remains unlocked with authority review pending. Applying the artifact therefore
does not turn model-produced labels into a golden benchmark.

Before an answer-quality benchmark can run, a human reviewer must verify every
claim against the bound canonical chunks, record their name and notes, and
explicitly move each accepted case to `label_status=human_adjudicated`.

## Provider references

The adapter follows the current official OpenRouter contracts for
`POST /api/v1/chat/completions`, strict `response_format=json_schema`,
`provider.require_parameters=true`, and live model discovery:

- <https://openrouter.ai/docs/api_reference/overview>
- <https://openrouter.ai/docs/guides/features/structured-outputs>
- <https://openrouter.ai/docs/guides/overview/models>

Install and run the isolated test suite with:

```bash
python3 -m venv .venv-evaluation
.venv-evaluation/bin/python -m pip install \
  -r evaluation/requirements.txt \
  -c evaluation/requirements-lock.txt
.venv-evaluation/bin/python -m pytest -q evaluation/tests
```

## Next stage

The next stage adds reviewed out-of-scope, insufficient-evidence and
false-premise candidates, then provides a human adjudication sheet. Legal
labels remain pending until that adjudication is completed.
