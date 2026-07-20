"""Assertion tools — fail-fast checks with optional custom messages.

All assertion tools throw an ``AssertionError`` with a descriptive message
when the assertion fails.  Successful assertions return ``{"ok": true}``.
"""

import difflib
import re
from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id
from mcp_tools.logging_utils import _log_action


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


def _assertion_error(message: str) -> dict:
    """Raise an AssertionError with ``message`` and return an error dict."""
    raise AssertionError(message)


@tool
@_log_action("assert_visible")
async def assert_visible(
    session_id: str,
    selector: str,
    message: str | None = None,
) -> dict:
    """Check that an element matching ``selector`` exists and is visible.

    Uses Playwright's built-in auto-waiting — Playwright will wait for the
    element to become visible before asserting.

    Args:
        session_id: The session ID.
        selector: CSS selector, XPath, or locator string.
        message: Optional custom error message. Defaults to a descriptive default.

    Returns:
        ``{"ok": true}`` on success.

    Raises:
        AssertionError: If the element is not found or not visible.
    """
    err, session = await _resolve_session(session_id)
    if err:
        raise AssertionError(err["message"])

    try:
        locator = await _locator_for(session, selector)
        await locator.wait_for(state="visible")
        return {"ok": True}
    except Exception as e:
        default_msg = f"Element '{selector}' not found or not visible"
        raise AssertionError(message or default_msg) from e


@tool
@_log_action("assert_text")
async def assert_text(
    session_id: str,
    selector: str,
    expected: str,
    message: str | None = None,
) -> dict:
    """Fetch the text content of an element and assert it equals ``expected``.

    Retrieves ``textContent`` via Playwright's ``locator.text_content()``.
    On mismatch, shows a unified diff of the expected vs actual text.

    Args:
        session_id: The session ID.
        selector: CSS selector, XPath, or locator string.
        expected: The expected text value.
        message: Optional custom error message. Defaults to a descriptive default
            with a diff shown.

    Returns:
        ``{"ok": true, "text": <actual text>}`` on success.

    Raises:
        AssertionError: If the text does not match, with a diff shown.
    """
    err, session = await _resolve_session(session_id)
    if err:
        raise AssertionError(err["message"])

    try:
        locator = await _locator_for(session, selector)
        actual = await locator.text_content()
    except Exception as e:
        default_msg = f"Element '{selector}' not found"
        raise AssertionError(message or default_msg) from e

    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                [expected + "\n"],
                [actual + "\n" if actual else "\n"],
                fromfile="expected",
                tofile="actual",
                lineterm="",
            )
        )
        default_msg = f"Text mismatch for '{selector}':\n{diff}"
        raise AssertionError(message or default_msg)

    return {"ok": True, "text": actual}


@tool
@_log_action("assert_url")
async def assert_url(
    session_id: str,
    pattern: str,
    message: str | None = None,
) -> dict:
    """Assert the current URL matches ``pattern``.

    Supports glob-style patterns (``*``, ``**``, ``?``) and regex (patterns
    containing ``^``, ``$``, or regex metacharacters).

    Args:
        session_id: The session ID.
        pattern: Glob or regex pattern to match against the current URL.
        message: Optional custom error message.

    Returns:
        ``{"ok": true, "url": <current url>}`` on success.

    Raises:
        AssertionError: If the URL does not match the pattern.
    """
    err, session = await _resolve_session(session_id)
    if err:
        raise AssertionError(err["message"])

    current_url = session.page.url

    # Determine if pattern is regex or glob
    is_regex = any(c in pattern for c in "^$*+?[]{}()|\\")
    if is_regex:
        match = re.search(pattern, current_url)
    else:
        # Convert glob to regex: * -> .*, ** -> .*?, ? -> .
        regex_pattern = pattern.replace("**", "\x00").replace("*", ".*").replace("\x00", ".*")
        match = re.fullmatch(regex_pattern, current_url)

    if not match:
        default_msg = f"URL '{current_url}' does not match pattern '{pattern}'"
        raise AssertionError(message or default_msg)

    return {"ok": True, "url": current_url}


@tool
@_log_action("assert_title")
async def assert_title(
    session_id: str,
    expected: str,
    message: str | None = None,
) -> dict:
    """Assert the page title equals ``expected``.

    Args:
        session_id: The session ID.
        expected: The expected page title.
        message: Optional custom error message.

    Returns:
        ``{"ok": true, "title": <current title>}`` on success.

    Raises:
        AssertionError: If the title does not match.
    """
    err, session = await _resolve_session(session_id)
    if err:
        raise AssertionError(err["message"])

    actual = await session.page.title()

    if actual != expected:
        default_msg = f"Title '{actual}' does not match expected '{expected}'"
        raise AssertionError(message or default_msg)

    return {"ok": True, "title": actual}


@tool
@_log_action("assert_no_console_errors")
async def assert_no_console_errors(session_id: str) -> dict:
    """Check that no error-level console messages have fired since the last call.

    Console errors are buffered in the session. On each call, this tool checks
    the buffered errors and clears the buffer. If any errors are found, they
    are returned in the error response.

    Returns:
        ``{"ok": true, "errors": []}`` when no errors were emitted.
        ``{"ok": false, "errors": [...], "message": "Console errors detected"}``
        when errors were found.
    """
    err, session = await _resolve_session(session_id)
    if err:
        raise AssertionError(err["message"])

    errors = session.console_errors.copy()
    session.console_errors.clear()

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "message": "Console errors detected",
        }

    return {"ok": True, "errors": []}
