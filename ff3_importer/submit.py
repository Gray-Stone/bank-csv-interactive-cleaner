"""Submission and rollback helpers for Firefly III transactions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .app_paths import RUNS_DIR
from .json_store import read_json, write_json
from .models import SessionState, TransactionRecord


def _utc_now() -> str:
    """Internal helper for utc now."""
    return datetime.now(timezone.utc).isoformat()


def _absolute_amount(value: str) -> str:
    """Internal helper for absolute amount."""
    raw = str(value).strip()
    try:
        dec = Decimal(raw)
    except InvalidOperation:
        return raw
    if dec < 0:
        dec = -dec
    return f"{dec:.2f}"


def _record_to_split_payload(record: TransactionRecord) -> dict[str, Any]:
    """Internal helper for record to split payload."""
    payload: dict[str, Any] = {
        "type": record.type or "withdrawal",
        "date": f"{record.date}T00:00:00+00:00" if len(record.date) == 10 else record.date,
        "amount": _absolute_amount(record.amount),
        "description": record.description,
    }
    optional_map = {
        "source_name": record.source_account,
        "destination_name": record.destination_account,
        "currency_code": record.currency,
        "category_name": record.category,
        "budget_name": record.budget,
        "notes": record.notes,
        "external_id": record.external_id,
        "internal_reference": record.internal_reference,
    }
    for key, value in optional_map.items():
        if value:
            payload[key] = value
    if record.tags:
        payload["tags"] = record.tags
    return payload


def _record_to_transaction_payload(record: TransactionRecord) -> dict[str, Any]:
    """Internal helper for record to transaction payload."""
    return {
        "apply_rules": False,
        "fire_webhooks": True,
        "transactions": [_record_to_split_payload(record)],
    }


def _run_report_path(run_id: str) -> Path:
    """Internal helper for run report path."""
    return RUNS_DIR / f"{run_id}.json"


@dataclass
class SubmitResult:
    """Summarize the outcome of a submit operation."""
    run_id: str
    dry_run: bool
    created_ids: list[str]
    failures: list[dict[str, Any]]
    report_path: Path


def submit_session(
    session: SessionState,
    firefly_client: Any | None,
    dry_run: bool = False,
) -> SubmitResult:
    """Handle submit session."""
    approved_records = [record for record in session.records if record.status == "approved"]
    report_rows: list[dict[str, Any]] = []
    created_ids: list[str] = []
    failures: list[dict[str, Any]] = []

    for record in approved_records:
        if any("Potential duplicate" in warning for warning in record.warnings) and not record.duplicate_override:
            failure = {
                "row_id": record.row_id,
                "error": "Duplicate warning requires explicit override before submit.",
            }
            failures.append(failure)
            report_rows.append(
                {
                    "row_id": record.row_id,
                    "status": "submit_failed",
                    "error": failure["error"],
                    "payload": _record_to_transaction_payload(record),
                }
            )
            break

        if (
            any(
                warning.startswith("New external counterparty confirmation required:")
                for warning in record.warnings
            )
            and not record.new_external_counterparty_override
        ):
            failure = {
                "row_id": record.row_id,
                "error": "New external counterparty requires explicit confirmation before submit.",
            }
            failures.append(failure)
            report_rows.append(
                {
                    "row_id": record.row_id,
                    "status": "submit_failed",
                    "error": failure["error"],
                    "payload": _record_to_transaction_payload(record),
                }
            )
            break

        payload = _record_to_transaction_payload(record)
        if dry_run:
            report_rows.append(
                {
                    "row_id": record.row_id,
                    "status": "dry_run_ready",
                    "payload": payload,
                }
            )
            continue

        if firefly_client is None:
            failure = {"row_id": record.row_id, "error": "Firefly client unavailable for live submit."}
            failures.append(failure)
            report_rows.append(
                {
                    "row_id": record.row_id,
                    "status": "submit_failed",
                    "error": failure["error"],
                    "payload": payload,
                }
            )
            break

        try:
            response = firefly_client.create_transaction(payload)
            transaction_id = str(response.get("data", {}).get("id", ""))
            created_ids.append(transaction_id)
            record.status = "submitted"
            report_rows.append(
                {
                    "row_id": record.row_id,
                    "status": "submitted",
                    "payload": payload,
                    "transaction_id": transaction_id,
                }
            )
        except Exception as exc:  # noqa: BLE001
            record.status = "submit_failed"
            failure = {"row_id": record.row_id, "error": str(exc)}
            failures.append(failure)
            report_rows.append(
                {
                    "row_id": record.row_id,
                    "status": "submit_failed",
                    "error": str(exc),
                    "payload": payload,
                }
            )
            break

    report = {
        "run_id": session.run_id,
        "profile_name": session.profile_name,
        "input_file": session.input_file,
        "created_at": _utc_now(),
        "dry_run": dry_run,
        "rows": report_rows,
        "created_transaction_ids": created_ids,
        "failures": failures,
    }
    report_path = _run_report_path(session.run_id)
    write_json(report_path, report)
    session.committed = not dry_run and not failures

    return SubmitResult(
        run_id=session.run_id,
        dry_run=dry_run,
        created_ids=created_ids,
        failures=failures,
        report_path=report_path,
    )


def rollback_run(run_id: str, firefly_client: Any) -> dict[str, Any]:
    """Handle rollback run."""
    report_path = _run_report_path(run_id)
    report = read_json(report_path, default=None)
    if report is None:
        raise FileNotFoundError(f"Run report not found: {report_path}")

    created_ids = [str(item) for item in report.get("created_transaction_ids", []) if str(item)]
    rollback_rows: list[dict[str, str]] = []
    for transaction_id in reversed(created_ids):
        try:
            firefly_client.delete_transaction(transaction_id)
            rollback_rows.append({"transaction_id": transaction_id, "status": "deleted"})
        except Exception as exc:  # noqa: BLE001
            rollback_rows.append(
                {
                    "transaction_id": transaction_id,
                    "status": "failed",
                    "error": str(exc),
                }
            )
    rollback_report = {
        "run_id": run_id,
        "rolled_back_at": _utc_now(),
        "results": rollback_rows,
    }
    write_json(RUNS_DIR / f"{run_id}.rollback.json", rollback_report)
    return rollback_report
