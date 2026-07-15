"""Tab management tools: new_tab, close_tab, switch_tab, list_tabs.

Each tool operates on ``session.tabs`` (the list of open Pages) and
``session.page`` (the active tab, equal to ``session.tabs[session.current_tab_index]``).
This keeps ``session.page`` always pointing at the active tab so all other
tools (navigate, click, wait_*, etc.) work without changes.
"""

from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id
from mcp_tools._recording_helper import add_recording_reminder


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = await get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


def _tab_index_out_of_range_error(index: int, total: int) -> dict:
    """Return the standard tab-index-error envelope."""
    return {
        "status": "error",
        "message": f"Tab index {index} out of range (0..{total - 1})",
    }


@tool
async def new_tab(session_id: str, url: str) -> dict:
    """Open a new tab within the session's context and navigate to the given URL.

    Uses ``context.new_page()`` — Playwright's recommended way to open a new tab
    in the same context, so cookies, storage, and settings are preserved.

    Args:
        session_id: The session ID.
        url: URL to navigate to immediately after opening.

    Returns:
        ``{"tab_index": <int>, "url": <url>}``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    new_page = await session.context.new_page()
    await new_page.goto(url, wait_until="domcontentloaded")
    session.tabs.append(new_page)
    session.current_tab_index = len(session.tabs) - 1
    session.page = new_page
    return add_recording_reminder({"tab_index": session.current_tab_index, "url": new_page.url})


@tool
async def close_tab(session_id: str, index: int | None = None) -> dict:
    """Close the tab at ``index``. Defaults to the current tab.

    Args:
        session_id: The session ID.
        index: Tab index to close. If ``None``, closes the current tab.

    Returns:
        ``{"closed": true, "tabs_remaining": <int>}``. If the current tab is
        closed, ``session.page`` is automatically switched to the next
        available tab (or becomes ``None`` if all tabs are closed).
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    total = len(session.tabs)
    if total == 0:
        return {"closed": True, "tabs_remaining": 0}
    if index is None:
        index = session.current_tab_index
    if not (0 <= index < total):
        return _tab_index_out_of_range_error(index, total)

    tab_to_close = session.tabs[index]
    await tab_to_close.close()
    session.tabs.pop(index)
    remaining = len(session.tabs)

    if remaining == 0:
        session.page = None
        session.current_tab_index = 0
        return add_recording_reminder({"closed": True, "tabs_remaining": 0})

    if index == session.current_tab_index:
        # Auto-switch to the next available tab. If we closed the last tab,
        # switch to the new last tab; otherwise stay at the same index.
        session.current_tab_index = min(index, remaining - 1)
        session.page = session.tabs[session.current_tab_index]
    elif index < session.current_tab_index:
        # Index was before current — shift current tab index down by 1.
        session.current_tab_index -= 1

    return add_recording_reminder({"closed": True, "tabs_remaining": remaining})


@tool
async def switch_tab(session_id: str, index: int) -> dict:
    """Switch the active tab to the tab at ``index``.

    Args:
        session_id: The session ID.
        index: Tab index to switch to.

    Returns:
        ``{"current_url": <url>, "current_title": <title>, "tab_index": <int>}``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    if not (0 <= index < len(session.tabs)):
        return _tab_index_out_of_range_error(index, len(session.tabs))

    session.current_tab_index = index
    session.page = session.tabs[index]
    return add_recording_reminder({
        "current_url": session.page.url,
        "current_title": await session.page.title(),
        "tab_index": index,
    })


@tool
async def list_tabs(session_id: str) -> dict:
    """List all tabs in the session with their URL and title.

    Syncs ``session.tabs`` with ``session.context.pages`` to catch externally-
    closed tabs (e.g. pages closed via JS ``window.close()``).

    Returns:
        ``{"tabs": [{"index": <int>, "url": <str|None>, "title": <str|None>}, ...],
        "current": <int>}``. Stale pages are marked with ``"stale": true``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    # Sync with live context.pages to handle externally-closed tabs.
    live_pages = session.context.pages
    session.tabs = live_pages
    if session.tabs and session.current_tab_index >= len(session.tabs):
        session.current_tab_index = max(0, len(session.tabs) - 1)
        session.page = session.tabs[session.current_tab_index]

    tabs_info = []
    for i, page in enumerate(session.tabs):
        try:
            tabs_info.append({
                "index": i,
                "url": page.url,
                "title": await page.title(),
            })
        except Exception:
            tabs_info.append({
                "index": i,
                "url": None,
                "title": None,
                "stale": True,
            })

    return {"tabs": tabs_info, "current": session.current_tab_index}