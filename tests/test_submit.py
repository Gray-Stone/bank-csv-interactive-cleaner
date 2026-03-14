"""Tests for submit payload and blocking behavior."""
from __future__ import annotations

import unittest

from ff3_importer.models import TransactionRecord
from ff3_importer.submit import _record_to_split_payload


class SubmitTests(unittest.TestCase):
    """Test cases for submit behavior."""
    def test_amount_is_sent_as_absolute_value(self) -> None:
        """Test that amount is sent as absolute value."""
        record = TransactionRecord(
            row_id="row-1",
            source_row_index=1,
            type="withdrawal",
            date="2026-03-01",
            amount="-5.90",
            description="Coffee",
            source_account="4101",
            destination_account="Coffee Shop",
        )
        payload = _record_to_split_payload(record)
        self.assertEqual(payload["amount"], "5.90")

    def test_submit_blocks_unconfirmed_new_external_counterparty(self) -> None:
        """Test that submit blocks unconfirmed new external counterparty."""
        from ff3_importer.models import SessionState
        from ff3_importer.submit import submit_session

        record = TransactionRecord(
            row_id="row-1",
            source_row_index=1,
            type="withdrawal",
            date="2026-03-01",
            amount="-5.90",
            description="Coffee",
            source_account="Checking",
            destination_account="New Cafe",
            status="approved",
            warnings=["New external counterparty confirmation required: destination_account=New Cafe"],
        )
        session = SessionState(
            run_id="run-1",
            profile_name="bank-a",
            input_file="/tmp/in.csv",
            parse_hints={},
            column_mapping={},
            records=[record],
        )

        result = submit_session(session, firefly_client=None, dry_run=False)

        self.assertEqual(len(result.failures), 1)
        self.assertIn("explicit confirmation", result.failures[0]["error"])


if __name__ == "__main__":
    unittest.main()
