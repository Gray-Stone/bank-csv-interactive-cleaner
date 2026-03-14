"""Minimal Firefly III API client used by the importer."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .app_paths import PROJECT_ROOT


FIREFLY_CONFIG_FILE = PROJECT_ROOT / "FIREFLY.yaml"


class FireflyAPIError(RuntimeError):
    """Represent an API-level Firefly III failure."""
    pass


def _parse_simple_yaml(path: Path) -> dict[str, str]:
    """Internal helper for parse simple yaml."""
    if not path.exists():
        return {}
    payload: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        cleaned = value.strip()
        if cleaned[:1] in {'"', "'"} and cleaned[-1:] == cleaned[:1]:
            cleaned = cleaned[1:-1]
        elif " #" in cleaned:
            cleaned = cleaned.split(" #", 1)[0].rstrip()
        payload[key.strip().lower()] = cleaned
    return payload


def _load_firefly_credentials(
    env: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> tuple[str, str]:
    """Internal helper for load firefly credentials."""
    import os

    env_values = env if env is not None else os.environ
    if config_path is None:
        config_path = FIREFLY_CONFIG_FILE
    base_url = str(env_values.get("FIREFLY_URL", "")).strip()
    token = str(env_values.get("FIREFLY_TOKEN", "")).strip()

    if not base_url or not token:
        config_values = _parse_simple_yaml(config_path)
        if not base_url:
            for key in ("firefly_url", "url"):
                candidate = str(config_values.get(key, "")).strip()
                if candidate:
                    base_url = candidate
                    break
        if not token:
            for key in (
                "firefly_personal_access_token",
                "personal_access_token",
                "token",
                "firefly_token",
            ):
                candidate = str(config_values.get(key, "")).strip()
                if candidate:
                    token = candidate
                    break

    if not base_url or not token:
        raise FireflyAPIError(
            "Firefly credentials are required via FIREFLY_URL/FIREFLY_TOKEN or project-root FIREFLY.yaml."
        )
    return base_url, token


@dataclass
class FireflyClient:
    """Wrap the Firefly III endpoints used by the importer."""
    base_url: str
    token: str
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "FireflyClient":
        """Build a client from environment variables or project configuration."""
        base_url, token = _load_firefly_credentials()
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/api"):
            base_url = f"{base_url}/api"
        return cls(base_url=base_url, token=token)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Send an authenticated request to the Firefly III API."""
        url = f"{self.base_url}{path}"
        if query:
            encoded = urllib.parse.urlencode(query)
            url = f"{url}?{encoded}"
        body: bytes | None = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url=url, method=method.upper(), data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8") if response.length != 0 else ""
                if not response_body:
                    return None
                return json.loads(response_body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise FireflyAPIError(f"Firefly API error {exc.code} for {method} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise FireflyAPIError(f"Firefly API request failed for {method} {path}: {exc}") from exc

    def _list_paginated(self, path: str, query: dict[str, str] | None = None) -> list[dict[str, Any]]:
        """Fetch every page from a paginated Firefly III endpoint."""
        page = 1
        output: list[dict[str, Any]] = []
        while True:
            merged_query = dict(query or {})
            merged_query["page"] = str(page)
            merged_query["limit"] = str(200)
            payload = self._request("GET", path, query=merged_query) or {}
            data = payload.get("data", [])
            if not data:
                break
            output.extend(item for item in data if isinstance(item, dict))
            if len(data) < 200:
                break
            page += 1
        return output

    def list_accounts(self, type_filter: str | None = None) -> list[dict[str, Any]]:
        """Return accounts."""
        query = {"type": type_filter} if type_filter else None
        return self._list_paginated("/v1/accounts", query=query)

    def list_categories(self) -> list[dict[str, Any]]:
        """Return categories."""
        return self._list_paginated("/v1/categories")

    def list_budgets(self) -> list[dict[str, Any]]:
        """Return budgets."""
        return self._list_paginated("/v1/budgets")

    def list_tags(self) -> list[dict[str, Any]]:
        """Return tags."""
        return self._list_paginated("/v1/tags")

    def list_transactions(self, start: str, end: str) -> list[dict[str, Any]]:
        """Return transactions."""
        return self._list_paginated("/v1/transactions", query={"start": start, "end": end})

    def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create transaction."""
        return self._request("POST", "/v1/transactions", payload=payload) or {}

    def delete_transaction(self, transaction_id: str) -> None:
        """Handle delete transaction."""
        self._request("DELETE", f"/v1/transactions/{transaction_id}")
