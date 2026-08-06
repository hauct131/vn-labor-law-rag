# VBPL production ingestion runbook

## Production contract

A document is publishable only when all of these conditions hold:

1. The exact normalized document number matches the requested number.
2. The item ID in the API payload agrees with the detail-page ID.
3. Full text is non-empty and the expected article sequence is contiguous.
4. The original API response is persisted byte-for-byte.
5. Every published file is bound by `SHA256SUMS.txt`.
6. Resume re-verifies all checksums; a corrupted snapshot is never reused.
7. Snapshot publication is atomic. Failed work remains only in `_diagnostics`.
8. Attachment requirements follow an explicit policy and never pass because an empty list makes `all([])` true.
9. A per-document lock prevents concurrent writers.
10. A batch writes `run_manifest.json` after every document and can resume safely.

The crawler does not bypass CAPTCHA and never persists browser Bearer tokens.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-corpus.txt
python -m playwright install --with-deps chromium
```

## Local and CI tests

```bash
make test-ingestion
python -m py_compile scripts/vbpl_portal.py
```

## Environment doctor

```bash
make vbpl-doctor
```

The sitemap and direct gateway checks must pass. Playwright may remain unused when the public gateway works, but Chromium must be installed for fallback and strict attachment discovery.

## Canary text ingestion

```bash
make vbpl-canary
```

The canary deliberately uses `best_effort`: it proves the full-text path and records unresolved official files as warnings. It does not pretend that missing attachment bytes were downloaded.

Verify the resulting snapshot:

```bash
SNAPSHOT=$(find data/raw/vbpl/219_2025_nd_cp -mindepth 1 -maxdepth 1 -type d ! -name '.tmp-*' | sort | tail -n 1)
python scripts/vbpl_portal.py verify "$SNAPSHOT" --document-number '219/2025/NĐ-CP'
(cd "$SNAPSHOT" && sha256sum -c SHA256SUMS.txt)
```

## Attachment policies

- `ignore`: do not inspect or download files.
- `best_effort`: publish verified text and report every unresolved or failed file.
- `required_if_listed`: if the API exposes file metadata, every listed file must resolve and download.
- `required`: at least one file must exist and all files must resolve, download, and pass snapshot gates.

Documents whose legal scope depends on appendices use strict policies in `config/vbpl_corpus.json`. A strict failure is expected and safe until the official file endpoint is resolved.

## Live soak gate

```bash
make vbpl-soak
```

The default gate performs ten live reads of Nghị định 219/2025/NĐ-CP. It requires:

- zero failed iterations;
- exact identity on every iteration;
- 36 contiguous articles every time;
- one stable full-text SHA-256 hash;
- a report at `data/raw/vbpl/soak_219.json`.

Run the soak test on at least three separate time windows before declaring the source stable. For a scheduled production job, retain the reports as deployment evidence.

## Batch ingestion

```bash
make vbpl-fetch
```

Properties:

- sitemap is cached once per batch;
- requests use retry, exponential backoff, jitter, and `Retry-After`;
- one reusable browser context is used only when direct gateway access is insufficient;
- each document has its own lock and immutable snapshot;
- a failure does not erase previous progress;
- the process exits non-zero if any document fails;
- `data/raw/vbpl/run_manifest.json` lists created, unchanged, skipped, and failed documents.

Inspect failures:

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path('data/raw/vbpl/run_manifest.json')
r = json.loads(p.read_text(encoding='utf-8'))
for item in r['results']:
    if item['status'] == 'failed':
        print(item['document_number'], item['error'])
PY
```

Diagnostics are written under `data/raw/vbpl/_diagnostics/<document>/...` and include rendered HTML plus every captured raw response that was safe to persist.

## Promotion rule

Do not let a successful crawl directly replace the active chatbot corpus. Promotion remains:

```text
raw immutable snapshot
→ verify checksums and gates
→ build candidate
→ legal-effect review
→ explicit approval
→ rebuild all indexes
→ golden regression
→ switch active collection alias
```

## Demo rule

Never put live VBPL crawling on the chatbot request path. Before the defense:

1. crawl and approve snapshots;
2. build the active corpus and indexes;
3. archive the run and soak manifests;
4. run the chatbot entirely from local/Qdrant data.

The live crawler may be demonstrated separately as an ingestion feature. If VBPL or the network is unavailable, the chatbot demo must continue normally.
