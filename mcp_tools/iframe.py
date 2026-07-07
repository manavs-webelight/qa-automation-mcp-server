"""Iframe tools: switch_to_frame, switch_to_main.

These tools manage the active iframe context for a session. When inside an iframe,
subsequent DOM / wait / form tools operate inside the iframe's frame instead of
the main page.

Uses ``page.frame_locator(selector)`` — Playwright's recommended approach. The
``FrameLocator`` is stored on ``session.active_frame``; when it's set, tools use
``frame_locator.locator(selector)`` to resolve elements inside the iframe.
"""

from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


@tool
async def switch_to_frame(session_id: str, selector: str) -> dict:
    """Switch into an iframe so subsequent tools operate inside it.

    Uses ``page.frame_locator(selector)`` — Playwright's locator-first approach,
    which auto-waits for the iframe to be available and re-resolves on changes.

    Args:
        session_id: The session ID.
        selector: CSS selector, XPath, or role-based selector for the iframe
            element (e.g. ``"iframe[name='editor']"``).

    Returns:
        ``{"switched": True}``. Use ``switch_to_main`` to exit the iframe.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    session.active_frame = session.page.frame_locator(selector)
    return {"switched": True}


@tool
async def switch_to_main(session_id: str) -> dict:
    """Switch back to the main page frame.

    Subsequent tools operate on the main page again.

    Returns:
        ``{"switched": True}``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    session.active_frame = None
    return {"switched": True}