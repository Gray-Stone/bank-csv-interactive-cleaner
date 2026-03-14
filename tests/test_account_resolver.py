"""Tests for account resolution helpers."""
from __future__ import annotations

import unittest

from ff3_importer.account_resolver import make_placeholder, resolve_account_name


class AccountResolverTests(unittest.TestCase):
    """Test cases for account resolution logic."""
    def test_alias_resolves(self) -> None:
        """Test that alias resolves."""
        names = ["Checking 4101", "Savings 1234"]
        result = resolve_account_name(
            "primary checking",
            account_names=names,
            aliases={"primary checking": "Checking 4101"},
        )
        self.assertEqual(result.resolved, "Checking 4101")

    def test_last4_resolves_single_candidate(self) -> None:
        """Test that last4 resolves single candidate."""
        names = ["Primary Checking ****4101", "Savings 1234"]
        result = resolve_account_name("4101", account_names=names, aliases={})
        self.assertEqual(result.resolved, "Primary Checking ****4101")

    def test_server_account_number_is_used_after_name_match_fails(self) -> None:
        """Test that server account number is used after name match fails."""
        names = ["Main Checking", "Vacation Savings"]
        catalog = [
            {"name": "Main Checking", "account_number": "xxxx4101", "iban": "", "bic": ""},
            {"name": "Vacation Savings", "account_number": "xxxx5202", "iban": "", "bic": ""},
        ]
        result = resolve_account_name(
            "5202",
            account_names=names,
            aliases={},
            account_catalog=catalog,
        )
        self.assertEqual(result.resolved, "Vacation Savings")
        self.assertIn(result.reason, {"identifier_exact", "identifier_suffix"})

    def test_name_match_stays_primary_over_server_account_number(self) -> None:
        """Test that name match stays primary over server account number."""
        names = ["Checking 4101", "Main Wallet"]
        catalog = [
            {"name": "Checking 4101", "account_number": "xxxx1111", "iban": "", "bic": ""},
            {"name": "Main Wallet", "account_number": "xxxx4101", "iban": "", "bic": ""},
        ]
        result = resolve_account_name(
            "checking 4101",
            account_names=names,
            aliases={},
            account_catalog=catalog,
        )
        self.assertEqual(result.resolved, "Checking 4101")
        self.assertEqual(result.reason, "name_exact")

    def test_placeholder_format(self) -> None:
        """Test that placeholder format."""
        self.assertEqual(make_placeholder(" 4101 "), "PLACEHOLDER::4101")


if __name__ == "__main__":
    unittest.main()
