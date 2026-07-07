"""In-memory session storage for the QA automation MCP server."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class SessionData:
    """Holds all data for a single browser session."""

    session_id: str
    email: str
    profile: Optional[str]
    context: Any  # Playwright browser context
    page: Any  # Playwright page (main page, index 0)
    current_tab_index: int = 0
    tabs: list = field(default_factory=list)
    active_frame: Any = None  # FrameLocator for iframe context, None = main frame
    started_at: datetime = field(default_factory=datetime.utcnow)
    console_errors: list = field(default_factory=list)  # Buffered console error messages
    console_messages: list = field(default_factory=list)  # Buffered console messages (all levels)
    is_tracing: bool = False  # Whether tracing is currently active
    routes: list = field(default_factory=list)  # Registered route patterns
    request_history: list = field(default_factory=list)  # Captured network requests


# Index: email -> SessionData (one session per email)
_session_by_email: dict[str, SessionData] = {}

# Index: session_id -> SessionData (for fast session_id lookups)
_session_by_id: dict[str, SessionData] = {}


def get_session_by_email(email: str) -> Optional[SessionData]:
    """Return the active session for a given email, or None."""
    return _session_by_email.get(email)


def get_session_by_id(session_id: str) -> Optional[SessionData]:
    """Return the session with the given session_id, or None."""
    return _session_by_id.get(session_id)


def register_session(session_id: str, session: SessionData) -> None:
    """Store a new session in both indexes. Sets session_id on SessionData."""
    session.session_id = session_id
    _session_by_email[session.email] = session
    _session_by_id[session_id] = session


def unregister_session(session_id: str) -> Optional[SessionData]:
    """Remove a session from both indexes. Returns the removed session."""
    session = _session_by_id.pop(session_id, None)
    if session is not None:
        _session_by_email.pop(session.email, None)
    return session


def list_all_sessions() -> list[SessionData]:
    """Return all active sessions."""
    return list(_session_by_id.values())


def get_session_id_by_email(email: str) -> str | None:
    """Return the session_id for a session given its email, or None."""
    for session_id, session in _session_by_id.items():
        if session.email == email:
            return session_id
    return None
