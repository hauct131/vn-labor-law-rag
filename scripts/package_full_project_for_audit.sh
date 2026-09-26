#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

PROJECT="$(basename "$ROOT")"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$ROOT/../${PROJECT}-full-audit-${TS}"
STAGE="$OUT/project"
AUDIT="$OUT/audit"
ARCHIVE="$OUT/${PROJECT}-full-audit-${TS}.tar.gz"

mkdir -p "$STAGE" "$AUDIT"

echo "============================================================"
echo "VN LABOR LAW RAG - FULL SOURCE AUDIT PACKAGE"
echo "============================================================"
echo "Root    : $ROOT"
echo "Output  : $OUT"
echo
echo "Không sửa/xóa file trong repo gốc."
echo "Giữ data/experiment/golden/benchmark để audit."
echo "Chỉ loại secret, dependency, cache và .git."
echo

# ------------------------------------------------------------
# 1. GIT SNAPSHOT
# ------------------------------------------------------------

{
    echo "=== ROOT ==="
    echo "$ROOT"

    echo
    echo "=== BRANCH ==="
    git branch --show-current 2>/dev/null || true

    echo
    echo "=== HEAD ==="
    git rev-parse HEAD 2>/dev/null || true

    echo
    echo "=== STATUS ==="
    git status --short --branch 2>/dev/null || true

    echo
    echo "=== DIFF NAME STATUS ==="
    git diff --name-status 2>/dev/null || true

    echo
    echo "=== CACHED DIFF NAME STATUS ==="
    git diff --cached --name-status 2>/dev/null || true

    echo
    echo "=== DIFF STAT ==="
    git diff --stat 2>/dev/null || true

    echo
    echo "=== LAST 20 COMMITS ==="
    git log -20 \
      --date=short \
      --pretty=format:'%h %ad %an <%ae> %s' \
      2>/dev/null || true
} > "$AUDIT/git_state.txt"

git ls-files > "$AUDIT/tracked_files.txt" 2>/dev/null || true
git ls-files --others --exclude-standard \
  > "$AUDIT/untracked_files.txt" 2>/dev/null || true
git ls-files --others --ignored --exclude-standard \
  > "$AUDIT/ignored_files.txt" 2>/dev/null || true

# ------------------------------------------------------------
# 2. PROJECT TREE
# ------------------------------------------------------------

if command -v tree >/dev/null 2>&1; then
    tree -a \
      -I '.git|.venv|venv|node_modules|__pycache__|.pytest_cache|.mypy_cache|.ruff_cache|dist|build|htmlcov' \
      > "$AUDIT/tree.txt"
else
    find . -print \
      | sed 's#^\./##' \
      | sort \
      > "$AUDIT/tree.txt"
fi

# ------------------------------------------------------------
# 3. FILES THAT MUST NEVER BE COPIED
# ------------------------------------------------------------

is_secret_file() {
    local rel="$1"
    local base
    base="$(basename "$rel")"
    local lower
    lower="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"

    case "$lower" in
        .env.example|.env.sample|env.example|env.sample)
            return 1
            ;;
    esac

    case "$lower" in
        .env|.env.*)
            return 0
            ;;
        *.pem|*.key|*.p12|*.pfx|*.jks|*.keystore)
            return 0
            ;;
        id_rsa|id_rsa.*|id_ed25519|id_ed25519.*)
            return 0
            ;;
        credentials.json|credential.json|secrets.json|secret.json)
            return 0
            ;;
        service-account.json|service_account.json)
            return 0
            ;;
    esac

    return 1
}

is_generated_or_dependency() {
    local rel="$1"

    case "$rel" in
        .git|.git/*)
            return 0
            ;;

        .venv|.venv/*|venv|venv/*)
            return 0
            ;;

        node_modules|node_modules/*|*/node_modules|*/node_modules/*)
            return 0
            ;;

        __pycache__|__pycache__/*|*/__pycache__|*/__pycache__/*)
            return 0
            ;;

        .pytest_cache|.pytest_cache/*|*/.pytest_cache|*/.pytest_cache/*)
            return 0
            ;;

        .mypy_cache|.mypy_cache/*|*/.mypy_cache|*/.mypy_cache/*)
            return 0
            ;;

        .ruff_cache|.ruff_cache/*|*/.ruff_cache|*/.ruff_cache/*)
            return 0
            ;;

        htmlcov|htmlcov/*|*/htmlcov|*/htmlcov/*)
            return 0
            ;;

        dist|dist/*|*/dist|*/dist/*)
            return 0
            ;;

        build|build/*|*/build|*/build/*)
            return 0
            ;;

        frontend/dist|frontend/dist/*)
            return 0
            ;;

        frontend/.vite|frontend/.vite/*)
            return 0
            ;;

        *.pyc)
            return 0
            ;;
    esac

    return 1
}

# ------------------------------------------------------------
# 4. FULL INVENTORY
# ------------------------------------------------------------

printf "size_bytes\tpath\n" > "$AUDIT/all_files.tsv"
printf "sha256\tsize_bytes\tpath\n" > "$AUDIT/included_manifest.tsv"
printf "reason\tpath\n" > "$AUDIT/excluded.tsv"

: > "$AUDIT/included_files.txt"

while IFS= read -r -d '' file; do
    rel="${file#"$ROOT"/}"
    size="$(stat -c '%s' "$file" 2>/dev/null || echo 0)"

    printf "%s\t%s\n" "$size" "$rel" >> "$AUDIT/all_files.tsv"

    if is_generated_or_dependency "$rel"; then
        printf "generated_or_dependency\t%s\n" "$rel" \
          >> "$AUDIT/excluded.tsv"
        continue
    fi

    if is_secret_file "$rel"; then
        printf "secret_file\t%s\n" "$rel" \
          >> "$AUDIT/excluded.tsv"
        continue
    fi

    printf "%s\n" "$rel" >> "$AUDIT/included_files.txt"

    sha="$(sha256sum "$file" | awk '{print $1}')"
    printf "%s\t%s\t%s\n" "$sha" "$size" "$rel" \
      >> "$AUDIT/included_manifest.tsv"

done < <(find "$ROOT" -type f -print0)

# ------------------------------------------------------------
# 5. IMPORTANT PROJECT-SPECIFIC INVENTORIES
# ------------------------------------------------------------

find . -type f \
  | sed 's#^\./##' \
  | grep -Ei \
    '(^|/)(data|corpus|golden|eval|evaluation|experiments?|benchmarks?|results?|runtime-runs|artifacts?|legacy|reports?)/|sensitivity|ablation|rerank|cross.encoder|candidate.k|rrf|answer.quality|contract.review' \
  | sort \
  > "$AUDIT/data_eval_experiment_files.txt" || true

find . -type f \
  | sed 's#^\./##' \
  | grep -Ei \
    '(^|/)(test|tests)/|(^|/)(test_.*\.py|.*_test\.py)$' \
  | sort \
  > "$AUDIT/test_files.txt" || true

find . -type f \
  \( \
      -name '*.py' -o \
      -name '*.js' -o \
      -name '*.jsx' -o \
      -name '*.ts' -o \
      -name '*.tsx' -o \
      -name '*.css' -o \
      -name '*.html' -o \
      -name '*.sh' \
  \) \
  | sed 's#^\./##' \
  | sort \
  > "$AUDIT/source_files.txt"

find . -type f \
  \( \
      -name 'Dockerfile*' -o \
      -name 'docker-compose*.yml' -o \
      -name 'docker-compose*.yaml' -o \
      -name 'compose*.yml' -o \
      -name 'compose*.yaml' -o \
      -name 'requirements*.txt' -o \
      -name 'pyproject.toml' -o \
      -name 'package.json' -o \
      -name 'package-lock.json' -o \
      -name 'README*' -o \
      -name '.gitignore' -o \
      -name '.dockerignore' -o \
      -name '.env.example' -o \
      -name '.env.sample' \
  \) \
  | sed 's#^\./##' \
  | sort \
  > "$AUDIT/runtime_config_files.txt"

# Project scripts are particularly important for this repo.
find scripts -type f 2>/dev/null \
  | sort \
  > "$AUDIT/scripts_inventory.txt" || true

# ------------------------------------------------------------
# 6. LARGE FILES
# ------------------------------------------------------------

{
    printf "size_bytes\tsize_mb\tpath\n"

    awk -F '\t' 'NR > 1 {
        printf "%s\t%.2f\t%s\n", $1, $1/1024/1024, $2
    }' "$AUDIT/all_files.tsv" \
      | sort -nr -k1,1 \
      | sed -n '1,150p'
} > "$AUDIT/largest_files.tsv"

# ------------------------------------------------------------
# 7. POSSIBLE SECRET SCAN
#
# IMPORTANT:
# Only file + line + category.
# Never print the matched value.
# ------------------------------------------------------------

python3 - "$ROOT" "$AUDIT/possible_secrets.tsv" <<'PY'
import os
import re
import sys

root, output = sys.argv[1], sys.argv[2]

skip_dirs = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "htmlcov",
}

binary_ext = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".docx", ".xlsx", ".pptx",
    ".zip", ".tar", ".gz", ".7z",
    ".woff", ".woff2", ".ttf", ".otf",
    ".pyc", ".so", ".dll", ".exe",
}

patterns = [
    ("openrouter_or_openai_key",
     re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{20,}\b|\bsk-[A-Za-z0-9_-]{20,}\b")),

    ("github_token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),

    ("aws_access_key",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),

    ("private_key_header",
     re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY")),

    ("possible_secret_assignment",
     re.compile(
        r"""(?ix)
        \b(
            api[_-]?key|
            secret[_-]?key|
            access[_-]?token|
            auth[_-]?token|
            client[_-]?secret|
            password
        )
        \b
        \s*[:=]\s*
        ["']?
        [^\s"'#]{12,}
        """
     )),
]

hits = []

for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [
        d for d in dirnames
        if d not in skip_dirs
    ]

    for name in filenames:
        path = os.path.join(dirpath, name)
        rel = os.path.relpath(path, root)

        # Never inspect the real .env.
        if name == ".env" or name.startswith(".env."):
            continue

        if os.path.splitext(name)[1].lower() in binary_ext:
            continue

        try:
            if os.path.getsize(path) > 15 * 1024 * 1024:
                continue
        except OSError:
            continue

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    for label, pattern in patterns:
                        if pattern.search(line):
                            hits.append((rel, lineno, label))
        except Exception:
            pass

with open(output, "w", encoding="utf-8") as f:
    f.write("path\tline\tcategory\n")
    for item in sorted(set(hits)):
        f.write(f"{item[0]}\t{item[1]}\t{item[2]}\n")
PY

# ------------------------------------------------------------
# 8. DEBUG / PATCH / TEMP FILES
# ------------------------------------------------------------

find . -type f \
  | sed 's#^\./##' \
  | grep -Ei \
    '\.patch$|\.bak$|~$|(^|/)(tmp|temp|debug|scratch|backup|draft|old)(/|_|-)|context\.txt$|raw.responses?' \
  | sort \
  > "$AUDIT/debug_temp_patch_files.txt" || true

# ------------------------------------------------------------
# 9. TODO / FIXME
# ------------------------------------------------------------

grep -RInE \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=venv \
  --exclude-dir=node_modules \
  --exclude-dir=__pycache__ \
  --exclude-dir=.pytest_cache \
  --exclude='*.min.js' \
  --exclude='*.map' \
  '\b(TODO|FIXME|HACK|XXX)\b' \
  . \
  > "$AUDIT/todo_fixme.txt" 2>/dev/null || true

# ------------------------------------------------------------
# 10. COPY SAFE PROJECT
# ------------------------------------------------------------

echo "[INFO] Copying project to staging..."

while IFS= read -r rel; do
    [ -z "$rel" ] && continue

    src="$ROOT/$rel"
    dst="$STAGE/$rel"

    mkdir -p "$(dirname "$dst")"
    cp -a "$src" "$dst"
done < "$AUDIT/included_files.txt"

# Audit information is useful to me, so include it.
mkdir -p "$STAGE/_SOURCE_AUDIT"
cp -a "$AUDIT/." "$STAGE/_SOURCE_AUDIT/"

# ------------------------------------------------------------
# 11. ARCHIVE
# ------------------------------------------------------------

echo "[INFO] Creating archive..."

tar -C "$STAGE" -czf "$ARCHIVE" .

sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"

# ------------------------------------------------------------
# 12. SUMMARY
# ------------------------------------------------------------

TOTAL="$(tail -n +2 "$AUDIT/all_files.tsv" | wc -l)"
INCLUDED="$(wc -l < "$AUDIT/included_files.txt")"
EXCLUDED="$(tail -n +2 "$AUDIT/excluded.tsv" | wc -l)"
SECRET_HITS="$(tail -n +2 "$AUDIT/possible_secrets.tsv" | wc -l)"
SIZE="$(du -h "$ARCHIVE" | awk '{print $1}')"

{
    echo "VN LABOR LAW RAG AUDIT BUNDLE"
    echo
    echo "Project       : $PROJECT"
    echo "Root          : $ROOT"
    echo "Total files   : $TOTAL"
    echo "Included      : $INCLUDED"
    echo "Excluded      : $EXCLUDED"
    echo "Secret hits   : $SECRET_HITS"
    echo "Archive size  : $SIZE"
    echo
    echo "Archive:"
    echo "$ARCHIVE"
    echo
    echo "SHA256:"
    cat "$ARCHIVE.sha256"
} | tee "$AUDIT/SUMMARY.txt"

echo
echo "============================================================"
echo "DONE"
echo "============================================================"
echo
echo "Review first:"
echo "$AUDIT/possible_secrets.tsv"
echo "$AUDIT/excluded.tsv"
echo
echo "Then upload:"
echo "$ARCHIVE"
echo "$ARCHIVE.sha256"
