"""In-memory session storage for the QA automation MCP server."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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
    viewport: dict | None = None  # {"width": int, "height": int} — current viewport setting
    started_at: datetime = field(default_factory=datetime.utcnow)
    console_errors: list = field(default_factory=list)  # Buffered console error messages
    console_messages: list = field(default_factory=list)  # Buffered console messages (all levels)
    is_tracing: bool = False  # Whether tracing is currently active
    routes: list = field(default_factory=list)  # Registered route patterns
    request_history: list = field(default_factory=list)  # Captured network requests
    is_recording: bool = False  # Whether a recording session is active
    recording_name: str | None = None  # Name of the current recording (automation name)
    recording_tools: list = field(default_factory=list)  # List of {"tool": str, "args": dict}
    # Logging state
    log_config: dict | None = None  # {"active": bool, "log_file": str}
    # Human recording state
    is_human_recording: bool = False
    human_recording_name: str | None = None
    human_recording_events: list = field(default_factory=list)
    human_recording_cdp_playwright: Any = None  # Playwright instance for CDP connection (if used)
    cdp_endpoint: str | None = None
    connect_method: str = "launch"  # "cdp" | "persistent" | "launch"
    playwright: Any = None  # Playwright instance (for CDP, must be stopped on close)
    base_dir: Path | None = None  # Base directory for file storage (automations/, etc.)


# Index: email -> SessionData (one session per email)
_session_by_email: dict[str, SessionData] = {}

# Index: session_id -> SessionData (for fast session_id lookups)
_session_by_id: dict[str, SessionData] = {}

# Lock to prevent race conditions during concurrent access
_session_lock = asyncio.Lock()


async def get_session_by_email(email: str) -> Optional[SessionData]:
    """Return the active session for a given email, or None."""
    async with _session_lock:
        return _session_by_email.get(email)


async def get_session_by_id(session_id: str) -> Optional[SessionData]:
    """Return the session with the given session_id, or None."""
    async with _session_lock:
        return _session_by_id.get(session_id)


async def register_session(session_id: str, session: SessionData) -> None:
    """Store a new session in both indexes. Sets session_id on SessionData."""
    async with _session_lock:
        session.session_id = session_id
        _session_by_email[session.email] = session
        _session_by_id[session_id] = session


async def unregister_session(session_id: str) -> Optional[SessionData]:
    """Remove a session from both indexes. Returns the removed session."""
    async with _session_lock:
        session = _session_by_id.pop(session_id, None)
        if session is not None:
            _session_by_email.pop(session.email, None)
        return session


async def list_all_sessions() -> list[SessionData]:
    """Return all active sessions."""
    async with _session_lock:
        return list(_session_by_id.values())


async def get_session_id_by_email(email: str) -> str | None:
    """Return the session_id for a session given its email, or None."""
    async with _session_lock:
        for session_id, session in _session_by_id.items():
            if session.email == email:
                return session_id
        return None


def parse_viewport(viewport_spec: str) -> dict | None:
    """Parse a viewport spec into {width, height} dict.

    Accepts either a preset name (e.g. "iphone-14-pro") or "WxH" (e.g. "393x852").
    Returns None if the spec is unrecognised.
    """
    from mcp_tools.screenshots import VIEWPORT_PRESETS

    if viewport_spec in VIEWPORT_PRESETS:
        w, h = VIEWPORT_PRESETS[viewport_spec]
        return {"width": w, "height": h}
    if "x" in viewport_spec:
        parts = viewport_spec.lower().split("x")
        if len(parts) == 2:
            try:
                w, h = int(parts[0]), int(parts[1])
                return {"width": w, "height": h}
            except ValueError:
                pass
    return None


def apply_viewport(session: "SessionData", viewport: dict) -> None:
    """Apply a viewport size to the session's browser context and record it.

    Note: context.viewport_size is the sync Playwright API and does nothing on
    async contexts. The viewport must be set on the page itself via
    page.set_viewport_size() — but that is an async call, so this helper only
    stores the viewport on the session. Callers must await page.set_viewport_size
    themselves (see navigate.py / screenshots.py).
    """
    session.viewport = viewport
