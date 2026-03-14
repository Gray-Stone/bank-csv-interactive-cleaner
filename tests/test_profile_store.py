"""Tests for profile persistence and matching."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ff3_importer.models import Profile
from ff3_importer.profile_store import ProfileStore, build_header_signature


class ProfileStoreTests(unittest.TestCase):
    """Test cases for profile storage and matching."""
    def test_confident_profile_match(self) -> None:
        """Test that confident profile match."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            store.save(
                Profile(
                    name="bank_a",
                    parse_hints={
                        "header_signature": build_header_signature(
                            headers=["Date", "Description", "Amount"],
                            delimiter=",",
                        )
                    },
                )
            )
            store.save(
                Profile(
                    name="bank_b",
                    parse_hints={
                        "header_signature": build_header_signature(
                            headers=["Booked", "Memo", "Debit", "Credit"],
                            delimiter=";",
                        )
                    },
                )
            )
            candidate = build_header_signature(
                headers=["Date", "Description", "Amount"],
                delimiter=",",
            )
            result = store.match_from_signature(candidate)
            self.assertTrue(result.confident)
            self.assertEqual(result.selected, "bank_a")

    def test_ambiguous_match_is_not_confident(self) -> None:
        """Test that ambiguous match is not confident."""
        with tempfile.TemporaryDirectory() as tmp:
            store = ProfileStore(Path(tmp))
            signature = build_header_signature(
                headers=["Date", "Description", "Amount"],
                delimiter=",",
            )
            store.save(Profile(name="a", parse_hints={"header_signature": signature}))
            store.save(Profile(name="b", parse_hints={"header_signature": signature}))
            result = store.match_from_signature(signature)
            self.assertFalse(result.confident)
            self.assertIsNone(result.selected)


if __name__ == "__main__":
    unittest.main()
