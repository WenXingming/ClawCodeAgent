"""Session domain storage service.

AgentSessionStore consolidates persistence and runtime state construction into
one cohesive service so SessionGateway can remain a thin facade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core_contracts.session_contracts import (
    AgentSessionState,
    AgentSessionSnapshot,
    SessionContractError,
    SessionPersistenceError,
    SessionValidationError,
)
from .session_id_policy import SessionIdPolicy
from .session_repository import DEFAULT_AGENT_SESSION_DIR, SessionFileRepository
from .session_snapshot_codec import SessionSnapshotCodec


class AgentSessionStore:
    """Session domain unified store service.

    The class owns file persistence, payload encoding/decoding and session state
    reconstruction so callers interact with one object only.
    """

    def __init__(
        self,
        directory: Path | None = None,
        repository: SessionFileRepository | None = None,
        snapshot_codec: SessionSnapshotCodec | None = None,
        state_factory: Any | None = None,
    ) -> None:
        """Initialize store collaborators.
        Args:
            directory (Path | None): Session storage directory.
            repository (SessionFileRepository | None): File repository dependency.
            snapshot_codec (SessionSnapshotCodec | None): Snapshot codec dependency.
            state_factory (Any | None): Optional state factory with create/resume.
        Returns:
            None
        Raises:
            None.
        """
        self.directory = (directory or DEFAULT_AGENT_SESSION_DIR).resolve()
        # Path: Current snapshot root directory.
        self._repository = repository or SessionFileRepository(directory=self.directory)
        # SessionFileRepository: Handles raw text persistence.
        self._snapshot_codec = snapshot_codec or SessionSnapshotCodec()
        # SessionSnapshotCodec: Encodes/decodes snapshot payloads.
        self._state_factory = state_factory
        # Any | None: Optional injected factory for state creation/resume.

    def save(self, session_snapshot: AgentSessionSnapshot) -> Path:
        """Persist snapshot as UTF-8 JSON.
        Args:
            session_snapshot (AgentSessionSnapshot): Snapshot to persist.
        Returns:
            Path: Written snapshot path.
        Raises:
            ValueError: Raised when contract or persistence fails.
        """
        try:
            payload_text = self._snapshot_codec.encode(session_snapshot)
            return self._repository.save_text(session_snapshot.session_id, payload_text)
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            wrapped = SessionPersistenceError(f'Failed to save session snapshot: {exc}')
            raise ValueError(str(wrapped)) from wrapped

    def load(self, session_id: str) -> AgentSessionSnapshot:
        """Load snapshot by session id.
        Args:
            session_id (str): Session identifier.
        Returns:
            AgentSessionSnapshot: Restored snapshot object.
        Raises:
            ValueError: Raised when validation or decoding fails.
        """
        try:
            normalized_id = self._repository._id_policy.normalize(session_id)
            payload_text = self._repository.load_text(normalized_id)
            snapshot = self._snapshot_codec.decode(payload_text)
            if snapshot.session_id != normalized_id:
                file_path = self._repository._session_file_path(normalized_id)
                raise SessionValidationError(f'Session id mismatch in session file: {file_path}')
            return snapshot
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc

    def create_state(self, prompt: str) -> AgentSessionState:
        """Create runtime state for a new session.
        Args:
            prompt (str): Initial user prompt.
        Returns:
            AgentSessionState: Created runtime state.
        Raises:
            ValueError: Raised when prompt is invalid.
        """
        try:
            if self._state_factory is not None:
                return self._state_factory.create(prompt)
            return AgentSessionState.create(prompt)
        except Exception as exc:
            wrapped = SessionValidationError(f'Failed to create session state: {exc}')
            raise ValueError(str(wrapped)) from wrapped

    def resume_state(self, messages: tuple[dict, ...], transcript: tuple[dict, ...]) -> AgentSessionState:
        """Resume runtime state from persisted payloads.
        Args:
            messages (tuple[dict, ...]): Persisted messages.
            transcript (tuple[dict, ...]): Persisted transcript entries.
        Returns:
            AgentSessionState: Resumed runtime state.
        Raises:
            ValueError: Raised when persisted payloads are invalid.
        """
        try:
            if self._state_factory is not None:
                return self._state_factory.resume(messages, transcript)
            return AgentSessionState.from_persisted(
                [dict(item) for item in messages],
                [dict(item) for item in transcript],
            )
        except Exception as exc:
            wrapped = SessionValidationError(f'Failed to resume session state: {exc}')
            raise ValueError(str(wrapped)) from wrapped

    def _session_file_path(self, session_id: str) -> Path:
        """Resolve snapshot path for a session id.
        Args:
            session_id (str): Session identifier.
        Returns:
            Path: Snapshot file path.
        Raises:
            ValueError: Raised when session id is invalid.
        """
        try:
            return self._repository._session_file_path(session_id)
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _normalize_session_id(session_id: str) -> str:
        """Normalize and validate session id.
        Args:
            session_id (str): Raw session identifier.
        Returns:
            str: Normalized identifier.
        Raises:
            ValueError: Raised when session id is invalid.
        """
        try:
            return SessionIdPolicy().normalize(session_id)
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc

