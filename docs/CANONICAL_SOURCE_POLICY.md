# Canonical source policy

The corpus acquisition policy is `official-gazette-word-then-vbpl-v1`:

1. Fetch the exact document page from `congbao.chinhphu.vn`.
2. Select every exact Word part in page order. Prefer DOCX; otherwise retain
   the original DOC and normalize it to DOCX with headless LibreOffice.
3. Record the page, original files, normalized files, URLs, retrieval time and
   SHA-256 hashes in an immutable snapshot.
4. Use VBPL only when the configured Gazette record is not found or the exact
   page exposes no Word attachment.
5. Never fall back after an identity mismatch, metadata conflict, corrupt
   attachment, checksum failure, network error or local conversion failure.

`10/2020/TT-BLĐTBXH` is the explicit current exception: its Gazette portal
record is marked `not_found`, so item `146696` from VBPL is canonical for that
document. The remaining 17 documents prefer Gazette Word files.

## Run

LibreOffice is required because older Gazette records publish `.doc` files:

```bash
sudo apt-get install -y libreoffice
make canonical-source-fetch
```

The command writes:

```text
data/raw/canonical_sources/run_manifest.json
data/raw/official_docx/<document>/<snapshot>/
data/raw/vbpl/<fallback-document>/<snapshot>/
data/sources/official_docx/
```

A passing run must report:

```json
{
  "documents_requested": 18,
  "documents_succeeded": 18,
  "documents_failed": 0,
  "official_gazette_documents": 17,
  "vbpl_fallback_documents": 1,
  "one_canonical_snapshot_per_document": true,
  "production_promotion_performed": false,
  "status": "PASS"
}
```

This acquisition command intentionally stops before release building, Qdrant
indexing and alias promotion. Those remain separate reviewed gates.
