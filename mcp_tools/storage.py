"""Cookies & storage tools — browser cookie jar and localStorage manipulation."""

import json
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
async def get_cookies(session_id: str) -> dict:
    """
    Return all cookies in the browser context for this session.

    Uses ``context.cookies()`` — returns cookies from all origins.

    Returns:
        ``{"cookies": [...]}`` — list of cookie objects, each containing:
        ``name``, ``value``, ``domain``, ``path``, ``httpOnly``, ``secure``,
        ``sameSite``, ``expires``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    cookies = await session.context.cookies()
    return {"cookies": cookies}


@tool
async def set_cookies(session_id: str, cookies: list) -> dict:
    """
    Add cookies to the browser context.

    Useful for injecting a known session without loading a full profile.
    Cookies are passed as a list of cookie descriptor objects.

    Args:
        session_id: The session ID.
        cookies: List of cookie dicts. Required fields: ``name``, ``value``.
            Optional: ``domain``, ``path``, ``httpOnly``, ``secure``,
            ``sameSite``, ``expires``.

    Returns:
        ``{"set": <count>}`` — number of cookies added.

    Example::

        set_cookies(
            session_id="sess_abc123",
            cookies=[{"name": "session_id", "value": "abc123", "domain": ".example.com"}]
        )
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    await session.context.add_cookies(cookies)
    return {"set": len(cookies)}


@tool
async def delete_cookie(session_id: str, name: str) -> dict:
    """
    Delete a cookie by name from the browser context.

    Uses ``context.delete_cookies(name)``.

    Args:
        session_id: The session ID.
        name: Name of the cookie to delete.

    Returns:
        ``{"deleted": true}``
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    # Get all cookies, filter out the one to delete, clear all, re-add the rest
    all_cookies = await session.context.cookies()
    remaining = [c for c in all_cookies if c.get("name") != name]
    await session.context.clear_cookies()
    if remaining:
        await session.context.add_cookies(remaining)
    return {"deleted": True}


@tool
async def set_storage_state(session_id: str, file_path: str) -> dict:
    """
    Restore browser storage (cookies, localStorage, etc.) from a Playwright
    storage-state file.

    Uses ``context.set_storage_from_file(file_path)`` which expects the JSON
    format produced by ``context.storage_state()``.

    Args:
        session_id: The session ID.
        file_path: Path to the storage-state JSON file.

    Returns:
        ``{"status": "restored"}``
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    with open(file_path) as f:
        storage_state = json.load(f)
    await session.context.set_storage_state(storage_state)
    return {"status": "restored"}


@tool
async def get_local_storage(session_id: str) -> dict:
    """
    Return all key/value pairs from the page's localStorage.

    Uses ``page.evaluate(() => Object.entries(localStorage))``.

    Returns:
        ``{"localStorage": [[key, value], ...]}``
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    result = await session.page.evaluate("() => Object.entries(localStorage)")
    return {"localStorage": result}


@tool
async def set_local_storage(session_id: str, key: str, value: str) -> dict:
    """
    Set a single localStorage key/value pair on the current page.

    Uses ``page.evaluate(() => localStorage.setItem(key, value))``.

    Args:
        session_id: The session ID.
        key: localStorage key.
        value: localStorage value (will be stored as a string).

    Returns:
        ``{"set": true}``
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    await session.page.evaluate(
        "([k, v]) => localStorage.setItem(k, v)",
        [key, value],
    )
    return {"set": True}
