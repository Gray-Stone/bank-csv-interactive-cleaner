"""Tests for Firefly client configuration loading."""
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ff3_importer.firefly_client import FireflyAPIError, FireflyClient, _load_firefly_credentials


class FireflyClientConfigTests(unittest.TestCase):
    """Test cases for Firefly credential loading."""
    def test_load_credentials_from_yaml_file(self) -> None:
        """Test that load credentials from yaml file."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "FIREFLY.yaml"
            config_path.write_text(
                "firefly_url: https://example.test\n"
                "firefly_personal_access_token: token-123\n",
                encoding="utf-8",
            )

            url, token = _load_firefly_credentials(env={}, config_path=config_path)

        self.assertEqual(url, "https://example.test")
        self.assertEqual(token, "token-123")

    def test_env_values_override_yaml_file(self) -> None:
        """Test that env values override yaml file."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "FIREFLY.yaml"
            config_path.write_text(
                "firefly_url: https://yaml.test\n"
                "firefly_personal_access_token: yaml-token\n",
                encoding="utf-8",
            )

            url, token = _load_firefly_credentials(
                env={"FIREFLY_URL": "https://env.test", "FIREFLY_TOKEN": "env-token"},
                config_path=config_path,
            )

        self.assertEqual(url, "https://env.test")
        self.assertEqual(token, "env-token")

    def test_missing_credentials_raises(self) -> None:
        """Test that missing credentials raises."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "FIREFLY.yaml"

            with self.assertRaises(FireflyAPIError):
                _load_firefly_credentials(env={}, config_path=config_path)

    def test_from_env_uses_project_yaml_fallback(self) -> None:
        """Test that from env uses project yaml fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "FIREFLY.yaml"
            config_path.write_text(
                "firefly_url: https://yaml.test\n"
                "firefly_personal_access_token: yaml-token\n",
                encoding="utf-8",
            )

            with patch("ff3_importer.firefly_client.FIREFLY_CONFIG_FILE", config_path):
                with patch.dict("os.environ", {}, clear=True):
                    client = FireflyClient.from_env()

        self.assertEqual(client.base_url, "https://yaml.test/api")
        self.assertEqual(client.token, "yaml-token")


if __name__ == "__main__":
    unittest.main()
