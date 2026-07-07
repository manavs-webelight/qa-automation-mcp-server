"""Navigation tools: navigate, navigate_with_retry, navigate_back, reload."""

import asyncio

from fastmcp.tools import tool
from playwright._impl._errors import Error as PlaywrightError
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError

from helpers.session_store import (
    get_session_by_id,
    apply_viewport,
    parse_viewport,
)


async def _do_navigate(page, url: str) -> dict:
    """
    Core navigation logic with proper error categorization.

    Returns:
        dict with url, title, final_url on success.
        On error, returns status: "error" with categorized error type.
    """
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        final_url = page.url
        title = await page.title()

        if response is not None and response.status >= 400:
            return {
                "status": "error",
                "error": "page_error",
                "http_status": response.status,
                "message": f"HTTP {response.status}",
            }

        return {"url": url, "title": title, "final_url": final_url}

    except PlaywrightTimeoutError:
        return {"status": "error", "error": "timeout", "message": "Navigation timed out"}

    except PlaywrightError as e:
        error_msg = str(e).lower()
        # Detect redirect loop (too many redirects)
        if "redirect" in error_msg or "too many" in error_msg:
            return {"status": "error", "error": "redirect_loop"}
        # Network failure (connection refused, DNS failed, etc.)
        if any(x in error_msg for x in ["net::", "failed to fetch", "network", "connection"]):
            return {"status": "error", "error": "network_error", "message": str(e)}
        # Other Playwright errors
        return {"status": "error", "error": "navigation_error", "message": str(e)}

    except Exception as e:
        error_msg = str(e).lower()
        # Network failures from lower layers
        if any(x in error_msg for x in ["net::", "failed to fetch", "network", "connection"]):
            return {"status": "error", "error": "network_error", "message": str(e)}
        return {"status": "error", "error": "navigation_error", "message": str(e)}


@tool
async def navigate(
    session_id: str,
    url: str,
    viewport: str | None,
) -> dict:
    """
    Navigate to a URL in the session's current page.

    Args:
        session_id: The session ID.
        url: The URL to navigate to.
        viewport: Optional. Preset name (e.g. "iphone-14-pro") or "WxH" (e.g. "393x852").
                  Sets the session context's viewport size before navigating.

    Returns:
        dict with url, title, and final_url on success.
        Error cases:
        - page_error: HTTP 400+ response (404, 500, etc.)
        - network_error: Network failure (DNS, connection refused, etc.)
        - timeout: Navigation timed out
        - redirect_loop: Too many redirects
    """
    session = get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    if viewport:
        parsed = parse_viewport(viewport)
        if parsed:
            apply_viewport(session, parsed)
            # Set viewport on the page before navigating so it loads at the
            # correct size (context.viewport_size is sync API — does nothing
            # on async contexts).
            await session.page.set_viewport_size(parsed)

    return await _do_navigate(session.page, url)


@tool
async def navigate_with_retry(
    session_id: str, url: str, options: dict | None = None
) -> dict:
    """
    Navigate with automatic retry on failure.

    Useful for flaky networks or slow servers.

    Args:
        session_id: The session ID.
        url: The URL to navigate to.
        options: Retry options:
            - retries: Number of retry attempts (default: 3)
            - retry_delay_ms: Delay between retries in milliseconds (default: 2000)

    Returns:
        Same as navigate on success.
        On failure after all retries: { status: "error", error: "all_retries_exhausted", attempts: N }
    """
    session = get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    options = options or {}
    retries = options.get("retries", 3)
    retry_delay_ms = options.get("retry_delay_ms", 2000)

    page = session.page
    attempts = 0

    for attempt in range(retries + 1):
        attempts += 1
        result = await _do_navigate(page, url)

        if result.get("status") != "error":
            result["attempts"] = attempts
            return result

        # If this was the last attempt, return all_retries_exhausted
        if attempt >= retries:
            return {
                "status": "error",
                "error": "all_retries_exhausted",
                "attempts": attempts,
                "last_error": result,
            }

        # Wait before retrying
        await asyncio.sleep(retry_delay_ms / 1000)

    # Should not reach here, but just in case
    return {
        "status": "error",
        "error": "all_retries_exhausted",
        "attempts": attempts,
    }


@tool
async def navigate_back(session_id: str) -> dict:
    """
    Navigate back in the browser history.

    Args:
        session_id: The session ID.

    Returns:
        dict with url and title after going back.
    """
    session = get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    page = session.page
    await page.go_back()
    return {"url": page.url, "title": await page.title()}


@tool
async def reload(session_id: str) -> dict:
    """
    Reload the current page.

    Args:
        session_id: The session ID.

    Returns:
        dict with url and title after reloading.
    """
    session = get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    page = session.page
    await page.reload()
    return {"url": page.url, "title": await page.title()}
