"""CLI entry points and end-to-end workflow orchestration."""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from .account_resolver import (
    is_placeholder,
    list_account_names,
    make_placeholder,
    resolve_account_name,
)
from .app_paths import CONFIG_FILE, PROJECT_ROOT, ensure_runtime_dirs
from .dedup import append_history, fetch_remote_fingerprints, mark_duplicate_warnings
from .firefly_client import FireflyAPIError, FireflyClient
from .io_loader import load_tabular_file
from .json_store import read_json, write_json
from .mapping import choose_column_mapping, normalize_rows_to_records
from .models import Profile, SessionState
from .parse_wizard import parse_with_wizard
from .profile_store import ProfileStore
from .rules import attach_rule_suggestions
from .session_store import SessionStore
from .submit import rollback_run, submit_session
from .tui_app import ReviewApp


def _is_interactive() -> bool:
    """Internal helper for is interactive."""
    return sys.stdin.isatty()


def _prompt(message: str, default: str = "") -> str:
    """Internal helper for prompt."""
    if not _is_interactive():
        return default
    prompt_label = f"{message} (default: {default}, press Enter to keep)" if default else message
    raw = input(f"{prompt_label}: ").strip()
    return raw or default


def _sanitize_profile_name(value: str) -> str:
    """Internal helper for sanitize profile name."""
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return normalized or "default-profile"


def _load_config_defaults() -> dict[str, Any]:
    """Internal helper for load config defaults."""
    return read_json(CONFIG_FILE, default={})


def _normalize_alias_key(value: str) -> str:
    """Internal helper for normalize alias key."""
    return " ".join(value.strip().lower().split())


def _choose_profile_name(
    store: ProfileStore,
    provided_name: str | None,
    input_file: Path,
    file_type: str,
    header_signature: dict[str, Any],
) -> str:
    """Internal helper for choose profile name."""
    if _is_interactive():
        print("\nProfile selection")
        print("Choose an existing profile or type a new profile name.\n")

    if provided_name:
        return _sanitize_profile_name(provided_name)

    if file_type == "csv":
        match = store.match_from_signature(header_signature)
        if match.confident and match.selected:
            print(f"Auto-detected profile '{match.selected}' (score={match.ranked[0][1]}).")
            return match.selected

        print("Could not confidently auto-detect a profile.")
        if match.ranked:
            print("Top profile matches:")
            for idx, (name, score) in enumerate(match.ranked[:5], start=1):
                print(f"  {idx}. {name} (score={score})")

    names = store.list_names()
    if names:
        print("Available profiles:")
        for idx, name in enumerate(names, start=1):
            print(f"  {idx}. {name}")
    suggested = _sanitize_profile_name(input_file.stem)
    choice = _prompt("Profile name (existing or new)", default=suggested)
    return _sanitize_profile_name(choice)


def _derive_file_account_name(profile: Profile, input_file: Path) -> str:
    """Internal helper for derive file account name."""
    existing = profile.defaults.get("source_account", "").strip()
    if existing:
        return existing
    if profile.name and profile.name != "default-profile":
        return profile.name
    digits = re.findall(r"\d{3,}", input_file.stem)
    if digits:
        return digits[-1]
    return input_file.stem


def _load_account_aliases(profile: Profile, config: dict[str, Any]) -> dict[str, str]:
    """Internal helper for load account aliases."""
    merged: dict[str, str] = {}
    config_aliases = config.get("account_aliases", {})
    profile_aliases = profile.parse_hints.get("account_aliases", {})
    for source in (config_aliases, profile_aliases):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            key_s = _normalize_alias_key(str(key))
            value_s = str(value).strip()
            if key_s and value_s:
                merged[key_s] = value_s
    return merged


def _collect_session_defaults(profile: Profile, config: dict[str, Any], input_file: Path) -> dict[str, str]:
    """Internal helper for collect session defaults."""
    merged = dict(config.get("defaults", {}))
    merged.update(profile.defaults)
    source_account = _derive_file_account_name(profile, input_file)
    currency = merged.get("currency", "").strip() or "USD"
    defaults = {
        "type": "",
        "source_account": source_account,
        "destination_account": "",
        "payee": "",
        "currency": currency,
    }
    if _is_interactive():
        print("\nAutomatic defaults applied (no prompt):")
        print(f"- Source account (file account): {source_account}")
        print("- Transaction type: inferred per row from amount sign")
        print("- Destination/payee: inferred per row from transaction description")
        print(f"- Currency: {currency}")
    return defaults


def _looks_like_account_reference(value: str) -> bool:
    """Internal helper for looks like account reference."""
    text = value.strip()
    if not text:
        return False
    if re.search(r"\d{3,}", text):
        return True
    lowered = text.lower()
    return lowered.startswith(("chk ", "acct ", "account ", "card ")) or "xxxx" in lowered or "***" in lowered


def _select_account_interactively(
    identifier: str,
    candidates: list[str],
    account_names: list[str],
    *,
    field_name: str = "account",
    row_label: str = "",
    description: str = "",
) -> str | None:
    """Internal helper for select account interactively."""
    if not _is_interactive():
        return None

    pool = candidates[:] if candidates else account_names[:10]
    exact_lookup = {_normalize_alias_key(name): name for name in account_names}
    while True:
        print(f"\nManual {field_name} lookup")
        if row_label:
            print(f"Row: {row_label}")
        print(f"Identifier from import: {identifier}")
        if description:
            print(f"Description: {description}")
        if pool:
            print("Candidate accounts:")
            for idx, name in enumerate(pool[:10], start=1):
                print(f"  {idx}. {name}")
        else:
            print("No account candidates found.")
        choice = input(
            "Choose index, type search text for more matches, or press Enter to keep placeholder: "
        ).strip()
        if not choice:
            return None
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(pool[:10]):
                return pool[idx - 1]
            print("Invalid index.")
            continue
        exact = exact_lookup.get(_normalize_alias_key(choice))
        if exact:
            return exact
        query = choice.lower()
        pool = [name for name in account_names if query in name.lower()][:10]
        if len(pool) == 1:
            return pool[0]


def _run_review(
    session: SessionState,
    store: SessionStore,
    *,
    load_account_catalog: Callable[[], dict[str, Any]] | None = None,
    prepare_upload: Callable[[SessionState], dict[str, Any]] | None = None,
    submit_upload: Callable[[SessionState], dict[str, Any]] | None = None,
    start_in_upload_if_resolved: bool = False,
) -> ReviewApp:
    """Internal helper for run review."""
    if not _is_interactive():
        raise RuntimeError(
            "Interactive review requires a TTY terminal. Run this command in an interactive shell."
        )
    app = ReviewApp(
        session=session,
        on_change=store.save,
        load_account_catalog=load_account_catalog,
        prepare_upload=prepare_upload,
        submit_upload=submit_upload,
        start_in_upload_if_resolved=start_in_upload_if_resolved,
    )
    app.run()
    store.save(session)
    return app


def _has_unresolved_records(session: SessionState) -> bool:
    """Internal helper for has unresolved records."""
    unresolved_statuses = {"pending", "blocked_missing_ref"}
    return any(record.status in unresolved_statuses for record in session.records)


def _try_build_firefly_client() -> FireflyClient | None:
    """Internal helper for try build firefly client."""
    try:
        return FireflyClient.from_env()
    except FireflyAPIError:
        return None


def _persist_profile_updates(profile: Profile, session: SessionState, store: ProfileStore) -> None:
    """Internal helper for persist profile updates."""
    new_rules = session.parse_hints.pop("new_rules", [])
    if new_rules:
        profile.rules.extend(new_rules)
    profile.parse_hints.update(session.parse_hints)
    profile.column_mapping = dict(session.column_mapping)
    store.save(profile)


def _extract_name_set(rows: list[dict[str, Any]]) -> set[str]:
    """Internal helper for extract name set."""
    names = set()
    for row in rows:
        name = str(row.get("attributes", {}).get("name", "")).strip()
        if name:
            names.add(name)
    return names


def _extract_account_type_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Internal helper for extract account type map."""
    mapping: dict[str, str] = {}
    for row in rows:
        attributes = row.get("attributes", {})
        name = str(attributes.get("name", "")).strip()
        account_type = str(attributes.get("type", "")).strip().lower()
        if name and account_type:
            mapping[name] = account_type
    return mapping


def _extract_account_catalog(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Internal helper for extract account catalog."""
    catalog: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        attributes = row.get("attributes", {})
        name = str(attributes.get("name", "")).strip()
        account_type = str(attributes.get("type", "")).strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        catalog.append(
            {
                "name": name,
                "type": account_type,
                "account_number": str(attributes.get("account_number", "")).strip(),
                "iban": str(attributes.get("iban", "")).strip(),
                "bic": str(attributes.get("bic", "")).strip(),
            }
        )
    return catalog


def _classify_firefly_account_type(value: str) -> str:
    """Internal helper for classify firefly account type."""
    normalized = re.sub(r"[\s_-]+", " ", value.strip().lower())
    if not normalized:
        return "unknown"
    if "expense" in normalized or "beneficiar" in normalized:
        return "expense"
    if "revenue" in normalized or "income" in normalized:
        return "revenue"
    if any(token in normalized for token in ("loan", "debt", "mortgage", "liabil")):
        return "liability"
    if any(token in normalized for token in ("asset", "cash")):
        return "asset"
    return "unknown"


def _infer_live_transaction_type(record: Any, account_types: dict[str, str]) -> str | None:
    """Internal helper for infer live transaction type."""
    source_type = _classify_firefly_account_type(account_types.get(record.source_account, ""))
    destination_type = _classify_firefly_account_type(account_types.get(record.destination_account, ""))

    # Use Firefly account classes only when both sides are known enough to classify.
    if source_type == "asset" and destination_type == "asset":
        return "transfer"
    if source_type == "liability" and destination_type == "liability":
        return "transfer"
    if source_type == "asset" and destination_type in {"expense", "liability"}:
        return "withdrawal"
    if source_type == "liability" and destination_type == "expense":
        return "withdrawal"
    if source_type == "revenue" and destination_type in {"asset", "liability"}:
        return "deposit"
    if source_type == "liability" and destination_type == "asset":
        return "deposit"
    return None


def _reinfer_record_types_from_live_accounts(
    records: list[Any],
    account_types: dict[str, str],
) -> int:
    """Internal helper for reinfer record types from live accounts."""
    changed = 0
    for record in records:
        inferred = _infer_live_transaction_type(record, account_types)
        if inferred and record.type != inferred:
            record.type = inferred
            changed += 1
    return changed


def _extract_placeholder_value(value: str) -> str:
    """Internal helper for extract placeholder value."""
    raw = str(value).strip()
    if not is_placeholder(raw):
        return raw
    _, _, identifier = raw.partition("::")
    return identifier.strip()


def _is_duplicate_warning(warning: str) -> bool:
    """Internal helper for is duplicate warning."""
    return warning.startswith("Potential duplicate detected (")


def _is_new_external_warning(warning: str) -> bool:
    """Internal helper for is new external warning."""
    return warning.startswith("New external counterparty confirmation required:")


def _is_live_reference_warning(warning: str) -> bool:
    """Internal helper for is live reference warning."""
    prefixes = (
        "Missing Firefly references:",
        "Destination account unresolved placeholder:",
        "Unknown category (will be reviewed):",
        "Unknown budget (will be reviewed):",
        "Unresolved account reference in ",
        "New external counterparty confirmation required:",
    )
    return warning.startswith(prefixes)


def _clear_live_submit_warnings(records: list[Any]) -> None:
    """Internal helper for clear live submit warnings."""
    for record in records:
        record.warnings = [
            warning
            for warning in record.warnings
            if not _is_duplicate_warning(warning) and not _is_live_reference_warning(warning)
        ]
        if record.status == "submit_failed":
            record.status = "approved"


def _external_counterparty_field(record: Any) -> str:
    """Internal helper for external counterparty field."""
    if record.type == "withdrawal":
        return "destination_account"
    if record.type == "deposit":
        return "source_account"
    return ""


def _internal_account_fields(record: Any) -> tuple[str, ...]:
    """Internal helper for internal account fields."""
    if record.type == "withdrawal":
        return ("source_account",)
    if record.type == "deposit":
        return ("destination_account",)
    if record.type == "transfer":
        return ("source_account", "destination_account")
    return ("source_account",)


def _remove_warning_prefixes(record: Any, prefixes: tuple[str, ...]) -> None:
    """Internal helper for remove warning prefixes."""
    record.warnings = [
        warning for warning in record.warnings if not any(warning.startswith(prefix) for prefix in prefixes)
    ]


def _normalize_external_counterparty_fields(records: list[Any]) -> None:
    """Internal helper for normalize external counterparty fields."""
    for record in records:
        external_field = _external_counterparty_field(record)
        if not external_field:
            continue
        current_value = str(getattr(record, external_field, "")).strip()
        if is_placeholder(current_value):
            setattr(record, external_field, _extract_placeholder_value(current_value))
        prefixes = [f"Unresolved account reference in {external_field}:"]
        if external_field == "destination_account":
            prefixes.append("Destination account unresolved placeholder:")
        _remove_warning_prefixes(record, tuple(prefixes))


def _mark_new_external_counterparty_warnings(records: list[Any], account_names: set[str]) -> int:
    """Internal helper for mark new external counterparty warnings."""
    count = 0
    for record in records:
        external_field = _external_counterparty_field(record)
        if not external_field:
            continue
        value = str(getattr(record, external_field, "")).strip()
        if not value:
            continue
        if is_placeholder(value):
            value = _extract_placeholder_value(value)
            setattr(record, external_field, value)
        if value in account_names:
            continue
        warning = (
            f"New external counterparty confirmation required: {external_field}={value}"
        )
        if warning not in record.warnings:
            record.warnings.append(warning)
        if not getattr(record, "new_external_counterparty_override", False):
            count += 1
    return count


def _has_new_external_blockers(session: SessionState) -> bool:
    """Internal helper for has new external blockers."""
    for record in session.records:
        if record.status != "approved":
            continue
        if any(_is_new_external_warning(warning) for warning in record.warnings) and not record.new_external_counterparty_override:
            return True
    return False


def _save_profile_account_alias(profile: Profile, identifier: str, account_name: str) -> bool:
    """Internal helper for save profile account alias."""
    key = _normalize_alias_key(identifier)
    if not key:
        return False
    aliases = profile.parse_hints.setdefault("account_aliases", {})
    if not isinstance(aliases, dict):
        aliases = {}
        profile.parse_hints["account_aliases"] = aliases
    if aliases.get(key) == account_name:
        return False
    aliases[key] = account_name
    return True


def _save_global_account_alias(config: dict[str, Any], identifier: str, account_name: str) -> bool:
    """Internal helper for save global account alias."""
    key = _normalize_alias_key(identifier)
    if not key:
        return False
    aliases = config.setdefault("account_aliases", {})
    if not isinstance(aliases, dict):
        aliases = {}
        config["account_aliases"] = aliases
    if aliases.get(key) == account_name:
        return False
    aliases[key] = account_name
    return True


def _choose_alias_memory_scope(identifier: str, account_name: str) -> str:
    """Internal helper for choose alias memory scope."""
    if not _is_interactive():
        return "no"
    while True:
        choice = _prompt(
            f"Save alias '{identifier}' -> '{account_name}' scope [profile/global/no]",
            default="profile",
        ).strip().lower()
        if choice in {"p", "profile"}:
            return "profile"
        if choice in {"g", "global"}:
            return "global"
        if choice in {"n", "no", "skip"}:
            return "no"
        print("Choose 'profile', 'global', or 'no'.")


def _remember_account_alias(
    profile: Profile,
    profile_store: ProfileStore,
    config: dict[str, Any],
    aliases: dict[str, str],
    identifier: str,
    account_name: str,
) -> None:
    """Internal helper for remember account alias."""
    scope = _choose_alias_memory_scope(identifier, account_name)
    key = _normalize_alias_key(identifier)
    if scope == "profile":
        if _save_profile_account_alias(profile, identifier, account_name):
            aliases[key] = account_name
            profile_store.save(profile)
            print(f"Saved profile alias: {identifier} -> {account_name}")
        return
    if scope == "global":
        if _save_global_account_alias(config, identifier, account_name):
            aliases[key] = account_name
            write_json(CONFIG_FILE, config)
            print(f"Saved global alias: {identifier} -> {account_name}")


def _validate_firefly_references(
    records: list[Any],
    firefly_client: FireflyClient | None,
) -> None:
    """Internal helper for validate firefly references."""
    if firefly_client is None:
        return
    try:
        account_rows = firefly_client.list_accounts()
        account_names = _extract_name_set(account_rows)
        account_types = _extract_account_type_map(account_rows)
        category_names = _extract_name_set(firefly_client.list_categories())
        budget_names = _extract_name_set(firefly_client.list_budgets())
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: could not validate reference objects: {exc}")
        return

    _reinfer_record_types_from_live_accounts(records, account_types)
    _normalize_external_counterparty_fields(records)

    for record in records:
        missing: list[str] = []
        for field_name in _internal_account_fields(record):
            value = str(getattr(record, field_name, "")).strip()
            if value and is_placeholder(value):
                missing.append(f"{field_name}={value}")
            elif value and value not in account_names:
                missing.append(f"{field_name}={value}")

        if record.category and record.category not in category_names:
            record.warnings.append(f"Unknown category (will be reviewed): {record.category}")
        if record.budget and record.budget not in budget_names:
            record.warnings.append(f"Unknown budget (will be reviewed): {record.budget}")

        if missing:
            record.status = "blocked_missing_ref"
            record.warnings.append("Missing Firefly references: " + ", ".join(missing))

    _mark_new_external_counterparty_warnings(records, account_names)


def _resolve_accounts_for_records(
    records: list[Any],
    account_names: list[str],
    aliases: dict[str, str],
    account_catalog: list[dict[str, str]] | None = None,
) -> None:
    """Internal helper for resolve accounts for records."""
    if not account_names:
        return
    for record in records:
        for field_name in ("source_account", "destination_account"):
            value = str(getattr(record, field_name, "")).strip()
            identifier = _extract_placeholder_value(value)
            if not identifier:
                continue
            resolution = resolve_account_name(
                identifier,
                account_names=account_names,
                aliases=aliases,
                account_catalog=account_catalog,
            )
            if resolution.resolved:
                setattr(record, field_name, resolution.resolved)
                continue
            if _looks_like_account_reference(identifier):
                placeholder = make_placeholder(identifier)
                setattr(record, field_name, placeholder)
                warning = f"Unresolved account reference in {field_name}: {identifier} (saved as placeholder)"
                if warning not in record.warnings:
                    record.warnings.append(warning)


def _validate_firefly_references_with_names(
    records: list[Any],
    account_names: set[str],
    category_names: set[str],
    budget_names: set[str],
) -> None:
    """Internal helper for validate firefly references with names."""
    _normalize_external_counterparty_fields(records)
    for record in records:
        missing: list[str] = []
        for field_name in _internal_account_fields(record):
            value = str(getattr(record, field_name, "")).strip()
            if value and is_placeholder(value):
                missing.append(f"{field_name}={value}")
            elif value and value not in account_names:
                missing.append(f"{field_name}={value}")

        if record.category and record.category not in category_names:
            record.warnings.append(f"Unknown category (will be reviewed): {record.category}")
        if record.budget and record.budget not in budget_names:
            record.warnings.append(f"Unknown budget (will be reviewed): {record.budget}")

        if missing:
            record.status = "blocked_missing_ref"
            record.warnings.append("Missing Firefly references: " + ", ".join(missing))

    _mark_new_external_counterparty_warnings(records, account_names)


def _manual_account_resolution_pass(
    records: list[Any],
    account_names: list[str],
    aliases: dict[str, str],
    account_catalog: list[dict[str, str]],
    profile: Profile,
    profile_store: ProfileStore,
    config: dict[str, Any],
) -> bool:
    """Internal helper for manual account resolution pass."""
    if not _is_interactive():
        return False

    changed = False
    resolved_cache: dict[str, str | None] = {}
    for record in records:
        for field_name in ("source_account", "destination_account"):
            current_value = str(getattr(record, field_name, "")).strip()
            identifier = _extract_placeholder_value(current_value)
            if not identifier or not _looks_like_account_reference(identifier):
                continue

            cache_key = _normalize_alias_key(identifier)
            if cache_key in resolved_cache:
                cached = resolved_cache[cache_key]
                if cached:
                    setattr(record, field_name, cached)
                else:
                    setattr(record, field_name, make_placeholder(identifier))
                continue

            resolution = resolve_account_name(
                identifier,
                account_names=account_names,
                aliases=aliases,
                account_catalog=account_catalog,
            )
            if resolution.resolved:
                resolved_cache[cache_key] = resolution.resolved
                setattr(record, field_name, resolution.resolved)
                continue

            chosen = _select_account_interactively(
                identifier=identifier,
                candidates=resolution.candidates,
                account_names=account_names,
                field_name=field_name.replace("_", " "),
                row_label=str(record.source_row_index),
                description=record.description,
            )
            resolved_cache[cache_key] = chosen
            if not chosen:
                setattr(record, field_name, make_placeholder(identifier))
                continue

            setattr(record, field_name, chosen)
            changed = True
            _remember_account_alias(
                profile=profile,
                profile_store=profile_store,
                config=config,
                aliases=aliases,
                identifier=identifier,
                account_name=chosen,
            )
    return changed


def _has_duplicate_blockers(session: SessionState) -> bool:
    """Internal helper for has duplicate blockers."""
    for record in session.records:
        if record.status != "approved":
            continue
        if any(_is_duplicate_warning(warning) for warning in record.warnings) and not record.duplicate_override:
            return True
    return False


def _live_reconcile_before_submit(
    session: SessionState,
    profile: Profile,
    profile_store: ProfileStore,
    config: dict[str, Any],
    firefly_client: FireflyClient,
    *,
    allow_manual_prompt: bool = True,
) -> tuple[bool, str, dict[str, Any]]:
    """Internal helper for live reconcile before submit."""
    approved_records = [record for record in session.records if record.status == "approved"]
    if not approved_records:
        return True, "No approved rows to submit.", {"account_names": [], "account_catalog": []}

    # Start from a clean live-validation slate every time. The session keeps the
    # operator's edits, but warnings from a previous refresh should not survive
    # if the current server state no longer justifies them.
    _clear_live_submit_warnings(approved_records)
    alias_map = _load_account_aliases(profile, config)

    try:
        account_rows = firefly_client.list_accounts()
        category_rows = firefly_client.list_categories()
        budget_rows = firefly_client.list_budgets()
    except Exception as exc:  # noqa: BLE001
        return False, f"Live Firefly lookup failed before submit: {exc}", {"account_names": [], "account_catalog": []}

    account_names = list_account_names(account_rows)
    account_types = _extract_account_type_map(account_rows)
    account_catalog = _extract_account_catalog(account_rows)

    # First pass: resolve placeholders and partial references against the live
    # Firefly catalog, then optionally offer an interactive manual correction
    # step, then re-run resolution so saved aliases apply immediately.
    _resolve_accounts_for_records(
        records=approved_records,
        account_names=account_names,
        aliases=alias_map,
        account_catalog=account_catalog,
    )
    if allow_manual_prompt:
        _manual_account_resolution_pass(
            records=approved_records,
            account_names=account_names,
            aliases=alias_map,
            account_catalog=account_catalog,
            profile=profile,
            profile_store=profile_store,
            config=config,
        )
        alias_map = _load_account_aliases(profile, config)
        _resolve_accounts_for_records(
            records=approved_records,
            account_names=account_names,
            aliases=alias_map,
            account_catalog=account_catalog,
        )

    # Once account identities are stable, use the matched Firefly account types
    # to correct transaction type where amount-sign-only inference was too weak.
    type_changes = _reinfer_record_types_from_live_accounts(approved_records, account_types)

    try:
        remote_fingerprints = fetch_remote_fingerprints(firefly_client, approved_records)
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            f"Live duplicate check failed before submit: {exc}",
            {"account_names": account_names, "account_catalog": account_catalog},
        )

    # Duplicate warnings and reference validation both depend on the final,
    # live-matched account state, so they run after account reconciliation.
    mark_duplicate_warnings(
        records=approved_records,
        profile_name=session.profile_name,
        remote_fingerprints=remote_fingerprints,
    )
    _validate_firefly_references_with_names(
        records=approved_records,
        account_names=set(account_names),
        category_names=_extract_name_set(category_rows),
        budget_names=_extract_name_set(budget_rows),
    )

    blocked = sum(1 for record in approved_records if record.status == "blocked_missing_ref")
    duplicates = sum(
        1
        for record in approved_records
        if any(_is_duplicate_warning(warning) for warning in record.warnings) and not record.duplicate_override
    )
    new_external = sum(
        1
        for record in approved_records
        if any(_is_new_external_warning(warning) for warning in record.warnings)
        and not record.new_external_counterparty_override
    )
    adjusted_suffix = (
        f" Auto-adjusted transaction type on {type_changes} row(s) from matched Firefly account types."
        if type_changes
        else ""
    )
    if blocked or duplicates or new_external:
        return (
            True,
            (
                f"Live Firefly checks found {blocked} blocked account reference(s) "
                f"and {duplicates} duplicate warning(s) "
                f"and {new_external} new external counterparty confirmation(s).{adjusted_suffix}"
            ),
            {"account_names": account_names, "account_catalog": account_catalog},
        )
    return (
        True,
        f"Live Firefly checks completed with no blocking issues.{adjusted_suffix}",
        {"account_names": account_names, "account_catalog": account_catalog},
    )


def _make_tui_prepare_upload(
    profile: Profile,
    profile_store: ProfileStore,
    session_store: SessionStore,
    config: dict[str, Any],
) -> Callable[[SessionState], dict[str, Any]]:
    """Internal helper for make tui prepare upload."""
    def prepare(session: SessionState) -> dict[str, Any]:
        """Handle prepare."""
        firefly_client = _try_build_firefly_client()
        if firefly_client is None:
            return {
                "available": False,
                "ready": False,
                "message": "Firefly credentials are not configured. Upload stage is offline.",
                "account_names": [],
                "account_catalog": [],
            }

        ready, message, context = _live_reconcile_before_submit(
            session=session,
            profile=profile,
            profile_store=profile_store,
            config=config,
            firefly_client=firefly_client,
            allow_manual_prompt=False,
        )
        _persist_profile_updates(profile, session, profile_store)
        session_store.save(session)
        context["available"] = True
        context["ready"] = (
            bool(ready)
            and not _has_unresolved_records(session)
            and not _has_duplicate_blockers(session)
            and not _has_new_external_blockers(session)
        )
        context["message"] = message
        return context

    return prepare


def _make_tui_account_catalog_loader() -> Callable[[], dict[str, Any]]:
    """Internal helper for make tui account catalog loader."""
    def load() -> dict[str, Any]:
        """Load a stored object from disk."""
        firefly_client = _try_build_firefly_client()
        if firefly_client is None:
            return {
                "available": False,
                "message": "Firefly credentials are not configured. Live account hints are offline.",
                "account_names": [],
                "account_catalog": [],
            }
        try:
            account_rows = firefly_client.list_accounts()
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "message": f"Live Firefly account lookup failed: {exc}",
                "account_names": [],
                "account_catalog": [],
            }
        return {
            "available": True,
            "message": "Live Firefly account hints loaded.",
            "account_names": list_account_names(account_rows),
            "account_catalog": _extract_account_catalog(account_rows),
        }

    return load


def _make_tui_submit_upload(
    profile: Profile,
    profile_store: ProfileStore,
    session_store: SessionStore,
    config: dict[str, Any],
    *,
    dry_run: bool,
) -> Callable[[SessionState], dict[str, Any]]:
    """Internal helper for make tui submit upload."""
    def submit(session: SessionState) -> dict[str, Any]:
        """Handle submit."""
        firefly_client = _try_build_firefly_client()
        if firefly_client is None and not dry_run:
            return {
                "ok": False,
                "message": "Firefly credentials are not configured. Upload is unavailable.",
                "result": None,
                "state": {
                "available": False,
                "ready": False,
                "message": "Firefly credentials are not configured. Upload stage is offline.",
                "account_names": [],
                "account_catalog": [],
            },
        }

        if firefly_client is None:
            state = {
                "available": False,
                "ready": True,
                "message": "Dry run is ready without live Firefly connectivity.",
                "account_names": [],
                "account_catalog": [],
            }
        else:
            ready, message, state = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=profile_store,
                config=config,
                firefly_client=firefly_client,
                allow_manual_prompt=False,
            )
            _persist_profile_updates(profile, session, profile_store)
            session_store.save(session)
            state["available"] = True
            state["ready"] = (
                bool(ready)
                and not _has_unresolved_records(session)
                and not _has_duplicate_blockers(session)
                and not _has_new_external_blockers(session)
            )
            state["message"] = message
            if not state["ready"]:
                return {
                    "ok": False,
                    "message": "Upload blocked. Resolve highlighted issues in the TUI, then refresh or upload again.",
                    "result": None,
                    "state": state,
                }

        result = submit_session(session, firefly_client=firefly_client, dry_run=dry_run)
        session_store.save(session)
        if not dry_run and not result.failures:
            append_history(profile_name=session.profile_name, records=session.records)
        message = (
            f"Dry run report ready: {result.report_path}"
            if dry_run
            else f"Upload complete: created={len(result.created_ids)} failures={len(result.failures)}"
        )
        state["ready"] = False
        state["message"] = message
        return {
            "ok": not result.failures,
            "message": message,
            "result": result,
            "state": state,
        }

    return submit


def _print_submit_summary(result: Any) -> None:
    """Internal helper for print submit summary."""
    print(f"Run report: {result.report_path}")
    print(f"Created transaction IDs: {len(result.created_ids)}")
    print(f"Failures: {len(result.failures)}")
    if result.failures:
        for item in result.failures:
            print(f"  - {item.get('row_id')}: {item.get('error')}")


def command_import(args: argparse.Namespace) -> int:
    """Handle command import."""
    ensure_runtime_dirs()
    config = _load_config_defaults()
    loaded = load_tabular_file(args.input_file, sheet_name=args.sheet)
    parsed = parse_with_wizard(loaded)

    profile_store = ProfileStore()
    profile_name = _choose_profile_name(
        store=profile_store,
        provided_name=args.profile,
        input_file=Path(args.input_file),
        file_type=loaded.file_type,
        header_signature=parsed.parse_hints.get("header_signature", {}),
    )
    profile = profile_store.load(profile_name) or Profile(name=profile_name)

    mapping = choose_column_mapping(
        headers=parsed.headers,
        existing_mapping=profile.column_mapping,
        interactive=_is_interactive(),
    )
    defaults = _collect_session_defaults(profile, config, Path(args.input_file))
    profile.defaults = defaults

    firefly_client = _try_build_firefly_client()
    account_names: list[str] = []
    account_catalog: list[dict[str, str]] = []
    alias_map = _load_account_aliases(profile, config)
    if firefly_client is not None:
        try:
            account_rows = firefly_client.list_accounts()
            account_names = list_account_names(account_rows)
            account_catalog = _extract_account_catalog(account_rows)
            source_identifier = defaults.get("source_account", "")
            if source_identifier:
                resolution = resolve_account_name(
                    source_identifier,
                    account_names=account_names,
                    aliases=alias_map,
                    account_catalog=account_catalog,
                )
                if resolution.resolved:
                    defaults["source_account"] = resolution.resolved
                    if _is_interactive():
                        print(
                            f"Resolved source account '{source_identifier}' -> '{resolution.resolved}' ({resolution.reason})."
                        )
                else:
                    chosen = _select_account_interactively(
                        identifier=source_identifier,
                        candidates=resolution.candidates,
                        account_names=account_names,
                    )
                    if chosen:
                        defaults["source_account"] = chosen
                        if _is_interactive():
                            print(f"Selected source account: {chosen}")
                            _remember_account_alias(
                                profile=profile,
                                profile_store=profile_store,
                                config=config,
                                aliases=alias_map,
                                identifier=source_identifier,
                                account_name=chosen,
                            )
                    else:
                        defaults["source_account"] = make_placeholder(source_identifier)
                        if _is_interactive():
                            print(
                                f"No source account match selected. Using placeholder: {defaults['source_account']}"
                            )
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: account resolution lookup failed: {exc}")

    records = normalize_rows_to_records(rows=parsed.rows, mapping=mapping, defaults=defaults)
    _resolve_accounts_for_records(
        records=records,
        account_names=account_names,
        aliases=alias_map,
        account_catalog=account_catalog,
    )
    attach_rule_suggestions(records, profile.rules)

    remote_fingerprints: set[str] = set()
    if firefly_client is not None:
        try:
            remote_fingerprints = fetch_remote_fingerprints(firefly_client, records)
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: remote duplicate lookup failed: {exc}")
    mark_duplicate_warnings(records=records, profile_name=profile_name, remote_fingerprints=remote_fingerprints)
    _validate_firefly_references(records=records, firefly_client=firefly_client)

    session_store = SessionStore()
    session = session_store.create(
        profile_name=profile_name,
        input_file=str(Path(args.input_file).resolve()),
        parse_hints=parsed.parse_hints,
        column_mapping=mapping,
        records=records,
    )

    print(f"\nSession started: {session.run_id}")
    print("Step 3/3 - Entering review app.")
    print("Resolve rows, then continue straight into upload/pairing inside the same TUI.")
    try:
        app = _run_review(
            session,
            session_store,
            load_account_catalog=_make_tui_account_catalog_loader(),
            prepare_upload=_make_tui_prepare_upload(profile, profile_store, session_store, config),
            submit_upload=_make_tui_submit_upload(
                profile,
                profile_store,
                session_store,
                config,
                dry_run=bool(args.dry_run),
            ),
        )
        session = app.session
    except RuntimeError as exc:
        print(str(exc))
        print(f"Session saved. Resume later with: python -m ff3_importer resume {session.run_id}")
        return 2

    _persist_profile_updates(profile, session, profile_store)
    session_store.save(session)

    if app.submit_result is not None:
        _print_submit_summary(app.submit_result)
        return 0

    if _has_unresolved_records(session):
        print(f"Session paused with unresolved rows. Resume with: python -m ff3_importer resume {session.run_id}")
        return 0

    print(f"TUI paused before upload. Resume later with: python -m ff3_importer resume {session.run_id}")
    return 0


def command_resume(args: argparse.Namespace) -> int:
    """Handle command resume."""
    ensure_runtime_dirs()
    config = _load_config_defaults()
    session_store = SessionStore()
    profile_store = ProfileStore()
    session = session_store.load(args.run_id)
    profile = profile_store.load(session.profile_name) or Profile(name=session.profile_name)
    attach_rule_suggestions(session.records, profile.rules)

    print(f"Resuming session: {session.run_id}")
    dry_run = bool(args.dry_run)
    try:
        app = _run_review(
            session,
            session_store,
            load_account_catalog=_make_tui_account_catalog_loader(),
            prepare_upload=_make_tui_prepare_upload(profile, profile_store, session_store, config),
            submit_upload=_make_tui_submit_upload(
                profile,
                profile_store,
                session_store,
                config,
                dry_run=dry_run,
            ),
            start_in_upload_if_resolved=not _has_unresolved_records(session),
        )
        session = app.session
    except RuntimeError as exc:
        print(str(exc))
        print(f"Session remains saved. Resume in TTY with: python -m ff3_importer resume {session.run_id}")
        return 2

    _persist_profile_updates(profile, session, profile_store)
    session_store.save(session)

    if app.submit_result is not None:
        _print_submit_summary(app.submit_result)
        return 0

    if _has_unresolved_records(session):
        print("Session still has unresolved rows; no upload performed.")
        return 0

    print(f"TUI paused before upload. Resume later with: python -m ff3_importer resume {session.run_id}")
    return 0


def command_rollback(args: argparse.Namespace) -> int:
    """Handle command rollback."""
    ensure_runtime_dirs()
    client = FireflyClient.from_env()
    rollback_report = rollback_run(args.run_id, firefly_client=client)
    print(f"Rollback report written for run {args.run_id}")
    for row in rollback_report.get("results", []):
        line = f"  - {row.get('transaction_id')}: {row.get('status')}"
        if row.get("error"):
            line += f" ({row.get('error')})"
        print(line)
    return 0


def command_profiles_list(_: argparse.Namespace) -> int:
    """Handle command profiles list."""
    ensure_runtime_dirs()
    store = ProfileStore()
    names = store.list_names()
    if not names:
        print("No profiles found.")
        return 0
    print("Profiles:")
    for name in names:
        print(f"  - {name}")
    return 0


def command_profiles_show(args: argparse.Namespace) -> int:
    """Handle command profiles show."""
    ensure_runtime_dirs()
    store = ProfileStore()
    profile = store.load(args.profile_name)
    if profile is None:
        print(f"Profile not found: {args.profile_name}")
        return 1
    print(f"Profile: {profile.name}")
    print(f"Parse hints: {profile.parse_hints}")
    print(f"Column mapping: {profile.column_mapping}")
    print(f"Defaults: {profile.defaults}")
    print(f"Rules: {len(profile.rules)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI argument parser."""
    parser = argparse.ArgumentParser(prog="ff3_importer", description="Interactive Firefly III importer.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Start a new import session.")
    import_parser.add_argument("input_file", help="Input CSV or XLSX file path.")
    import_parser.add_argument("--profile", help="Profile name (optional for CSV, auto-detected if omitted).")
    import_parser.add_argument("--sheet", help="Worksheet name for XLSX imports.", default=None)
    import_parser.add_argument("--dry-run", action="store_true", help="Generate payload report without API submit.")
    import_parser.set_defaults(handler=command_import)

    resume_parser = subparsers.add_parser("resume", help="Resume an existing import session.")
    resume_parser.add_argument("run_id", help="Session run ID.")
    resume_parser.add_argument("--dry-run", action="store_true", help="Do not submit to API.")
    resume_parser.set_defaults(handler=command_resume)

    rollback_parser = subparsers.add_parser("rollback", help="Rollback a submitted run by deleting created rows.")
    rollback_parser.add_argument("run_id", help="Run ID from runtime_data/runs/<run_id>.json")
    rollback_parser.set_defaults(handler=command_rollback)

    profiles_parser = subparsers.add_parser("profiles", help="Profile operations.")
    profiles_sub = profiles_parser.add_subparsers(dest="profiles_command", required=True)

    profiles_list = profiles_sub.add_parser("list", help="List profiles.")
    profiles_list.set_defaults(handler=command_profiles_list)

    profiles_show = profiles_sub.add_parser("show", help="Show profile detail.")
    profiles_show.add_argument("profile_name", help="Profile name.")
    profiles_show.set_defaults(handler=command_profiles_show)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))
