"""Dataclasses for transaction, profile, and session state."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


TRANSACTION_STATUSES = {
    "pending",
    "approved",
    "skipped",
    "blocked_missing_ref",
    "submitted",
    "submit_failed",
}


@dataclass
class TransactionRecord:
    """Represent a normalized transaction during review and upload."""
    row_id: str
    source_row_index: int
    type: str = "withdrawal"
    date: str = ""
    amount: str = ""
    description: str = ""
    source_account: str = ""
    destination_account: str = ""
    payee: str = ""
    currency: str = ""
    category: str = ""
    budget: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    external_id: str = ""
    internal_reference: str = ""
    status: str = "pending"
    warnings: list[str] = field(default_factory=list)
    duplicate_override: bool = False
    new_external_counterparty_override: bool = False
    raw: dict[str, str] = field(default_factory=dict)
    rule_suggestions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the instance to a JSON-friendly dictionary."""
        payload = asdict(self)
        if payload["status"] not in TRANSACTION_STATUSES:
            payload["status"] = "pending"
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransactionRecord":
        """Build an instance from a stored dictionary."""
        return cls(
            row_id=str(data.get("row_id", "")),
            source_row_index=int(data.get("source_row_index", 0)),
            type=str(data.get("type", "withdrawal")),
            date=str(data.get("date", "")),
            amount=str(data.get("amount", "")),
            description=str(data.get("description", "")),
            source_account=str(data.get("source_account", "")),
            destination_account=str(data.get("destination_account", "")),
            payee=str(data.get("payee", "")),
            currency=str(data.get("currency", "")),
            category=str(data.get("category", "")),
            budget=str(data.get("budget", "")),
            tags=[str(item) for item in data.get("tags", [])],
            notes=str(data.get("notes", "")),
            external_id=str(data.get("external_id", "")),
            internal_reference=str(data.get("internal_reference", "")),
            status=str(data.get("status", "pending")),
            warnings=[str(item) for item in data.get("warnings", [])],
            duplicate_override=bool(data.get("duplicate_override", False)),
            new_external_counterparty_override=bool(
                data.get("new_external_counterparty_override", False)
            ),
            raw={str(k): str(v) for k, v in data.get("raw", {}).items()},
            rule_suggestions=[
                item for item in data.get("rule_suggestions", []) if isinstance(item, dict)
            ],
        )


@dataclass
class Profile:
    """Store reusable parsing, mapping, and alias settings."""
    name: str
    parse_hints: dict[str, Any] = field(default_factory=dict)
    column_mapping: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    rules: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the instance to a JSON-friendly dictionary."""
        return {
            "name": self.name,
            "parse_hints": self.parse_hints,
            "column_mapping": self.column_mapping,
            "defaults": self.defaults,
            "rules": self.rules,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        """Build an instance from a stored dictionary."""
        return cls(
            name=str(data.get("name", "")),
            parse_hints=dict(data.get("parse_hints", {})),
            column_mapping={str(k): str(v) for k, v in data.get("column_mapping", {}).items()},
            defaults={str(k): str(v) for k, v in data.get("defaults", {}).items()},
            rules=[item for item in data.get("rules", []) if isinstance(item, dict)],
        )


@dataclass
class SessionState:
    """Store the working state for an import run."""
    run_id: str
    profile_name: str
    input_file: str
    parse_hints: dict[str, Any]
    column_mapping: dict[str, str]
    records: list[TransactionRecord]
    current_index: int = 0
    created_at: str = ""
    updated_at: str = ""
    committed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the instance to a JSON-friendly dictionary."""
        return {
            "run_id": self.run_id,
            "profile_name": self.profile_name,
            "input_file": self.input_file,
            "parse_hints": self.parse_hints,
            "column_mapping": self.column_mapping,
            "records": [record.to_dict() for record in self.records],
            "current_index": self.current_index,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "committed": self.committed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        """Build an instance from a stored dictionary."""
        return cls(
            run_id=str(data.get("run_id", "")),
            profile_name=str(data.get("profile_name", "")),
            input_file=str(data.get("input_file", "")),
            parse_hints=dict(data.get("parse_hints", {})),
            column_mapping={str(k): str(v) for k, v in data.get("column_mapping", {}).items()},
            records=[
                TransactionRecord.from_dict(item)
                for item in data.get("records", [])
                if isinstance(item, dict)
            ],
            current_index=int(data.get("current_index", 0)),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            committed=bool(data.get("committed", False)),
        )
