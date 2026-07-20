"""Console & log tools — capture and clear browser console messages."""

from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id
from mcp_tools.logging_utils import _log_action

# Valid log levels in ascending order of severity
VALID_LEVELS = ("debug", "info", "warning", "error")


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = await get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


async def _setup_console_listener(session):
    """Attach a console listener to the page if not already active."""
    if getattr(session, "_console_listener_active", False):
        return

    def on_console(msg):
        msg_type = msg.type
        text = msg.text
        location = msg.location

        entry = {
            "type": msg_type,
            "text": text,
            "location": {
                "url": location.get("url", ""),
                "lineNumber": location.get("lineNumber", 0),
                "columnNumber": location.get("columnNumber", 0),
            },
        }

        # Always buffer in console_messages
        session.console_messages.append(entry)

        # Also buffer errors in console_errors for assert_no_console_errors
        if msg_type == "error":
            session.console_errors.append(entry)

    # Attach the listener — Playwright keeps it alive as long as the page is
    session.page.on("console", on_console)
    session._console_listener_active = True


@tool
@_log_action("console_messages")
async def console_messages(
    session_id: str,
    level: str | None = None,
) -> dict:
    """
    Return all console messages captured since the last call, then clear the buffer.

    The browser's ``page.on('console', ...)`` listener is attached automatically
    on first call. Subsequent calls return messages emitted since the previous call.

    Args:
        session_id: The session ID.
        level: Minimum severity level to return. One of ``"error"``, ``"warning"``,
            ``"info"``, ``"debug"``. Defaults to ``"info"`` (returns info, warning,
            and error messages). Pass ``"debug"`` to capture all levels.

    Returns:
        ``{"messages": [...]}`` — list of console message objects, oldest first.
        Each object has: ``type``, ``text``, ``location { url, lineNumber, columnNumber }``.

    Example::

        console_messages(session_id="sess_abc123", level="error")
        → {
            "messages": [
                {
                    "type": "error",
                    "text": "Failed to load resource",
                    "location": {"url": "https://app.example.com/", "lineNumber": 42, "columnNumber": 5}
                }
            ]
        }
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    await _setup_console_listener(session)

    # Normalize level — default to 'info'
    if level is None:
        level = "info"

    level = level.lower()
    if level not in VALID_LEVELS:
        return {
            "status": "error",
            "message": f"Invalid level '{level}'. Must be one of: {', '.join(VALID_LEVELS)}",
        }

    # Find the index of the requested level
    min_level_index = VALID_LEVELS.index(level)

    # Filter messages at or above the requested severity
    # Unknown types (e.g. "log", "table") are treated as debug-level (index 0)
    filtered = []
    for msg in session.console_messages:
        try:
            msg_level_index = VALID_LEVELS.index(msg["type"])
        except ValueError:
            msg_level_index = 0  # treat unknown types as debug
        if msg_level_index >= min_level_index:
            filtered.append(msg)

    # Clear the buffer
    session.console_messages.clear()

    return {"messages": filtered}


@tool
@_log_action("clear_console_messages")
async def clear_console_messages(session_id: str) -> dict:
    """
    Discard all buffered console messages without returning them.

    Use this to reset the console buffer before performing an action
    whose console output you don't want to include in the next
    ``console_messages`` call.

    Returns:
        ``{"cleared": true}``
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    session.console_messages.clear()
    return {"cleared": True}
