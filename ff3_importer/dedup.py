"""Duplicate detection helpers for local and remote transaction history."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from .app_paths import HISTORY_FINGERPRINTS_FILE
from .json_store import read_json, write_json
from .models import TransactionRecord


def _normalize_text(value: str) -> str:
    """Internal helper for normalize text."""
    return " ".join(value.lower().strip().split())


def transaction_fingerprint(
    date: str,
    amount: str,
    description: str,
    counterparty: str,
) -> str:
    """Handle transaction fingerprint."""
    base = "|".join(
        [
            _normalize_text(date),
            _normalize_text(amount),
            _normalize_text(description),
            _normalize_text(counterparty),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def fingerprint_for_record(record: TransactionRecord) -> str:
    """Handle fingerprint for record."""
    counterparty = record.payee or record.destination_account or record.source_account
    return transaction_fingerprint(
        date=record.date,
        amount=record.amount,
        description=record.description,
        counterparty=counterparty,
    )


def load_local_history() -> dict[str, list[str]]:
    """Load local history."""
    return read_json(HISTORY_FINGERPRINTS_FILE, default={})


def save_local_history(history: dict[str, list[str]]) -> None:
    """Save local history."""
    write_json(HISTORY_FINGERPRINTS_FILE, history)


def append_history(profile_name: str, records: list[TransactionRecord]) -> None:
    """Append history."""
    history = load_local_history()
    entries = set(history.get(profile_name, []))
    for record in records:
        if record.status not in {"approved", "submitted"}:
            continue
        entries.add(fingerprint_for_record(record))
    history[profile_name] = sorted(entries)
    save_local_history(history)


def _extract_remote_fingerprints(response_rows: list[dict[str, Any]]) -> set[str]:
    """Internal helper for extract remote fingerprints."""
    fingerprints: set[str] = set()
    for entry in response_rows:
        attributes = entry.get("attributes", {})
        transactions = attributes.get("transactions", [])
        for tx in transactions:
            date = str(tx.get("date", ""))[:10]
            amount = str(tx.get("amount", ""))
            description = str(tx.get("description", ""))
            counterparty = str(tx.get("destination_name") or tx.get("source_name") or "")
            fingerprints.add(
                transaction_fingerprint(
                    date=date,
                    amount=amount,
                    description=description,
                    counterparty=counterparty,
                )
            )
    return fingerprints


def fetch_remote_fingerprints(
    firefly_client: Any,
    records: list[TransactionRecord],
) -> set[str]:
    """Handle fetch remote fingerprints."""
    if not records:
        return set()
    dates = [record.date for record in records if record.date]
    if not dates:
        return set()

    start = min(dates)
    end = max(dates)
    response_rows = firefly_client.list_transactions(start=start, end=end)
    return _extract_remote_fingerprints(response_rows)


def mark_duplicate_warnings(
    records: list[TransactionRecord],
    profile_name: str,
    remote_fingerprints: set[str] | None = None,
) -> list[TransactionRecord]:
    """Mark duplicate warnings."""
    remote_fingerprints = remote_fingerprints or set()
    history = load_local_history()
    local_fingerprints = set(history.get(profile_name, []))

    for record in records:
        fp = fingerprint_for_record(record)
        duplicate_sources: list[str] = []
        if fp in local_fingerprints:
            duplicate_sources.append("local_history")
        if fp in remote_fingerprints:
            duplicate_sources.append("firefly_api")
        if duplicate_sources:
            warning = "Potential duplicate detected (" + ", ".join(duplicate_sources) + ")."
            if warning not in record.warnings:
                record.warnings.append(warning)
    return records


def utc_today() -> str:
    """Return today."""
    return datetime.utcnow().strftime("%Y-%m-%d")
