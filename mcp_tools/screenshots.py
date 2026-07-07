"""Screenshot tool — page capture with optional viewport presets and file persistence."""

import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id

# Viewport presets as specified in QA_AUTOMATION_TOOLS_SPEC.md
VIEWPORT_PRESETS = {
    "desktop-1080p": (1920, 1080),
    "desktop-720p": (1280, 720),
    "desktop-1440p": (2560, 1440),
    "iphone-14-pro": (393, 852),
    "iphone-se": (375, 667),
    "pixel-7": (412, 915),
    "galaxy-s24": (360, 780),
    "ipad-pro-12": (1024, 1366),
    "ipad-mini": (768, 1024),
    "surface-pro": (1024, 1336),
}


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


async def _get_screenshot_dir() -> Path:
    """Return the qa-automation/screenshots directory, creating it if necessary."""
    screenshot_dir = Path.cwd() / "qa-automation" / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    return screenshot_dir


@tool
async def screenshot(
    session_id: str,
    name: str,
    viewport: str | None = None,
    full_page: bool = False,
    img_type: str = "png",
) -> dict:
    """
    Capture a screenshot of the current page and save to qa-automation/screenshots/.

    Args:
        session_id: The session ID.
        name: Name for the screenshot (used as filename prefix).
        viewport: Optional. Preset name (e.g. "iphone-14-pro") or "WxH" (e.g. "393x852").
                  Creates a temp context at that size for true responsive screenshots.
                  Omit to use current page size.
        full_page: Capture the entire scrollable page (default: False).
        img_type: Image type "png" (default) or "jpeg".

    Returns:
        ``{"path": "qa-automation/screenshots/{name}_{timestamp}.png"}``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    screenshot_dir = await _get_screenshot_dir()
    timestamp = int(time.time() * 1000)
    filename = f"{name}_{timestamp}.png"
    filepath = screenshot_dir / filename

    width, height = None, None
    if viewport:
        if viewport in VIEWPORT_PRESETS:
            width, height = VIEWPORT_PRESETS[viewport]
        elif "x" in viewport:
            parts = viewport.lower().split("x")
            if len(parts) == 2:
                width, height = int(parts[0]), int(parts[1])

    if width and height:
        # Temp context at correct viewport size
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": width, "height": height},
            ignore_https_errors=True,
        )
        page = await ctx.new_page()
        await page.goto(session.page.url, wait_until="domcontentloaded", timeout=30000)
        buf = await page.screenshot(full_page=full_page, type=img_type)
        await ctx.close()
        await browser.close()
        await p.stop()
    else:
        buf = await session.page.screenshot(full_page=full_page, type=img_type)

    filepath.write_bytes(buf)
    return {"path": str(filepath)}
