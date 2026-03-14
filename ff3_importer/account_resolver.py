"""Account-name and identifier matching helpers for Firefly III objects."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


PLACEHOLDER_PREFIX = "PLACEHOLDER::"


def is_placeholder(value: str) -> bool:
    """Return whether placeholder."""
    return value.strip().startswith(PLACEHOLDER_PREFIX)


def make_placeholder(value: str) -> str:
    """Handle make placeholder."""
    cleaned = " ".join(value.strip().split()) or "unknown"
    return f"{PLACEHOLDER_PREFIX}{cleaned}"


def _normalize_key(value: str) -> str:
    """Internal helper for normalize key."""
    return re.sub(r"\s+", " ", value.strip().lower())


def _digits_only(value: str) -> str:
    """Internal helper for digits only."""
    return re.sub(r"[^0-9]", "", value)


def _masked_to_suffix(value: str) -> str:
    """Internal helper for masked to suffix."""
    match = re.search(r"(?:x+|\*+)(\d{2,})$", value.strip(), flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _token_stop_words() -> set[str]:
    """Internal helper for token stop words."""
    return {
        "banking",
        "bank",
        "online",
        "payment",
        "transfer",
        "from",
        "to",
        "txn",
        "transaction",
        "conf",
        "id",
        "web",
        "for",
        "with",
        "at",
        "on",
        "the",
        "and",
        "or",
        "of",
        "in",
        "a",
        "an",
    }


def _extract_tokens(value: str) -> list[str]:
    """Internal helper for extract tokens."""
    tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]+", value)]
    stop_words = _token_stop_words()
    return [
        token
        for token in tokens
        if len(token) >= 3 and token not in stop_words and not token.isdigit()
    ]


def _numeric_tokens(value: str) -> list[str]:
    """Internal helper for numeric tokens."""
    found = []
    seen: set[str] = set()
    for token in re.findall(r"\d{4,}", value):
        if token not in seen:
            seen.add(token)
            found.append(token)
        suffix = token[-4:]
        if suffix not in seen:
            seen.add(suffix)
            found.append(suffix)
    return found


@dataclass
class AccountResolution:
    """Store the result of resolving an account reference."""
    resolved: str | None
    candidates: list[str]
    reason: str


def list_account_names(firefly_accounts: list[dict[str, Any]]) -> list[str]:
    """Return account names."""
    names: list[str] = []
    for row in firefly_accounts:
        name = str(row.get("attributes", {}).get("name", "")).strip()
        if name:
            names.append(name)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def _dedupe_names(values: list[str]) -> list[str]:
    """Internal helper for dedupe names."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _identifier_fields() -> tuple[str, ...]:
    """Internal helper for identifier fields."""
    return ("account_number", "iban", "bic")


def _catalog_name(row: dict[str, str]) -> str:
    """Internal helper for catalog name."""
    return str(row.get("name", "")).strip()


def _catalog_identifier_values(row: dict[str, str]) -> list[str]:
    """Internal helper for catalog identifier values."""
    values: list[str] = []
    for field_name in _identifier_fields():
        raw = str(row.get(field_name, "")).strip()
        if raw:
            values.append(raw)
    return values


def _token_name_candidates(value: str, account_names: list[str]) -> list[str]:
    """Internal helper for token name candidates."""
    tokens = _extract_tokens(value)
    if not tokens:
        return []
    token_candidates: list[str] = []
    for name in account_names:
        normalized = _normalize_key(name)
        if all(token in normalized for token in tokens):
            token_candidates.append(name)
    return _dedupe_names(token_candidates)


def _match_catalog_identifiers(
    identifier: str,
    account_catalog: list[dict[str, str]],
) -> AccountResolution | None:
    """Internal helper for match catalog identifiers."""
    normalized_value = _normalize_key(identifier)
    digit_tokens = _numeric_tokens(identifier)
    masked_suffix = _masked_to_suffix(identifier)
    if masked_suffix and masked_suffix not in digit_tokens:
        digit_tokens.append(masked_suffix)

    exact_matches: list[str] = []
    suffix_matches: list[str] = []
    partial_matches: list[str] = []

    for row in account_catalog:
        name = _catalog_name(row)
        if not name:
            continue
        for raw_identifier in _catalog_identifier_values(row):
            normalized_identifier = _normalize_key(raw_identifier)
            identifier_digits = _digits_only(raw_identifier)

            if normalized_value and normalized_identifier == normalized_value:
                exact_matches.append(name)
                continue

            if digit_tokens:
                matched = False
                for token in digit_tokens:
                    if not token:
                        continue
                    if identifier_digits and identifier_digits == token:
                        exact_matches.append(name)
                        matched = True
                        break
                    if identifier_digits and len(token) == 4 and identifier_digits.endswith(token):
                        suffix_matches.append(name)
                        matched = True
                        break
                    if identifier_digits and token in identifier_digits:
                        partial_matches.append(name)
                        matched = True
                        break
                if matched:
                    continue

            if normalized_value and normalized_value in normalized_identifier:
                partial_matches.append(name)

    exact_matches = _dedupe_names(exact_matches)
    suffix_matches = _dedupe_names(suffix_matches)
    partial_matches = _dedupe_names(partial_matches)

    for matches, reason in (
        (exact_matches, "identifier_exact"),
        (suffix_matches, "identifier_suffix"),
    ):
        if len(matches) == 1:
            return AccountResolution(resolved=matches[0], candidates=matches, reason=reason)

    matches = exact_matches or suffix_matches or partial_matches
    if matches:
        if len(matches) == 1:
            return AccountResolution(resolved=matches[0], candidates=matches, reason="identifier_single")
        return AccountResolution(resolved=None, candidates=matches[:10], reason="identifier_multi")
    return None


def _match_name_digits(identifier: str, account_names: list[str]) -> AccountResolution | None:
    """Internal helper for match name digits."""
    digit_tokens = _numeric_tokens(identifier)
    masked_suffix = _masked_to_suffix(identifier)
    if masked_suffix and masked_suffix not in digit_tokens:
        digit_tokens.append(masked_suffix)
    if not digit_tokens:
        return None

    for token in digit_tokens:
        if not token:
            continue
        exact_matches: list[str] = []
        suffix_matches: list[str] = []
        partial_matches: list[str] = []

        for name in account_names:
            normalized = _normalize_key(name)
            account_digits = _digits_only(name)
            if account_digits and account_digits == token:
                exact_matches.append(name)
                continue
            if account_digits and len(token) == 4 and account_digits.endswith(token):
                suffix_matches.append(name)
                continue
            if account_digits and token in account_digits:
                partial_matches.append(name)
                continue
            if token in normalized:
                partial_matches.append(name)

        for matches, reason in (
            (_dedupe_names(exact_matches), "digit_exact"),
            (_dedupe_names(suffix_matches), "digit_suffix"),
        ):
            if len(matches) == 1:
                return AccountResolution(
                    resolved=matches[0],
                    candidates=matches,
                    reason=reason,
                )

        matches = _dedupe_names(exact_matches) or _dedupe_names(suffix_matches) or _dedupe_names(partial_matches)
        if matches:
            if len(matches) == 1:
                return AccountResolution(
                    resolved=matches[0],
                    candidates=matches,
                    reason="digit_single",
                )
            return AccountResolution(resolved=None, candidates=matches[:10], reason="digit_multi")
    return None


def resolve_account_name(
    identifier: str,
    account_names: list[str],
    aliases: dict[str, str] | None = None,
    account_catalog: list[dict[str, str]] | None = None,
) -> AccountResolution:
    """Resolve account name."""
    aliases = aliases or {}
    account_catalog = account_catalog or []
    value = identifier.strip()
    if not value:
        return AccountResolution(resolved=None, candidates=[], reason="empty")

    # Resolution order is intentional: prefer user memory first, then an exact
    # Firefly name match, then server-side identifiers, and only then looser
    # numeric/name fallbacks. That keeps partial account numbers useful without
    # overriding a good name match.
    alias_match = aliases.get(_normalize_key(value))
    if alias_match:
        if alias_match in account_names:
            return AccountResolution(resolved=alias_match, candidates=[alias_match], reason="alias_exact")
        return AccountResolution(resolved=None, candidates=[], reason="alias_missing_target")

    lower_map = {_normalize_key(name): name for name in account_names}
    if _normalize_key(value) in lower_map:
        matched = lower_map[_normalize_key(value)]
        return AccountResolution(resolved=matched, candidates=[matched], reason="name_exact")

    token_candidates = _token_name_candidates(value, account_names)
    if len(token_candidates) == 1:
        return AccountResolution(
            resolved=token_candidates[0],
            candidates=token_candidates,
            reason="token_single",
        )

    identifier_match = _match_catalog_identifiers(value, account_catalog)
    if identifier_match is not None:
        return identifier_match

    name_digit_match = _match_name_digits(value, account_names)
    if name_digit_match is not None:
        return name_digit_match

    if token_candidates:
        return AccountResolution(resolved=None, candidates=token_candidates[:10], reason="token_multi")

    return AccountResolution(resolved=None, candidates=[], reason="no_match")
