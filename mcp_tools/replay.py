"""Replay tools — replay recorded automations and DOM events inside the MCP server.

Two tools:

``replay_automation``  — replays a structured ``tools`` JSON (the format
produced by ``start_recording`` / ``stop_recording``). Reads the automation
file, substitutes ``{{VARIABLE}}`` placeholders, and calls the corresponding
internal handlers on ``session.page``.

``replay_interactions``  — replays a flat JSON array of DOM events
(the format produced by ``start_human_recording`` / ``stop_human_recording``).
Drives Playwright directly — navigate, click (by locator + rect fallback),
fill, drag, scroll, keydown.

Both operate on a browser session already created via ``session_start``.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id


# ---------------------------------------------------------------------------
# Helpers — placeholder substitution, URL comparison, optional step detection
# ---------------------------------------------------------------------------

# Selector field names per tool type. Used by optional-step detection.
_SELECTOR_TOOLS: dict[str, str] = {
    "click": "selector",
    "dblclick": "selector",
    "fill": "selector",
    "type": "selector",
    "press_key": "selector",
    "select_option": "selector",
    "check": "selector",
}


async def _check_optional_step(session, tool_name: str, tool_args: dict) -> bool:
    """Return True if the element exists for an optional step.

    Checks only action tools that target a specific selector. Returns True
    (element found) or False (element not found → step should be skipped).
    """
    selector_field = _SELECTOR_TOOLS.get(tool_name)
    if not selector_field:
        return True  # no selector to check — step is safe to execute

    selector = tool_args.get(selector_field)
    if not selector:
        return True  # no selector in args — shouldn't happen, but skip gracefully

    count = await session.page.locator(selector).count()
    return count > 0


def _same_page(current_url: str, target_url: str) -> bool:
    """True when only the fragment differs — no real navigation needed."""
    a, b = urlsplit(current_url), urlsplit(target_url)
    return (a.scheme, a.netloc, a.path, a.query) == (b.scheme, b.netloc, b.path, b.query)


def _looks_redacted(value) -> bool:
    if not isinstance(value, str):
        return False
    return any(marker in value for marker in ("REDACTED", "***", "[hidden]"))


# ---------------------------------------------------------------------------
# Automation replay — internal tool handlers
# ---------------------------------------------------------------------------

async def _handler_navigate(session, args: dict) -> dict:
    url = args["url"]
    try:
        response = await session.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"url": url, "final_url": session.page.url, "title": await session.page.title()}
    except Exception as e:
        return {"status": "error", "error": "navigation_error", "message": str(e)}


async def _handler_click(session, args: dict) -> dict:
    selector = args["selector"]
    button = {0: "left", 1: "middle", 2: "right"}.get(args.get("button", 0), "left")
    try:
        await session.page.locator(selector).click(timeout=args.get("timeout", 5000), button=button)
        # If clicked element is a button inside a form, also submit the form
        # (React's event delegation listens for `submit` on the form, not the button)
        await session.page.evaluate(f"""
            (sel) => {{
                const el = document.querySelector(sel);
                if (el && el.tagName === 'BUTTON') {{
                    const form = el.closest('form');
                    if (form) {{
                        form.dispatchEvent(new Event('submit', {{bubbles: true, cancelable: true}}));
                    }}
                }}
            }}
        """, selector)
        return {"found": True}
    except Exception:
        return await _fallback_click(session, args["selector"], button)


async def _handler_dblclick(session, args: dict) -> dict:
    selector = args["selector"]
    try:
        await session.page.locator(selector).dblclick(timeout=args.get("timeout", 5000))
        return {"found": True}
    except Exception:
        return await _fallback_dblclick(session, selector)


async def _fallback_click(session, selector: str, button: str = "left") -> dict:
    """Click by centre of element rect when locator fails."""
    try:
        rect = await session.page.evaluate(
            """([sel]) => {
                const el = document.querySelector(sel);
                return el ? el.getBoundingClientRect() : null;
            }""",
            selector,
        )
        if rect:
            x = rect["x"] + rect["width"] / 2
            y = rect["y"] + rect["height"] / 2
            await session.page.mouse.click(x, y, button=button)
            return {"found": True}
    except Exception:
        pass
    return {"found": False}


async def _fallback_dblclick(session, selector: str) -> dict:
    try:
        rect = await session.page.evaluate(
            """([sel]) => {
                const el = document.querySelector(sel);
                return el ? el.getBoundingClientRect() : null;
            }""",
            selector,
        )
        if rect:
            x = rect["x"] + rect["width"] / 2
            y = rect["y"] + rect["height"] / 2
            await session.page.mouse.dblclick(x, y)
            return {"found": True}
    except Exception:
        pass
    return {"found": False}


async def _handler_fill(session, args: dict) -> dict:
    selector = args["selector"]
    value = args["value"]
    input_type = args.get("inputType", "")
    try:
        locator = session.page.locator(selector)
        if input_type in ("checkbox", "radio"):
            await locator.check(timeout=args.get("timeout", 5000))
            return {"found": True}
        if input_type == "select" or args.get("tag") == "select":
            await locator.select_option(str(value), timeout=args.get("timeout", 5000))
            return {"found": True}
        await locator.fill(str(value), timeout=args.get("timeout", 5000))
        return {"found": True}
    except Exception:
        return {"found": False}


async def _handler_type(session, args: dict) -> dict:
    return await _handler_fill(session, args)


async def _handler_press_key(session, args: dict) -> dict:
    selector = args.get("selector", "")
    key = args["key"]
    try:
        if selector:
            await session.page.locator(selector).press(key, timeout=args.get("timeout", 5000))
        else:
            await session.page.keyboard.press(key)
        return {"found": True}
    except Exception:
        return {"found": False}


async def _handler_select_option(session, args: dict) -> dict:
    try:
        await session.page.locator(args["selector"]).select_option(
            args["value"], timeout=args.get("timeout", 5000),
        )
        return {"found": True}
    except Exception:
        return {"found": False}


async def _handler_check(session, args: dict) -> dict:
    try:
        await session.page.locator(args["selector"]).check(timeout=args.get("timeout", 5000))
        return {"found": True}
    except Exception:
        return {"found": False}


async def _handler_wait_for_selector(session, args: dict) -> dict:
    try:
        await session.page.wait_for_selector(
            args["selector"],
            state=args.get("state", "visible"),
            timeout=args.get("timeout", 30000),
        )
        return {"found": True}
    except Exception:
        return {"found": False}


async def _handler_wait_for_url(session, args: dict) -> dict:
    try:
        await session.page.wait_for_url(
            args["url"], timeout=args.get("timeout", 30000),
        )
        return {"matched": True}
    except Exception:
        return {"matched": False}


async def _handler_wait_for_load_state(session, args: dict) -> dict:
    try:
        await session.page.wait_for_load_state(
            args.get("state", "domcontentloaded"), timeout=args.get("timeout", 30000),
        )
        return {"loaded": True}
    except Exception:
        return {"loaded": False}


async def _handler_reload(session, _args: dict) -> dict:
    try:
        await session.page.reload()
        return {"url": session.page.url, "title": await session.page.title()}
    except Exception as e:
        return {"status": "error", "error": "reload_error", "message": str(e)}


async def _handler_navigate_back(session, _args: dict) -> dict:
    try:
        await session.page.go_back()
        return {"url": session.page.url, "title": await session.page.title()}
    except Exception as e:
        return {"status": "error", "error": "back_error", "message": str(e)}


async def _handler_get_text(session, args: dict) -> dict:
    try:
        text = await session.page.locator(args["selector"]).text_content()
        return {"text": text}
    except Exception:
        return {"text": None}


async def _handler_get_value(session, args: dict) -> dict:
    try:
        value = await session.page.locator(args["selector"]).input_value()
        return {"value": value}
    except Exception:
        return {"value": None}


async def _handler_get_attribute(session, args: dict) -> dict:
    try:
        value = await session.page.locator(args["selector"]).get_attribute(args["attr"])
        return {"value": value}
    except Exception:
        return {"value": None}


async def _handler_execute(session, args: dict) -> dict:
    try:
        result = await session.page.evaluate(args["script"])
        return {"result": result}
    except Exception as e:
        return {"status": "error", "error": "js_error", "message": str(e)}


async def _handler_console_messages(session, args: dict) -> dict:
    level = args.get("level", "info")
    msgs = [m for m in session.console_messages if _level_rank(level) <= _level_rank(m.get("level", "info"))]
    session.console_messages.clear()
    return {"messages": msgs}


def _level_rank(level: str) -> int:
    return {"debug": 0, "info": 1, "warning": 2, "error": 3}.get(level, 1)


async def _handler_clear_console(session, _args: dict) -> dict:
    session.console_messages.clear()
    return {"cleared": True}


async def _handler_assert_url(session, args: dict) -> dict:
    pattern = args["pattern"]
    url = session.page.url
    matched = _glob_match(url, pattern)
    if not matched:
        return {"status": "error", "error": "url_mismatch", "expected": pattern, "actual": url}
    return {"matched": True}


def _glob_match(url: str, pattern: str) -> bool:
    """Match URL against a glob pattern (* and ? wildcards) or regex."""
    import re
    # If pattern looks like regex (anchors or complex meta), use it as-is
    if pattern.startswith("^") or pattern.endswith("$"):
        return bool(re.match(pattern, url))
    # Convert glob to regex: escape everything, then un-escape * and ?
    regex = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return bool(re.match(regex, url))


async def _handler_assert_title(session, args: dict) -> dict:
    expected = args["expected"]
    actual = await session.page.title()
    if actual != expected:
        return {"status": "error", "error": "title_mismatch", "expected": expected, "actual": actual}
    return {"matched": True}


async def _handler_assert_text(session, args: dict) -> dict:
    expected = args["expected"]
    actual = await session.page.locator(args["selector"]).text_content() or ""
    if actual != expected:
        return {"status": "error", "error": "text_mismatch", "expected": expected, "actual": actual}
    return {"matched": True}


# Tool name → handler mapping.
# Each handler takes (session, args) and returns a dict.
_TOOL_HANDLERS: dict[str, Any] = {
    "navigate": _handler_navigate,
    "navigate_with_retry": _handler_navigate,
    "click": _handler_click,
    "dblclick": _handler_dblclick,
    "fill": _handler_fill,
    "type": _handler_type,
    "press_key": _handler_press_key,
    "select_option": _handler_select_option,
    "check": _handler_check,
    "wait_for_selector": _handler_wait_for_selector,
    "wait_for_url": _handler_wait_for_url,
    "wait_for_load_state": _handler_wait_for_load_state,
    "reload": _handler_reload,
    "navigate_back": _handler_navigate_back,
    "get_text": _handler_get_text,
    "get_value": _handler_get_value,
    "get_attribute": _handler_get_attribute,
    "execute": _handler_execute,
    "console_messages": _handler_console_messages,
    "clear_console_messages": _handler_clear_console,
    "assert_url": _handler_assert_url,
    "assert_title": _handler_assert_title,
    "assert_text": _handler_assert_text,
}


async def _call_tool_with_retry(session, tool_name: str, tool_args: dict, max_retries: int = 3) -> tuple[Any, str | None]:
    """Call a tool handler with exponential backoff for transient failures.

    Returns (result, error_msg). On final failure, error_msg is set.
    """
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return None, f"Unknown tool: {tool_name}"

    last_err = None
    backoff = 1.0
    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.wait_for(
                handler(session, tool_args),
                timeout=30.0,
            )
            return result, None
        except asyncio.TimeoutError:
            last_err = f"timeout after 30s"
            if attempt == max_retries:
                break
            await asyncio.sleep(backoff)
            backoff *= 2
        except Exception as e:
            err_str = str(e)
            transient = any(
                kw in err_str.lower()
                for kw in ("connection", "refused", "timed out", "broken", "reset", "eof", "chrome")
            )
            if transient and attempt < max_retries:
                last_err = err_str
                await asyncio.sleep(backoff)
                backoff *= 2
            else:
                return None, err_str
    return None, last_err


async def _do_replay_automation(session, automation: dict) -> dict:
    """Execute all tool steps in an automation. Returns a summary dict.

    Calls the actual MCP tools directly (not internal handlers) so replay
    behaves identically to manual tool calls.
    """
    from mcp_tools import dom, navigate, wait, tabs, console, form

    name = automation.get("name", "unnamed")
    tools = automation.get("tools", [])
    on_error = automation.get("on_error", "stop")

    # Map tool names to MCP functions + their argument keys
    _MCP_TOOLS: dict[str, tuple] = {
        "navigate": (navigate.navigate, ["url"]),
        "navigate_with_retry": (navigate.navigate_with_retry, ["url"]),
        "click": (dom.click, ["selector", "timeout"]),
        "dblclick": (dom._dblclick if hasattr(dom, "_dblclick") else None, ["selector", "timeout"]),
        "fill": (form.fill if hasattr(form, "fill") else dom.fill, ["selector", "value", "timeout"]),
        "type": (dom.type, ["selector", "text", "timeout"]),
        "press_key": (dom.press_key, ["selector", "key", "timeout"]),
        "select_option": (dom.select_option, ["selector", "value", "timeout"]),
        "check": (dom.check, ["selector", "timeout"]),
        "wait_for_selector": (wait.wait_for_selector, ["selector", "state", "timeout"]),
        "wait_for_url": (wait.wait_for_url, ["url", "timeout"]),
        "wait_for_load_state": (wait.wait_for_load_state, ["state", "timeout"]),
        "wait_for_navigation": (wait.wait_for_navigation, ["timeout"]),
        "reload": (navigate.reload, []),
        "navigate_back": (navigate.navigate_back, []),
        "get_text": (dom.get_text, ["selector"]),
        "get_value": (dom.get_value, ["selector"]),
        "get_attribute": (dom.get_attribute, ["selector", "attr"]),
        "execute": (dom.execute, ["script"]),
        "console_messages": (console.console_messages, ["level"]),
        "clear_console_messages": (console.clear_console_messages, []),
        "assert_url": (None, ["pattern"]),
        "assert_title": (None, ["expected"]),
        "assert_text": (None, ["selector", "expected"]),
    }

    results: list[dict] = []
    for i, entry in enumerate(tools, 1):
        tool_name = entry.get("tool", "unknown")
        raw_args = entry.get("args", {})
        tool_args = dict(raw_args)
        session_id = tool_args.pop("session_id", session.session_id)

        # Optional step: skip if the target element doesn't exist.
        if entry.get("optional"):
            if tool_name in _SELECTOR_TOOLS:
                selector = tool_args.get(_SELECTOR_TOOLS[tool_name])
                if selector:
                    count = await session.page.locator(selector).count()
                    if count == 0:
                        print(f"  [replay:{i}] skipping optional step {tool_name!r} — element not found")
                        results.append({"tool": tool_name, "success": True, "skipped": True, "duration": 0})
                        continue

        # # Dismiss modals before each step
        # # ponytail: removed — user wants batch replay to match replay_automation exactly
        # try:
        #     await _dismiss_all_modals(session.page)
        # except Exception:
        #     pass

        # Call the actual MCP tool
        start_step = time.time()
        mcp_fn_info = _MCP_TOOLS.get(tool_name)
        result = {"status": "error", "error": "unknown_tool"}

        if mcp_fn_info and mcp_fn_info[0] is not None:
            mcp_fn, arg_keys = mcp_fn_info
            # Build kwargs from recorded args
            kwargs = {k: tool_args[k] for k in arg_keys if k in tool_args}
            kwargs["session_id"] = session_id
            try:
                result = await mcp_fn(**kwargs)
            except Exception as e:
                result = {"status": "error", "error": str(e)}
        elif tool_name in _MCP_TOOLS:
            # Assertion tools — handle specially
            pass

        step_duration = round(time.time() - start_step, 3)
        result["duration"] = step_duration

        is_error = result.get("status") == "error"

        if is_error:
            results.append({"tool": tool_name, "success": False, "result": result, "duration": step_duration})
            if on_error == "stop":
                break
            continue

        results.append({"tool": tool_name, "success": True, "result": result, "duration": step_duration})

    successful = sum(1 for r in results if r["success"])
    return {
        "name": name,
        "total": len(tools),
        "completed": len(results),
        "successful": successful,
        "failed": len(results) - successful,
        "status": "success" if all(r["success"] for r in results) else "partial_failure",
        "results": results,
    }


# ---------------------------------------------------------------------------
# Manual interactions replay — DOM event handlers
# ---------------------------------------------------------------------------

REPLAYABLE_TYPES = {"click", "dblclick", "contextmenu", "fill", "drag", "keydown", "scroll"}


def _resolve_locator(page, element: dict):
    """Return a Playwright locator for the recorded element, or None."""
    if not element or not element.get("selector"):
        return None
    try:
        locator = page.locator(element["selector"]).first
        # Don't block on wait_for — return whatever we have
        return locator
    except Exception:
        return None


async def _dismiss_blocking_overlay(page, target_rect: dict) -> bool:
    """If a modal/overlay is blocking the target, dismiss it and return True.

    Uses document.elementFromPoint to check what's actually on top at the
    target's centre. Only dismisses genuine modals (role=dialog, aria-modal,
    or z-index > 100) — never a normal tooltip or dropdown.
    """
    cx = target_rect["x"] + target_rect["width"] / 2
    cy = target_rect["y"] + target_rect["height"] / 2

    blocking = await page.evaluate(f"""
        () => {{
            const x = {cx}, y = {cy};
            const el = document.elementFromPoint(x, y);
            if (!el) return null;

            const role = el.getAttribute('role');
            const ariaModal = el.getAttribute('aria-modal');
            const style = window.getComputedStyle(el);
            const zIndex = parseInt(style.zIndex) || 0;

            if (role === 'dialog' || ariaModal === 'true' || zIndex > 100) {{
                return {{ tag: el.tagName, role: role || null }};
            }}
            return null;
        }}
    """)

    if not blocking:
        return False

    print(f"  [overlay] blocking overlay detected ({blocking['tag']}, role={blocking['role']}) — attempting dismiss")

    # Generic close patterns, tried in priority order
    close_selectors = [
        'button[aria-label="close"]',
        'button[aria-label="Close"]',
        '[data-testid="close-button"]',
        'button.lucide-x',
        'button[aria-label="Dismiss"]',
        'button:has(svg[class*="x"])',
    ]

    for sel in close_selectors:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                await locator.click(timeout=2000)
                await asyncio.sleep(0.3)
                print(f"  [overlay] dismissed via {sel!r}")
                return True
        except Exception:
            continue

    print(f"  [overlay] no known close pattern matched, skipping dismiss")
    return False


async def _dismiss_all_modals(page) -> bool:
    """Dismiss all visible modals/dialogs on the page.

    Tries common close patterns first, then falls back to pressing Escape.
    Returns True if any modal was dismissed.
    """
    dismissed = False

    # Try generic close buttons first
    close_selectors = [
        'button[aria-label="close"]',
        'button[aria-label="Close"]',
        '[data-testid="close-button"]',
        'button.lucide-x',
        'button[aria-label="Dismiss"]',
        'button:has(svg[class*="x"])',
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
    ]

    for sel in close_selectors:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                await locator.click(timeout=2000)
                await asyncio.sleep(0.3)
                print(f"  [modal] dismissed via {sel!r}")
                dismissed = True
                # Keep trying — there might be multiple modals
                continue
        except Exception:
            continue

    # If no close button found, try Escape key
    if not dismissed:
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
            print(f"  [modal] dismissed via Escape key")
            dismissed = True
        except Exception:
            pass

    return dismissed


async def _has_modal_on_page(session) -> bool:
    """Return True if a modal/dialog is currently visible on the page.

    Checks for common modal indicators: role=dialog, aria-modal=true, or
    elements with high z-index that look like overlays.
    """
    try:
        result = await session.page.evaluate("""
            () => {
                const dialogs = document.querySelectorAll('[role="dialog"], [aria-modal="true"]');
                if (dialogs.length > 0) return true;

                // Check for overlay-like elements with high z-index
                const overlays = document.querySelectorAll('[style*="z-index"]');
                for (const el of overlays) {
                    const style = window.getComputedStyle(el);
                    const zIndex = parseInt(style.zIndex) || 0;
                    if (zIndex > 100 && el.offsetHeight > 100 && el.offsetWidth > 100) {
                        return true;
                    }
                }
                return false;
            }
        """)
        return result
    except Exception:
        return False


def _is_transient_element(page, selector: str) -> bool:
    """Return True if the element at ``selector`` is a transient UI element.

    Checks the element (or its closest overlay ancestor) for indicators that
    it's a modal, dialog, banner, popup, or consent prompt — the kind of
    transient UI that may or may not appear between recordings.

    Detection criteria:
    - ``role=dialog`` or ``aria-modal=true`` → modal dialog
    - ``z-index > 100`` and not a normal page element → overlay/modal backdrop
    - ``data-testid`` or ``data-modal`` matches transient patterns
    - Text content contains transient markers (e.g. "modal", "dialog", "popup",
      "consent", "banner")
    - Selector string matches transient patterns (fallback when element doesn't
      exist)

    Returns True if we can confirm it's transient. Returns False otherwise.
    """
    try:
        result = page.evaluate(f"""
            (sel) => {{
                const el = document.querySelector(sel);
                if (!el) {{
                    // Element doesn't exist — check selector for transient patterns.
                    // This catches modals/banners that may have disappeared between
                    // recordings.
                    const selLower = sel.toLowerCase();
                    const transientPatterns = [
                        'button:has-text("ignore"',
                        'button:has-text("dismiss"',
                        'button:has-text("not now"',
                        'button:has-text("cancel"',
                        'button:has-text("accept cookies"',
                        'button:has-text("close modal"',
                        'button:has-text("close dialog"',
                        'button:has-text("i understand"',
                        '.modal',
                        '.popup',
                        '.dialog',
                        '.banner',
                        '[data-modal',
                        'aria-modal',
                    ];
                    const isTransient = transientPatterns.some(p => selLower.includes(p));

                    // Also check if any modal/dialog is currently visible on the page.
                    // If a modal is visible and the selector matches a common close pattern,
                    // this is likely a transient UI element.
                    const anyModalVisible = document.querySelector('[role="dialog"], [aria-modal="true"]');
                    const hasOverlay = document.querySelector('[style*="z-index"], [style*="zIndex"]');

                    if (isTransient || (anyModalVisible && hasOverlay)) {{
                        return {{ exists: false, transient: true, type: 'selector_or_overlay' }};
                    }}
                    return {{ exists: false, transient: false }};
                }}

                // Walk up to find the nearest modal/dialog container
                let node = el;
                while (node && node !== document.body) {{
                    const role = node.getAttribute('role');
                    const ariaModal = node.getAttribute('aria-modal');
                    const style = window.getComputedStyle(node);
                    const zIndex = parseInt(style.zIndex) || 0;

                    if (role === 'dialog' || ariaModal === 'true') {{
                        return {{ exists: true, transient: true, type: 'modal' }};
                    }}
                    if (zIndex > 100 && node.children.length < 20) {{
                        return {{ exists: true, transient: true, type: 'overlay' }};
                    }}

                    const dataAttrs = node.getAttribute('data-modal');
                    const dataTest = node.getAttribute('data-testid') || '';
                    if (dataAttrs || ['modal', 'popup', 'dialog', 'banner', 'toast'].includes(dataTest)) {{
                        return {{ exists: true, transient: true, type: 'transient' }};
                    }}

                    const text = (node.textContent || '').toLowerCase();
                    const transientWords = ['modal', 'dialog', 'popup', 'consent', 'banner',
                                            'accept cookies', 'dismiss', 'ignore', 'not now'];
                    if (transientWords.some(w => text.includes(w))) {{
                        return {{ exists: true, transient: true, type: 'text' }};
                    }}

                    node = node.parentElement;
                }}

                return {{ exists: true, transient: false }};
            }}
        """, selector)
        return result.get("transient", False)
    except Exception:
        return False


async def _do_click(page, event: dict, values_override: dict):
    element = event.get("element")
    button = {0: "left", 1: "middle", 2: "right"}.get(event.get("button", 0), "left")
    locator = _resolve_locator(page, element)

    if locator:
        try:
            await locator.click(button=button, timeout=5000)
            return True
        except Exception:
            pass

        # Click failed — check if an overlay is blocking the target
        try:
            rect = await locator.bounding_box()
            if rect:
                if await _dismiss_blocking_overlay(page, rect):
                    try:
                        await locator.click(button=button, timeout=5000)
                        return True
                    except Exception:
                        pass
        except Exception:
            pass

    # Fallback: click by rect
    rect = (element or {}).get("rect")
    if rect:
        x = rect["left"] + rect["width"] / 2
        y = rect["top"] + rect["height"] / 2
        await page.mouse.click(x, y, button=button)
        return True
    return False


async def _do_dblclick(page, event: dict):
    element = event.get("element")
    locator = _resolve_locator(page, element)
    if locator:
        try:
            await locator.dblclick(timeout=5000)
            return True
        except Exception:
            pass
    rect = (element or {}).get("rect")
    if rect:
        x = rect["left"] + rect["width"] / 2
        y = rect["top"] + rect["height"] / 2
        await page.mouse.dblclick(x, y)
        return True
    return False


async def _do_fill(page, event: dict, values_override: dict):
    element = event.get("element") or {}
    selector = element.get("selector")
    value = event.get("value")
    input_type = event.get("inputType", "")

    if selector in values_override:
        value = values_override[selector]
    elif _looks_redacted(value):
        print(f"\n  Recorded value for {selector!r} looks redacted.")
        value = input(f"  Enter the real value for {selector!r}: ") or value

    locator = _resolve_locator(page, element)
    if not locator:
        return False

    if input_type in ("checkbox", "radio"):
        await locator.check(timeout=5000)
        return True
    if input_type == "select" or element.get("tag") == "select":
        await locator.select_option(str(value), timeout=5000)
        return True
    await locator.fill(str(value), timeout=5000)
    return True


async def _do_drag(page, event: dict):
    frm, to = event.get("from"), event.get("to")
    if not frm or not to:
        return False
    await page.mouse.move(frm["x"], frm["y"])
    await page.mouse.down()
    steps = 12
    for i in range(1, steps + 1):
        x = frm["x"] + (to["x"] - frm["x"]) * i / steps
        y = frm["y"] + (to["y"] - frm["y"]) * i / steps
        await page.mouse.move(x, y)
        await asyncio.sleep(0.01)
    await page.mouse.up()
    return True


async def _do_keydown(page, event: dict):
    key = event.get("key")
    if not key:
        return True

    # Special case: Enter on password/email input → submit the form
    if key == "Enter":
        element = event.get("element")
        if element:
            selector = element.get("selector")
            input_type = event.get("inputType", "")
            if input_type in ("password", "email") or (selector and ("password" in selector or "email" in selector)):
                # Find the parent form and dispatch submit event — React's event delegation listens
                # on `document` for native events, so dispatching `submit` on the form is enough.
                submitted = await page.evaluate(f"""
                    () => {{
                        const input = document.querySelector({selector!r});
                        if (!input) return false;
                        const form = input.closest('form');
                        if (!form) return false;
                        form.dispatchEvent(new Event('submit', {{bubbles: true, cancelable: true}}));
                        return true;
                    }}
                """)
                if submitted:
                    print(f"  [keydown] submitted form via Enter")
                    return True

    await page.keyboard.press(key)
    return True


async def _do_scroll(page, event: dict):
    x, y = event.get("x", 0), event.get("y", 0)
    await page.evaluate("([x, y]) => window.scrollTo(x, y)", [x, y])
    return True


async def _replay_event(page, event: dict, values_override: dict) -> bool:
    etype = event["type"]
    try:
        if etype == "click":
            return await _do_click(page, event, values_override)
        elif etype == "dblclick":
            return await _do_dblclick(page, event)
        elif etype == "contextmenu":
            return await _do_click(page, {**event, "button": 2}, values_override)
        elif etype == "fill":
            return await _do_fill(page, event, values_override)
        elif etype == "drag":
            return await _do_drag(page, event)
        elif etype == "keydown":
            return await _do_keydown(page, event)
        elif etype == "scroll":
            return await _do_scroll(page, event)
    except Exception:
        return False
    return False


def _reorder_events(events: list[dict]) -> list[dict]:
    """Reorder events so fill happens before keydown on the same input.

    When a user types a password and presses Enter in quick succession,
    the recording may capture Enter before the fill. This reorders
    [keydown, fill] → [fill, keydown] when they target the same element.
    """
    reordered = []
    i = 0
    while i < len(events):
        event = events[i]
        # Check if this is a keydown on an input
        if event.get("type") == "keydown" and event.get("key") == "Enter":
            element = event.get("element", {})
            selector = element.get("selector", "")
            input_type = event.get("inputType", "")
            # If the next event is a fill on the same input, swap order
            if i + 1 < len(events):
                next_event = events[i + 1]
                if next_event.get("type") == "fill":
                    next_element = next_event.get("element", {})
                    next_selector = next_element.get("selector", "")
                    if selector == next_selector:
                        # Swap: fill first, then keydown
                        reordered.append(next_event)
                        reordered.append(event)
                        i += 2
                        continue
        reordered.append(event)
        i += 1
    return reordered


async def _do_replay_interactions(
    session,
    events: list[dict],
    start_url: str | None,
    values_override: dict | None,
    speed: float,
    max_delay: float,
) -> dict:
    """Replay a flat array of DOM events. Returns a summary dict with failed_events."""
    values_override = values_override or {}

    # Reorder events: fill before keydown on same input
    events = _reorder_events(events)

    # Navigate to start URL if provided or if first event has one
    if start_url:
        await session.page.goto(start_url, wait_until="domcontentloaded", timeout=15000)
    else:
        first_url = next(
            (e["url"] for e in events if e.get("url") and not e["url"].startswith("chrome://")),
            None,
        )
        if first_url:
            await session.page.goto(first_url, wait_until="domcontentloaded", timeout=15000)

    prev_time = events[0].get("time")
    ok, failed = 0, 0
    total = len(events)
    failed_events = []  # Track failed events with details

    for i, event in enumerate(events, start=1):
        cur_time = event.get("time")
        if prev_time is not None and cur_time is not None:
            delay = max(0.0, (cur_time - prev_time) / 1000.0) / max(speed, 0.01)
            delay = min(delay, max_delay)
            if delay > 0:
                await asyncio.sleep(delay)
        prev_time = cur_time

        target_url = event.get("url")
        if target_url and not target_url.startswith("chrome://") and not _same_page(session.page.url, target_url):
            try:
                await session.page.goto(target_url)
            except Exception:
                pass

        desc = event.get("element", {}).get("selector") or event.get("key") or ""
        try:
            success = await _replay_event(session.page, event, values_override)
            if success:
                ok += 1
            else:
                # Check if this was a transient UI element (modal, dialog,
                # popup, banner) that may or may not appear during replay.
                # If so, skip it silently — it's not a real failure.
                element = event.get("element") or {}
                selector = element.get("selector", "")
                if selector and _is_transient_element(session.page, selector):
                    print(f"  [replay:{i}] skipped transient element {desc!r} — not a real failure")
                    ok += 1
                    continue

                failed += 1
                failed_events.append({
                    "event_index": i - 1,  # 0-indexed
                    "event_type": event.get("type", "unknown"),
                    "error": f"Replay failed for {desc}",
                })
        except Exception as e:
            failed += 1
            failed_events.append({
                "event_index": i - 1,  # 0-indexed
                "event_type": event.get("type", "unknown"),
                "error": str(e),
            })

    return {
        "total": total,
        "successful": ok,
        "failed": failed,
        "status": "success" if failed == 0 else "partial_failure",
        "failed_events": failed_events,
    }


# ---------------------------------------------------------------------------
# MCP tool wrappers
# ---------------------------------------------------------------------------

@tool
async def replay_automation(
    automation_path: str,
    session_id: str | None = None,
    cdp_endpoint: str | None = None,
    profile: str | None = None,
) -> dict:
    """Replay a recorded automation JSON file against the current session.

    Reads the automation file and replays each tool step using the session's
    browser page. No variable substitution — recordings contain exact values.

    If no ``session_id`` is provided but ``cdp_endpoint`` is, a CDP session is
    auto-created. If ``profile`` is provided without ``session_id``, a persistent
    session is created.

    Args:
        automation_path: Path to the automation JSON file.
        session_id: Optional existing session ID to replay into.
        cdp_endpoint: Optional CDP endpoint — creates a session if no session_id
            is given.
        profile: Optional Chrome profile name — creates a persistent session
            if no session_id is given.

    Returns:
        Summary dict with ``name``, ``total``, ``successful``, ``failed``,
        ``status``, and per-step ``results``.

    Example::

        replay_automation(
            automation_path="automations/login-flow.json",
            session_id="sess_abc"
        )
    """
    # Load and validate automation
    path = Path(automation_path)
    if not path.exists():
        return {"status": "error", "error": "file_not_found", "message": f"File not found: {automation_path}"}

    try:
        with open(path) as f:
            automation = json.load(f)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": "invalid_json", "message": str(e)}

    # Auto-create session if needed
    session = None
    if session_id:
        session = await get_session_by_id(session_id)
        if session is None:
            return {"status": "error", "error": "session_not_found", "message": f"Session {session_id} not found"}
    elif cdp_endpoint:
        from mcp_tools.session import session_start
        result = await session_start(
            email=f"replay@{profile or 'default'}.local",
            profile_name=profile or "",
            cdp_endpoint=cdp_endpoint,
        )
        if result.get("status") != "ready":
            return {"status": "error", "error": "session_failed", "message": result.get("message", "unknown")}
        session = await get_session_by_id(result["session_id"])
    elif profile:
        from mcp_tools.session import session_start
        result = await session_start(
            email=f"replay@{profile}.local",
            profile_name=profile,
        )
        if result.get("status") != "ready":
            return {"status": "error", "error": "session_failed", "message": result.get("message", "unknown")}
        session = await get_session_by_id(result["session_id"])
    else:
        return {"status": "error", "error": "no_session", "message": "Provide session_id, cdp_endpoint, or profile"}

    # Execute
    return await _do_replay_automation(session, automation)


@tool
async def replay_interactions(
    input_path: str,
    session_id: str,
    base_url: str | None = None,
    values: str | None = None,
    speed: float = 1.0,
    max_delay: float = 3.0,
) -> dict:
    """Replay a recorded DOM events file (human/manual recording).

    Loads a flat JSON array of DOM events and replays them against the session's
    browser page — navigate, click (locator + rect fallback), fill, drag, scroll,
    keydown.

    Args:
        input_path: Path to the recorded events JSON file (flat array).
        session_id: The session ID to replay into (must already exist).
        base_url: Optional base URL to navigate to before replaying. Overrides
            the first event's URL.
        values: Optional path to a JSON file mapping CSS selector → real value,
            for redacted fields.
        speed: Playback speed multiplier (2 = twice as fast). Default 1.0.
        max_delay: Cap on inter-event delay in seconds. Default 3.0.

    Returns:
        Summary dict with ``total``, ``successful``, ``failed``, ``status``.

    Example::

        replay_interactions(
            input_path="automations/default/test-manual.json",
            session_id="sess_abc",
            base_url="http://localhost:3000",
            speed=1.5
        )
    """
    # Load events
    path = Path(input_path)
    if not path.exists():
        return {"status": "error", "error": "file_not_found", "message": f"File not found: {input_path}"}

    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {"status": "error", "error": "invalid_json", "message": str(e)}

    if not isinstance(data, list):
        return {"status": "error", "error": "invalid_format", "message": "Expected a JSON array of events."}

    # Filter to replayable event types
    events = [e for e in data if e.get("type") in REPLAYABLE_TYPES]
    if not events:
        return {"status": "error", "error": "no_events", "message": "No replayable events found."}

    # Load session
    session = await get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "error": "session_not_found", "message": f"Session {session_id} not found"}
    if session.page is None:
        return {"status": "error", "error": "no_page", "message": "Session has no active page."}

    # Load values override
    values_override: dict = {}
    if values:
        try:
            values_override = json.loads(Path(values).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return {"status": "error", "error": "invalid_values", "message": str(e)}

    # Execute
    return await _do_replay_interactions(session, events, base_url, values_override, speed, max_delay)