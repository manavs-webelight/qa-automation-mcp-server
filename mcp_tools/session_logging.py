"""Tools for reading session tool-call logs."""

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id
from mcp_tools.logging_utils import read_log_entries, _log_action


@tool
@_log_action("list_log_entries")
async def list_log_entries(
    session_id: str,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """List paginated tool-call log entries for a session.

    Reads the session's tool-call log file (created at ``session_start``
    time when ``exploration=False``) and returns a paginated list of
    entries.

    Args:
        session_id: The session ID whose log to read.
        offset: Number of entries to skip (default 0).
        limit: Maximum number of entries to return (default 20, max 200).

    Returns:
        ``{"entries": [...], "total": int, "offset": int, "limit": int,
        "has_more": bool}`` on success.
        ``{"status": "error", "message": "Session not found"}`` if the
        session does not exist.

    Example::

        list_log_entries(session_id="sess_abc", offset=0, limit=10)
    """
    session = await get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    # Clamp limit to a sane maximum
    limit = min(limit, 200)
    if limit < 1:
        limit = 1
    if offset < 0:
        offset = 0

    return await read_log_entries(session, offset=offset, limit=limit)