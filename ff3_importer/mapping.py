"""Column mapping and row normalization logic."""
from __future__ import annotations

import sys
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .models import TransactionRecord


REQUIRED_FIELDS = ("date", "amount", "description")
OPTIONAL_FIELDS = (
    "type",
    "source_account",
    "destination_account",
    "payee",
    "currency",
    "category",
    "budget",
    "tags",
    "notes",
    "external_id",
    "internal_reference",
    "debit_amount",
    "credit_amount",
)
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS
FIELD_HELP = {
    "date": "Transaction date column (required)",
    "description": "Transaction description/memo column (required)",
    "amount": "Signed amount column (required unless debit/credit pair is used)",
    "debit_amount": "Debit amount column (optional alternative to amount)",
    "credit_amount": "Credit amount column (optional alternative to amount)",
    "type": "Transaction type column (optional: withdrawal/deposit/transfer)",
    "source_account": "Source account column (optional)",
    "destination_account": "Destination account/payee account column (optional)",
    "payee": "Payee/counterparty column (optional)",
    "currency": "Currency column (optional)",
    "category": "Category column (optional)",
    "budget": "Budget column (optional)",
    "tags": "Tags column (optional, comma-separated)",
    "notes": "Notes column (optional)",
    "external_id": "External ID column (optional)",
    "internal_reference": "Internal reference column (optional)",
}


def _prompt(message: str, default: str = "") -> str:
    """Internal helper for prompt."""
    prompt_label = f"{message} [current: {default}] (Enter keeps current)" if default else message
    raw = input(f"{prompt_label}: ").strip()
    return raw or default


def _best_guess_for_field(field_name: str, headers: Iterable[str]) -> str:
    """Internal helper for best guess for field."""
    if field_name == "debit_amount":
        for header in headers:
            normalized = header.replace("_", "").replace(" ", "").lower()
            if "debit" in normalized:
                return header
        return ""
    if field_name == "credit_amount":
        for header in headers:
            normalized = header.replace("_", "").replace(" ", "").lower()
            if "credit" in normalized:
                return header
        return ""

    target = field_name.replace("_", "").lower()
    for header in headers:
        normalized = header.replace("_", "").replace(" ", "").lower()
        if normalized == target:
            return header
    for header in headers:
        normalized = header.replace("_", "").replace(" ", "").lower()
        if target in normalized or normalized in target:
            return header
    return ""


def _resolve_header_choice(choice: str, headers: list[str]) -> str | None:
    """Internal helper for resolve header choice."""
    stripped = choice.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        index = int(stripped)
        if 0 <= index < len(headers):
            return headers[index]
        return None

    exact = [header for header in headers if header == stripped]
    if exact:
        return exact[0]

    lowered = stripped.lower()
    ci = [header for header in headers if header.lower() == lowered]
    if ci:
        return ci[0]
    return None


def _clear_screen() -> None:
    """Internal helper for clear screen."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _required_missing(mapping: dict[str, str]) -> list[str]:
    """Internal helper for required missing."""
    missing = [field for field in ("date", "description") if field not in mapping]
    has_amount = "amount" in mapping
    has_pair = "debit_amount" in mapping and "credit_amount" in mapping
    if not has_amount and not has_pair:
        missing.append("amount (or debit_amount+credit_amount)")
    return missing


def _render_mapping_screen(
    headers: list[str],
    selections: dict[str, str],
    current_field: str | None = None,
    last_error: str | None = None,
) -> None:
    """Internal helper for render mapping screen."""
    _clear_screen()
    print("Step 2/3 - Choose column mapping")
    print("Select header by index number or exact header name.")
    print("Use '-' to force empty for optional fields.")
    print("Required: date, description, and amount OR debit_amount+credit_amount.\n")

    print("Available headers:")
    for idx, header in enumerate(headers):
        print(f"  [{idx}] {header}")
    print("")

    print("Current field mapping:")
    for field_name in ALL_FIELDS:
        marker = "->" if current_field == field_name else "  "
        selected = selections.get(field_name, "empty")
        print(f"{marker} {field_name:<20} : {selected}")
    if last_error:
        print(f"\nValidation: {last_error}")


def choose_column_mapping(
    headers: list[str],
    existing_mapping: dict[str, str] | None = None,
    interactive: bool = True,
) -> dict[str, str]:
    """Choose column mapping."""
    existing_mapping = existing_mapping or {}
    mapping: dict[str, str] = {}

    for field_name in ALL_FIELDS:
        previous = existing_mapping.get(field_name, "")
        guessed = previous or _best_guess_for_field(field_name, headers)
        if guessed and guessed in headers:
            mapping[field_name] = guessed

    if not interactive:
        missing = _required_missing(mapping)
        if missing:
            raise ValueError(f"Missing required mappings: {missing}")
        return mapping

    last_error: str | None = None
    while True:
        for field_name in ALL_FIELDS:
            _render_mapping_screen(
                headers=headers,
                selections=mapping,
                current_field=field_name,
                last_error=last_error,
            )
            help_text = FIELD_HELP.get(field_name, field_name)
            current = mapping.get(field_name, "")
            raw = _prompt(
                f"Header for '{field_name}' - {help_text}. Enter index/name or '-' for empty",
                default=current,
            ).strip()

            if raw in {"-", "none", "empty"}:
                mapping.pop(field_name, None)
                last_error = None
                continue
            if raw == "":
                last_error = None
                continue

            resolved = _resolve_header_choice(raw, headers)
            if resolved is None:
                last_error = f"Invalid header selection '{raw}' for field '{field_name}'."
                continue
            mapping[field_name] = resolved
            last_error = None

        missing = _required_missing(mapping)
        if not missing:
            _render_mapping_screen(headers=headers, selections=mapping, current_field=None, last_error=None)
            print("\nMapping complete.\n")
            return mapping
        last_error = "Missing required mappings: " + ", ".join(missing)


def _normalize_date(value: str) -> str:
    """Internal helper for normalize date."""
    candidate = value.strip()
    if not candidate:
        return ""
    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(candidate, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed.strftime("%Y-%m-%d")
    except ValueError:
        return candidate


def _normalize_amount(value: str) -> str:
    """Internal helper for normalize amount."""
    raw = value.strip().replace(",", "")
    if not raw:
        return ""
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return raw
    return f"{amount:.2f}"


def _combine_debit_credit(debit: str, credit: str) -> str:
    """Internal helper for combine debit credit."""
    debit_norm = _normalize_amount(debit)
    credit_norm = _normalize_amount(credit)
    if debit_norm and debit_norm not in {"0", "0.00"}:
        if debit_norm.startswith("-"):
            return debit_norm
        return f"-{debit_norm}"
    return credit_norm


def _decimal_or_none(value: str) -> Decimal | None:
    """Internal helper for decimal or none."""
    raw = value.strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _infer_type_from_amount(amount: str) -> str:
    """Internal helper for infer type from amount."""
    parsed = _decimal_or_none(amount)
    if parsed is None:
        return "withdrawal"
    if parsed < 0:
        return "withdrawal"
    if parsed > 0:
        return "deposit"
    return "transfer"


def _clean_text(value: str) -> str:
    """Internal helper for clean text."""
    return " ".join(value.replace('"', "").split()).strip()


def _clip(value: str, max_len: int = 80) -> str:
    """Internal helper for clip."""
    if len(value) <= max_len:
        return value
    return value[:max_len].rstrip()


def _extract_after_token(text: str, token: str) -> str:
    """Internal helper for extract after token."""
    lowered = text.lower()
    token_l = token.lower()
    if token_l not in lowered:
        return ""
    start = lowered.find(token_l) + len(token_l)
    chunk = text[start:].strip()
    for stop in (
        " id:",
        " conf",
        " co id",
        " for ",
        " web",
        " des:",
        ";",
        " to ",
    ):
        idx = chunk.lower().find(stop)
        if idx != -1:
            chunk = chunk[:idx].strip()
    return _clip(_clean_text(chunk))


def _extract_before_token(text: str, token: str) -> str:
    """Internal helper for extract before token."""
    lowered = text.lower()
    token_l = token.lower()
    if token_l not in lowered:
        return ""
    head = text[: lowered.find(token_l)].strip()
    return _clip(_clean_text(head))


def _infer_destination(description: str) -> str:
    """Internal helper for infer destination."""
    desc = _clean_text(description)
    if not desc:
        return ""

    # Many bank descriptions put merchant/counterparty before DES:/INDN.
    before_des = _extract_before_token(desc, "DES:")
    if before_des:
        return before_des

    for token in ("to ", "from ", "DES:"):
        value = _extract_after_token(desc, token)
        if value:
            return value

    fallback = _clip(desc)
    return fallback


def _extract_reference_token(description: str) -> tuple[str, str]:
    """Internal helper for extract reference token."""
    text = description or ""
    external_matchers = [
        r"\bCONFIRMATION#?\s*([A-Za-z0-9_-]{6,})\b",
        r"\bCONF#?\s*([A-Za-z0-9_-]{6,})\b",
        r"\bTRACE#?\s*([A-Za-z0-9_-]{6,})\b",
        r"\bID:?\s*([A-Za-z0-9Xx*_-]{6,})\b",
    ]
    internal_matchers = [
        r"\bCHK\s*#?\s*([0-9]{3,})\b",
        r"\bACCT(?:OUNT)?\s*#?\s*([0-9]{3,})\b",
        r"\bCARD\s*#?\s*([0-9]{3,})\b",
    ]

    external_id = ""
    internal_ref = ""
    for pattern in external_matchers:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            external_id = match.group(1)
            break
    for pattern in internal_matchers:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            internal_ref = match.group(1)
            break
    return external_id, internal_ref


def normalize_rows_to_records(
    rows: list[dict[str, str]],
    mapping: dict[str, str],
    defaults: dict[str, str] | None = None,
) -> list[TransactionRecord]:
    """Normalize rows to records."""
    defaults = defaults or {}
    records: list[TransactionRecord] = []
    for index, row in enumerate(rows):
        source_index = int(row.get("__source_row_index", index))
        date_raw = row.get(mapping["date"], "")
        amount_raw = row.get(mapping.get("amount", ""), "")
        if not amount_raw and mapping.get("debit_amount") and mapping.get("credit_amount"):
            amount_raw = _combine_debit_credit(
                debit=row.get(mapping["debit_amount"], ""),
                credit=row.get(mapping["credit_amount"], ""),
            )

        normalized_amount = _normalize_amount(amount_raw)
        mapped_type = row.get(mapping.get("type", ""), defaults.get("type", "")).strip()
        inferred_type = mapped_type or _infer_type_from_amount(normalized_amount)

        mapped_source = row.get(mapping.get("source_account", ""), "").strip()
        mapped_destination = row.get(mapping.get("destination_account", ""), "").strip()
        mapped_payee = row.get(mapping.get("payee", ""), "").strip()

        # The file-level account is the operator's own account represented by
        # the statement. Description parsing supplies the other side when the
        # export has no dedicated counterparty columns.
        file_account = defaults.get("source_account", "").strip()
        description_value = row.get(mapping["description"], "")
        destination_guess = mapped_destination or _infer_destination(description_value)
        inferred_external_id, inferred_internal_ref = _extract_reference_token(description_value)

        # Deposits invert the source/destination assumption compared with
        # withdrawals. Transfers keep the file account on the source side until
        # live Firefly account typing can refine the classification later.
        if inferred_type == "deposit":
            source_account = mapped_source or destination_guess
            destination_account = mapped_destination or file_account
            payee = mapped_payee
        elif inferred_type == "transfer":
            source_account = mapped_source or file_account
            destination_account = mapped_destination or destination_guess
            payee = mapped_payee
        else:
            source_account = mapped_source or file_account
            destination_account = mapped_destination or destination_guess
            payee = mapped_payee

        tags_raw = row.get(mapping.get("tags", ""), "")
        tags = [item.strip() for item in tags_raw.split(",") if item.strip()] if tags_raw else []
        record = TransactionRecord(
            row_id=f"row-{source_index}",
            source_row_index=source_index,
            type=inferred_type,
            date=_normalize_date(date_raw),
            amount=normalized_amount,
            description=row.get(mapping["description"], "").strip(),
            source_account=source_account,
            destination_account=destination_account,
            payee=payee,
            currency=row.get(mapping.get("currency", ""), defaults.get("currency", "")).strip(),
            category=row.get(mapping.get("category", ""), defaults.get("category", "")).strip(),
            budget=row.get(mapping.get("budget", ""), defaults.get("budget", "")).strip(),
            tags=tags,
            notes=row.get(mapping.get("notes", ""), "").strip(),
            external_id=row.get(mapping.get("external_id", ""), "").strip() or inferred_external_id,
            internal_reference=row.get(mapping.get("internal_reference", ""), "").strip()
            or inferred_internal_ref,
            raw={k: v for k, v in row.items() if k != "__source_row_index"},
        )
        if not record.date or not record.amount or not record.description:
            record.status = "blocked_missing_ref"
            record.warnings.append("Missing required fields after mapping.")
        records.append(record)
    return records
