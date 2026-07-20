"""Screenshot tool — page capture with optional viewport presets and file persistence."""

import time
from pathlib import Path
from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id, parse_viewport, apply_viewport
from mcp_tools.logging_utils import _log_action

# Viewport presets as specified in QA_AUTOMATION_TOOLS_SPEC.md
VIEWPORT_PRESETS = {
    # Generic presets
    "desktop": (1920, 1080),
    "tablet": (1024, 1366),
    "mobile": (393, 852),
    # Named desktop presets
    "desktop-1080p": (1920, 1080),
    "desktop-720p": (1280, 720),
    "desktop-1440p": (2560, 1440),
    # Named mobile presets
    "iphone-14-pro": (393, 852),
    "iphone-se": (375, 667),
    "pixel-7": (412, 915),
    "galaxy-s24": (360, 780),
    # Named tablet presets
    "ipad-pro-12": (1024, 1366),
    "ipad-mini": (768, 1024),
    "surface-pro": (1024, 1336),
}


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = await get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


async def _get_screenshot_dir(base_dir: Path = None) -> Path:
    """Return the automations/screenshots directory, creating it if necessary."""
    base = base_dir / "automations" if base_dir else Path.cwd() / "automations"
    screenshot_dir = base / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    return screenshot_dir


@tool
@_log_action("screenshot")
async def screenshot(
    session_id: str,
    name: str,
    viewport: str,
    full_page: bool = True,
    img_type: str = "png",
) -> dict:
    """
    Capture a screenshot of the current page and save to automations/screenshots/.

    Args:
        session_id: The session ID.
        name: Name for the screenshot (used as filename prefix).
        viewport: Preset name (e.g. "iphone-14-pro") or "WxH" (e.g. "393x852").
                  Resizes the session context's viewport so the page reflows at that size.
        full_page: Capture the entire scrollable page (default: True).
        img_type: Image type "png" (default) or "jpeg".

    Returns:
        ``{"path": "automations/screenshots/{name}_{timestamp}.png"}``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    screenshot_dir = await _get_screenshot_dir(session.base_dir)
    timestamp = int(time.time() * 1000)
    filename = f"{name}_{timestamp}.png"
    filepath = screenshot_dir / filename

    if viewport:
        parsed = parse_viewport(viewport)
        if parsed:
            apply_viewport(session, parsed)
            # Resize the page viewport, then reload so the CSS reflows at the
            # new size. (context.viewport_size is sync API — does nothing on
            # async contexts, so we use page.set_viewport_size instead.)
            await session.page.set_viewport_size(parsed)

    buf = await session.page.screenshot(full_page=full_page, type=img_type)
    filepath.write_bytes(buf)
    return {"path": str(filepath)}


@tool
@_log_action("snapshot")
async def snapshot(
    session_id: str,
    name: str,
) -> dict:
    """
    Capture an accessibility snapshot of the current page and save to automations/snapshots/.

    Returns the full accessibility tree in YAML format, useful for verifying
    page structure and accessibility attributes.

    Args:
        session_id: The session ID.
        name: Name for the snapshot (used as filename prefix).

    Returns:
        ``{"path": "automations/snapshots/{name}_{timestamp}.yml"}``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    base = session.base_dir / "automations" if session.base_dir else Path.cwd() / "automations"
    snapshot_dir = base / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time() * 1000)
    filename = f"{name}_{timestamp}.yml"
    filepath = snapshot_dir / filename

    yaml_content = await session.page.aria_snapshot()
    filepath.write_text(yaml_content)
    return {"path": str(filepath)}
