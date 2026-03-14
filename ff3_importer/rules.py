"""Reusable rule suggestion and persistence helpers."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .models import TransactionRecord


def _utc_now() -> str:
    """Internal helper for utc now."""
    return datetime.now(timezone.utc).isoformat()


def _record_value(record: TransactionRecord, key: str) -> str:
    """Internal helper for record value."""
    if key in record.raw:
        return str(record.raw.get(key, ""))
    if hasattr(record, key):
        value = getattr(record, key)
        if isinstance(value, list):
            return ",".join(str(item) for item in value)
        return str(value)
    return ""


def _exact_match_score(rule: dict[str, Any], record: TransactionRecord) -> float:
    """Internal helper for exact match score."""
    score = 0.0
    exact = rule.get("match_exact", {})
    for key, expected in exact.items():
        actual = _record_value(record, key)
        if str(actual).strip().lower() == str(expected).strip().lower():
            score += 20.0
        else:
            return 0.0

    regex_rules = rule.get("match_regex", {})
    for key, pattern in regex_rules.items():
        actual = _record_value(record, key)
        if not re.search(pattern, actual, flags=re.IGNORECASE):
            return 0.0
        score += 12.0

    score += float(rule.get("confidence", 0.0)) * 10.0
    score += min(10.0, float(rule.get("accepted_count", 0)) * 0.3)
    score += max(0.0, 5.0 - float(rule.get("overridden_count", 0)) * 0.5)
    return score


def rank_rule_suggestions(record: TransactionRecord, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank rule suggestions."""
    ranked: list[dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        score = _exact_match_score(rule, record)
        if score <= 0:
            continue
        ranked.append(
            {
                "rule_id": rule.get("rule_id", ""),
                "score": round(score, 2),
                "set_fields": dict(rule.get("set_fields", {})),
                "priority": int(rule.get("priority", 100)),
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["priority"]))
    return ranked


def attach_rule_suggestions(
    records: list[TransactionRecord], rules: list[dict[str, Any]]
) -> list[TransactionRecord]:
    """Attach rule suggestions."""
    for record in records:
        record.rule_suggestions = rank_rule_suggestions(record, rules)
    return records


def apply_suggestion(record: TransactionRecord, suggestion: dict[str, Any]) -> None:
    """Apply suggestion."""
    for field_name, value in suggestion.get("set_fields", {}).items():
        if hasattr(record, field_name):
            setattr(record, field_name, value)


def create_rule_from_record(
    record: TransactionRecord,
    set_fields: dict[str, str],
    regex_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Create rule from record."""
    regex_fields = regex_fields or []
    match_exact: dict[str, str] = {}
    match_regex: dict[str, str] = {}

    if record.description:
        if "description" in regex_fields:
            match_regex["description"] = re.escape(record.description)
        else:
            match_exact["description"] = record.description

    for key in ("payee", "source_account", "destination_account"):
        value = _record_value(record, key)
        if value:
            if key in regex_fields:
                match_regex[key] = re.escape(value)
            else:
                match_exact[key] = value

    return {
        "rule_id": str(uuid.uuid4()),
        "enabled": True,
        "priority": 100,
        "match_exact": match_exact,
        "match_regex": match_regex,
        "set_fields": set_fields,
        "confidence": 0.5,
        "uses": 0,
        "accepted_count": 0,
        "overridden_count": 0,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def mark_rule_feedback(
    rules: list[dict[str, Any]], rule_id: str, accepted: bool
) -> list[dict[str, Any]]:
    """Mark rule feedback."""
    for rule in rules:
        if rule.get("rule_id") != rule_id:
            continue
        rule["uses"] = int(rule.get("uses", 0)) + 1
        if accepted:
            rule["accepted_count"] = int(rule.get("accepted_count", 0)) + 1
        else:
            rule["overridden_count"] = int(rule.get("overridden_count", 0)) + 1
        uses = max(1, int(rule.get("uses", 1)))
        confidence = int(rule.get("accepted_count", 0)) / uses
        rule["confidence"] = round(confidence, 3)
        rule["updated_at"] = _utc_now()
        break
    return rules
