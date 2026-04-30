"""Session domain facade.

SessionGateway is the only public entrypoint for session operations. It accepts
contract DTOs, delegates to AgentSessionStore, and translates exceptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core_contracts.session_contracts import (
    AgentSessionSnapshot,
    AgentSessionState,
    SessionContractError,
    SessionLoadRequest,
    SessionLoadResult,
    SessionSaveRequest,
    SessionSaveResult,
    SessionStateCreateRequest,
    SessionStateResumeRequest,
)
from .session_repository import SessionFileRepository
from .session_snapshot_codec import SessionSnapshotCodec
from .session_state_factory import SessionStateFactory
from .session_store import AgentSessionStore


class SessionGateway:
    """Session domain public facade."""

    def __init__(
        self,
        session_store_directory: Path | None = None,
        repository: SessionFileRepository | None = None,
        snapshot_codec: SessionSnapshotCodec | None = None,
        state_factory: SessionStateFactory | None = None,
        store: AgentSessionStore | None = None,
    ) -> None:
        """Initialize the session facade.
        Args:
            session_store_directory (Path | None): Optional store directory.
            repository (SessionFileRepository | None): Optional repository override.
            snapshot_codec (SessionSnapshotCodec | None): Optional codec override.
            state_factory (SessionStateFactory | None): Optional state factory override.
            store (AgentSessionStore | None): Optional full store injection.
        Returns:
            None
        Raises:
            None.
        """
        if store is not None and repository is None and snapshot_codec is None and state_factory is None:
            self._store = store
        else:
            self._store = AgentSessionStore(
                directory=session_store_directory,
                repository=repository,
                snapshot_codec=snapshot_codec,
                state_factory=state_factory,
            )
        # AgentSessionStore: Unified session persistence and runtime-state service.

    @property
    def directory(self) -> Path:
        """Return current session storage directory.
        Args:
            None
        Returns:
            Path: Current store directory.
        Raises:
            None.
        """
        return self._store.directory

    def save(self, request: SessionSaveRequest) -> SessionSaveResult:
        """Persist a snapshot through the store.
        Args:
            request (SessionSaveRequest): Save request contract.
        Returns:
            SessionSaveResult: Save result contract.
        Raises:
            SessionContractError: Raised when save fails.
        """
        try:
            session_path = self._store.save(request.snapshot)
            return SessionSaveResult(session_id=request.snapshot.session_id, session_path=str(session_path))
        except ValueError as exc:
            contract_error = self._translate_store_error(exc)
            raise contract_error from exc

    def load(self, request: SessionLoadRequest) -> SessionLoadResult:
        """Load a snapshot through the store.
        Args:
            request (SessionLoadRequest): Load request contract.
        Returns:
            SessionLoadResult: Load result contract.
        Raises:
            SessionContractError: Raised when load fails.
        """
        try:
            snapshot = self._store.load(request.session_id)
            return SessionLoadResult(session_id=snapshot.session_id, snapshot=snapshot)
        except ValueError as exc:
            contract_error = self._translate_store_error(exc)
            raise contract_error from exc

    def create_state(self, request: SessionStateCreateRequest) -> AgentSessionState:
        """Create runtime session state.
        Args:
            request (SessionStateCreateRequest): State-create request.
        Returns:
            AgentSessionState: Created runtime state.
        Raises:
            SessionContractError: Raised when request is invalid.
        """
        try:
            return self._store.create_state(request.prompt)
        except Exception as exc:
            if isinstance(exc, ValueError):
                contract_error = self._translate_store_error(exc)
                raise contract_error from exc
            raise SessionContractError(f'Failed to create session state: {exc}') from exc

    def resume_state(self, request: SessionStateResumeRequest) -> AgentSessionState:
        """Resume runtime session state.
        Args:
            request (SessionStateResumeRequest): State-resume request.
        Returns:
            AgentSessionState: Resumed runtime state.
        Raises:
            SessionContractError: Raised when request is invalid.
        """
        try:
            return self._store.resume_state(request.messages, request.transcript)
        except Exception as exc:
            if isinstance(exc, ValueError):
                contract_error = self._translate_store_error(exc)
                raise contract_error from exc
            raise SessionContractError(f'Failed to resume session state: {exc}') from exc

    def save_session(self, snapshot: AgentSessionSnapshot) -> Path:
        """Legacy API: save snapshot to disk.
        Args:
            snapshot (AgentSessionSnapshot): Snapshot to persist.
        Returns:
            Path: Written snapshot path.
        Raises:
            ValueError: Raised when save fails.
        """
        try:
            result = self.save(SessionSaveRequest(snapshot=snapshot))
            return Path(result.session_path)
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc

    def load_session(self, session_id: str) -> AgentSessionSnapshot:
        """Legacy API: load snapshot by session id.
        Args:
            session_id (str): Session identifier.
        Returns:
            AgentSessionSnapshot: Restored snapshot.
        Raises:
            ValueError: Raised when load fails.
        """
        try:
            return self.load(SessionLoadRequest(session_id=session_id)).snapshot
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc

    def create_session_state(self, prompt: str) -> AgentSessionState:
        """Legacy API: create state for a new session.
        Args:
            prompt (str): Initial user prompt.
        Returns:
            AgentSessionState: Created runtime state.
        Raises:
            ValueError: Raised when creation fails.
        """
        try:
            return self.create_state(SessionStateCreateRequest(prompt=prompt))
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc

    def resume_session_state(
        self,
        messages: list[dict],
        transcript: list[dict],
    ) -> AgentSessionState:
        """Legacy API: resume state from persisted payloads.
        Args:
            messages (list[dict]): Persisted messages.
            transcript (list[dict]): Persisted transcript.
        Returns:
            AgentSessionState: Resumed runtime state.
        Raises:
            ValueError: Raised when resume fails.
        """
        try:
            request = SessionStateResumeRequest(
                messages=tuple(dict(item) for item in messages),
                transcript=tuple(dict(item) for item in transcript),
            )
            return self.resume_state(request)
        except SessionContractError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _translate_store_error(error: ValueError) -> SessionContractError:
        """Translate store ValueError into contract-level errors.
        Args:
            error (ValueError): Raw store error.
        Returns:
            SessionContractError: Normalized contract exception.
        Raises:
            None.
        """
        cause = error.__cause__
        if isinstance(cause, SessionContractError):
            return cause
        return SessionContractError(str(error))

