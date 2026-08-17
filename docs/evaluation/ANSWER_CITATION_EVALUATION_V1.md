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

It does not yet call an LLM or create legal labels.

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

Install and run the isolated test suite with:

```bash
python3 -m venv .venv-evaluation
.venv-evaluation/bin/python -m pip install \
  -r evaluation/requirements.txt
.venv-evaluation/bin/python -m pytest -q evaluation/tests
```

## Next stage

The next stage migrates the existing 20 held-out candidates without inventing
missing claims, adds negative categories, and creates independent multi-LLM
annotation files. Legal labels remain pending until human adjudication.
