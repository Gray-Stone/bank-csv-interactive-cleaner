"""Tests for CLI argument parsing."""
from __future__ import annotations

import unittest

from ff3_importer.cli import build_parser


class CLIParserTests(unittest.TestCase):
    """Test cases for CLI parsing."""
    def test_profile_argument_optional(self) -> None:
        """Test that profile argument optional."""
        parser = build_parser()
        args = parser.parse_args(["import", "statement.csv"])
        self.assertEqual(args.command, "import")
        self.assertEqual(args.input_file, "statement.csv")
        self.assertIsNone(args.profile)


if __name__ == "__main__":
    unittest.main()
