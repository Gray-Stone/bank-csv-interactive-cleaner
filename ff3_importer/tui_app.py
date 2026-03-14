"""Textual TUI for review, pairing, and upload stages."""
from __future__ import annotations

import shutil
import re
from typing import Any, Callable
import textwrap
import math

from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.suggester import Suggester
from textual.widgets import Footer, Header, Input, Static

from .models import SessionState, TransactionRecord
from .rules import apply_suggestion, create_rule_from_record


EDITABLE_FIELDS = {
    "type",
    "date",
    "amount",
    "description",
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
}
EDITABLE_FIELD_ORDER = [
    "type",
    "date",
    "amount",
    "description",
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
]
EDITABLE_INDEX_TO_FIELD = {
    index: field_name for index, field_name in enumerate(EDITABLE_FIELD_ORDER, start=1)
}
FIELD_TO_INDEX = {name: index for index, name in EDITABLE_INDEX_TO_FIELD.items()}
TYPE_OPTIONS = ("withdrawal", "deposit", "transfer")
ACCOUNT_EDIT_FIELDS = {"source_account", "destination_account"}
FIELD_HINTS = {
    "type": "Transaction type. Use withdrawal for spending, deposit for incoming money, transfer for moves between your own accounts.",
    "date": "Transaction date. Prefer YYYY-MM-DD format.",
    "amount": "Signed amount. Negative is outflow and positive is inflow.",
    "description": "Human-readable description or memo from your bank export.",
    "source_account": "Source account name in Firefly III.",
    "destination_account": "Destination account or payee account name.",
    "payee": "Counterparty name for this transaction.",
    "currency": "Currency code such as USD.",
    "category": "Optional category label in Firefly III.",
    "budget": "Optional budget name in Firefly III.",
    "tags": "Comma-separated tags.",
    "notes": "Free-text notes for this transaction.",
    "external_id": "External reference ID from bank/export.",
    "internal_reference": "Internal reference value like check number.",
}


def command_token(command: str) -> str:
    """Handle command token."""
    safe = escape(command)
    return f"[b yellow]\\[{safe}\\][/b yellow]"


def plain_command_token(command: str) -> str:
    """Handle plain command token."""
    return f"[{command}]"


def parse_numeric_edit(raw: str) -> tuple[int, str] | None:
    """Parse numeric edit."""
    match = re.match(r"^\s*(\d+)(?:\s*:\s*|\s+)(.*)\s*$", raw)
    if not match:
        return None
    return int(match.group(1)), match.group(2).strip()


def parse_bare_field_index(raw: str) -> int | None:
    """Parse bare field index."""
    match = re.match(r"^\s*(\d+)$", raw)
    if not match:
        return None
    return int(match.group(1))


def parse_edit_context(raw: str) -> dict[str, Any] | None:
    """Parse edit context."""
    edit_numeric = re.match(r"^\s*edit\s+(\d+)(?:(\s*:\s*|\s+)(.*))?\s*$", raw, flags=re.IGNORECASE)
    if edit_numeric:
        field_index = int(edit_numeric.group(1))
        field_name = EDITABLE_INDEX_TO_FIELD.get(field_index)
        if field_name is None:
            return None
        separator = edit_numeric.group(2) or " "
        prefix = f"edit {field_index}: " if ":" in separator else f"edit {field_index} "
        return {
            "field_index": field_index,
            "field_name": field_name,
            "prefix": prefix,
            "query": (edit_numeric.group(3) or "").strip(),
            "style": "edit",
            "has_value_slot": edit_numeric.group(2) is not None,
        }

    edit_named = re.match(r"^\s*edit\s+([a-z_]+)(?:(\s+)(.*))?\s*$", raw, flags=re.IGNORECASE)
    if edit_named:
        field_name = edit_named.group(1).lower()
        if field_name not in EDITABLE_FIELDS:
            return None
        return {
            "field_index": FIELD_TO_INDEX.get(field_name, 0),
            "field_name": field_name,
            "prefix": f"edit {field_name} ",
            "query": (edit_named.group(3) or "").strip(),
            "style": "edit",
            "has_value_slot": edit_named.group(2) is not None,
        }

    numeric = re.match(r"^\s*(\d+)(?:(\s*:\s*|\s+)(.*))?\s*$", raw)
    if numeric:
        field_index = int(numeric.group(1))
        field_name = EDITABLE_INDEX_TO_FIELD.get(field_index)
        if field_name is None:
            return None
        separator = numeric.group(2) or " "
        prefix = f"{field_index}: " if ":" in separator else f"{field_index} "
        return {
            "field_index": field_index,
            "field_name": field_name,
            "prefix": prefix,
            "query": (numeric.group(3) or "").strip(),
            "style": "numeric",
            "has_value_slot": numeric.group(2) is not None,
        }

    set_match = re.match(r"^\s*set\s+([a-z_]+)(?:\s+(.*))?\s*$", raw, flags=re.IGNORECASE)
    if set_match:
        field_name = set_match.group(1).lower()
        if field_name not in EDITABLE_FIELDS:
            return None
        return {
            "field_index": FIELD_TO_INDEX.get(field_name, 0),
            "field_name": field_name,
            "prefix": f"set {field_name} ",
            "query": (set_match.group(2) or "").strip(),
            "style": "set",
            "has_value_slot": set_match.group(2) is not None,
        }
    return None


def rank_account_catalog(query: str, account_catalog: list[dict[str, str]], limit: int = 8) -> list[dict[str, str]]:
    """Rank account catalog."""
    normalized_query = " ".join(query.strip().lower().split())
    query_digits = re.sub(r"[^0-9]", "", normalized_query)
    query_tokens = [token for token in re.findall(r"[a-z0-9]+", normalized_query) if len(token) >= 2]

    ranked_by_name: list[tuple[int, int, str, dict[str, str]]] = []
    ranked_by_identifier: list[tuple[int, int, str, dict[str, str]]] = []
    for row in account_catalog:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        account_type = str(row.get("type", "")).strip().lower()
        normalized_name = " ".join(name.lower().split())
        digits = re.sub(r"[^0-9]", "", normalized_name)
        row_copy = {
            "name": name,
            "type": account_type,
            "account_number": str(row.get("account_number", "")).strip(),
            "iban": str(row.get("iban", "")).strip(),
            "bic": str(row.get("bic", "")).strip(),
        }

        name_score: int | None = None
        if not normalized_query:
            name_score = 999
        elif normalized_name == normalized_query:
            name_score = 0
        elif normalized_name.startswith(normalized_query):
            name_score = 15
        elif normalized_query in normalized_name:
            name_score = 25
        elif query_tokens and all(token in normalized_name for token in query_tokens):
            name_score = 30 + max(0, len(normalized_name) - len(normalized_query))

        if name_score is not None:
            ranked_by_name.append(
                (
                    name_score,
                    len(name),
                    normalized_name,
                    row_copy,
                )
            )
            continue

        identifier_score: int | None = None
        identifier_values = [
            str(row_copy.get("account_number", "")).strip().lower(),
            str(row_copy.get("iban", "")).strip().lower(),
            str(row_copy.get("bic", "")).strip().lower(),
        ]
        identifier_digits = [re.sub(r"[^0-9]", "", value) for value in identifier_values]
        if query_digits:
            for digits_value in identifier_digits:
                if not digits_value:
                    continue
                if digits_value == query_digits:
                    identifier_score = 5
                    break
                if len(query_digits) >= 4 and digits_value.endswith(query_digits):
                    identifier_score = 10
                    break
                if query_digits in digits_value:
                    identifier_score = 20
                    break
        if identifier_score is None and normalized_query:
            for identifier_value in identifier_values:
                if not identifier_value:
                    continue
                if identifier_value == normalized_query:
                    identifier_score = 6
                    break
                if normalized_query in identifier_value:
                    identifier_score = 22
                    break

        if identifier_score is not None:
            ranked_by_identifier.append(
                (
                    identifier_score,
                    len(name),
                    normalized_name,
                    row_copy,
                )
            )
            continue

        if query_digits and digits:
            fallback_score: int | None = None
            if digits == query_digits:
                fallback_score = 40
            elif len(query_digits) >= 4 and digits.endswith(query_digits):
                fallback_score = 45
            elif query_digits in digits:
                fallback_score = 50
            if fallback_score is not None:
                ranked_by_identifier.append(
                    (
                        fallback_score,
                        len(name),
                        normalized_name,
                        row_copy,
                    )
                )

    ranked = ranked_by_name if ranked_by_name else ranked_by_identifier
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return [row for _score, _length, _normalized, row in ranked[:limit]]


def picker_candidate_detail_text(candidate: dict[str, str]) -> str:
    """Handle picker candidate detail text."""
    name = str(candidate.get("name", "")).strip() or "-"
    account_type = str(candidate.get("type", "")).strip().lower() or "unknown"
    account_number = str(candidate.get("account_number", "")).strip() or "-"
    iban = str(candidate.get("iban", "")).strip() or "-"
    bic = str(candidate.get("bic", "")).strip() or "-"
    return (
        f"{name} | type={account_type} | acct={account_number} | iban={iban} | bic={bic}"
    )


class NumericPrefillSuggester(Suggester):
    """Provide live command suggestions for the TUI input."""
    def __init__(self, resolver: Callable[[str], str | None]) -> None:
        """Initialize the instance."""
        super().__init__(use_cache=False, case_sensitive=True)
        self._resolver = resolver

    async def get_suggestion(self, value: str) -> str | None:
        """Return the live suggestion for the current input value."""
        return self._resolver(value)


class ReviewApp(App[None]):
    """Drive the interactive review, pairing, and upload TUI."""
    TITLE = "Firefly III Import Review"
    CSS = """
    Screen {
      layout: vertical;
    }
    #summary {
      height: 3;
      border: round #4f8cc9;
      padding: 0 1;
    }
    #record {
      height: 1fr;
      border: round #7d7d7d;
      padding: 0 1;
      overflow-y: auto;
    }
    #warnings {
      height: 6;
      border: round #b5651d;
      padding: 0 1;
      overflow-y: auto;
    }
    #log {
      height: 6;
      border: round #2a9d8f;
      padding: 0 1;
      overflow-y: auto;
    }
    #picker {
      height: 7;
      border: round #5f6b77;
      padding: 0 1;
      overflow-y: auto;
    }
    """
    BINDINGS = [
        Binding("ctrl+c", "pause", "Pause"),
        Binding("up", "select_prev", "Prev"),
        Binding("down", "select_next", "Next"),
    ]

    def __init__(
        self,
        session: SessionState,
        on_change: Callable[[SessionState], None],
        load_account_catalog: Callable[[], dict[str, Any]] | None = None,
        prepare_upload: Callable[[SessionState], dict[str, Any]] | None = None,
        submit_upload: Callable[[SessionState], dict[str, Any]] | None = None,
        start_in_upload_if_resolved: bool = False,
    ) -> None:
        """Initialize the instance."""
        super().__init__()
        self.session = session
        self.on_change = on_change
        self.load_account_catalog = load_account_catalog
        self.prepare_upload = prepare_upload
        self.submit_upload = submit_upload
        self.start_in_upload_if_resolved = start_in_upload_if_resolved
        self.paused = False
        self.completed = False
        self.prefill_suggester = NumericPrefillSuggester(self._prefill_suggestion)
        self.mode = "review"
        self.upload_state: dict[str, Any] = {
            "available": False,
            "ready": False,
            "message": "Upload stage not prepared yet.",
            "account_names": [],
            "account_catalog": [],
        }
        self.submit_result: Any | None = None
        self.auto_enter_upload_requested = False
        self.quick_start_line = ""
        self.status_message = "Nothing is submitted yet. Finish local review, then continue into upload/pairing here."
        self.edit_history: list[str] = []
        self.picker_candidates: list[dict[str, str]] = []
        self.picker_active = False
        self.picker_field_name = ""
        self.picker_field_index = 0
        self.picker_query = ""
        self.picker_prefix = ""
        self.picker_selected_index = 0
        self.picker_row_offset = 0
        self.account_catalog_attempted = False

    def compose(self) -> ComposeResult:
        """Compose the Textual widget tree."""
        yield Header(show_clock=True)
        yield Static("", id="summary")
        yield Static("", id="record")
        yield Static("", id="warnings")
        yield Static("", id="log")
        yield Static("", id="picker")
        yield Input(
            placeholder="Enter command here, then press Enter (type 'help' for guide)",
            id="command",
            suggester=self.prefill_suggester,
        )
        yield Footer()

    def _safe_record(self) -> TransactionRecord | None:
        """Internal helper for safe record."""
        if not self.session.records:
            return None
        self.session.current_index = max(0, min(self.session.current_index, len(self.session.records) - 1))
        return self.session.records[self.session.current_index]

    def _terminal_width(self) -> int:
        """Internal helper for terminal width."""
        try:
            return max(60, int(self.size.width))
        except Exception:
            terminal = shutil.get_terminal_size(fallback=(120, 24))
            return max(60, terminal.columns)

    def _terminal_height(self) -> int:
        """Internal helper for terminal height."""
        try:
            return max(20, int(self.size.height))
        except Exception:
            return 24

    def _has_unresolved_records(self) -> bool:
        """Internal helper for has unresolved records."""
        return any(record.status in {"pending", "blocked_missing_ref"} for record in self.session.records)

    def _has_duplicate_blockers(self) -> bool:
        """Internal helper for has duplicate blockers."""
        for record in self.session.records:
            if record.status != "approved":
                continue
            if any("Potential duplicate detected" in warning for warning in record.warnings) and not record.duplicate_override:
                return True
        return False

    def _has_new_external_blockers(self) -> bool:
        """Internal helper for has new external blockers."""
        for record in self.session.records:
            if record.status != "approved":
                continue
            if (
                any(
                    warning.startswith("New external counterparty confirmation required:")
                    for warning in record.warnings
                )
                and not record.new_external_counterparty_override
            ):
                return True
        return False

    def _current_mode_label(self) -> str:
        """Internal helper for current mode label."""
        if self.mode == "upload":
            return "UPLOAD & PAIRING"
        return "INTERACTIVE REVIEW MODE"

    def _apply_mode_layout(self) -> None:
        """Internal helper for apply mode layout."""
        warnings_widget = self.query_one("#warnings", Static)
        log_widget = self.query_one("#log", Static)
        if self.mode == "upload":
            warnings_widget.styles.height = 8
            log_widget.styles.height = 5
        else:
            warnings_widget.styles.height = 6
            log_widget.styles.height = 6

    def _ensure_current_index(self) -> None:
        """Internal helper for ensure current index."""
        if not self.session.records:
            self.session.current_index = 0
            return
        self.session.current_index = max(0, min(self.session.current_index, len(self.session.records) - 1))

    def _enter_upload_mode(self, auto: bool = False) -> None:
        """Internal helper for enter upload mode."""
        if self.prepare_upload is None:
            if auto:
                self._log("Local review is complete. Configure Firefly credentials, then quit and resume for upload.")
            return
        self.mode = "upload"
        self._clear_picker()
        self.auto_enter_upload_requested = False
        self._ensure_current_index()
        self._refresh_upload_state()
        if auto:
            self.status_message = self.upload_state.get("message", "Upload stage ready.")
        else:
            self.status_message = "Upload stage ready. Review live pairing status, refresh, or upload."
        self._refresh()

    def _enter_review_mode(self) -> None:
        """Internal helper for enter review mode."""
        self.mode = "review"
        self._clear_picker()
        self.auto_enter_upload_requested = False
        self.status_message = "Back in local review mode. Edit rows or approve/skip as needed."
        self._refresh()

    def _refresh_upload_state(self) -> None:
        """Internal helper for refresh upload state."""
        if self.prepare_upload is None:
            self.upload_state = {
                "available": False,
                "ready": False,
                "message": "Upload stage is unavailable.",
                "account_names": [],
                "account_catalog": [],
            }
            return
        state = self.prepare_upload(self.session) or {}
        account_names = state.get("account_names", [])
        account_catalog = state.get("account_catalog", [])
        self.upload_state = {
            "available": bool(state.get("available", False)),
            "ready": bool(state.get("ready", False)),
            "message": str(state.get("message", "")),
            "account_names": [str(item) for item in account_names],
            "account_catalog": [
                {
                    "name": str(item.get("name", "")).strip(),
                    "type": str(item.get("type", "")).strip().lower(),
                    "account_number": str(item.get("account_number", "")).strip(),
                    "iban": str(item.get("iban", "")).strip(),
                    "bic": str(item.get("bic", "")).strip(),
                }
                for item in account_catalog
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            ],
        }

    def _cell_chunks(self, value: str, width: int) -> list[str]:
        """Internal helper for cell chunks."""
        if width <= 0:
            return [""]
        text = str(value)
        wrapped = textwrap.wrap(text, width=width, break_long_words=False, break_on_hyphens=False)
        if not wrapped:
            return [""]
        return wrapped

    def _fit_widths_to_space(self, natural_widths: list[int], available: int) -> list[int]:
        """Internal helper for fit widths to space."""
        if not natural_widths:
            return []
        widths = [max(1, width) for width in natural_widths]
        separators = 3 * (len(widths) - 1)
        total = sum(widths) + separators
        if total <= available:
            return widths

        floors = [1] * len(widths)
        while total > available:
            # Reduce widest still-reducible column first.
            candidate_index = -1
            candidate_slack = -1
            for index, width in enumerate(widths):
                slack = width - floors[index]
                if slack > candidate_slack:
                    candidate_slack = slack
                    candidate_index = index
            if candidate_index < 0 or candidate_slack <= 0:
                break
            widths[candidate_index] -= 1
            total -= 1

        return widths

    def _status_badge(self, status: str) -> tuple[str, str]:
        """Internal helper for status badge."""
        mapping = {
            "approved": ("A", "green"),
            "pending": ("P", "white"),
            "skipped": ("S", "cyan"),
            "blocked_missing_ref": ("B", "white on red"),
            "submitted": ("U", "green"),
            "submit_failed": ("F", "white on red"),
        }
        return mapping.get(status, ("?", "white"))

    def _upload_row_state(self, record: TransactionRecord) -> tuple[str, str]:
        """Internal helper for upload row state."""
        if record.status == "submitted":
            return ("UPLOADED", "green")
        if record.status == "submit_failed":
            return ("UPLOAD FAIL", "white on red")
        if record.status == "skipped":
            return ("SKIPPED", "cyan")
        if record.status == "blocked_missing_ref":
            return ("MISSING REF", "white on red")
        if any("Potential duplicate detected" in warning for warning in record.warnings) and not record.duplicate_override:
            return ("DUPLICATE", "yellow")
        if (
            any(
                warning.startswith("New external counterparty confirmation required:")
                for warning in record.warnings
            )
            and not record.new_external_counterparty_override
        ):
            return ("NEW EXT", "yellow")
        if any("Unresolved account reference" in warning for warning in record.warnings):
            return ("ACCOUNT", "red")
        if record.status == "approved":
            return ("READY", "white")
        return ("REVIEW", "white")

    def _display_account_value(self, value: str) -> str:
        """Internal helper for display account value."""
        raw = str(value).strip()
        if raw.startswith("PLACEHOLDER::"):
            return raw.split("::", 1)[1]
        return raw

    def _record_has_new_external_warning(self, record: TransactionRecord) -> bool:
        """Internal helper for record has new external warning."""
        return any(
            warning.startswith("New external counterparty confirmation required:")
            for warning in record.warnings
        ) and not record.new_external_counterparty_override

    def _upload_account_role(self, record: TransactionRecord, field_name: str) -> str:
        """Internal helper for upload account role."""
        if record.type == "transfer":
            return "internal"
        if record.type == "withdrawal":
            return "internal" if field_name == "source_account" else "external"
        if record.type == "deposit":
            return "external" if field_name == "source_account" else "internal"
        return "internal" if field_name == "source_account" else "external"

    def _upload_account_detail(self, record: TransactionRecord, field_name: str, account_names: set[str]) -> str:
        """Internal helper for upload account detail."""
        label = "Source" if field_name == "source_account" else "Destination"
        raw_value = str(getattr(record, field_name, "")).strip()
        display_value = escape(self._display_account_value(raw_value) or "-")
        role = self._upload_account_role(record, field_name)

        if not raw_value:
            return f"{label}: -"
        if raw_value in account_names:
            if role == "internal":
                return f"{label}: [green]{display_value}[/] [dim](matched Firefly internal)[/]"
            return f"{label}: [green]{display_value}[/] [dim](known in Firefly)[/]"
        if raw_value.startswith("PLACEHOLDER::") or role == "internal":
            return f"{label}: [red]{display_value}[/] [dim](needs Firefly account match)[/]"
        if self._record_has_new_external_warning(record):
            return f"{label}: [yellow]{display_value}[/] [dim](new external, use 'confirm-ext on')[/]"
        return f"{label}: {display_value} [dim](external/raw)[/]"

    def _upload_action_hint(self, record: TransactionRecord) -> str:
        """Internal helper for upload action hint."""
        if self._upload_view_read_only():
            return (
                f"Upload already completed for this run. Use {plain_command_token('quit')} to exit "
                f"or {plain_command_token('help')} for rollback instructions."
            )
        state_label, _ = self._upload_row_state(record)
        if state_label == "NEW EXT":
            return (
                f"{plain_command_token('confirm-ext on')} accepts this new external name. "
                f"Use {plain_command_token('edit 5')} / {plain_command_token('edit 6')} to edit accounts first if needed."
            )
        if state_label == "DUPLICATE":
            return (
                f"{plain_command_token('override-dup on')} allows this duplicate to upload. "
                f"Use {plain_command_token('edit 4')} to edit the description instead if it is a bad match."
            )
        if state_label in {"MISSING REF", "ACCOUNT"}:
            return (
                f"Edit {plain_command_token('edit 5')} source or {plain_command_token('edit 6')} destination, "
                f"then press {plain_command_token('Tab')} for Firefly matches."
            )
        if state_label == "READY":
            return (
                f"Row is ready. Use {plain_command_token('upload')} to submit all ready rows, "
                f"or {plain_command_token('edit 5')} / {plain_command_token('edit 6')} / {plain_command_token('edit 1')} to edit."
            )
        if state_label == "UPLOADED":
            return f"Row already uploaded. Use {plain_command_token('review')} only if you need to inspect earlier cleanup."
        if state_label == "SKIPPED":
            return f"Row is skipped. Use {plain_command_token('review')} if you want to change it."
        if state_label == "UPLOAD FAIL":
            return (
                f"Fix the row with {plain_command_token('edit 1')} / {plain_command_token('edit 4')} / "
                f"{plain_command_token('edit 5')} / {plain_command_token('edit 6')}, "
                f"then run {plain_command_token('refresh')} and {plain_command_token('upload')} again."
            )
        return (
            f"Review the selected row, edit with {plain_command_token('edit 1')} / {plain_command_token('edit 4')} / "
            f"{plain_command_token('edit 5')} / {plain_command_token('edit 6')}, then run {plain_command_token('refresh')} if needed."
        )

    def _upload_status_summary(self) -> str:
        """Internal helper for upload status summary."""
        blocked = sum(1 for record in self.session.records if record.status == "blocked_missing_ref")
        duplicates = sum(
            1
            for record in self.session.records
            if any("Potential duplicate detected" in warning for warning in record.warnings)
            and not record.duplicate_override
        )
        new_external = sum(
            1
            for record in self.session.records
            if self._record_has_new_external_warning(record)
        )
        if blocked == 0 and duplicates == 0 and new_external == 0 and self.upload_state.get("ready", False):
            return "Live sync: no blockers. Upload is ready."

        parts = [f"missing={blocked}", f"dup={duplicates}", f"new-ext={new_external}"]
        suffix = ""
        match = re.search(r"Auto-adjusted transaction type on (\d+) row", self.status_message)
        if match:
            suffix = f" | type-adjusted={match.group(1)}"
        return "Live sync: " + " ".join(parts) + suffix

    def _upload_view_read_only(self) -> bool:
        """Internal helper for upload view read only."""
        if self.mode != "upload":
            return False
        has_uploaded = any(record.status == "submitted" for record in self.session.records)
        if not has_uploaded:
            return False
        has_actionable = any(
            record.status in {"approved", "pending", "blocked_missing_ref", "submit_failed"}
            for record in self.session.records
        )
        return not has_actionable and not self._has_duplicate_blockers() and not self._has_new_external_blockers()

    def _upload_read_only_message(self) -> str:
        """Internal helper for upload read only message."""
        return (
            f"Upload is complete. This view is read-only. Use {plain_command_token('quit')} to exit "
            f"or {plain_command_token('help')} for rollback instructions."
        )

    def _upload_mode_hint(self, record: TransactionRecord | None) -> str:
        """Internal helper for upload mode hint."""
        if self._upload_view_read_only():
            return self._upload_read_only_message()
        if record is None:
            return (
                f"{plain_command_token('up')}/{plain_command_token('down')} move | "
                f"{plain_command_token('refresh')} reruns live checks | "
                f"{plain_command_token('upload')} submits ready rows"
            )
        return self._upload_action_hint(record)

    def _upload_quick_actions(self, record: TransactionRecord | None) -> str:
        """Internal helper for upload quick actions."""
        if self._upload_view_read_only():
            return (
                "Actions: "
                + " | ".join(
                    [
                        f"{command_token('up')}/{command_token('down')} move",
                        f"{command_token('jump <n>')} inspect row",
                        f"{command_token('help')} rollback info",
                        f"{command_token('quit')} exit",
                    ]
                )
            )
        actions = [
            f"{command_token('up')}/{command_token('down')} move",
            f"{command_token('review')} local cleanup",
            f"{command_token('refresh')} live checks",
            f"{command_token('edit')} selected row",
        ]
        if record is not None:
            state_label, _ = self._upload_row_state(record)
            if state_label == "NEW EXT":
                actions.append(f"{command_token('confirm-ext on')} accept external")
            elif state_label == "DUPLICATE":
                actions.append(f"{command_token('override-dup on')} allow duplicate")
            elif state_label in {"MISSING REF", "ACCOUNT"}:
                actions.append(f"{command_token('edit 5')} / {command_token('edit 6')} fix accounts")
            elif state_label == "READY":
                actions.append(f"{command_token('upload')} send ready rows")
            elif state_label == "UPLOAD FAIL":
                actions.append(f"{command_token('upload')} retry after fix")
        else:
            actions.append(f"{command_token('upload')} send ready rows")
        return "Actions: " + " | ".join(actions)

    def _build_upload_table(self, current_record: TransactionRecord) -> str:
        """Internal helper for build upload table."""
        if not self.session.records:
            return "No processed transactions."

        self._ensure_current_index()
        records = self.session.records
        table_width = max(72, self._terminal_width() - 4)
        row_number_width = max(2, len(str(len(records))))

        visible_capacity = max(6, self._terminal_height() - 18)
        current_pos = self.session.current_index
        start = max(0, current_pos - (visible_capacity // 2))
        end = min(len(records), start + visible_capacity)
        start = max(0, end - visible_capacity)
        visible_records = records[start:end]

        fixed_widths = {
            "status": 12,
            "date": 10,
            "amount": 12,
            "source": 20,
            "destination": 20,
        }
        available = max(
            20,
            table_width
            - (
                row_number_width
                + fixed_widths["status"]
                + fixed_widths["date"]
                + fixed_widths["amount"]
                + fixed_widths["source"]
                + fixed_widths["destination"]
                + (3 * 6)
            ),
        )
        description_width = available
        widths = [
            row_number_width,
            fixed_widths["status"],
            fixed_widths["date"],
            fixed_widths["amount"],
            fixed_widths["source"],
            fixed_widths["destination"],
            description_width,
        ]
        headers = ["#", "Status", "Date", "Amount", "Source", "Destination", "Description"]
        header_line = " | ".join(
            self._fit_inline(header, width)
            for header, width in zip(headers, widths, strict=True)
        )
        divider_line = "-+-".join("-" * width for width in widths)

        lines = [
            "Processed transactions for upload/pairing:",
            header_line,
            divider_line,
        ]

        account_names = set(self.upload_state.get("account_names", []))
        selected_row_style = "on #24323a"
        for index, record in enumerate(visible_records, start=start + 1):
            state_label, state_style = self._upload_row_state(record)
            source_value = self._display_account_value(record.source_account)
            destination_value = self._display_account_value(record.destination_account)

            cells_plain = [
                self._fit_inline(str(index), row_number_width),
                self._fit_inline(state_label, fixed_widths["status"]),
                self._fit_inline(record.date, fixed_widths["date"]),
                self._fit_inline(record.amount, fixed_widths["amount"]),
                self._fit_inline(source_value, fixed_widths["source"]),
                self._fit_inline(destination_value, fixed_widths["destination"]),
                self._fit_inline(record.description, description_width),
            ]
            cells_markup = list(cells_plain)
            cells_markup[1] = f"[{state_style}]{cells_plain[1]}[/]"
            if record.source_account in account_names:
                cells_markup[4] = f"[green]{cells_plain[4]}[/]"
            elif record.source_account.startswith("PLACEHOLDER::"):
                cells_markup[4] = f"[red]{cells_plain[4]}[/]"
            if record.destination_account in account_names:
                cells_markup[5] = f"[green]{cells_plain[5]}[/]"
            elif record.destination_account.startswith("PLACEHOLDER::"):
                cells_markup[5] = f"[red]{cells_plain[5]}[/]"

            rendered = " | ".join(cells_markup)
            if record.row_id == current_record.row_id:
                rendered = f"[{selected_row_style}]{rendered}[/]"
            lines.append(rendered)
        return "\n".join(lines)

    def _render_upload_details(self, record: TransactionRecord) -> str:
        """Internal helper for render upload details."""
        if self._upload_edit_prompt_active():
            return self._render_upload_edit_reference(record)

        state_label, _ = self._upload_row_state(record)
        warning_text = "; ".join(record.warnings[:2]) if record.warnings else "No live warnings."
        account_names = set(self.upload_state.get("account_names", []))
        action_text = self._trim_one_line(self._upload_action_hint(record), max(56, self._terminal_width() - 13))
        lines = [
            escape(
                f"Selected transaction | state={state_label} | record={record.row_id} | "
                f"source_row={record.source_row_index}"
            ),
            escape(
                f"[1] Type: {record.type or '-'} | [2] Date: {record.date or '-'} | "
                f"Amount: {record.amount or '-'} {record.currency or ''}".rstrip()
            ),
            self._upload_account_detail(record, "source_account", account_names).replace("Source:", "\\[5\\] Source:", 1),
            self._upload_account_detail(record, "destination_account", account_names).replace("Destination:", "\\[6\\] Destination:", 1),
            escape(
                f"Refs: [9] category={record.category or '-'} | [10] budget={record.budget or '-'} | "
                f"[13] ext={record.external_id or '-'} | [14] int={record.internal_reference or '-'}"
            ),
            escape(f"[4] Description: {self._trim_one_line(record.description or '-', max(56, self._terminal_width() - 22))}"),
            f"Action: {escape(action_text)}",
            f"Warnings: {escape(self._trim_one_line(warning_text, max(56, self._terminal_width() - 15)))}",
        ]
        return "\n".join(lines)

    def _build_raw_context(self, current_record: TransactionRecord) -> str:
        """Internal helper for build raw context."""
        if not self.session.records:
            return "No raw rows."
        ordered = sorted(self.session.records, key=lambda item: item.source_row_index)
        headers = list(ordered[0].raw.keys()) if ordered[0].raw else []
        if not headers:
            return "No raw columns."

        current_pos = next(
            (idx for idx, row in enumerate(ordered) if row.row_id == current_record.row_id),
            0,
        )
        start = max(0, current_pos - 2)
        end = min(len(ordered), current_pos + 3)

        visible_rows = ordered[start:end]
        raw_width = max(40, self._terminal_width() - 4)
        row_index_width = max(1, max(len(str(row.source_row_index)) for row in visible_rows))
        prefix_column_width = 2 + row_index_width
        available = max(8, raw_width - (prefix_column_width + 3))

        natural_widths: list[int] = []
        for header in headers:
            max_width = len(str(header))
            for row in visible_rows:
                value_width = len(str(row.raw.get(header, "")))
                if value_width > max_width:
                    max_width = value_width
            natural_widths.append(max(1, max_width))
        col_widths = self._fit_widths_to_space(natural_widths, available)

        # Use a muted slate background for the active row so selection is clear
        # without overpowering the table or competing with status colors.
        current_row_style = "on #24323a"

        lines = [
            "Raw file context (2 before / current / 2 after):",
        ]

        data_header_line = " | ".join(
            header_value.ljust(width)
            for header_value, width in zip(headers, col_widths, strict=True)
        )
        data_divider_line = "-" * len(data_header_line)
        header_prefix = " " * prefix_column_width
        lines.append(f"{header_prefix} | {data_header_line}")
        lines.append(f"{'-' * prefix_column_width}-+-{data_divider_line}")

        for row in visible_rows:
            is_current = row.row_id == current_record.row_id
            status_code, status_color = self._status_badge(row.status)
            slot_plain = f"{status_code} "
            slot_markup = f"[{status_color}]{status_code}[/] "

            row_number = f"{row.source_row_index:>{row_index_width}}"
            base_prefix = f"{slot_markup}{row_number} | "
            base_prefix_width = len(f"{slot_plain}{row_number} | ")
            row_values = [str(row.raw.get(header, "")) for header in headers]
            row_chunks = [
                self._cell_chunks(value=value, width=width)
                for value, width in zip(row_values, col_widths, strict=True)
            ]
            row_lines = max(len(chunks) for chunks in row_chunks)

            for line_index in range(row_lines):
                cells = []
                for chunks, width in zip(row_chunks, col_widths, strict=True):
                    chunk = chunks[line_index] if line_index < len(chunks) else ""
                    cells.append(chunk.ljust(width))
                line_prefix = base_prefix if line_index == 0 else " " * base_prefix_width
                rendered_line = f"{line_prefix}" + " | ".join(cells)
                if is_current:
                    rendered_line = f"[{current_row_style}]{rendered_line}[/]"
                lines.append(rendered_line)
        return "\n".join(lines)

    def _field_value_text(self, record: TransactionRecord, field_name: str) -> str:
        """Internal helper for field value text."""
        if field_name == "tags":
            return ", ".join(record.tags)
        return str(getattr(record, field_name, ""))

    def _upload_edit_prompt_active(self) -> bool:
        """Internal helper for upload edit prompt active."""
        if self._upload_view_read_only():
            return False
        raw = self._current_command_value().strip().lower()
        return raw == "edit" or raw.startswith("edit ")

    def _render_upload_edit_reference(self, record: TransactionRecord) -> str:
        """Internal helper for render upload edit reference."""
        detail_width = max(44, self._terminal_width() - 18)
        lines = [
            escape(f"Edit selected transaction | record={record.row_id} | source_row={record.source_row_index}"),
            (
                escape(
                    f"Syntax: {plain_command_token('edit 5 <value>')} or "
                    f"{plain_command_token('edit source_account <value>')}"
                )
            ),
            escape("[1] type    [2] date    [3] amount    [4] description"),
            escape("[5] source_account    [6] destination_account    [7] payee    [8] currency"),
            escape("[9] category    [10] budget    [11] tags    [12] notes"),
            escape("[13] external_id    [14] internal_reference"),
            (
                escape(
                    f"Current: [1] {record.type or '-'} | [5] {self._display_account_value(record.source_account) or '-'} | "
                    f"[6] {self._display_account_value(record.destination_account) or '-'}"
                )
            ),
            escape(f"[4] {self._trim_one_line(record.description or '-', detail_width)}"),
        ]
        return "\n".join(lines)

    def _fit_inline(self, value: str, width: int) -> str:
        """Internal helper for fit inline."""
        if width <= 0:
            return ""
        if len(value) <= width:
            return value.ljust(width)
        if width <= 3:
            return value[:width]
        return (value[: width - 3] + "...").ljust(width)

    def _render_editable_fields(self, record: TransactionRecord) -> list[str]:
        """Internal helper for render editable fields."""
        lines = ["Editable fields (shortcut: <number> <value> or <number>: <value>):"]
        long_indexes = [4, 11, 12]
        short_indexes = [index for index in EDITABLE_INDEX_TO_FIELD if index not in long_indexes]
        display_width = max(48, self._terminal_width() - 4)
        tall_layout = self._terminal_height() >= 48
        two_column = display_width >= 120 and not tall_layout
        column_width = max(20, (display_width - 3) // 2)
        right_width = max(20, display_width - column_width - 3)

        for idx in range(0, len(short_indexes), 2):
            left_index = short_indexes[idx]
            left_field = EDITABLE_INDEX_TO_FIELD[left_index]
            left_line = f"[{left_index}] {left_field}: {self._field_value_text(record, left_field)}"
            if two_column and idx + 1 < len(short_indexes):
                right_index = short_indexes[idx + 1]
                right_field = EDITABLE_INDEX_TO_FIELD[right_index]
                right_line = f"[{right_index}] {right_field}: {self._field_value_text(record, right_field)}"
                left_cell = self._fit_inline(left_line, column_width)
                right_cell = self._fit_inline(right_line, right_width)
                lines.append(f"{left_cell} | {right_cell}")
            else:
                lines.append(left_line)
                if idx + 1 < len(short_indexes):
                    right_index = short_indexes[idx + 1]
                    right_field = EDITABLE_INDEX_TO_FIELD[right_index]
                    right_line = f"[{right_index}] {right_field}: {self._field_value_text(record, right_field)}"
                    lines.append(right_line)

        wrap_width = max(36, display_width)
        for field_index in long_indexes:
            field_name = EDITABLE_INDEX_TO_FIELD[field_index]
            line = f"[{field_index}] {field_name}: {self._field_value_text(record, field_name)}"
            wrapped = textwrap.wrap(line, width=wrap_width, break_long_words=False, break_on_hyphens=False)
            lines.extend(wrapped or [line])

        return lines

    def _account_catalog(self) -> list[dict[str, str]]:
        """Internal helper for account catalog."""
        catalog = self.upload_state.get("account_catalog", [])
        if catalog:
            return [row for row in catalog if str(row.get("name", "")).strip()]
        return [{"name": name, "type": ""} for name in self.upload_state.get("account_names", [])]

    def _ensure_account_catalog_loaded(self) -> None:
        """Internal helper for ensure account catalog loaded."""
        if self._account_catalog() or self.account_catalog_attempted or self.load_account_catalog is None:
            return
        self.account_catalog_attempted = True
        state = self.load_account_catalog() or {}
        account_names = state.get("account_names", [])
        account_catalog = state.get("account_catalog", [])
        self.upload_state["account_names"] = [str(item) for item in account_names]
        self.upload_state["account_catalog"] = [
            {
                "name": str(item.get("name", "")).strip(),
                "type": str(item.get("type", "")).strip().lower(),
                "account_number": str(item.get("account_number", "")).strip(),
                "iban": str(item.get("iban", "")).strip(),
                "bic": str(item.get("bic", "")).strip(),
            }
            for item in account_catalog
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        ]
        message = str(state.get("message", "")).strip()
        if message and "not configured" in message.lower():
            self.status_message = message

    def _account_edit_context(self, raw: str) -> dict[str, Any] | None:
        """Internal helper for account edit context."""
        self._ensure_account_catalog_loaded()
        context = parse_edit_context(raw)
        if context is None:
            return None
        field_name = str(context.get("field_name", ""))
        if field_name not in ACCOUNT_EDIT_FIELDS:
            return None
        if not self._account_catalog():
            return None
        record = self._safe_record()
        current_value = self._field_value_text(record, field_name) if record is not None else ""
        context["current_value"] = current_value
        return context

    def _account_candidates_for_context(self, context: dict[str, Any]) -> list[dict[str, str]]:
        """Internal helper for account candidates for context."""
        query = str(context.get("query", "")).strip() or str(context.get("current_value", "")).strip()
        return rank_account_catalog(query, self._account_catalog())

    def _selected_picker_candidate(self) -> dict[str, str] | None:
        """Internal helper for selected picker candidate."""
        if not self.picker_candidates:
            return None
        index = max(0, min(self.picker_selected_index, len(self.picker_candidates) - 1))
        return self.picker_candidates[index]

    def _account_picker_signature(self) -> str:
        """Internal helper for account picker signature."""
        return f"{self.picker_field_name}|{self.picker_prefix}|{self.picker_query}"

    def _clear_picker(self) -> None:
        """Internal helper for clear picker."""
        self.picker_candidates = []
        self.picker_active = False
        self.picker_field_name = ""
        self.picker_field_index = 0
        self.picker_query = ""
        self.picker_prefix = ""
        self.picker_selected_index = 0
        self.picker_row_offset = 0

    def _sync_picker_from_input(self, raw: str) -> None:
        """Internal helper for sync picker from input."""
        if not self.picker_candidates:
            return
        context = self._account_edit_context(raw)
        if context is None:
            self._clear_picker()
            return
        query = str(context.get("query", "")).strip() or str(context.get("current_value", "")).strip()
        if (
            str(context.get("field_name", "")) != self.picker_field_name
            or str(context.get("prefix", "")) != self.picker_prefix
            or query != self.picker_query
        ):
            candidates = self._account_candidates_for_context(context)
            if not candidates:
                self._clear_picker()
                return
            self.picker_candidates = candidates
            self.picker_active = False
            self.picker_field_name = str(context.get("field_name", ""))
            self.picker_field_index = int(context.get("field_index", 0))
            self.picker_query = query
            self.picker_prefix = str(context.get("prefix", ""))
            self.picker_selected_index = 0
            self.picker_row_offset = 0

    def _account_completion_suggestion(self, raw: str) -> str | None:
        """Internal helper for account completion suggestion."""
        context = self._account_edit_context(raw)
        if context is None:
            return None
        query = str(context.get("query", "")).strip()
        if not query:
            return None
        candidates = self._account_candidates_for_context(context)
        if not candidates:
            return None
        return f"{context['prefix']}{candidates[0]['name']}"

    def _render_picker_panel(self) -> str:
        """Internal helper for render picker panel."""
        layout = self._picker_layout()
        if layout["visible_rows"] <= 0:
            return ""

        lines: list[str] = []
        start_row = self.picker_row_offset
        end_row = min(layout["rows_total"], start_row + layout["visible_rows"])
        gutter = self._picker_scroll_gutter(
            visible_rows=end_row - start_row,
            rows_total=layout["rows_total"],
            start_row=start_row,
        )

        for local_row, grid_row in enumerate(range(start_row, end_row)):
            cells: list[str] = []
            for column in range(layout["columns"]):
                candidate_index = grid_row * layout["columns"] + column
                if candidate_index >= len(self.picker_candidates):
                    cells.append(" " * layout["column_widths"][column])
                    continue
                candidate = self.picker_candidates[candidate_index]
                label = self._picker_cell_label(candidate)
                marker = ">" if self.picker_active and candidate_index == self.picker_selected_index else " "
                plain = self._fit_inline(f"{marker} {label}", layout["column_widths"][column])
                rendered = escape(plain)
                if self.picker_active and candidate_index == self.picker_selected_index:
                    rendered = f"[on #24323a]{rendered}[/]"
                cells.append(rendered)
            suffix = f" {gutter[local_row]}" if gutter else ""
            lines.append("   ".join(cells).rstrip() + suffix)
        return "\n".join(lines)

    def _update_picker_panel(self) -> None:
        """Internal helper for update picker panel."""
        picker_widget = self.query_one("#picker", Static)
        if self.picker_candidates:
            picker_widget.display = True
            picker_widget.update(self._render_picker_panel())
        else:
            picker_widget.display = False
            picker_widget.update("")

    def _picker_move(self, delta: int) -> None:
        """Internal helper for picker move."""
        if not self.picker_candidates:
            return
        self.picker_selected_index = (self.picker_selected_index + delta) % len(self.picker_candidates)
        self._ensure_picker_selection_visible()
        self._refresh()

    def _picker_layout(self) -> dict[str, Any]:
        """Internal helper for picker layout."""
        candidate_count = len(self.picker_candidates)
        if candidate_count == 0:
            return {"columns": 1, "rows_total": 0, "visible_rows": 0, "column_widths": [1]}

        visible_rows = self._picker_visible_rows()
        max_columns = min(candidate_count, max(1, self._terminal_width() // 18), 6)

        for columns in range(max_columns, 0, -1):
            rows_total = max(1, math.ceil(candidate_count / columns))
            visible_rows_clamped = min(visible_rows, rows_total)
            gutter_width = 2 if rows_total > visible_rows_clamped else 0
            available_width = max(20, self._terminal_width() - 8 - gutter_width)

            column_widths: list[int] = []
            for column in range(columns):
                labels = [
                    len(self._picker_cell_label(self.picker_candidates[index])) + 2
                    for index in range(column, candidate_count, columns)
                ]
                column_widths.append(max(labels) if labels else 12)

            total_width = sum(column_widths) + ((columns - 1) * 3)
            if total_width <= available_width:
                return {
                    "columns": columns,
                    "rows_total": rows_total,
                    "visible_rows": visible_rows_clamped,
                    "column_widths": column_widths,
                }

        rows_total = candidate_count
        visible_rows_clamped = min(visible_rows, rows_total)
        gutter_width = 2 if rows_total > visible_rows_clamped else 0
        available_width = max(20, self._terminal_width() - 8 - gutter_width)
        return {
            "columns": 1,
            "rows_total": rows_total,
            "visible_rows": visible_rows_clamped,
            "column_widths": [available_width],
        }

    def _picker_visible_rows(self) -> int:
        """Internal helper for picker visible rows."""
        try:
            widget_height = int(self.query_one("#picker", Static).size.height)
            return max(2, widget_height - 2)
        except Exception:
            return 5

    def _ensure_picker_selection_visible(self) -> None:
        """Internal helper for ensure picker selection visible."""
        layout = self._picker_layout()
        if layout["visible_rows"] <= 0:
            self.picker_row_offset = 0
            return
        selected_row = self.picker_selected_index // layout["columns"]
        max_offset = max(0, layout["rows_total"] - layout["visible_rows"])
        if selected_row < self.picker_row_offset:
            self.picker_row_offset = selected_row
        elif selected_row >= self.picker_row_offset + layout["visible_rows"]:
            self.picker_row_offset = selected_row - layout["visible_rows"] + 1
        self.picker_row_offset = max(0, min(self.picker_row_offset, max_offset))

    def _picker_scroll_gutter(
        self,
        *,
        visible_rows: int,
        rows_total: int,
        start_row: int,
    ) -> list[str]:
        """Internal helper for picker scroll gutter."""
        if rows_total <= visible_rows:
            return []
        thumb_size = max(1, (visible_rows * visible_rows) // rows_total)
        thumb_start = min(
            visible_rows - thumb_size,
            (start_row * visible_rows) // max(1, rows_total - visible_rows),
        )
        gutter: list[str] = []
        for row in range(visible_rows):
            char = "#"
            if row < thumb_start or row >= thumb_start + thumb_size:
                char = "|"
            if row == 0 and start_row > 0:
                char = "^"
            if row == visible_rows - 1 and start_row + visible_rows < rows_total:
                char = "v"
            gutter.append(char)
        return gutter

    def _picker_cell_label(self, candidate: dict[str, str]) -> str:
        """Internal helper for picker cell label."""
        name = str(candidate.get("name", "")).strip()
        if not name:
            return "-"
        account_number = str(candidate.get("account_number", "")).strip()
        if account_number:
            return f"{name} {account_number}"
        return name

    def _picker_move_vertical(self, delta_rows: int) -> None:
        """Internal helper for picker move vertical."""
        if not self.picker_candidates:
            return
        layout = self._picker_layout()
        target = self.picker_selected_index + (delta_rows * layout["columns"])
        if target < 0:
            target = self.picker_selected_index % layout["columns"]
        elif target >= len(self.picker_candidates):
            target = len(self.picker_candidates) - 1
        self.picker_selected_index = max(0, target)
        self._ensure_picker_selection_visible()
        self._refresh()

    def _apply_picker_selection(self, input_widget: Input) -> bool:
        """Internal helper for apply picker selection."""
        if not self.picker_candidates:
            return False
        candidate = self.picker_candidates[self.picker_selected_index]
        value = f"{self.picker_prefix}{candidate['name']}"
        input_widget.value = value
        input_widget.cursor_position = len(value)
        self.picker_active = False
        self.status_message = (
            f"Inserted server account into [{self.picker_field_index}] {self.picker_field_name}. "
            "Press Enter again to apply."
        )
        self._clear_picker()
        self._refresh()
        return True

    def _prefill_suggestion(self, raw: str) -> str | None:
        """Internal helper for prefill suggestion."""
        if self.mode == "upload":
            stripped = raw.strip().lower()
            if not (stripped == "edit" or stripped.startswith("edit ")):
                return None

        account_suggestion = self._account_completion_suggestion(raw)
        if account_suggestion:
            return account_suggestion
        account_context = self._account_edit_context(raw)
        if account_context is not None and not str(account_context.get("query", "")).strip():
            # For live account-backed fields, reserve Tab for server matches/picker
            # instead of overwriting the command with the current field value.
            return None

        bare_index = re.match(r"^\s*(\d+)\s*$", raw)
        if bare_index:
            field_index = int(bare_index.group(1))
            field_name = EDITABLE_INDEX_TO_FIELD.get(field_index)
            if field_name is None:
                return None
            record = self._safe_record()
            if record is None:
                return None
            return f"{field_index} {self._field_value_text(record, field_name)}"

        context = parse_edit_context(raw)
        if context is None:
            return None
        field_name = str(context.get("field_name", ""))
        record = self._safe_record()
        if record is None:
            return None
        suggested = f"{str(context.get('prefix', ''))}{self._field_value_text(record, field_name)}"
        if suggested.casefold().startswith(raw.casefold()):
            return suggested
        if bool(context.get("has_value_slot")):
            return suggested
        return None

    def _context_help_for_input(self, raw: str) -> str:
        """Internal helper for context help for input."""
        stripped = raw.strip()
        if not stripped:
            return ""

        context = parse_edit_context(raw)
        if context is None:
            return ""

        field_name = str(context.get("field_name", ""))
        field_index = int(context.get("field_index", 0))

        if field_name == "type":
            return (
                f"[{field_index or 1}] type enum: withdrawal | deposit | transfer. "
                "Use: '<n> withdrawal' or 'set type withdrawal'."
            )

        if field_name in ACCOUNT_EDIT_FIELDS:
            account_context = self._account_edit_context(raw)
            if account_context is not None:
                query = str(account_context.get("query", "")).strip() or str(account_context.get("current_value", "")).strip()
                candidates = self._account_candidates_for_context(account_context)
                if candidates:
                    if self.picker_candidates:
                        selected = self._selected_picker_candidate() or candidates[0]
                        action_hint = (
                            "Use arrows and Enter in picker."
                            if self.picker_active
                            else "Tab again enters picker."
                        )
                        return (
                            f"[{field_index}] {field_name} selected match: "
                            f"{picker_candidate_detail_text(selected)}. {action_hint}"
                        )
                    preview = " | ".join(
                        self._trim_one_line(picker_candidate_detail_text(item), 48)
                        for item in candidates[:3]
                    )
                    return (
                        f"[{field_index}] {field_name} server matches: {preview}. "
                        "Tab shows matches; Tab again enters picker."
                    )
                return (
                    f"[{field_index}] {field_name}: no Firefly account match for "
                    f"'{query or '(empty)'}'."
                )

        description = FIELD_HINTS.get(field_name, "Editable field.")
        if field_index:
            return f"[{field_index}] {field_name}: {description}"
        return f"{field_name}: {description}"

    def _trim_one_line(self, text: str, max_length: int) -> str:
        """Internal helper for trim one line."""
        single_line = " ".join(text.split())
        if len(single_line) <= max_length:
            return single_line
        return single_line[: max_length - 3].rstrip() + "..."

    def _render_log_panel(self) -> None:
        """Internal helper for render log panel."""
        log_widget = self.query_one("#log", Static)
        context_help = self._context_help_for_input(self._current_command_value())
        max_hint_len = max(50, self._terminal_width() - 26)
        max_status_len = max(50, self._terminal_width() - 12)
        record = self._safe_record()
        if self.mode == "upload":
            quick_start = self._upload_quick_actions(record)
        else:
            quick_start = (
                "Actions: a=approve s=skip n=next b=back jump <n> set <field> <value> <number> <value>"
            )
        rows: list[str] = [quick_start]
        if context_help:
            rows.append(f"[b yellow]Hint:[/b yellow] {escape(self._trim_one_line(context_help, max_hint_len))}")
        else:
            if self.mode == "upload":
                rows.append(f"[dim]Hint:[/dim] {escape(self._trim_one_line(self._upload_mode_hint(record), max_hint_len))}")
            else:
                rows.append("[dim]Hint:[/dim] Type 'help' for command syntax.")
        if self.mode == "upload" and self.status_message.startswith("Live Firefly checks"):
            status_line = self._upload_status_summary()
        else:
            status_line = self.status_message
        rows.append(f"Status: {escape(self._trim_one_line(status_line, max_status_len))}")
        log_widget.tooltip = context_help if context_help else None
        log_widget.update("\n".join(rows))

    def _current_command_value(self) -> str:
        """Internal helper for current command value."""
        try:
            return self.query_one("#command", Input).value
        except Exception:
            return ""

    def _refresh(self) -> None:
        """Internal helper for refresh."""
        self._apply_mode_layout()
        total = len(self.session.records)
        approved = sum(1 for record in self.session.records if record.status == "approved")
        skipped = sum(1 for record in self.session.records if record.status == "skipped")
        pending = sum(1 for record in self.session.records if record.status == "pending")
        blocked = sum(1 for record in self.session.records if record.status == "blocked_missing_ref")
        uploaded = sum(1 for record in self.session.records if record.status == "submitted")
        failed = sum(1 for record in self.session.records if record.status == "submit_failed")
        duplicate = sum(
            1
            for record in self.session.records
            if any("Potential duplicate detected" in warning for warning in record.warnings) and not record.duplicate_override
        )
        new_external = sum(
            1
            for record in self.session.records
            if any(
                warning.startswith("New external counterparty confirmation required:")
                for warning in record.warnings
            )
            and not record.new_external_counterparty_override
        )
        ready_to_upload = max(0, approved - duplicate - new_external)
        if self.mode == "upload":
            summary = (
                f"[b yellow]{self._current_mode_label()}[/b yellow] | "
                f"Ready={ready_to_upload} Dup={duplicate} New={new_external} Missing={blocked} Uploaded={uploaded} Failed={failed} | "
                f"Row {self.session.current_index + 1}/{max(total, 1)} | "
                f"Run {self.session.run_id}"
            )
        else:
            summary = (
                f"[b yellow]{self._current_mode_label()}[/b yellow] | "
                f"A={approved} S={skipped} P={pending} B={blocked} | "
                f"Row {self.session.current_index + 1}/{max(total, 1)} | "
                f"Run {self.session.run_id}"
            )
        self.query_one("#summary", Static).update(summary)

        record = self._safe_record()
        if record is None:
            self.query_one("#record", Static).update("No records available.")
            self.query_one("#warnings", Static).update("")
            return

        if self.mode == "upload":
            record_text = self._build_upload_table(record)
        else:
            record_text = "\n".join(
                [
                    f"row_id: {record.row_id} | source_row_index: {record.source_row_index} | status: {record.status}",
                    "",
                    self._build_raw_context(record),
                    "",
                    *self._render_editable_fields(record),
                ]
            )
        self.query_one("#record", Static).update(record_text)

        if self.mode == "upload":
            self.query_one("#warnings", Static).update(self._render_upload_details(record))
        else:
            warnings = list(record.warnings)
            if record.rule_suggestions:
                warnings.append("Rule suggestions:")
                for idx, suggestion in enumerate(record.rule_suggestions[:3], start=1):
                    warnings.append(
                        f"  [{idx}] rule_id={suggestion.get('rule_id')} score={suggestion.get('score')} "
                        f"set_fields={suggestion.get('set_fields')}"
                    )
            if self.edit_history:
                if warnings:
                    warnings.append("")
                warnings.append("Recent edits:")
                for item in self.edit_history[-5:]:
                    warnings.append(f"  - {item}")
            self.query_one("#warnings", Static).update("\n".join(warnings) or "No warnings.")
        self._update_picker_panel()
        self._render_log_panel()

    def on_mount(self) -> None:
        """Handle on mount."""
        self._ensure_account_catalog_loaded()
        self._refresh()
        self.query_one("#command", Input).focus()
        if self.start_in_upload_if_resolved and not self._has_unresolved_records():
            self._enter_upload_mode(auto=True)

    def _log(self, message: str) -> None:
        """Internal helper for log."""
        self.status_message = message
        self._refresh()

    def _persist(self) -> None:
        """Internal helper for persist."""
        self.on_change(self.session)
        self._refresh()

    def _goto_next_pending(self) -> None:
        """Internal helper for goto next pending."""
        for index, record in enumerate(self.session.records):
            if record.status in {"pending", "blocked_missing_ref"}:
                self.session.current_index = index
                return
        self.completed = True
        self.auto_enter_upload_requested = self.mode == "review" and self.prepare_upload is not None

    def _approve_current(self) -> None:
        """Internal helper for approve current."""
        if self.mode == "upload" and self._upload_view_read_only():
            self._log(self._upload_read_only_message())
            return
        record = self._safe_record()
        if record is None:
            return
        if any("Potential duplicate" in warning for warning in record.warnings) and not record.duplicate_override:
            self._log("Duplicate warning present. Use 'override-dup on' before approving.")
            return
        record.status = "approved"
        self._goto_next_pending()
        self._persist()

    def _skip_current(self) -> None:
        """Internal helper for skip current."""
        if self.mode == "upload" and self._upload_view_read_only():
            self._log(self._upload_read_only_message())
            return
        record = self._safe_record()
        if record is None:
            return
        record.status = "skipped"
        self._goto_next_pending()
        self._persist()

    def _go_back(self) -> None:
        """Internal helper for go back."""
        self.session.current_index = max(0, self.session.current_index - 1)
        self._persist()

    def _go_forward(self) -> None:
        """Internal helper for go forward."""
        if not self.session.records:
            return
        self.session.current_index = min(len(self.session.records) - 1, self.session.current_index + 1)
        self._persist()

    def _jump_to_review_index(self, index: int) -> None:
        """Internal helper for jump to review index."""
        if index < 1 or index > len(self.session.records):
            self._log(f"Review index out of range: {index}")
            return
        self.session.current_index = index - 1
        self._persist()

    def _jump_to_source_row(self, source_row_index: int) -> bool:
        """Internal helper for jump to source row."""
        for index, record in enumerate(self.session.records):
            if record.source_row_index == source_row_index:
                self.session.current_index = index
                self._persist()
                return True
        return False

    def _jump_to(self, index: int) -> None:
        """Internal helper for jump to."""
        if self._jump_to_source_row(index):
            return
        self._jump_to_review_index(index)

    def _set_field(self, field_name: str, value: str) -> None:
        """Internal helper for set field."""
        if self.mode == "upload" and self._upload_view_read_only():
            self._log(self._upload_read_only_message())
            return
        record = self._safe_record()
        if record is None:
            return
        if field_name not in EDITABLE_FIELDS:
            self._log(f"Field is not editable: {field_name}")
            return
        self._clear_picker()
        if field_name == "type":
            normalized = value.strip().lower()
            if normalized not in TYPE_OPTIONS:
                options = ", ".join(TYPE_OPTIONS)
                self._log(f"Invalid type '{value}'. Allowed values: {options}")
                return
            value = normalized
        if field_name == "tags":
            record.tags = [item.strip() for item in value.split(",") if item.strip()]
        else:
            setattr(record, field_name, value)
        if field_name in {"type", "source_account", "destination_account"}:
            record.new_external_counterparty_override = False
        field_index = FIELD_TO_INDEX.get(field_name, 0)
        stored_value = self._field_value_text(record, field_name)
        edit_note = self._trim_one_line(
            f"row {record.source_row_index} [{field_index}] {field_name} = {stored_value}",
            120,
        )
        self.edit_history.append(edit_note)
        if len(self.edit_history) > 100:
            self.edit_history = self.edit_history[-100:]
        self.status_message = f"Updated [{field_index}] {field_name}."
        if self.mode == "upload":
            self.upload_state["ready"] = False
            self.upload_state["message"] = "Field updated. Use 'refresh' to rerun live Firefly pairing checks."
        if record.status == "blocked_missing_ref":
            if record.date and record.amount and record.description:
                record.status = "pending"
        self._persist()

    def _set_duplicate_override(self, enabled: bool) -> None:
        """Internal helper for set duplicate override."""
        if self.mode == "upload" and self._upload_view_read_only():
            self._log(self._upload_read_only_message())
            return
        record = self._safe_record()
        if record is None:
            return
        record.duplicate_override = enabled
        self._persist()

    def _set_new_external_override(self, enabled: bool) -> None:
        """Internal helper for set new external override."""
        if self.mode == "upload" and self._upload_view_read_only():
            self._log(self._upload_read_only_message())
            return
        record = self._safe_record()
        if record is None:
            return
        if not any(
            warning.startswith("New external counterparty confirmation required:")
            for warning in record.warnings
        ):
            self._log("Current row does not need new external counterparty confirmation.")
            return
        record.new_external_counterparty_override = enabled
        self._persist()

    def _apply_rule(self, index: int) -> None:
        """Internal helper for apply rule."""
        record = self._safe_record()
        if record is None:
            return
        if not record.rule_suggestions:
            self._log("No rule suggestions for current row.")
            return
        if index < 1 or index > len(record.rule_suggestions):
            self._log(f"Suggestion index out of range: {index}")
            return
        suggestion = record.rule_suggestions[index - 1]
        apply_suggestion(record, suggestion)
        self._persist()

    def _save_rule(self, fields: list[str]) -> None:
        """Internal helper for save rule."""
        record = self._safe_record()
        if record is None:
            return
        set_fields = {}
        for field_name in fields:
            if not hasattr(record, field_name):
                continue
            value = getattr(record, field_name)
            if isinstance(value, list):
                if value:
                    set_fields[field_name] = value
            elif value:
                set_fields[field_name] = value
        if not set_fields:
            self._log("No non-empty fields selected for rule creation.")
            return
        rule = create_rule_from_record(record, set_fields=set_fields)
        self.session.parse_hints.setdefault("new_rules", [])
        self.session.parse_hints["new_rules"].append(rule)
        self._persist()
        self._log(f"Saved new rule {rule['rule_id']} with fields {sorted(set_fields.keys())}")

    def _print_help(self) -> None:
        """Internal helper for print help."""
        if self.mode == "upload":
            if self._upload_view_read_only():
                self._log("Upload for this run is already complete. This view is read-only.")
                self._log("up/down or jump <n>: Inspect uploaded rows.")
                self._log("quit|q: Exit the TUI.")
                self._log(f"Rollback: python -m ff3_importer rollback {self.session.run_id}")
                return
            self._log("review: Return to local review mode.")
            self._log("refresh: Re-run live Firefly pairing and duplicate checks.")
            self._log("upload|submit: Submit ready rows from inside the TUI.")
            self._log("up/down or n/b: Move the table selection.")
            self._log("edit <field> <value>: Edit the selected transaction row.")
            self._log("Account edits: type a partial account name, press Tab for matches, Tab again for the picker.")
            self._log("confirm-ext on|off: Confirm or revoke a first-seen external counterparty on this row.")
            self._log("approve|skip: Change selected row status without leaving upload stage.")
            self._log("quit|q: Pause and exit the TUI.")
            return
        self._log("approve|a: Approve current row and move to next unresolved row.")
        self._log("skip|s: Skip current row and move to next unresolved row.")
        self._log("next|n|f: Move to next row without changing approval status.")
        self._log("back|b: Move to previous row.")
        self._log("jump <n>: Jump by source row index n (fallback: review index n).")
        self._log("jump src <n> or jump idx <n>: Explicit source/review jump mode.")
        self._log("set <field> <value>: Edit a field on the current row.")
        self._log(
            "<number> <value> or <number>: <value>: Edit by numbered field, e.g. '4 Grocery' or '4: Grocery'."
        )
        self._log("Prefill: type '<number> ' or '<number>:' then press Tab to fill current value for quick edits.")
        self._log("In upload mode, account fields also use live Firefly matches from the server.")
        self._log("override-dup on|off: Allow/block approval when duplicate warning exists.")
        self._log("confirm-ext on|off: Confirm a first-seen external counterparty before upload.")
        self._log("applyrule [n]: Apply suggested rule n (default n=1).")
        self._log("saverule <field...>: Save selected current-row fields as a reusable rule.")
        self._log("quit|q: Pause and exit review (you can resume later).")

    def action_pause(self) -> None:
        """Handle action pause."""
        self.paused = True
        self.exit()

    def action_select_prev(self) -> None:
        """Handle action select prev."""
        if self.session.records:
            self.session.current_index = max(0, self.session.current_index - 1)
            self._refresh()

    def action_select_next(self) -> None:
        """Handle action select next."""
        if self.session.records:
            self.session.current_index = min(len(self.session.records) - 1, self.session.current_index + 1)
            self._refresh()

    def _run_upload_submit(self) -> None:
        """Internal helper for run upload submit."""
        if self._upload_view_read_only():
            self._log(self._upload_read_only_message())
            return
        if self.submit_upload is None:
            self._log("Upload is unavailable in this session.")
            return
        result = self.submit_upload(self.session) or {}
        state = result.get("state")
        if isinstance(state, dict):
            self.upload_state = {
                "available": bool(state.get("available", False)),
                "ready": bool(state.get("ready", False)),
                "message": str(state.get("message", "")),
                "account_names": [str(item) for item in state.get("account_names", [])],
                "account_catalog": [
                    {
                        "name": str(item.get("name", "")).strip(),
                        "type": str(item.get("type", "")).strip().lower(),
                        "account_number": str(item.get("account_number", "")).strip(),
                        "iban": str(item.get("iban", "")).strip(),
                        "bic": str(item.get("bic", "")).strip(),
                    }
                    for item in state.get("account_catalog", [])
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                ],
            }
        self.submit_result = result.get("result")
        self.status_message = str(result.get("message", "Upload action completed."))
        self._persist()

    def on_key(self, event: events.Key) -> None:
        """Handle on key."""
        command_input = self.query_one("#command", Input)
        if self.focused is not command_input:
            return
        if event.key == "escape" and self.picker_candidates:
            self._clear_picker()
            self._refresh()
            event.stop()
            event.prevent_default()
            return
        if self.picker_active and event.key in {"up", "down", "left", "right"}:
            if event.key == "up":
                self._picker_move_vertical(-1)
            elif event.key == "down":
                self._picker_move_vertical(1)
            elif event.key == "left":
                self._picker_move(-1)
            else:
                self._picker_move(1)
            event.stop()
            event.prevent_default()
            return
        if event.key == "tab":
            account_context = self._account_edit_context(command_input.value)
            if account_context is not None:
                query = str(account_context.get("query", "")).strip() or str(account_context.get("current_value", "")).strip()
                candidates = self._account_candidates_for_context(account_context)
                if candidates:
                    same_picker = (
                        self.picker_candidates
                        and self.picker_field_name == str(account_context.get("field_name", ""))
                        and self.picker_prefix == str(account_context.get("prefix", ""))
                        and self.picker_query == query
                    )
                    if not same_picker:
                        self.picker_candidates = candidates
                        self.picker_active = False
                        self.picker_field_name = str(account_context.get("field_name", ""))
                        self.picker_field_index = int(account_context.get("field_index", 0))
                        self.picker_query = query
                        self.picker_prefix = str(account_context.get("prefix", ""))
                        self.picker_selected_index = 0
                        self.picker_row_offset = 0
                        self.status_message = "Live Firefly account matches ready. Press Tab again to enter the picker."
                    elif not self.picker_active:
                        self.picker_active = True
                        self._ensure_picker_selection_visible()
                        self.status_message = "Account picker active. Use Up/Down and Enter to choose."
                    else:
                        self._picker_move(1)
                        self.status_message = "Account picker active. Use Up/Down and Enter to choose."
                    event.stop()
                    event.prevent_default()
                    self._refresh()
                    return

            suggestion = self._prefill_suggestion(command_input.value)
            if suggestion and suggestion != command_input.value:
                command_input.value = suggestion
                command_input.cursor_position = len(command_input.value)
                event.stop()
                event.prevent_default()
            self._refresh()
            return
        # Refresh after key processing so contextual hints appear as user types.
        self.set_timer(0.01, self._refresh)

    def on_resize(self, event: events.Resize) -> None:
        """Handle on resize."""
        self._refresh()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle on input changed."""
        input_widget = event.control
        if input_widget.id != "command":
            return
        self._sync_picker_from_input(input_widget.value)
        self._refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle on input submitted."""
        if self.picker_active and self.picker_candidates:
            if self._apply_picker_selection(event.input):
                return

        raw = event.value
        event.input.value = ""
        command_text = raw.strip()
        if not command_text:
            return

        # Once a run is fully uploaded, keep the screen inspect-only. That
        # avoids misleading local edits or accidental re-submit attempts after
        # Firefly already accepted the whole batch.
        read_only_upload = self.mode == "upload" and self._upload_view_read_only()
        if read_only_upload and (
            parse_bare_field_index(raw) is not None
            or parse_numeric_edit(raw) is not None
            or re.match(r"^\s*(edit|set)\b", raw, flags=re.IGNORECASE)
        ):
            self._log(self._upload_read_only_message())
            return

        if self.mode == "upload" and re.match(r"^\s*edit\s*$", raw, flags=re.IGNORECASE):
            self._log(
                f"Edit mode. Choose a field from the detail panel, then use {plain_command_token('edit 5 <value>')}."
            )
            return

        bare_field_index = parse_bare_field_index(raw)
        if bare_field_index is not None:
            if self.mode == "upload":
                self._log(
                    f"Use {plain_command_token('edit ' + str(bare_field_index) + ' <value>')} in upload mode."
                )
                return
            field_index = bare_field_index
            field_name = EDITABLE_INDEX_TO_FIELD.get(field_index)
            if field_name is not None:
                if field_name in ACCOUNT_EDIT_FIELDS and self._account_catalog():
                    prefill = f"{field_index} "
                    event.input.value = prefill
                    event.input.cursor_position = len(prefill)
                    self._log(
                        f"Editing [{field_index}] {field_name}. Type account text, then press Tab for Firefly matches."
                    )
                    return
                prefill = self._prefill_suggestion(raw) or f"{field_index} "
                event.input.value = prefill
                event.input.cursor_position = len(prefill)
                self._log(f"Editing [{field_index}] {field_name}.")
                return

        compact_jump = re.match(r"^\s*jump(\d+)\s*$", raw, flags=re.IGNORECASE)
        if compact_jump:
            command_text = f"jump {compact_jump.group(1)}"

        if self.mode == "upload":
            # Upload mode deliberately uses an explicit edit verb so navigation
            # commands and mutation commands stay visually distinct in the more
            # operational pairing/submission screen.
            edit_context = parse_edit_context(raw)
            if edit_context is not None and str(edit_context.get("style", "")) == "edit":
                field_name = str(edit_context.get("field_name", ""))
                field_index = int(edit_context.get("field_index", 0))
                if not bool(edit_context.get("has_value_slot")):
                    prefill = str(edit_context.get("prefix", ""))
                    event.input.value = prefill
                    event.input.cursor_position = len(prefill)
                    self._log(f"Editing [{field_index}] {field_name} in upload mode.")
                    return
                self._set_field(field_name, str(edit_context.get("query", "")))
                return
            if parse_numeric_edit(raw) is not None:
                self._log(f"Use {plain_command_token('edit <field> <value>')} in upload mode.")
                return

        numeric_edit = parse_numeric_edit(raw)
        if numeric_edit is not None:
            field_index, value = numeric_edit
            field_name = EDITABLE_INDEX_TO_FIELD.get(field_index)
            if field_name is None:
                self._log(f"Invalid field number: {field_index}")
                return
            self._set_field(field_name, value)
            return

        parts = command_text.split()
        command = parts[0].lower()
        args = parts[1:]

        if read_only_upload and command not in {"help", "h", "?", "quit", "q", "jump", "next", "n", "f", "forward", "back", "b"}:
            self._log(self._upload_read_only_message())
            return

        if command in {"approve", "a"}:
            self._approve_current()
        elif command in {"skip", "s"}:
            self._skip_current()
        elif command in {"next", "n", "f", "forward"}:
            self._go_forward()
        elif command in {"back", "b"}:
            self._go_back()
        elif command == "jump":
            if not args:
                self._log("Usage: jump <n>, jump src <n>, or jump idx <n>.")
                return
            if len(args) == 1:
                try:
                    self._jump_to(int(args[0]))
                except ValueError:
                    self._log("jump requires a numeric value.")
                return
            mode = args[0].lower()
            if len(args) >= 2 and args[1].isdigit():
                target = int(args[1])
                if mode in {"src", "source", "row"}:
                    if not self._jump_to_source_row(target):
                        self._log(f"Source row not found: {target}")
                elif mode in {"idx", "index", "review"}:
                    self._jump_to_review_index(target)
                else:
                    self._log("Unknown jump mode. Use src or idx.")
                return
            self._log("Usage: jump <n>, jump src <n>, or jump idx <n>.")
        elif command == "set" and len(args) >= 2:
            if self.mode == "upload":
                self._log(f"Use {plain_command_token('edit <field> <value>')} in upload mode.")
                return
            field_name = args[0]
            value = " ".join(args[1:])
            self._set_field(field_name, value)
        elif command in {"review", "local"}:
            self._enter_review_mode()
        elif command in {"upload-stage", "pairing", "server"}:
            self._enter_upload_mode(auto=False)
        elif command in {"refresh", "sync"}:
            if self.mode != "upload":
                self._log("refresh is only available in upload mode.")
                return
            self._refresh_upload_state()
            self.status_message = self.upload_state.get("message", "Upload stage refreshed.")
            self._refresh()
        elif command in {"upload", "submit", "u"}:
            if self.mode != "upload":
                self._log("upload is only available in upload mode.")
                return
            self._run_upload_submit()
        elif command == "override-dup" and args:
            state = args[0].lower() in {"true", "on", "1", "yes"}
            self._set_duplicate_override(state)
        elif command in {"confirm-ext", "override-ext"} and args:
            state = args[0].lower() in {"true", "on", "1", "yes"}
            self._set_new_external_override(state)
        elif command == "applyrule":
            index = 1
            if args:
                try:
                    index = int(args[0])
                except ValueError:
                    self._log("applyrule index must be numeric.")
                    return
            self._apply_rule(index)
        elif command == "saverule":
            if not args:
                self._log("saverule requires one or more fields (example: saverule category budget payee)")
                return
            self._save_rule(args)
        elif command in {"help", "h", "?"}:
            self._print_help()
        elif command in {"quit", "q"}:
            self.paused = True
            self.exit()
        else:
            self._log(f"Unknown command: {command}")

        if self.auto_enter_upload_requested and self.mode == "review" and self.prepare_upload is not None:
            self._enter_upload_mode(auto=True)
        elif self.completed and self.mode == "review":
            self._log("All rows are resolved (approved/skipped). You can quit to continue submit flow.")
