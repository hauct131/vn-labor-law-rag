"""Command-line interface for the independent annotation panel."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evaluation.answer_quality.panel import (
    PanelConfigurationError,
    dry_run,
    run_panel,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-tokens", type=int, default=2400)
    parser.add_argument(
        "--base-url",
        default="https://openrouter.ai/api/v1",
    )
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--app-url", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.dry_run:
            report = dry_run(
                dataset_path=args.dataset,
                models=args.models,
                repo_root=args.repo_root,
                case_ids=args.case_ids,
            )
        else:
            if args.output_dir is None:
                raise PanelConfigurationError(
                    "--output-dir is required unless --dry-run is used"
                )
            report = run_panel(
                dataset_path=args.dataset,
                output_dir=args.output_dir,
                models=args.models,
                repo_root=args.repo_root,
                api_key=os.environ.get(args.api_key_env, ""),
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
                max_tokens=args.max_tokens,
                workers=args.workers,
                case_ids=args.case_ids,
                resume=args.resume,
                retry_errors=args.retry_errors,
                app_url=args.app_url,
            )
    except (PanelConfigurationError, ValueError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
