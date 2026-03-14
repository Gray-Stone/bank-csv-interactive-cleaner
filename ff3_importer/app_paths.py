"""Centralized project paths for runtime data."""
from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = PROJECT_ROOT / "runtime_data"
PROFILES_DIR = RUNTIME_ROOT / "profiles"
SESSIONS_DIR = RUNTIME_ROOT / "sessions"
RUNS_DIR = RUNTIME_ROOT / "runs"
HISTORY_DIR = RUNTIME_ROOT / "history"
HISTORY_FINGERPRINTS_FILE = HISTORY_DIR / "fingerprints.json"
CONFIG_FILE = RUNTIME_ROOT / "config.json"


def ensure_runtime_dirs() -> None:
    """Ensure runtime dirs."""
    for folder in (RUNTIME_ROOT, PROFILES_DIR, SESSIONS_DIR, RUNS_DIR, HISTORY_DIR):
        folder.mkdir(parents=True, exist_ok=True)
