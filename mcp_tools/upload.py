"""File upload tool — the one thing JavaScript cannot do natively."""

from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = await get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


@tool
async def upload_file(session_id: str, selector: str, file_path: str) -> dict:
    """
    Upload a file to an ``<input type="file">`` element.

    Uses Playwright's ``page.set_input_files()`` — the one browser operation
    that cannot be performed via JavaScript alone.

    Args:
        session_id: The session ID.
        selector: CSS selector for the ``<input type="file">`` element.
        file_path: Absolute path to the file to upload.

    Returns:
        ``{"uploaded": true}`` on success.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    try:
        await session.page.set_input_files(selector, file_path)
        return {"uploaded": True}
    except Exception as e:
        return {"status": "error", "message": str(e)}
