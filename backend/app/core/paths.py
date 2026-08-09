"""Stable project-root path resolution for runtime files."""

from __future__ import annotations

import os
from pathlib import Path


def _configured_project_root() -> Path:
    """Use an explicit container root, with repository layout as local fallback."""
    configured = os.getenv("APP_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


PROJECT_ROOT = _configured_project_root()


def resolve_project_path(value: str | Path) -> Path:
    """Resolve relative runtime paths from the repository root."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()
