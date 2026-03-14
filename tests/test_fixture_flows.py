"""Fixture-driven tests for realistic import shapes."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ff3_importer.io_loader import load_tabular_file
from ff3_importer.mapping import normalize_rows_to_records
from ff3_importer.parse_wizard import parse_with_wizard


FIXTURES = Path(__file__).with_name("fixtures")


class FixtureFlowTests(unittest.TestCase):
    """Test realistic fixture-based import flows."""

    def test_checking_csv_fixture_parses_metadata_and_normalizes_rows(self) -> None:
        """Test that the checking fixture survives parsing and row normalization."""
        loaded = load_tabular_file(str(FIXTURES / "checking_statement_with_metadata.csv"))
        with patch("ff3_importer.parse_wizard._is_interactive", return_value=False):
            parsed = parse_with_wizard(loaded)

        self.assertEqual(parsed.headers, ["Date", "Description", "Amount", "Running Bal."])
        self.assertEqual(parsed.parse_hints["header_row_index"], 6)
        self.assertEqual(parsed.parse_hints["first_data_row_index"], 7)
        self.assertEqual(len(parsed.rows), 7)

        records = normalize_rows_to_records(
            rows=parsed.rows,
            mapping={"date": "Date", "description": "Description", "amount": "Amount"},
            defaults={"source_account": "Demo Checking 4101", "currency": "USD"},
        )

        self.assertEqual(len(records), 7)
        self.assertEqual(records[1].type, "withdrawal")
        self.assertEqual(records[1].source_account, "Demo Checking 4101")
        self.assertEqual(records[1].destination_account, "POWER UTILITY")
        self.assertEqual(records[1].external_id, "XXXXX11111")

        self.assertEqual(records[2].type, "deposit")
        self.assertEqual(records[2].source_account, "Taylor Example")
        self.assertEqual(records[2].destination_account, "Demo Checking 4101")
        self.assertEqual(records[2].external_id, "zelle1234")

    def test_credit_card_csv_fixture_keeps_payment_source_on_deposit(self) -> None:
        """Test that a card-payment credit keeps the external source account."""
        loaded = load_tabular_file(str(FIXTURES / "credit_card_activity.csv"))
        with patch("ff3_importer.parse_wizard._is_interactive", return_value=False):
            parsed = parse_with_wizard(loaded)

        records = normalize_rows_to_records(
            rows=parsed.rows,
            mapping={
                "date": "Posted Date",
                "description": "Payee",
                "amount": "Amount",
                "external_id": "Reference Number",
            },
            defaults={"source_account": "Rewards Card 6303", "currency": "USD"},
        )

        payment = records[2]
        self.assertEqual(payment.type, "deposit")
        self.assertEqual(payment.source_account, "CHK 4444")
        self.assertEqual(payment.destination_account, "Rewards Card 6303")
        self.assertEqual(payment.external_id, "REF00003")

    def test_xlsx_fixture_loads_and_parses_first_sheet(self) -> None:
        """Test that the XLSX fixture loads and exposes the activity header row."""
        loaded = load_tabular_file(str(FIXTURES / "amex_activity_sanitized.xlsx"))
        self.assertEqual(loaded.file_type, "xlsx")
        self.assertEqual(loaded.sheet_name, "Activity")

        with patch("ff3_importer.parse_wizard._is_interactive", return_value=False):
            parsed = parse_with_wizard(loaded)

        self.assertEqual(
            parsed.headers,
            [
                "Date",
                "Description",
                "Card Member",
                "Account #",
                "Amount",
                "Extended Details",
                "Appears On Your Statement As",
                "Address",
                "City/State",
                "Zip Code",
                "Country",
                "Reference",
                "Category",
            ],
        )
        self.assertEqual(parsed.parse_hints["header_row_index"], 4)
        self.assertEqual(len(parsed.rows), 3)


if __name__ == "__main__":
    unittest.main()
