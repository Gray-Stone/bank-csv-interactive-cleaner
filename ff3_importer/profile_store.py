"""Profile persistence and auto-detection helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .app_paths import PROFILES_DIR
from .json_store import read_json, write_json
from .models import Profile


def _normalize_header(value: str) -> str:
    """Internal helper for normalize header."""
    return re.sub(r"\s+", " ", value.strip().lower())


def build_header_signature(headers: list[str], delimiter: str) -> dict[str, Any]:
    """Build header signature."""
    normalized = [_normalize_header(item) for item in headers if item.strip()]
    return {
        "delimiter": delimiter,
        "headers_normalized": normalized,
        "column_count": len(headers),
    }


def _score_signature(lhs: dict[str, Any], rhs: dict[str, Any]) -> float:
    """Internal helper for score signature."""
    score = 0.0
    if lhs.get("delimiter") == rhs.get("delimiter"):
        score += 40.0

    lhs_headers = set(lhs.get("headers_normalized", []))
    rhs_headers = set(rhs.get("headers_normalized", []))
    if lhs_headers or rhs_headers:
        union = lhs_headers | rhs_headers
        intersection = lhs_headers & rhs_headers
        score += 30.0 * (len(intersection) / len(union))

    lhs_count = int(lhs.get("column_count", 0))
    rhs_count = int(rhs.get("column_count", 0))
    if lhs_count and rhs_count:
        if lhs_count == rhs_count:
            score += 30.0
        else:
            delta = abs(lhs_count - rhs_count)
            score += max(0.0, 30.0 - min(30.0, float(delta * 6)))
    return score


@dataclass
class ProfileMatchResult:
    """Describe the outcome of matching an input file to a stored profile."""
    selected: str | None
    confident: bool
    ranked: list[tuple[str, float]]


class ProfileStore:
    """Persist and retrieve reusable import profiles."""
    def __init__(self, profiles_dir: Path = PROFILES_DIR) -> None:
        """Initialize the instance."""
        self.profiles_dir = profiles_dir

    def list_names(self) -> list[str]:
        """Return the stored profile names."""
        if not self.profiles_dir.exists():
            return []
        return sorted(path.stem for path in self.profiles_dir.glob("*.json"))

    def load(self, name: str) -> Profile | None:
        """Load a stored object from disk."""
        path = self.profiles_dir / f"{name}.json"
        payload = read_json(path, default=None)
        if payload is None:
            return None
        return Profile.from_dict(payload)

    def save(self, profile: Profile) -> None:
        """Persist the object to disk."""
        path = self.profiles_dir / f"{profile.name}.json"
        write_json(path, profile.to_dict())

    def match_from_signature(
        self, signature: dict[str, Any], threshold: float = 70.0, tie_delta: float = 5.0
    ) -> ProfileMatchResult:
        """Match from signature."""
        ranked: list[tuple[str, float]] = []
        for profile_name in self.list_names():
            profile = self.load(profile_name)
            if profile is None:
                continue
            profile_signature = profile.parse_hints.get("header_signature", {})
            if not profile_signature:
                continue
            score = _score_signature(signature, profile_signature)
            ranked.append((profile_name, round(score, 2)))

        ranked.sort(key=lambda item: item[1], reverse=True)
        if not ranked:
            return ProfileMatchResult(selected=None, confident=False, ranked=ranked)

        best_name, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else -1
        confident = best_score >= threshold and (best_score - second_score) > tie_delta
        return ProfileMatchResult(selected=best_name if confident else None, confident=confident, ranked=ranked)
