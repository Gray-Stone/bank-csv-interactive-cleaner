"""Tests for TUI shortcuts and rendering helpers."""
from __future__ import annotations

import unittest

from ff3_importer.models import SessionState, TransactionRecord
from ff3_importer.tui_app import (
    EDITABLE_INDEX_TO_FIELD,
    ReviewApp,
    plain_command_token,
    parse_bare_field_index,
    parse_edit_context,
    parse_numeric_edit,
    picker_candidate_detail_text,
    rank_account_catalog,
)


class TUIShortcutTests(unittest.TestCase):
    """Test cases for TUI shortcuts and upload rendering."""
    def test_parse_numeric_edit(self) -> None:
        """Test that parse numeric edit."""
        self.assertEqual(parse_numeric_edit("4: Coffee shop"), (4, "Coffee shop"))
        self.assertEqual(parse_numeric_edit("  10 : groceries "), (10, "groceries"))
        self.assertEqual(parse_numeric_edit("13   "), (13, ""))
        self.assertIsNone(parse_numeric_edit("set amount 10"))

    def test_parse_bare_field_index_requires_no_trailing_separator_space(self) -> None:
        """Test that parse bare field index requires no trailing separator space."""
        self.assertEqual(parse_bare_field_index("13"), 13)
        self.assertEqual(parse_bare_field_index("  13"), 13)
        self.assertIsNone(parse_bare_field_index("13 "))
        self.assertIsNone(parse_bare_field_index("13   "))

    def test_field_index_map(self) -> None:
        """Test that field index map."""
        self.assertEqual(EDITABLE_INDEX_TO_FIELD[1], "type")
        self.assertEqual(EDITABLE_INDEX_TO_FIELD[14], "internal_reference")

    def test_parse_edit_context_for_numeric_and_set(self) -> None:
        """Test that parse edit context for numeric and set."""
        numeric = parse_edit_context("5 House")
        self.assertEqual(numeric["field_name"], "source_account")
        self.assertEqual(numeric["field_index"], 5)
        self.assertEqual(numeric["prefix"], "5 ")
        self.assertEqual(numeric["query"], "House")

        setter = parse_edit_context("set destination_account Ever")
        self.assertEqual(setter["field_name"], "destination_account")
        self.assertEqual(setter["prefix"], "set destination_account ")
        self.assertEqual(setter["query"], "Ever")

        edit = parse_edit_context("edit 5 House")
        self.assertEqual(edit["field_name"], "source_account")
        self.assertEqual(edit["field_index"], 5)
        self.assertEqual(edit["prefix"], "edit 5 ")
        self.assertEqual(edit["query"], "House")
        self.assertEqual(edit["style"], "edit")

    def test_rank_account_catalog_prefers_digit_suffix_and_prefix(self) -> None:
        """Test that rank account catalog prefers digit suffix and prefix."""
        catalog = [
            {"name": "Primary Checking ****4101", "type": "asset", "account_number": "", "iban": "", "bic": ""},
            {"name": "Reserve Savings ****5202", "type": "asset", "account_number": "", "iban": "", "bic": ""},
            {"name": "POWER UTILITY", "type": "expense", "account_number": "", "iban": "", "bic": ""},
        ]

        ranked_digits = rank_account_catalog("5202", catalog)
        self.assertEqual(ranked_digits[0]["name"], "Reserve Savings ****5202")

        ranked_text = rank_account_catalog("power", catalog)
        self.assertEqual(ranked_text[0]["name"], "POWER UTILITY")

    def test_rank_account_catalog_uses_server_account_number_when_name_has_no_match(self) -> None:
        """Test that rank account catalog uses server account number when name has no match."""
        catalog = [
            {"name": "Main Checking", "type": "asset", "account_number": "xxxx4101", "iban": "", "bic": ""},
            {"name": "Vacation Savings", "type": "asset", "account_number": "xxxx5202", "iban": "", "bic": ""},
        ]

        ranked = rank_account_catalog("5202", catalog)

        self.assertEqual(ranked[0]["name"], "Vacation Savings")

    def test_picker_candidate_detail_text_includes_account_identifiers(self) -> None:
        """Test that picker candidate detail text includes account identifiers."""
        detail = picker_candidate_detail_text(
            {
                "name": "Main Checking",
                "type": "asset",
                "account_number": "xxxx4101",
                "iban": "DE123",
                "bic": "BOFAUS3N",
            }
        )

        self.assertIn("Main Checking", detail)
        self.assertIn("acct=xxxx4101", detail)
        self.assertIn("iban=DE123", detail)
        self.assertIn("bic=BOFAUS3N", detail)

    def test_account_prefill_defers_to_live_server_matches(self) -> None:
        """Test that account prefill defers to live server matches."""
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    source_account="Existing Account",
                    destination_account="Other Account",
                )
            ],
        )
        app = ReviewApp(session=session, on_change=lambda _session: None)
        app.upload_state["account_catalog"] = [
            {"name": "Primary Checking ****4101", "type": "asset"},
            {"name": "Reserve Savings ****5202", "type": "asset"},
        ]

        self.assertIsNone(app._prefill_suggestion("5 "))
        self.assertEqual(
            app._prefill_suggestion("5 check"),
            "5 Primary Checking ****4101",
        )

    def test_account_context_can_lazy_load_catalog(self) -> None:
        """Test that account context can lazy load catalog."""
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    source_account="Primary Checking 4101",
                    destination_account="",
                )
            ],
        )
        app = ReviewApp(
            session=session,
            on_change=lambda _session: None,
            load_account_catalog=lambda: {
                "available": True,
                "message": "loaded",
                "account_names": ["Primary Checking ****4101"],
                "account_catalog": [{"name": "Primary Checking ****4101", "type": "asset"}],
            },
        )

        context = app._account_edit_context("5 4101")

        self.assertEqual(context["field_name"], "source_account")
        self.assertEqual(app._prefill_suggestion("5 4101"), "5 Primary Checking ****4101")

    def test_context_help_shows_full_selected_picker_details(self) -> None:
        """Test that context help shows full selected picker details."""
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    source_account="Primary Checking 4101",
                    destination_account="",
                )
            ],
        )
        app = ReviewApp(session=session, on_change=lambda _session: None)
        app.upload_state["account_catalog"] = [
            {
                "name": "Main Checking",
                "type": "asset",
                "account_number": "xxxx4101",
                "iban": "DE123",
                "bic": "BOFAUS3N",
            }
        ]
        app.picker_candidates = list(app.upload_state["account_catalog"])
        app.picker_active = True

        hint = app._context_help_for_input("5 4101")

        self.assertIn("selected match", hint)
        self.assertIn("acct=xxxx4101", hint)
        self.assertIn("iban=DE123", hint)

    def test_upload_details_show_clear_account_and_action_labels(self) -> None:
        """Test that upload details show clear account and action labels."""
        record = TransactionRecord(
            row_id="row-19",
            source_row_index=19,
            status="approved",
            type="withdrawal",
            date="2025-12-23",
            amount="-12.00",
            description="Monthly Maintenance Fee",
            source_account="Primary Checking 4101",
            destination_account="Monthly Maintenance Fee",
            currency="USD",
            warnings=["New external counterparty confirmation required: Monthly Maintenance Fee"],
        )
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[record],
        )
        app = ReviewApp(session=session, on_change=lambda _session: None)
        app.mode = "upload"
        app.upload_state["account_names"] = ["Primary Checking 4101"]

        details = app._render_upload_details(record)

        self.assertIn("Selected transaction | state=NEW EXT", details)
        self.assertIn("matched Firefly internal", details)
        self.assertIn("new external, use 'confirm-ext on'", details)
        self.assertIn("Action: ", details)
        self.assertIn(plain_command_token("confirm-ext on"), details)
        self.assertIn(plain_command_token("edit 5"), app._upload_action_hint(record))

    def test_upload_details_switch_to_edit_reference_when_edit_is_typed(self) -> None:
        """Test that upload details switch to edit reference when edit is typed."""
        record = TransactionRecord(
            row_id="row-19",
            source_row_index=19,
            status="approved",
            type="withdrawal",
            description="Monthly Maintenance Fee",
            source_account="Primary Checking 4101",
            destination_account="Monthly Maintenance Fee",
        )
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[record],
        )
        app = ReviewApp(session=session, on_change=lambda _session: None)
        app.mode = "upload"
        app._current_command_value = lambda: "edit"  # type: ignore[method-assign]

        details = app._render_upload_details(record)

        self.assertIn("Edit selected transaction", details)
        self.assertIn("[5] source_account", details)
        self.assertIn(plain_command_token("edit 5 <value>"), details)

    def test_upload_status_summary_shortens_live_check_counts(self) -> None:
        """Test that upload status summary shortens live check counts."""
        approved = TransactionRecord(
            row_id="row-1",
            source_row_index=1,
            status="approved",
            warnings=["Potential duplicate detected (test)"],
        )
        blocked = TransactionRecord(
            row_id="row-2",
            source_row_index=2,
            status="blocked_missing_ref",
        )
        new_external = TransactionRecord(
            row_id="row-3",
            source_row_index=3,
            status="approved",
            warnings=["New external counterparty confirmation required: Example"],
        )
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[approved, blocked, new_external],
        )
        app = ReviewApp(session=session, on_change=lambda _session: None)
        app.mode = "upload"
        app.status_message = (
            "Live Firefly checks found 1 blocked account reference(s) and 1 duplicate warning(s) "
            "and 1 new external counterparty confirmation(s). Auto-adjusted transaction type on 2 row(s) "
            "from matched Firefly account types."
        )

        self.assertEqual(
            app._upload_status_summary(),
            "Live sync: missing=1 dup=1 new-ext=1 | type-adjusted=2",
        )

    def test_upload_quick_actions_include_row_specific_command(self) -> None:
        """Test that upload quick actions include row specific command."""
        record = TransactionRecord(
            row_id="row-3",
            source_row_index=3,
            status="approved",
            warnings=["New external counterparty confirmation required: Example"],
        )
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[record],
        )
        app = ReviewApp(session=session, on_change=lambda _session: None)
        app.mode = "upload"

        actions = app._upload_quick_actions(record)

        self.assertTrue(actions.startswith("Actions: "))
        self.assertIn("confirm-ext on", actions)

    def test_completed_upload_view_is_read_only(self) -> None:
        """Test that completed upload view is read only."""
        record = TransactionRecord(
            row_id="row-27",
            source_row_index=27,
            status="submitted",
            type="withdrawal",
            source_account="Primary Checking 4101",
            destination_account="CITY UTILITY",
        )
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[record],
        )
        app = ReviewApp(session=session, on_change=lambda _session: None)
        app.mode = "upload"

        self.assertTrue(app._upload_view_read_only())
        self.assertIn("read-only", app._upload_mode_hint(record))
        actions = app._upload_quick_actions(record)
        self.assertIn("quit", actions)
        self.assertNotIn("review", actions)
        self.assertNotIn("upload", actions)

    def test_completed_upload_submit_is_noop(self) -> None:
        """Test that completed upload submit is noop."""
        record = TransactionRecord(
            row_id="row-27",
            source_row_index=27,
            status="submitted",
        )
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[record],
        )
        called = {"count": 0}

        def submit(_session: SessionState) -> dict[str, object]:
            """Handle submit."""
            called["count"] += 1
            return {"message": "should not run"}

        app = ReviewApp(session=session, on_change=lambda _session: None, submit_upload=submit)
        app.mode = "upload"
        app._log = lambda message: setattr(app, "status_message", message)  # type: ignore[method-assign]
        app._run_upload_submit()

        self.assertEqual(called["count"], 0)
        self.assertIn("read-only", app.status_message)


if __name__ == "__main__":
    unittest.main()
