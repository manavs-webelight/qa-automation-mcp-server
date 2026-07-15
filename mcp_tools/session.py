"""Session lifecycle tools: session_start, session_close, session_list."""

import uuid
from pathlib import Path

from fastmcp.tools import tool
from playwright.async_api import async_playwright

from helpers.session_store import (
    SessionData,
    _session_by_id,
    _session_lock,
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
async def session_start(email: str = "", profile_name: str = "", cdp_endpoint: str | None = None, *, base_dir: str) -> dict:
    """
    Start a new browser session for the given user.

    If the user already has an active session, returns the existing session
    without creating a new one.

    For CDP sessions, the cdp_endpoint uniquely identifies the session
    (one port = one Chrome instance). Email is ignored for CDP connections
    and only used to look up existing launch/persistent sessions.

    Args:
        email: The user's email address (only used for launch/persistent
            sessions to maintain uniqueness). Ignored for CDP connections.
        profile_name: Optional Chrome profile name (stored under profiles/).
        cdp_endpoint: Optional Chrome DevTools Protocol endpoint. When provided,
            the server connects to an existing Chrome instance instead of
            launching a new one.
        base_dir: **Required.** Base directory for all output files
            (recordings, screenshots, snapshots). Pass as keyword argument.

    Returns:
        dict with session_id, profile, status, reused, cdp_endpoint, and
        connect_method.
    """
    # Determine if this is a CDP session
    is_cdp = cdp_endpoint is not None

    if is_cdp:
        # CDP sessions: the endpoint is the unique identifier, not the email.
        # Find any existing session that shares this CDP endpoint.
        existing = None
        async with _session_lock:
            for session in _session_by_id.values():
                if session.connect_method == "cdp" and session.cdp_endpoint == cdp_endpoint:
                    existing = session
                    break

        if existing is not None:
            return {
                "session_id": existing.session_id,
                "profile": existing.profile,
                "status": "ready",
                "reused": True,
                "cdp_endpoint": existing.cdp_endpoint,
                "connect_method": existing.connect_method,
            }

        # Generate new session ID
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        # Connect to an already-running Chrome via CDP
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.connect_over_cdp(cdp_endpoint)
        except Exception as e:
            return {
                "status": "error",
                "message": f"Failed to connect to Chrome at {cdp_endpoint}: {e}",
            }
        connect_method = "cdp"

        # Reuse the existing default context so we get a new tab, not a new window.
        # Each agent gets its own page within the shared context.
        if not browser.contexts:
            await pw.stop()
            return {
                "status": "error",
                "message": f"No browser contexts found at {cdp_endpoint}. Is Chrome running?",
            }
        default_context = browser.contexts[0]
        context = default_context
        page = await context.new_page()
        print(f"Connected to CDP endpoint: {cdp_endpoint}")

        # Store tabs list (initially just the one page)
        tabs = [page]

        # Create session data
        session = SessionData(
            session_id="",  # set by register_session
            email=email or cdp_endpoint,
            profile=profile_name,
            context=context,
            page=page,
            current_tab_index=0,
            tabs=tabs,
            cdp_endpoint=cdp_endpoint,
            connect_method=connect_method,
            playwright=pw,
            base_dir=Path(base_dir).resolve() if base_dir else None,
        )

        # Register in store (handles both email and session_id indexes)
        await register_session(session_id, session)

        return {
            "session_id": session_id,
            "profile": profile_name,
            "status": "ready",
            "reused": False,
            "cdp_endpoint": cdp_endpoint,
            "connect_method": connect_method,
            "base_dir": str(session.base_dir) if session.base_dir else None,
        }

    # Non-CDP: launch or persistent (email still matters for uniqueness)
    # Check if user already has an active session
    existing = await get_session_by_email(email)
    if existing is not None:
        session_id = await get_session_id_by_email(email)
        return {
            "session_id": session_id,
            "profile": existing.profile,
            "status": "ready",
            "reused": True,
            "cdp_endpoint": existing.cdp_endpoint,
            "connect_method": existing.connect_method,
        }

    # Generate new session ID
    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    # Single Playwright instance for this session
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
        connect_method = "persistent"
    else:
        # Incognito / temporary context
        print("Using incognito context (no profile)")
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(no_viewport=True)
        page = await context.new_page()
        connect_method = "launch"

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
        cdp_endpoint=cdp_endpoint,
        connect_method=connect_method,
        playwright=None,
        base_dir=Path(base_dir).resolve() if base_dir else None,
    )

    # Register in store (handles both email and session_id indexes)
    await register_session(session_id, session)

    return {
        "session_id": session_id,
        "profile": profile_name,
        "status": "ready",
        "reused": False,
        "cdp_endpoint": cdp_endpoint,
        "connect_method": connect_method,
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
    session = await get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    # Close context/browser depending on how the session was created
    if session.connect_method == "cdp":
        # For CDP sessions, only close the agent's own pages.
        # The context is shared with the user's Chrome — don't close it.
        # Just disconnect Playwright from the Chrome instance (doesn't close Chrome)
        # Don't close the tabs we opened - they're part of the shared context
        if session.playwright is not None:
            try:
                await session.playwright.stop()
            except Exception:
                pass
    else:
        # For launch/persistent sessions, close the context then the browser
        try:
            await session.context.close()
        except Exception:
            pass
        try:
            await session.context.browser.close()
        except Exception:
            pass

    # Remove from store
    await unregister_session(session_id)

    return {"status": "closed"}


@tool
async def session_list() -> dict:
    """
    List all active sessions (admin view).

    Returns:
        dict with list of sessions (session_id, email, profile, started_at).
    """
    sessions = []
    for session in await list_all_sessions():
        sessions.append({
            "session_id": session.session_id,
            "email": session.email,
            "profile": session.profile,
            "started_at": session.started_at.isoformat(),
            "cdp_endpoint": session.cdp_endpoint,
            "connect_method": session.connect_method,
        })
    return {"sessions": sessions}
