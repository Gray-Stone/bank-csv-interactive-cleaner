"""Session persistence for resumable import runs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from .app_paths import SESSIONS_DIR
from .json_store import read_json, write_json
from .models import SessionState, TransactionRecord


def _utc_now() -> str:
    """Internal helper for utc now."""
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Persist and reload resumable import sessions."""
    def __init__(self, sessions_dir: Path = SESSIONS_DIR) -> None:
        """Initialize the instance."""
        self.sessions_dir = sessions_dir

    def path_for(self, run_id: str) -> Path:
        """Return the storage path for the given run id."""
        return self.sessions_dir / f"{run_id}.json"

    def create(
        self,
        profile_name: str,
        input_file: str,
        parse_hints: dict[str, object],
        column_mapping: dict[str, str],
        records: list[TransactionRecord],
    ) -> SessionState:
        """Create and persist a new session."""
        now = _utc_now()
        run_id = str(uuid.uuid4())
        session = SessionState(
            run_id=run_id,
            profile_name=profile_name,
            input_file=input_file,
            parse_hints=parse_hints,
            column_mapping=column_mapping,
            records=records,
            current_index=0,
            created_at=now,
            updated_at=now,
            committed=False,
        )
        self.save(session)
        return session

    def save(self, session: SessionState) -> None:
        """Persist the object to disk."""
        session.updated_at = _utc_now()
        write_json(self.path_for(session.run_id), session.to_dict())

    def load(self, run_id: str) -> SessionState:
        """Load a stored object from disk."""
        payload = read_json(self.path_for(run_id), default=None)
        if payload is None:
            raise FileNotFoundError(f"Session not found: {run_id}")
        return SessionState.from_dict(payload)
