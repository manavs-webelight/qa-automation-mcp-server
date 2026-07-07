"""Session lifecycle tools: session_start, session_close, session_list."""

import uuid
from pathlib import Path

from fastmcp.tools import tool
from playwright.async_api import async_playwright

from helpers.session_store import (
    SessionData,
    get_session_by_email,
    get_session_by_id,
    get_session_id_by_email,
    list_all_sessions,
    register_session,
    unregister_session,
)


async def _get_profiles_dir() -> Path:
    """Return the profiles directory, creating it if needed."""
    profiles = Path(__file__).parent.parent / "profiles"
    profiles.mkdir(exist_ok=True)
    return profiles


@tool
async def session_start(email: str, profile_name: str) -> dict:
    """
    Start a new browser session for the given user.

    If the user already has an active session, returns the existing session
    without creating a new one.

    Args:
        email: The user's email address (unique per session).
        profile_name: Optional Chrome profile name (stored under profiles/).

    Returns:
        dict with session_id, profile, status, and reused flag.
    """
    # Check if user already has an active session
    existing = get_session_by_email(email)
    if existing is not None:
        session_id = get_session_id_by_email(email)
        return {
            "session_id": session_id,
            "profile": existing.profile,
            "status": "ready",
            "reused": True,
        }

    # Generate new session ID
    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    # Launch Playwright browser (async)
    p = await async_playwright().start()

    if profile_name:
        profile_path = await _get_profiles_dir() / profile_name
        profile_path.mkdir(parents=True, exist_ok=True)
        # Use persistent context via launch_persistent_context
        context = await p.chromium.launch_persistent_context(
            str(profile_path),
            headless=False,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        print("Using persistent context with profile:", profile_path)
        page = context.pages[0] if context.pages else await context.new_page()
    else:
        # Incognito / temporary context
        print("Using incognito context (no profile)")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()

    # Store tabs list (initially just the one page)
    tabs = [page]

    # Create session data
    session = SessionData(
        session_id="",  # set by register_session
        email=email,
        profile=profile_name,
        context=context,
        page=page,
        current_tab_index=0,
        tabs=tabs,
    )

    # Register in store (handles both email and session_id indexes)
    register_session(session_id, session)

    return {
        "session_id": session_id,
        "profile": profile_name,
        "status": "ready",
        "reused": False,
    }


@tool
async def session_close(session_id: str) -> dict:
    """
    Close a browser session and clean up all resources.

    Args:
        session_id: The session ID to close.

    Returns:
        dict with status: "closed".
    """
    session = get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    # Close all tabs gracefully
    for tab in session.tabs:
        try:
            await tab.close()
        except Exception:
            pass

    # Close the context
    try:
        await session.context.close()
    except Exception:
        pass

    # Remove from store
    unregister_session(session_id)

    return {"status": "closed"}


@tool
async def session_list() -> dict:
    """
    List all active sessions (admin view).

    Returns:
        dict with list of sessions (session_id, email, profile, started_at).
    """
    sessions = []
    for session in list_all_sessions():
        sessions.append({
            "session_id": session.session_id,
            "email": session.email,
            "profile": session.profile,
            "started_at": session.started_at.isoformat(),
        })
    return {"sessions": sessions}
