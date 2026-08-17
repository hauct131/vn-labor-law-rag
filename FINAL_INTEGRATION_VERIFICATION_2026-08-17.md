# Final integration verification — 2026-08-17

## Scope

This branch integrates these three delivered lines:

- `integrate/canonical-word-804-e2e` at `3d6a0a5`;
- `feat/contract-review-v1` at `b2994a5`;
- `fix/windows-canonical-checksums` at `740de47`.

The normal merge produced 23 conflicts because the first two branches added
overlapping implementations on parallel histories. A direct final-tree
comparison showed that the Contract Review tree had 76 additional files, 26
modified files and no deleted files relative to the 804 integration tree.
Therefore the Contract Review tree was retained as the content base, the 804
integration history was recorded with an `ours` merge, and the checksum fix was
applied afterward.

## Corrections made during final integration

- Bound active Make targets and CLI defaults to canonical Word 804:
  - release ID `labor-law-canonical-word-20260804-164432-candidate`;
  - 804 chunks;
  - SHA-256 `fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307`.
- Separated the historical 833-chunk DOCX builder variable from active runtime
  variables so Make no longer silently overrides the 804 binding.
- Changed the golden split default to the authoritative
  `data/evaluation/splits/canonical_word_804` directory.
- Blocked locked-test reruns and post-test tuning from active Make targets.
- Added checksum verification for the release, golden splits and locked
  benchmark artifacts.
- Guarded the retired 833 runtime script behind explicit historical opt-in.
- Added a single final verification runner and a Playwright browser E2E for
  real DOCX/PDF upload, persistence, deletion, logout and error monitoring.
- Updated three vulnerable transitive frontend packages through the lockfile:
  `brace-expansion`, `nanoid` and `postcss`.

## Checks executed in the integration workspace

```text
Canonical release SHA256 inventory: PASS
Canonical golden split SHA256 inventory: PASS
Canonical locked benchmark SHA256 inventory: PASS
Golden split reproducibility check: PASS (31 dev, 13 test, 1 disabled)
Golden-to-corpus audit: PASS (166/166 evidence references found)
Sparse/dense probe dry-run: PASS (804 chunks)
Runtime benchmark dry-run on dev: PASS
Python compile: PASS
Backend: 632 passed, 3 skipped, 1 dependency deprecation warning
Frontend npm ci: PASS
Frontend lint: PASS
Frontend production build: PASS
Frontend npm audit: 0 vulnerabilities
Shell syntax and git diff check: PASS
```

The three skipped backend tests require a real PostgreSQL integration URL.
The warning is emitted by the installed FastAPI/Starlette TestClient dependency
and is not an application test failure.

## Runtime evidence and remaining final gate

The source Contract Review commit previously passed Docker/PostgreSQL E2E
10/10 on the target Linux machine. Docker is unavailable in the integration
workspace used to create this branch, so the combined final commit must still
be run once on that Linux machine with:

```bash
.venv/bin/python -m pip install -r requirements-e2e.txt
.venv/bin/playwright install chromium
bash scripts/verify_final_release.sh
```

Only `FINAL VERIFICATION: PASS` from that combined-commit run closes the Docker,
Qdrant, PostgreSQL and browser gates. The locked test split is verified by
checksum and is intentionally not rerun.

## Non-technical approval boundary

Authority review and legal-effect review remain pending. This branch can be
declared technically verified after the target-machine gate passes, but it is
not a legally approved production release and must not be described as one.
