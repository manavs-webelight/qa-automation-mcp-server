"""Form Filling tool: fill_form.

Dispatches the Playwright-native method for each field type in the supplied
field list — no ``page.evaluate()`` needed. Each field entry is a dict with
``selector``, ``type``, and (for text inputs / select) ``value``.

Supported field types (mapped to the corresponding Playwright locator method):

- ``"textbox" | "password" | "email" | "number" | "tel" | "url" | "search" |
  "textarea"``  → ``locator.fill(value)``
- ``"submit" | "button"``                → ``locator.click()``
- ``"checkbox"``                         → ``locator.check()``
- ``"radio"``                            → ``locator.check()``
- ``"select"``                           → ``locator.select_option(value)``
- anything else (or omitted)             → ``locator.fill(value)`` (permissive default)

Each field's per-field ``status`` in the result is one of: ``"ok"``,
``"not_found"``, ``"not_a_checkbox"``, ``"no_selector"``, ``"error"``.
"""

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


# Field-type → Playwright locator method name.
# Keeps the dispatch table in Python, not inside a JS string.
_METHOD_BY_TYPE: dict[str, str] = {
    # Text-like inputs (including the permissive default — anything else
    # falls through to this in the loop's `else` branch).
    "textbox": "fill",
    "password": "fill",
    "email": "fill",
    "number": "fill",
    "tel": "fill",
    "url": "fill",
    "search": "fill",
    "textarea": "fill",
    "submit": "click",
    "button": "click",
    "checkbox": "check",
    "radio": "check",
    "select": "select_option",
}


@tool
@_log_action("fill_form")
async def fill_form(session_id: str, fields: list[dict]) -> dict:
    """Fill multiple fields in sequence.

    Args:
        session_id: The session ID.
        fields: List of field descriptors ``{"selector": str, "type": str,
            "value": str (optional)}``. ``type`` defaults to ``"textbox"``
            when omitted.

    Returns:
        ``{"filled": <int>, "results": [{"selector", "type", "status"}, ...]}``
        where per-field ``status`` is one of ``"ok"``, ``"not_found"``,
        ``"not_a_checkbox"``, ``"no_selector"``, or ``"error"`` (with ``message``).
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    results: list[dict] = []
    filled = 0
    for field in fields:
        sel = field.get("selector")
        ftype = field.get("type", "textbox")
        val = field.get("value", "")
        if not sel:
            results.append({"selector": sel, "type": ftype, "status": "no_selector"})
            continue

        method_name = _METHOD_BY_TYPE.get(ftype, "fill")  # permissive default
        locator = await _locator_for(session, sel)
        try:
            if method_name == "fill":
                await locator.fill(val)
            elif method_name == "click":
                await locator.click()
            elif method_name == "check":
                await locator.check()
            elif method_name == "select_option":
                await locator.select_option(val)
            else:  # should not happen given the table above, but be defensive
                await locator.fill(val)
            results.append({"selector": sel, "type": ftype, "status": "ok"})
            filled += 1
        except Exception as e:
            err_entry: dict = {
                "selector": sel,
                "type": ftype,
                "status": "error",
                "message": str(e),
            }
            results.append(err_entry)
    return {"filled": filled, "results": results}