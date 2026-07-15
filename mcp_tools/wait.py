"""Waiting tools — Playwright-native primitives.

Every tool here delegates to Playwright's built-in ``wait_*`` methods, which
come with auto-waiting, auto-retry, and selector re-resolution.  Only
``sleep`` uses ``page.evaluate()`` — it wraps a JS ``setTimeout`` promise, and
Playwright awaits the promise resolution.

``timeout`` parameters are accepted in **milliseconds** (matching the spec).
Playwright's Python API accepts ``timeout`` as a ``float`` in milliseconds, so
we pass values through unchanged.
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


async def _locator_for(session, selector: str):
    """Return the right locator base for ``selector``.

    If the session is inside an iframe (``session.active_frame`` is set), returns
    a locator inside that frame. Otherwise, returns a locator on the main page.
    """
    if session.active_frame is not None:
        return session.active_frame.locator(selector)
    return session.page.locator(selector)


@tool
async def wait_for_selector(
    session_id: str,
    selector: str,
    options: dict | None = None,
) -> dict:
    """Wait for an element matching ``selector`` to appear in the DOM.

    Uses ``page.wait_for_selector`` — Playwright's built-in, which auto-retries
    and re-resolves the selector on each attempt.

    Args:
        session_id: The session ID.
        selector: CSS selector to wait for.
        options: Optional dict with:
            - ``state``: ``"visible"`` (default), ``"hidden"``, ``"attached"``,
              or ``"detached"``.
            - ``timeout``: Timeout in **milliseconds**. Defaults to Playwright's
              internal default (30 000 ms).

    Returns:
        ``{"found": true}`` on success, ``{"status": "timeout", "message": ...}``
        on timeout.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    opts = options or {}
    state = opts.get("state", "visible")
    timeout = opts.get("timeout")  # ms, passed through directly
    try:
        await (await _locator_for(session, selector)).wait_for(state=state, timeout=timeout)
        return add_recording_reminder({"found": True})
    except Exception as e:
        return {"status": "timeout", "message": str(e)}


@tool
async def wait_for_url(
    session_id: str,
    pattern: str,
    timeout: float | None = None,
) -> dict:
    """Wait for the current URL to match ``pattern`` (glob or regex).

    Uses ``page.wait_for_url`` — Playwright's built-in. Pattern supports glob
    syntax (``*``, ``?``, ``**``) and regex (if it begins with ``^`` or contains
    regex metacharacters).

    Args:
        session_id: The session ID.
        pattern: Glob or regex pattern to match against ``page.url``.
        timeout: Timeout in **milliseconds**. Defaults to Playwright's internal
            default (30 000 ms).

    Returns:
        ``{"url": <current url>}`` when matched.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    try:
        await session.page.wait_for_url(pattern, timeout=timeout)
        return add_recording_reminder({"url": session.page.url})
    except Exception as e:
        return {"status": "timeout", "message": str(e)}


@tool
async def wait_for_load_state(
    session_id: str,
    state: str = "domcontentloaded",
    timeout: float | None = None,
) -> dict:
    """Wait for the page to reach the given load state.

    Uses ``page.wait_for_load_state`` — Playwright's built-in.

    Args:
        session_id: The session ID.
        state: One of ``"domcontentloaded"`` (default), ``"load"``,
            ``"networkidle"``.
        timeout: Timeout in **milliseconds**. Defaults to Playwright's internal
            default (30 000 ms).

    Returns:
        ``{"state": <state>}`` on success.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    try:
        await session.page.wait_for_load_state(state=state, timeout=timeout)
        return add_recording_reminder({"state": state})
    except Exception as e:
        return {"status": "timeout", "message": str(e)}


@tool
async def sleep(session_id: str, ms: int) -> dict:
    """Sleep for ``ms`` milliseconds via ``setTimeout``.

    Use sparingly — prefer ``wait_for_selector`` / ``wait_for_url`` /
    ``wait_for_load_state`` whenever the condition is observable.

    Args:
        session_id: The session ID.
        ms: Milliseconds to sleep.

    Returns:
        ``{"slept": ms}`` on success. JS errors return the standard error envelope.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    try:
        await session.page.evaluate("ms => new Promise(r => setTimeout(r, ms))", ms)
        return {"slept": ms}
    except Exception as e:
        return {"status": "error", "error": "js_error", "message": str(e)}


@tool
async def wait_for_navigation(
    session_id: str,
    options: dict | None = None,
) -> dict:
    """Wait for the next navigation to complete.

    Call this after an action that triggers a redirect or page reload
    (e.g. ``click`` on a link, ``type`` that submits a form, or any JS-driven
    history change).

    Uses ``page.wait_for_navigation`` — Playwright's built-in. Resolves to the
    new response; we read ``page.url`` and ``page.title()`` after.

    Args:
        session_id: The session ID.
        options: Optional dict with:
            - ``waitUntil``: ``"load"`` (default), ``"domcontentloaded"``, or
              ``"networkidle"``. Controls when the navigation is considered
              complete.
            - ``timeout``: Timeout in **milliseconds**. Defaults to Playwright's
              internal default (30 000 ms).

    Returns:
        ``{"url": <final url>, "title": <page title>}`` on success.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err
    opts = options or {}
    wait_until = opts.get("waitUntil", "load")
    timeout = opts.get("timeout")  # ms, passed through directly
    try:
        await session.page.wait_for_navigation(wait_until=wait_until, timeout=timeout)
        return add_recording_reminder({"url": session.page.url, "title": await session.page.title()})
    except Exception as e:
        return {"status": "timeout", "message": str(e)}