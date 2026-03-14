"""Tests for session persistence."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ff3_importer.models import TransactionRecord
from ff3_importer.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    """Test cases for session storage."""
    def test_create_and_load_session(self) -> None:
        """Test that create and load session."""
        with tempfile.TemporaryDirectory() as tmp:
            sessions_dir = Path(tmp) / "sessions"
            store = SessionStore(sessions_dir=sessions_dir)
            session = store.create(
                profile_name="bank_a",
                input_file="/tmp/in.csv",
                parse_hints={"delimiter": ","},
                column_mapping={"date": "Date", "amount": "Amount", "description": "Description"},
                records=[
                    TransactionRecord(
                        row_id="row-1",
                        source_row_index=1,
                        date="2026-02-01",
                        amount="-4.50",
                        description="Coffee",
                    )
                ],
            )
            loaded = store.load(session.run_id)
            self.assertEqual(loaded.run_id, session.run_id)
            self.assertEqual(loaded.profile_name, "bank_a")
            self.assertEqual(len(loaded.records), 1)
            self.assertEqual(loaded.records[0].description, "Coffee")


if __name__ == "__main__":
    unittest.main()
