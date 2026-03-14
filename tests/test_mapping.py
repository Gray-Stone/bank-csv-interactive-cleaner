"""Tests for mapping and normalization helpers."""
from __future__ import annotations

import unittest

from ff3_importer.mapping import _resolve_header_choice, normalize_rows_to_records


class MappingTests(unittest.TestCase):
    """Test cases for mapping and normalization."""
    def test_normalize_signed_amount(self) -> None:
        """Test that normalize signed amount."""
        rows = [
            {
                "__source_row_index": "5",
                "Date": "2026-02-01",
                "Description": "Coffee",
                "Amount": "-4.50",
            }
        ]
        mapping = {"date": "Date", "description": "Description", "amount": "Amount"}
        records = normalize_rows_to_records(rows, mapping, defaults={})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].date, "2026-02-01")
        self.assertEqual(records[0].amount, "-4.50")
        self.assertEqual(records[0].description, "Coffee")
        self.assertEqual(records[0].status, "pending")

    def test_debit_credit_pair(self) -> None:
        """Test that debit credit pair."""
        rows = [
            {
                "__source_row_index": "9",
                "Booked": "02/15/2026",
                "Memo": "Payroll",
                "Debit": "",
                "Credit": "1200.00",
            }
        ]
        mapping = {
            "date": "Booked",
            "description": "Memo",
            "amount": "",
            "debit_amount": "Debit",
            "credit_amount": "Credit",
        }
        records = normalize_rows_to_records(rows, mapping, defaults={})
        self.assertEqual(records[0].date, "2026-02-15")
        self.assertEqual(records[0].amount, "1200.00")

    def test_resolve_header_choice_by_index(self) -> None:
        """Test that resolve header choice by index."""
        headers = ["Date", "Description", "Amount"]
        self.assertEqual(_resolve_header_choice("0", headers), "Date")
        self.assertEqual(_resolve_header_choice("2", headers), "Amount")
        self.assertIsNone(_resolve_header_choice("7", headers))

    def test_type_and_accounts_inferred_from_amount_sign(self) -> None:
        """Test that type and accounts inferred from amount sign."""
        rows = [
            {
                "__source_row_index": "1",
                "Date": "2026-02-01",
                "Description": "Coffee Shop",
                "Amount": "-5.00",
            },
            {
                "__source_row_index": "2",
                "Date": "2026-02-02",
                "Description": "Payroll",
                "Amount": "1000.00",
            },
        ]
        mapping = {"date": "Date", "description": "Description", "amount": "Amount"}
        records = normalize_rows_to_records(
            rows,
            mapping,
            defaults={"source_account": "4101", "currency": "USD"},
        )
        self.assertEqual(records[0].type, "withdrawal")
        self.assertEqual(records[0].source_account, "4101")
        self.assertEqual(records[0].destination_account, "Coffee Shop")
        self.assertEqual(records[1].type, "deposit")
        self.assertEqual(records[1].destination_account, "4101")
        self.assertEqual(records[1].source_account, "Payroll")

    def test_description_extracts_references(self) -> None:
        """Test that description extracts references."""
        rows = [
            {
                "__source_row_index": "3",
                "Date": "2026-02-03",
                "Description": "Online Banking transfer from CHK 7404 Confirmation# ABCD1234",
                "Amount": "100.00",
            }
        ]
        mapping = {"date": "Date", "description": "Description", "amount": "Amount"}
        records = normalize_rows_to_records(
            rows,
            mapping,
            defaults={"source_account": "4101", "currency": "USD"},
        )
        self.assertEqual(records[0].internal_reference, "7404")
        self.assertEqual(records[0].external_id, "ABCD1234")

    def test_payee_is_not_inferred_from_description(self) -> None:
        """Test that payee is not inferred from description."""
        rows = [
            {
                "__source_row_index": "15",
                "Date": "12/09/2025",
                "Description": (
                    "POWER UTILITY DES:WEB_PAY ID:XXXXXXXXXX0725 INDN:SAMPLE USER "
                    "CO ID:XXXXX42013 WEB"
                ),
                "Amount": "-163.20",
            }
        ]
        mapping = {"date": "Date", "description": "Description", "amount": "Amount"}
        records = normalize_rows_to_records(
            rows,
            mapping,
            defaults={"source_account": "4101", "currency": "USD"},
        )
        self.assertEqual(records[0].type, "withdrawal")
        self.assertEqual(records[0].destination_account, "POWER UTILITY")
        self.assertEqual(records[0].payee, "")


if __name__ == "__main__":
    unittest.main()
