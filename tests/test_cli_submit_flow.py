"""Tests for live reconcile and submit preparation flows."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ff3_importer.cli import (
    _has_duplicate_blockers,
    _load_account_aliases,
    _live_reconcile_before_submit,
    _resolve_accounts_for_records,
    _save_global_account_alias,
    _save_profile_account_alias,
)
from ff3_importer.models import Profile, SessionState, TransactionRecord
from ff3_importer.profile_store import ProfileStore


class FakeFireflyClient:
    """Test double that mimics the Firefly API surface used by the CLI."""
    def __init__(
        self,
        *,
        account_names: list[str],
        account_types: dict[str, str] | None = None,
        account_numbers: dict[str, str] | None = None,
        category_names: list[str] | None = None,
        budget_names: list[str] | None = None,
        transactions: list[dict[str, object]] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._account_names = account_names
        self._account_types = account_types or {name: "asset" for name in account_names}
        self._account_numbers = account_numbers or {}
        self._category_names = category_names or []
        self._budget_names = budget_names or []
        self._transactions = transactions or []

    def list_accounts(self) -> list[dict[str, object]]:
        """Return accounts."""
        return [
            {
                "attributes": {
                    "name": name,
                    "type": self._account_types.get(name, "asset"),
                    "account_number": self._account_numbers.get(name, ""),
                    "iban": "",
                    "bic": "",
                }
            }
            for name in self._account_names
        ]

    def list_categories(self) -> list[dict[str, object]]:
        """Return categories."""
        return [{"attributes": {"name": name}} for name in self._category_names]

    def list_budgets(self) -> list[dict[str, object]]:
        """Return budgets."""
        return [{"attributes": {"name": name}} for name in self._budget_names]

    def list_transactions(self, start: str, end: str) -> list[dict[str, object]]:
        """Return transactions."""
        return list(self._transactions)


class CliSubmitFlowTests(unittest.TestCase):
    """Test cases for live reconcile and submit preparation."""
    def test_global_and_profile_aliases_merge_with_profile_override(self) -> None:
        """Test that global and profile aliases merge with profile override."""
        profile = Profile(name="bank-a", parse_hints={"account_aliases": {"4101": "Profile Checking"}})
        config = {"account_aliases": {"4101": "Global Checking", "1234": "Global Savings"}}

        merged = _load_account_aliases(profile, config)

        self.assertEqual(merged["4101"], "Profile Checking")
        self.assertEqual(merged["1234"], "Global Savings")

    def test_alias_save_targets_are_separate(self) -> None:
        """Test that alias save targets are separate."""
        profile = Profile(name="bank-a")
        config: dict[str, object] = {}

        self.assertTrue(_save_profile_account_alias(profile, "4101", "Profile Checking"))
        self.assertTrue(_save_global_account_alias(config, "1234", "Global Savings"))

        self.assertEqual(profile.parse_hints["account_aliases"]["4101"], "Profile Checking")
        self.assertEqual(config["account_aliases"]["1234"], "Global Savings")

    def test_resolve_accounts_handles_placeholder_value(self) -> None:
        """Test that resolve accounts handles placeholder value."""
        record = TransactionRecord(
            row_id="row-1",
            source_row_index=1,
            type="withdrawal",
            date="2026-03-01",
            amount="-5.00",
            description="Coffee",
            source_account="PLACEHOLDER::4101",
            destination_account="Cafe",
            status="approved",
        )
        _resolve_accounts_for_records(
            records=[record],
            account_names=["Primary Checking ****4101"],
            aliases={},
        )
        self.assertEqual(record.source_account, "Primary Checking ****4101")

    def test_live_reconcile_adds_remote_duplicate_warning(self) -> None:
        """Test that live reconcile adds remote duplicate warning."""
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
                    type="withdrawal",
                    date="2026-03-01",
                    amount="-5.00",
                    description="Coffee",
                    source_account="4101",
                    destination_account="Cafe",
                    status="approved",
                )
            ],
        )
        profile = Profile(name="bank-a")
        client = FakeFireflyClient(
            account_names=["Primary Checking ****4101"],
            transactions=[
                {
                    "attributes": {
                        "transactions": [
                            {
                                "date": "2026-03-01",
                                "amount": "-5.00",
                                "description": "Coffee",
                                "destination_name": "Cafe",
                            }
                        ]
                    }
                }
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            ok, message, context = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=store,
                config={},
                firefly_client=client,
            )

        self.assertTrue(ok)
        self.assertIn("duplicate warning", message)
        self.assertIn("Primary Checking ****4101", context["account_names"])
        self.assertEqual(context["account_catalog"][0]["name"], "Primary Checking ****4101")
        self.assertEqual(session.records[0].source_account, "Primary Checking ****4101")
        self.assertTrue(
            any("Potential duplicate detected" in warning for warning in session.records[0].warnings)
        )
        self.assertTrue(_has_duplicate_blockers(session))

    def test_live_reconcile_blocks_unresolved_transfer_account(self) -> None:
        """Test that live reconcile blocks unresolved transfer account."""
        session = SessionState(
            run_id="run-2",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    type="transfer",
                    date="2026-03-01",
                    amount="-40.00",
                    description="Transfer to savings",
                    source_account="4101",
                    destination_account="9999",
                    status="approved",
                )
            ],
        )
        profile = Profile(name="bank-a")
        client = FakeFireflyClient(account_names=["Primary Checking ****4101"])

        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            ok, message, context = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=store,
                config={},
                firefly_client=client,
            )

        self.assertTrue(ok)
        self.assertIn("blocked account reference", message)
        self.assertIn("Primary Checking ****4101", context["account_names"])
        self.assertEqual(session.records[0].status, "blocked_missing_ref")
        self.assertTrue(
            any("Missing Firefly references:" in warning for warning in session.records[0].warnings)
        )

    def test_live_reconcile_reinfers_asset_to_asset_as_transfer(self) -> None:
        """Test that live reconcile reinfers asset to asset as transfer."""
        session = SessionState(
            run_id="run-3",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    type="deposit",
                    date="2026-03-01",
                    amount="400.00",
                    description="Transfer from savings",
                    source_account="5202",
                    destination_account="4101",
                    status="approved",
                )
            ],
        )
        profile = Profile(name="bank-a")
        client = FakeFireflyClient(
            account_names=["Primary Checking ****4101", "Reserve Savings ****5202"],
            account_types={
                "Primary Checking ****4101": "asset",
                "Reserve Savings ****5202": "asset",
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            ok, message, _context = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=store,
                config={},
                firefly_client=client,
            )

        self.assertTrue(ok)
        self.assertIn("Auto-adjusted transaction type", message)
        self.assertEqual(session.records[0].type, "transfer")
        self.assertEqual(session.records[0].source_account, "Reserve Savings ****5202")
        self.assertEqual(session.records[0].destination_account, "Primary Checking ****4101")

    def test_live_reconcile_reinfers_asset_to_expense_as_withdrawal(self) -> None:
        """Test that live reconcile reinfers asset to expense as withdrawal."""
        session = SessionState(
            run_id="run-4",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    type="transfer",
                    date="2026-03-01",
                    amount="-163.20",
                    description="POWER UTILITY payment",
                    source_account="4101",
                    destination_account="POWER UTILITY",
                    status="approved",
                )
            ],
        )
        profile = Profile(name="bank-a")
        client = FakeFireflyClient(
            account_names=["Primary Checking ****4101", "POWER UTILITY"],
            account_types={
                "Primary Checking ****4101": "asset",
                "POWER UTILITY": "expense",
            },
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            ok, message, _context = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=store,
                config={},
                firefly_client=client,
            )

        self.assertTrue(ok)
        self.assertIn("Auto-adjusted transaction type", message)
        self.assertEqual(session.records[0].type, "withdrawal")
        self.assertEqual(session.records[0].source_account, "Primary Checking ****4101")
        self.assertEqual(session.records[0].destination_account, "POWER UTILITY")
        self.assertEqual(session.records[0].status, "approved")

    def test_live_reconcile_marks_new_external_withdrawal_destination_for_confirmation(self) -> None:
        """Test that live reconcile marks new external withdrawal destination for confirmation."""
        session = SessionState(
            run_id="run-5",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    type="withdrawal",
                    date="2026-03-01",
                    amount="-163.20",
                    description="Utility payment",
                    source_account="4101",
                    destination_account="POWER UTILITY",
                    status="approved",
                )
            ],
        )
        profile = Profile(name="bank-a")
        client = FakeFireflyClient(account_names=["Primary Checking ****4101"])

        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            ok, message, _context = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=store,
                config={},
                firefly_client=client,
            )

        self.assertTrue(ok)
        self.assertIn("new external counterparty confirmation", message)
        self.assertEqual(session.records[0].status, "approved")
        self.assertTrue(
            any(
                "New external counterparty confirmation required: destination_account=POWER UTILITY"
                in warning
                for warning in session.records[0].warnings
            )
        )

    def test_live_reconcile_marks_new_external_deposit_source_for_confirmation(self) -> None:
        """Test that live reconcile marks new external deposit source for confirmation."""
        session = SessionState(
            run_id="run-6",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    type="deposit",
                    date="2026-03-01",
                    amount="80.00",
                    description="Friend paid back dinner",
                    source_account="PLACEHOLDER::P2P CREDIT 9988",
                    destination_account="4101",
                    status="approved",
                )
            ],
        )
        profile = Profile(name="bank-a")
        client = FakeFireflyClient(account_names=["Primary Checking ****4101"])

        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            ok, message, _context = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=store,
                config={},
                firefly_client=client,
            )

        self.assertTrue(ok)
        self.assertIn("new external counterparty confirmation", message)
        self.assertEqual(session.records[0].status, "approved")
        self.assertEqual(session.records[0].source_account, "P2P CREDIT 9988")
        self.assertTrue(
            any(
                "New external counterparty confirmation required: source_account=P2P CREDIT 9988"
                in warning
                for warning in session.records[0].warnings
            )
        )

    def test_live_reconcile_uses_server_account_number_when_name_has_no_match(self) -> None:
        """Test that live reconcile uses server account number when name has no match."""
        session = SessionState(
            run_id="run-7",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[
                TransactionRecord(
                    row_id="row-1",
                    source_row_index=1,
                    type="deposit",
                    date="2026-03-01",
                    amount="400.00",
                    description="Transfer from SAV 5202",
                    source_account="5202",
                    destination_account="4101",
                    status="approved",
                )
            ],
        )
        profile = Profile(name="bank-a")
        client = FakeFireflyClient(
            account_names=["Main Checking", "Vacation Savings"],
            account_types={"Main Checking": "asset", "Vacation Savings": "asset"},
            account_numbers={"Main Checking": "xxxx4101", "Vacation Savings": "xxxx5202"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            ok, _message, _context = _live_reconcile_before_submit(
                session=session,
                profile=profile,
                profile_store=store,
                config={},
                firefly_client=client,
            )

        self.assertTrue(ok)
        self.assertEqual(session.records[0].source_account, "Vacation Savings")
        self.assertEqual(session.records[0].destination_account, "Main Checking")
        self.assertEqual(session.records[0].type, "transfer")


if __name__ == "__main__":
    unittest.main()
