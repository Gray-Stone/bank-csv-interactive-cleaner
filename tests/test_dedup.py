"""Tests for duplicate detection helpers."""
from __future__ import annotations

import unittest

from ff3_importer.dedup import transaction_fingerprint


class DedupTests(unittest.TestCase):
    """Test cases for duplicate detection."""
    def test_fingerprint_normalizes_case_and_spacing(self) -> None:
        """Test that fingerprint normalizes case and spacing."""
        left = transaction_fingerprint(
            date="2026-02-01",
            amount="-4.50",
            description="Coffee Shop",
            counterparty="Main Street Cafe",
        )
        right = transaction_fingerprint(
            date=" 2026-02-01 ",
            amount="-4.50",
            description="coffee   shop",
            counterparty="main street cafe",
        )
        self.assertEqual(left, right)


if __name__ == "__main__":
    unittest.main()
