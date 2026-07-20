"""DOM Interaction tools — Playwright-native (locator API).

Every tool here uses Playwright's built-in methods (locator.click, locator.fill,
etc.) rather than ``page.evaluate()``.  Playwright's native methods give us
auto-waiting, auto-retry, and selector resolution for free; ``evaluate()`` is
reserved for the one tool that truly needs arbitrary JS execution.

Only ``execute`` uses ``page.evaluate()`` — everything else delegates to the
corresponding locator method on ``session.page``.
"""

from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id
from mcp_tools.logging_utils import _log_action


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, page). If error is set, page is None."""
    session = await get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session.page)


@tool
@_log_action("execute")
async def execute(session_id: str, script: str) -> dict:
    """Run arbitrary JS in the active page via ``page.evaluate``.

    Use this for reads, DOM mutations, and complex interactions that Playwright's
    native methods can't cover (e.g. reading computed styles, parsing JSON from
    a ``<script>`` tag, calling React's ``window.__NEXT_DATA__``).

    Args:
        session_id: The session ID.
        script: A JS expression or statement to evaluate in the page context.
            Can return any serialisable value.

    Returns:
        ``{"result": <js value>}`` on success, where the value is the JS return.
        JS errors return ``{"status": "error", "error": "js_error", "message": ...}``.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        result = await page.evaluate(script)
        return {"result": result}
    except Exception as e:
        return {"status": "error", "error": "js_error", "message": str(e)}


@tool
@_log_action("click")
async def click(session_id: str, selector: str, timeout: int = 5000) -> dict:
    """Click the element matched by ``selector``.

    Uses ``locator.click()`` — Playwright's built-in, which handles
    auto-waiting (waiting for the element to become visible/interactive),
    retries on detached elements, and works with CSS, XPath, or role selectors.

    Args:
        session_id: The session ID.
        selector: CSS selector, XPath, or locator string for the target element.
        timeout: Max wait time in ms (default 5000). Pass higher value if needed.

    Returns:
        ``{"found": true}`` on success, ``{"found": false}`` if the selector
        matched no element.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        await page.locator(selector).click(timeout=timeout)
        return {"found": True}
    except Exception:
        return {"found": False}


@tool
@_log_action("type")
async def type(session_id: str, selector: str, text: str, timeout: int = 5000) -> dict:
    """Focus the element, set its value, and dispatch ``input`` + ``change`` events.

    Maps to ``locator.fill()`` — Playwright's built-in for text inputs. Handles
    auto-waiting and dispatches native events so that React/Vue form wiring
    (controlled inputs, state reactivity) picks up the value.

    Args:
        session_id: The session ID.
        selector: CSS selector for the target input.
        text: Value to set.
        timeout: Max wait time in ms (default 5000). Pass higher value if needed.

    Returns:
        ``{"found": true}`` on success, ``{"found": false}`` if no element matched.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        await page.locator(selector).fill(text, timeout=timeout)
        return {"found": True}
    except Exception:
        return {"found": False}


@tool
@_log_action("fill")
async def fill(session_id: str, selector: str, value: str, timeout: int = 5000) -> dict:
    """Shorthand for ``type`` — text inputs.

    Identical to ``type``; ``fill`` is the name from the spec.

    Args:
        session_id: The session ID.
        selector: CSS selector for the target input.
        value: Value to set.
        timeout: Max wait time in ms (default 5000). Pass higher value if needed.

    Returns:
        ``{"found": true}`` or ``{"found": false}``.
    """
    return await type(session_id, selector, value, timeout)


@tool
@_log_action("select_option")
async def select_option(session_id: str, selector: str, value: str, timeout: int = 5000) -> dict:
    """Set a ``<select>`` element's value and dispatch a ``change`` event.

    Maps to ``locator.select_option(value)`` — Playwright's built-in for
    ``<select>`` elements. Handles matching by ``value``, ``label``, or ``index``,
    and fires the ``change`` event automatically.

    Args:
        session_id: The session ID.
        selector: CSS selector for the ``<select>``.
        value: The ``value`` attribute of the option to select.
        timeout: Max wait time in ms (default 5000). Pass higher value if needed.

    Returns:
        ``{"found": true}`` on success, ``{"found": false}`` if no ``<select>`` matched.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        await page.locator(selector).select_option(value, timeout=timeout)
        return {"found": True}
    except Exception:
        return {"found": False}


@tool
@_log_action("check")
async def check(session_id: str, selector: str, timeout: int = 5000) -> dict:
    """Check an ``<input type="checkbox">``.

    Maps to ``locator.check()`` — Playwright's built-in. Handles
    auto-waiting, auto-dispatching ``change``/``click`` events, and only
    works on checkbox-style inputs.

    Args:
        session_id: The session ID.
        selector: CSS selector for the checkbox.
        timeout: Max wait time in ms (default 5000). Pass higher value if needed.

    Returns:
        ``{"found": true}`` when a checkbox was successfully checked,
        ``{"found": false}`` if no element matched or the matched element
        was not a checkbox.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        await page.locator(selector).check(timeout=timeout)
        return {"found": True}
    except Exception:
        return {"found": False}


@tool
@_log_action("press_key")
async def press_key(session_id: str, selector: str, key: str, timeout: int = 5000) -> dict:
    """Press a keyboard key on the element matched by ``selector``.

    Maps to ``locator.press(key)`` — Playwright's built-in for key events.
    Handles focus, native ``KeyboardEvent`` dispatch (``keydown`` + ``keyup``),
    and supports all Playwright key tokens (``Enter``, ``Tab``, ``Escape``,
    ``ArrowLeft``, ``Control``, ``Meta``, ``Shift``, etc.).

    Args:
        session_id: The session ID.
        selector: CSS selector for the target element (Playwright focuses it
            automatically before pressing).
        key: Playwright key token (see ``https://playwright.dev/python/docs/keys``).
        timeout: Max wait time in ms (default 5000). Pass higher value if needed.

    Returns:
        ``{"found": true}`` on success, ``{"found": false}`` if no element matched.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        await page.locator(selector).press(key, timeout=timeout)
        return {"found": True}
    except Exception:
        return {"found": False}


@tool
@_log_action("get_text")
async def get_text(session_id: str, selector: str) -> dict:
    """Return ``textContent`` of the matched element, or ``None`` if not found.

    Maps to ``locator.text_content()`` — Playwright's built-in.

    Args:
        session_id: The session ID.
        selector: CSS selector for the target element.

    Returns:
        ``{"text": <str or None>}``.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        text = await page.locator(selector).text_content()
        return {"text": text}
    except Exception:
        return {"text": None}


@tool
@_log_action("get_value")
async def get_value(session_id: str, selector: str) -> dict:
    """Return ``.value`` of the matched element, or ``None`` if not found.

    Maps to ``locator.input_value()`` — Playwright's built-in for
    ``<input>``, ``<textarea>``, and ``<select>`` elements.

    Args:
        session_id: The session ID.
        selector: CSS selector for the target element.

    Returns:
        ``{"value": <str or None>}``.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        value = await page.locator(selector).input_value()
        return {"value": value}
    except Exception:
        return {"value": None}


@tool
@_log_action("get_attribute")
async def get_attribute(session_id: str, selector: str, attr: str) -> dict:
    """Return ``getAttribute(attr)`` of the matched element, or ``None`` if missing.

    Maps to ``locator.get_attribute(attr)`` — Playwright's built-in.

    Args:
        session_id: The session ID.
        selector: CSS selector for the target element.
        attr: Attribute name to read.

    Returns:
        ``{"value": <str or None>}``.
    """
    err, page = await _resolve_session(session_id)
    if err:
        return err
    try:
        value = await page.locator(selector).get_attribute(attr)
        return {"value": value}
    except Exception:
        return {"value": None}