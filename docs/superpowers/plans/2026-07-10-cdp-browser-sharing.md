# CDP Browser Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the MCP server to connect to an externally-opened Chrome instance via CDP, enabling multiple agents to share the same browser profile.

**Architecture:** Three connection modes for `session_start`: (A) CDP connect to existing browser, (B) persistent profile launch, (C) fresh incognito launch. The session store gains two new fields (`cdp_endpoint`, `connect_method`) to track which mode was used. `session_close` branches on `connect_method`: CDP sessions `disconnect()` the Playwright handle, launch/persistent sessions `close()` the browser they own. All other tools are unchanged because they operate on `session.page` and `session.context` which are populated correctly for all three modes.

**Tech Stack:** Python 3.12+, Playwright async API, fastmcp, pytest

## Global Constraints

- Requires Python >= 3.12 (already in pyproject.toml).
- No new dependencies — use existing `playwright` and `fastmcp`.
- All new async functions follow the existing `async def` + `@tool` pattern.
- Tests must run without a real browser — mock Playwright objects.
- Existing tests (recording, navigation, tabs) must keep passing after changes.
- `cdp_endpoint` parameter defaults to `None` everywhere — never breaks existing callers.
- Plan: `docs/superpowers/plans/2026-07-10-cdp-browser-sharing.md`

---

### Task 1: Test infrastructure and SessionData field tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_session_store.py`
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: `helpers.session_store.SessionData` dataclass
- Produces: Unit tests for the new `cdp_endpoint` and `connect_method` fields

**Step 1: Create test package and write SessionData field tests**

Create `tests/__init__.py` (empty file).

Create `tests/test_session_store.py`:

```python
"""Tests for helpers.session_store — specifically the new CDP fields."""

from helpers.session_store import SessionData


def test_session_data_default_values():
    """SessionData must have sensible defaults for new CDP fields."""
    s = SessionData(
        session_id="sess_test",
        email="test@example.com",
        profile=None,
        context=None,
        page=None,
    )
    assert s.cdp_endpoint is None
    assert s.connect_method == "launch"


def test_session_data_cdp_fields_explicit():
    """Setting CDP fields explicitly must persist."""
    s = SessionData(
        session_id="sess_cdp",
        email="cdp@example.com",
        profile=None,
        context=None,
        page=None,
        cdp_endpoint="ws://localhost:9222",
        connect_method="cdp",
    )
    assert s.cdp_endpoint == "ws://localhost:9222"
    assert s.connect_method == "cdp"


def test_session_data_persistent_profile():
    """Persistent profile sessions have connect_method='persistent'."""
    s = SessionData(
        session_id="sess_prof",
        email="prof@example.com",
        profile="ekta10",
        context=None,
        page=None,
        cdp_endpoint=None,
        connect_method="persistent",
    )
    assert s.profile == "ekta10"
    assert s.connect_method == "persistent"
    assert s.cdp_endpoint is None


def test_session_data_register_and_lookup():
    """Existing register/get/unregister must still work with new fields."""
    from helpers.session_store import (
        register_session,
        get_session_by_id,
        unregister_session,
    )

    s = SessionData(
        session_id="sess_lookup_test",
        email="lookup@example.com",
        profile=None,
        context=None,
        page=None,
        cdp_endpoint="ws://localhost:9223",
        connect_method="cdp",
    )
    register_session("sess_lookup_test", s)

    found = get_session_by_id("sess_lookup_test")
    assert found is not None
    assert found.cdp_endpoint == "ws://localhost:9223"
    assert found.connect_method == "cdp"

    unregister_session("sess_lookup_test")
    assert get_session_by_id("sess_lookup_test") is None
```

**Step 2: Install pytest and run tests to verify they fail**

```bash
cd /home/web-h-063/Documents/office-beacon-fe/qa-automation-mcp-server
pip install pytest pytest-asyncio 2>&1 | tail -3
python -m pytest tests/test_session_store.py -v
```

Expected: FAIL — `cdp_endpoint` and `connect_method` don't exist on `SessionData` yet. Errors will be `AttributeError: 'SessionData' object has no attribute 'cdp_endpoint'`.

**Step 3: Add the two new fields to SessionData**

Edit `helpers/session_store.py` at line 21 (after `started_at`), add two fields:

```python
    cdp_endpoint: str | None = None
    connect_method: str = "launch"  # "cdp" | "persistent" | "launch"
```

The field order after edit should be:

```python
@dataclass
class SessionData:
    session_id: str
    email: str
    profile: Optional[str]
    context: Any
    page: Any
    current_tab_index: int = 0
    tabs: list = field(default_factory=list)
    active_frame: Any = None
    viewport: dict | None = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    console_errors: list = field(default_factory=list)
    console_messages: list = field(default_factory=list)
    is_tracing: bool = False
    routes: list = field(default_factory=list)
    request_history: list = field(default_factory=list)
    is_recording: bool = False
    recording_name: str | None = None
    recording_tools: list = field(default_factory=list)
    cdp_endpoint: str | None = None
    connect_method: str = "launch"
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_session_store.py -v
```

Expected: 4 PASS.

**Step 5: Commit**

```bash
git add tests/__init__.py tests/test_session_store.py helpers/session_store.py
git commit -m "test: add SessionData CDP field tests and add cdp_endpoint/connect_method fields"
```

---

### Task 2: session_start — CDP connection path

**Files:**
- Modify: `mcp_tools/session.py` (the `session_start` function)

**Interfaces:**
- Consumes: `helpers.session_store.SessionData` (now has `cdp_endpoint`, `connect_method`), existing `register_session`, `async_playwright` from playwright
- Produces: `session_start(email, profile_name=None, cdp_endpoint=None)` that returns CDP-connected sessions

**Step 1: Write the failing test**

Create `tests/test_session_cdp_connection.py`:

```python
"""Tests for session_start CDP connection path.

We mock Playwright's async_playwright() so we never need a real browser.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# -- Fixtures ---------------------------------------------------------------


class MockPage:
    """Mimics a Playwright Page object."""
    def __init__(self, url="about:blank"):
        self.url = url
        self._title = "Test Page"

    async def title(self):
        return self._title


class MockContext:
    """Mimics a Playwright BrowserContext."""
    def __init__(self, page):
        self.pages = [page]
        self._browser = MockBrowser()

    async def new_page(self):
        new = MockPage("https://example.com")
        self.pages.append(new)
        return new

    async def close(self):
        pass


class MockBrowser:
    """Mimics a Playwright Browser object."""
    def __init__(self):
        self.contexts = []
        self._closed = False

    async def new_context(self):
        page = MockPage("https://example.com")
        ctx = MockContext(page)
        self.contexts.append(ctx)
        return ctx

    async def close(self):
        self._closed = True

    async def disconnect(self):
        pass


# -- Tests ------------------------------------------------------------------


@pytest.fixture
def mock_playwright_module():
    """Return a fully mocked playwright.async_api module."""
    mock_pw = MagicMock()
    mock_chromium = MagicMock()
    mock_pw.chromium = mock_chromium
    return mock_pw, mock_chromium


@pytest.mark.asyncio
async def test_session_start_with_cdp_endpoint(mock_playwright_module):
    """When cdp_endpoint is provided, connect_over_cdp must be called."""
    mock_pw, mock_chromium = mock_playwright_module

    # Simulate: connect_over_cdp returns a browser with one context/page already
    existing_page = MockPage("http://localhost:3000")
    existing_context = MockContext(existing_page)
    mock_browser = MockBrowser()
    mock_browser.contexts = [existing_context]
    mock_chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    # Mock async_playwright().start() to return our mocked module
    async_playwright_mock = AsyncMock()
    async_playwright_mock.start = AsyncMock(return_value=asyncio.coroutine(lambda: mock_pw)())

    with patch("mcp_tools.session.async_playwright", return_value=async_playwright_mock):
        from mcp_tools.session import session_start
        result = await session_start(
            email="cdp_user@test.com",
            cdp_endpoint="ws://localhost:9222",
        )

    # Must call connect_over_cdp with the endpoint
    mock_chromium.connect_over_cdp.assert_called_once_with("ws://localhost:9222")

    # Result must contain session_id and status
    assert result["status"] == "ready"
    assert "session_id" in result
    assert result["session_id"].startswith("sess_")
    assert result["reused"] is False


@pytest.mark.asyncio
async def test_session_start_cdp_no_existing_pages(mock_playwright_module):
    """When CDP browser has no contexts/pages, create new context + page."""
    mock_pw, mock_chromium = mock_playwright_module

    mock_browser = MockBrowser()  # no contexts
    mock_chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    async_playwright_mock = AsyncMock()
    async_playwright_mock.start = AsyncMock(return_value=asyncio.coroutine(lambda: mock_pw)())

    with patch("mcp_tools.session.async_playwright", return_value=async_playwright_mock):
        from mcp_tools.session import session_start
        result = await session_start(
            email="cdp_nopage@test.com",
            cdp_endpoint="ws://localhost:9223",
        )

    # Must create a new context when none exist
    mock_browser.new_context.assert_called_once()


@pytest.mark.asyncio
async def test_session_start_existing_session_returns_same_id():
    """If email already has a session, return existing session_id (reused)."""
    from helpers.session_store import register_session, SessionData

    existing = SessionData(
        session_id="sess_existing",
        email="reuse@test.com",
        profile=None,
        context=MagicMock(),
        page=MagicMock(),
        cdp_endpoint=None,
        connect_method="launch",
    )
    register_session("sess_existing", existing)

    try:
        from mcp_tools.session import session_start
        result = await session_start(email="reuse@test.com")

        assert result["session_id"] == "sess_existing"
        assert result["reused"] is True
    finally:
        from helpers.session_store import unregister_session
        unregister_session("sess_existing")


@pytest.mark.asyncio
async def test_session_start_fresh_launch_no_args(mock_playwright_module):
    """With no cdp_endpoint and no profile, launch() + new_context() must be called."""
    mock_pw, mock_chromium = mock_playwright_module

    mock_browser_obj = MockBrowser()
    mock_chromium.launch = AsyncMock(return_value=mock_browser_obj)

    async_playwright_mock = AsyncMock()
    async_playwright_mock.start = AsyncMock(return_value=asyncio.coroutine(lambda: mock_pw)())

    with patch("mcp_tools.session.async_playwright", return_value=async_playwright_mock):
        from mcp_tools.session import session_start
        result = await session_start(email="fresh@test.com")

    mock_chromium.launch.assert_called_once()
    assert result["status"] == "ready"
```

**Step 2: Run tests to verify they fail**

```bash
cd /home/web-h-063/Documents/office-beacon-fe/qa-automation-mcp-server
python -m pytest tests/test_session_cdp_connection.py -v
```

Expected: All 4 FAIL. The first two will fail with `AttributeError: 'SessionData' object has no attribute 'cdp_endpoint'` (from Task 1 not being committed yet) or `NameError` for `connect_over_cdp` not being called. The existing tests for `reused` and `fresh_launch` will fail because `session_start` doesn't yet handle the `cdp_endpoint` parameter.

**Step 3: Modify `session_start` to handle the CDP path**

Edit `mcp_tools/session.py`. Replace the entire `session_start` function (lines 27-103) with:

```python
@tool
async def session_start(email: str, profile_name: str | None = None, cdp_endpoint: str | None = None) -> dict:
    """
    Start a new browser session for the given user.

    Supports three connection modes:
    - CDP: ``cdp_endpoint`` is provided → connects to an existing Chrome instance
      via Chrome DevTools Protocol. The browser is NOT closed on session_close.
    - Persistent profile: ``profile_name`` is provided → launches Chrome with
      a saved profile directory under ``profiles/``.
    - Fresh launch: neither parameter provided → launches a temporary incognito
      browser.

    If the user already has an active session (matched by email), returns the
    existing session without creating a new one.

    Args:
        email: The user's email address (unique per session).
        profile_name: Optional Chrome profile name (stored under profiles/).
        cdp_endpoint: Optional CDP WebSocket URL (e.g. ``ws://localhost:9222``).
            When provided, connects to an already-running Chrome instance
            opened with ``--remote-debugging-port``.

    Returns:
        dict with session_id, profile, status, cdp_endpoint, connect_method,
        and reused flag.
    """
    # Check if user already has an active session
    existing = get_session_by_email(email)
    if existing is not None:
        session_id = get_session_id_by_email(email)
        return {
            "session_id": session_id,
            "profile": existing.profile,
            "cdp_endpoint": existing.cdp_endpoint,
            "connect_method": existing.connect_method,
            "status": "ready",
            "reused": True,
        }

    # Generate new session ID
    session_id = f"sess_{uuid.uuid4().hex[:12]}"

    # --- Mode A: CDP Connection ---
    if cdp_endpoint is not None:
        p = await async_playwright().start()
        # connect_over_cdp returns a Browser object, not a Context
        browser = await p.chromium.connect_over_cdp(cdp_endpoint)

        # Find an existing context with pages, or create one
        context = None
        page = None
        for ctx in browser.contexts:
            if ctx.pages:
                context = ctx
                page = ctx.pages[0]
                break

        if context is None:
            # No existing context with pages → create new context + page
            context = await browser.new_context()
            page = await context.new_page()

        profile_path = None
        connect_method = "cdp"
        print(f"Connected to CDP endpoint: {cdp_endpoint}")

    # --- Mode B: Persistent Profile ---
    elif profile_name:
        profile_path = await _get_profiles_dir() / profile_name
        profile_path.mkdir(parents=True, exist_ok=True)
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

    # --- Mode C: Fresh Launch ---
    else:
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
    )

    # Register in store (handles both email and session_id indexes)
    register_session(session_id, session)

    return {
        "session_id": session_id,
        "profile": profile_name,
        "cdp_endpoint": cdp_endpoint,
        "connect_method": connect_method,
        "status": "ready",
        "reused": False,
    }
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_session_cdp_connection.py -v
```

Expected: 4 PASS.

**Step 5: Also run existing session tests to make sure nothing broke**

```bash
python -m pytest tests/ -v
```

Expected: All tests pass (Task 1 + Task 2 = 8 tests).

**Step 6: Commit**

```bash
git add tests/test_session_cdp_connection.py mcp_tools/session.py
git commit -m "feat: session_start supports CDP connection via cdp_endpoint parameter"
```

---

### Task 3: session_close — disconnect for CDP, close for launch/persistent

**Files:**
- Modify: `mcp_tools/session.py` (the `session_close` function)

**Interfaces:**
- Consumes: `SessionData.connect_method` (set by Task 2)
- Produces: `session_close` that correctly tears down the browser based on who owns it

**Step 1: Write the failing test**

```python
"""Tests for session_close CDP vs launch cleanup behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockPage:
    async def close(self):
        pass


class MockContext:
    def __init__(self):
        self.pages = [MockPage()]
        self._browser = MockBrowser()

    async def close(self):
        pass


class MockBrowser:
    def __init__(self):
        self._closed = False
        self._disconnected = False

    async def close(self):
        self._closed = True

    async def disconnect(self):
        self._disconnected = True


@pytest.mark.asyncio
async def test_session_close_cdp_disconnects_not_closes():
    """CDP sessions must disconnect Playwright, not close the browser."""
    from helpers.session_store import register_session, SessionData, unregister_session

    browser = MockBrowser()
    context = MagicMock()
    context.pages = [MockPage()]
    context.browser = browser
    context.close = AsyncMock()

    session = SessionData(
        session_id="sess_cdp_close",
        email="cdp_close@test.com",
        profile=None,
        context=context,
        page=MockPage(),
        cdp_endpoint="ws://localhost:9222",
        connect_method="cdp",
    )
    register_session("sess_cdp_close", session)

    try:
        from mcp_tools.session import session_close
        result = await session_close("sess_cdp_close")

        assert result["status"] == "closed"
        # disconnect must be called on the browser (we own the Playwright handle)
        assert browser._disconnected is True
        # close must NOT be called on the browser (user owns the Chrome instance)
        assert browser._closed is False
    finally:
        unregister_session("sess_cdp_close")


@pytest.mark.asyncio
async def test_session_close_launch_closes_browser():
    """Launch sessions must close the browser (we own it)."""
    from helpers.session_store import register_session, SessionData, unregister_session

    browser = MockBrowser()
    context = MagicMock()
    context.pages = [MockPage()]
    context.browser = browser
    context.close = AsyncMock()

    session = SessionData(
        session_id="sess_launch_close",
        email="launch_close@test.com",
        profile=None,
        context=context,
        page=MockPage(),
        cdp_endpoint=None,
        connect_method="launch",
    )
    register_session("sess_launch_close", session)

    try:
        from mcp_tools.session import session_close
        result = await session_close("sess_launch_close")

        assert result["status"] == "closed"
        # close must be called on the context (we created it)
        context.close.assert_called_once()
    finally:
        unregister_session("sess_launch_close")


@pytest.mark.asyncio
async def test_session_close_persistent_closes_browser():
    """Persistent profile sessions must close the browser (we own it)."""
    from helpers.session_store import register_session, SessionData, unregister_session

    browser = MockBrowser()
    context = MagicMock()
    context.pages = [MockPage()]
    context.browser = browser
    context.close = AsyncMock()

    session = SessionData(
        session_id="sess_persist_close",
        email="persist_close@test.com",
        profile="ekta10",
        context=context,
        page=MockPage(),
        cdp_endpoint=None,
        connect_method="persistent",
    )
    register_session("sess_persist_close", session)

    try:
        from mcp_tools.session import session_close
        result = await session_close("sess_persist_close")

        assert result["status"] == "closed"
        context.close.assert_called_once()
    finally:
        unregister_session("sess_persist_close")


@pytest.mark.asyncio
async def test_session_close_not_found():
    """Closing a non-existent session returns an error."""
    from mcp_tools.session import session_close
    result = await session_close("sess_nonexistent")
    assert result["status"] == "error"
    assert "not found" in result["message"]
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_session_cdp_close.py -v
```

Expected: FAIL — `session_close` doesn't check `connect_method` yet. It always calls `session.context.close()` regardless.

**Step 3: Modify `session_close` in `mcp_tools/session.py`**

Replace the existing `session_close` function (lines 106-137) with:

```python
@tool
async def session_close(session_id: str) -> dict:
    """
    Close a browser session and clean up all resources.

    For CDP sessions (``connect_method == "cdp"``), this disconnects the
    Playwright handle from the browser. The actual Chrome browser is NOT
    closed — it was opened externally by the user.

    For launch/persistent sessions, this closes the browser entirely since
    we own it.

    Args:
        session_id: The session ID to close.

    Returns:
        dict with status: "closed" or {"status": "error", "message": ...}.
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

    # Connection-specific cleanup
    if session.connect_method == "cdp":
        # Mode A: we connected to someone else's browser.
        # Disconnect Playwright. Do NOT close the actual Chrome browser.
        try:
            await session.context.browser.disconnect()
        except Exception:
            pass
    else:
        # Modes B/C: we launched the browser. Close it.
        try:
            await session.context.close()
        except Exception:
            pass
        # Also close the browser object if we have a reference to it
        try:
            await session.context.browser.close()
        except Exception:
            pass

    # Remove from store
    unregister_session(session_id)

    return {"status": "closed"}
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_session_cdp_close.py -v
```

Expected: 4 PASS.

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: All tests pass (12 total).

**Step 6: Commit**

```bash
git add tests/test_session_cdp_close.py mcp_tools/session.py
git commit -m "feat: session_close disconnects CDP sessions, closes launch/persistent sessions"
```

---

### Task 4: session_list — return CDP info

**Files:**
- Modify: `mcp_tools/session.py` (the `session_list` function)

**Interfaces:**
- Consumes: `SessionData.cdp_endpoint`, `SessionData.connect_method` (from Task 1)
- Produces: `session_list()` that returns CDP endpoint and connect method in each session entry

**Step 1: Write the failing test**

```python
"""Tests for session_list returning CDP information."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_session_list_includes_cdp_info():
    """session_list must include cdp_endpoint and connect_method per session."""
    from helpers.session_store import register_session, SessionData, unregister_session

    # Register two sessions: one CDP, one launch
    cdp_session = SessionData(
        session_id="sess_list_cdp",
        email="list_cdp@test.com",
        profile=None,
        context=MagicMock(),
        page=MagicMock(),
        cdp_endpoint="ws://localhost:9222",
        connect_method="cdp",
    )
    register_session("sess_list_cdp", cdp_session)

    launch_session = SessionData(
        session_id="sess_list_launch",
        email="list_launch@test.com",
        profile=None,
        context=MagicMock(),
        page=MagicMock(),
        cdp_endpoint=None,
        connect_method="launch",
    )
    register_session("sess_list_launch", launch_session)

    try:
        from mcp_tools.session import session_list
        result = await session_list()

        sessions = result["sessions"]
        assert len(sessions) == 2

        # Find the CDP session
        cdp_entry = next(s for s in sessions if s["session_id"] == "sess_list_cdp")
        assert cdp_entry["cdp_endpoint"] == "ws://localhost:9222"
        assert cdp_entry["connect_method"] == "cdp"

        # Find the launch session
        launch_entry = next(s for s in sessions if s["session_id"] == "sess_list_launch")
        assert launch_entry["cdp_endpoint"] is None
        assert launch_entry["connect_method"] == "launch"
    finally:
        from helpers.session_store import unregister_session
        unregister_session("sess_list_cdp")
        unregister_session("sess_list_launch")


@pytest.mark.asyncio
async def test_session_list_empty():
    """With no sessions, session_list returns empty list."""
    from helpers.session_store import unregister_session

    # Clean slate
    for sid in list(_all_session_ids()):
        unregister_session(sid)

    from mcp_tools.session import session_list
    result = await session_list()
    assert result["sessions"] == []


def _all_session_ids():
    """Helper to get all registered session IDs for cleanup."""
    from helpers.session_store import _session_by_id
    return list(_session_by_id.keys())
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_session_list.py -v
```

Expected: FAIL — `session_list` doesn't include `cdp_endpoint` or `connect_method` in the output.

**Step 3: Modify `session_list` in `mcp_tools/session.py`**

Replace the existing `session_list` function (lines 140-156) with:

```python
@tool
async def session_list() -> dict:
    """
    List all active sessions (admin view).

    Returns:
        dict with list of sessions. Each session includes:
        session_id, email, profile, started_at, cdp_endpoint, connect_method.
        The cdp_endpoint field allows other agents to discover and connect
        to the same browser instance.
    """
    sessions = []
    for session in list_all_sessions():
        sessions.append({
            "session_id": session.session_id,
            "email": session.email,
            "profile": session.profile,
            "started_at": session.started_at.isoformat(),
            "cdp_endpoint": session.cdp_endpoint,
            "connect_method": session.connect_method,
        })
    return {"sessions": sessions}
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_session_list.py -v
```

Expected: 2 PASS.

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: 14 PASS.

**Step 6: Commit**

```bash
git add tests/test_session_list.py mcp_tools/session.py
git commit -m "feat: session_list returns cdp_endpoint and connect_method for inter-agent discovery"
```

---

### Task 5: start_recording — cdp_endpoint passthrough

**Files:**
- Modify: `mcp_tools/recording.py` (the `start_recording` function)

**Interfaces:**
- Consumes: `session_start` from `mcp_tools.session` (with `cdp_endpoint` parameter)
- Produces: `start_recording(session_id, name, cdp_endpoint=None)` that auto-creates a session via CDP if needed

**Step 1: Write the failing test**

```python
"""Tests for start_recording CDP passthrough."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockPage:
    async def title(self):
        return "Test"


class MockContext:
    def __init__(self):
        self.pages = [MockPage()]
        self.browser = MagicMock()


@pytest.mark.asyncio
async def test_start_recording_with_existing_session():
    """If session exists, cdp_endpoint is ignored and recording starts normally."""
    from helpers.session_store import register_session, SessionData, unregister_session

    session = SessionData(
        session_id="sess_rec_existing",
        email="rec_existing@test.com",
        profile=None,
        context=MockContext(),
        page=MockPage(),
        cdp_endpoint=None,
        connect_method="launch",
    )
    register_session("sess_rec_existing", session)

    try:
        from mcp_tools.recording import start_recording
        result = await start_recording(
            session_id="sess_rec_existing",
            name="test-automation",
            cdp_endpoint="ws://localhost:9222",  # should be ignored
        )
        assert result["status"] == "started"
        assert result["name"] == "test-automation"
    finally:
        from helpers.session_store import unregister_session
        unregister_session("sess_rec_existing")


@pytest.mark.asyncio
async def test_start_recording_no_session_no_cdp_returns_error():
    """Without session_id and without cdp_endpoint, returns error."""
    from mcp_tools.recording import start_recording
    result = await start_recording(
        session_id="sess_does_not_exist",
        name="test-automation",
    )
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_start_recording_no_session_with_cdp_creates_session():
    """Without session_id but with cdp_endpoint, creates session via CDP then starts recording."""
    mock_browser = MagicMock()
    mock_browser.contexts = [MagicMock(pages=[MockPage()])]

    mock_chromium = MagicMock()
    mock_chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    async_playwright_mock = AsyncMock()
    async_playwright_mock.start = AsyncMock(return_value=asyncio.coroutine(lambda: mock_pw)())

    with patch("mcp_tools.session.async_playwright", return_value=async_playwright_mock):
        with patch("mcp_tools.recording.session_start") as mock_session_start:
            mock_session_start.return_value = {
                "session_id": "sess_auto_created",
                "profile": None,
                "cdp_endpoint": "ws://localhost:9222",
                "connect_method": "cdp",
                "status": "ready",
                "reused": False,
            }

            from mcp_tools.recording import start_recording
            result = await start_recording(
                session_id="sess_nonexistent",
                name="test-auto-session",
                cdp_endpoint="ws://localhost:9222",
            )

            # session_start must have been called to create the session
            mock_session_start.assert_called_once()
            call_kwargs = mock_session_start.call_args
            assert call_kwargs.kwargs.get("email") is not None  # auto-generated email
            assert call_kwargs.kwargs.get("cdp_endpoint") == "ws://localhost:9222"

            assert result["status"] == "started"
            assert result["name"] == "test-auto-session"
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_recording_cdp.py -v
```

Expected: FAIL — `start_recording` doesn't accept `cdp_endpoint` parameter.

**Step 3: Modify `start_recording` in `mcp_tools/recording.py`**

Replace the existing `start_recording` function (lines 107-136) with:

```python
@tool
async def start_recording(
    session_id: str,
    name: str,
    cdp_endpoint: str | None = None,
) -> dict:
    """Start a new recording session.

    Only one recording can be active per session. Subsequent calls to
    ``start_recording`` without first stopping the current recording will
    return an error.

    If the given ``session_id`` does not exist and ``cdp_endpoint`` is provided,
    a new session is created by connecting to the specified CDP endpoint.
    This lets the agent start recording without calling ``session_start`` first.

    Args:
        session_id: The session to record in.
        name: Name for the automation (e.g. ``"login-flow"``). Used as the
            output filename.
        cdp_endpoint: Optional CDP WebSocket URL. If ``session_id`` doesn't
            exist, this is used to connect to an existing browser via
            ``session_start`` before starting recording. If ``session_id``
            already exists, this parameter is ignored.

    Returns:
        ``{"status": "started", "name": str, "recorded": 0}`` on success.
        ``{"status": "error", "error": "already_recording"}`` if a recording is
        already active in this session.
        ``{"status": "error", "message": "..."}`` if session not found and no
        cdp_endpoint to connect with.
    """
    err, session = await _resolve_session(session_id)
    if err:
        # Session doesn't exist — try to create it via CDP if endpoint provided
        if cdp_endpoint is not None:
            from mcp_tools.session import session_start as _session_start
            auto_result = await _session_start(
                email=f"auto_{uuid.uuid4().hex[:8]}",
                cdp_endpoint=cdp_endpoint,
            )
            if auto_result.get("status") != "ready":
                return {
                    "status": "error",
                    "error": "cdp_connection_failed",
                    "details": auto_result,
                }
            session_id = auto_result["session_id"]
            session = get_session_by_id(session_id)
            if session is None:
                return {
                    "status": "error",
                    "message": f"Failed to create session via CDP endpoint {cdp_endpoint}",
                }
        else:
            return err

    if session.is_recording:
        return {"status": "error", "error": "already_recording"}

    session.is_recording = True
    session.recording_name = name
    session.recording_tools = []

    return {"status": "started", "name": name, "recorded": 0}
```

Note the import of `uuid` at the top of the file is already present (it's used elsewhere). Also note we import `session_start` locally inside the function to avoid circular imports (both `session.py` and `recording.py` are loaded by the FileSystemProvider).

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_recording_cdp.py -v
```

Expected: 3 PASS.

**Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: 17 PASS.

**Step 6: Commit**

```bash
git add tests/test_recording_cdp.py mcp_tools/recording.py
git commit -m "feat: start_recording accepts cdp_endpoint to auto-create CDP session"
```

---

### Task 6: Integration smoke test — end-to-end CDP flow with mocks

**Files:**
- Create: `tests/test_cdp_integration.py`

**Interfaces:**
- Consumes: All changes from Tasks 1-5
- Produces: One integration test that exercises the full flow: CDP connect → recording → list → close

**Step 1: Write the integration test**

```python
"""End-to-end integration test for the CDP flow.

Exercises: session_start(CDP) → list (verifies CDP info) → recording → close (verifies disconnect).
All browser interactions are mocked.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class MockPage:
    def __init__(self, url="about:blank"):
        self.url = url
        self._closed = False

    async def title(self):
        return "Test"

    async def close(self):
        self._closed = True


class MockContext:
    def __init__(self, page):
        self.pages = [page]
        self._browser = MockBrowser()

    async def new_page(self):
        new = MockPage("https://new.tab")
        self.pages.append(new)
        return new

    async def close(self):
        pass


class MockBrowser:
    def __init__(self):
        self.contexts = []
        self._closed = False
        self._disconnected = False

    async def new_context(self):
        page = MockPage()
        ctx = MockContext(page)
        self.contexts.append(ctx)
        return ctx

    async def close(self):
        self._closed = True

    async def disconnect(self):
        self._disconnected = True


@pytest.fixture
def mock_cdp_browser():
    """A mock browser that simulates an externally-opened Chrome with one page."""
    page = MockPage("http://localhost:3000")
    ctx = MockContext(page)
    browser = MockBrowser()
    browser.contexts = [ctx]
    return browser


@pytest.mark.asyncio
async def test_full_cdp_flow(mock_cdp_browser):
    """Full flow: connect CDP → list sessions (see CDP info) → start recording → close → verify disconnect."""
    mock_chromium = MagicMock()
    mock_chromium.connect_over_cdp = AsyncMock(return_value=mock_cdp_browser)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium

    async_playwright_mock = AsyncMock()
    async_playwright_mock.start = AsyncMock(return_value=asyncio.coroutine(lambda: mock_pw)())

    with patch("mcp_tools.session.async_playwright", return_value=async_playwright_mock):
        # 1. Connect via CDP
        from mcp_tools.session import session_start
        result = await session_start(email="integration@test.com", cdp_endpoint="ws://localhost:9222")
        assert result["status"] == "ready"
        assert result["connect_method"] == "cdp"
        assert result["cdp_endpoint"] == "ws://localhost:9222"
        session_id = result["session_id"]

        # 2. List sessions — must show CDP info
        from mcp_tools.session import session_list
        list_result = await session_list()
        cdp_entries = [s for s in list_result["sessions"] if s["session_id"] == session_id]
        assert len(cdp_entries) == 1
        assert cdp_entries[0]["cdp_endpoint"] == "ws://localhost:9222"
        assert cdp_entries[0]["connect_method"] == "cdp"

        # 3. Start recording
        from mcp_tools.recording import start_recording, record_step, stop_recording
        rec_result = await start_recording(session_id=session_id, name="integration-test")
        assert rec_result["status"] == "started"

        # Record a step
        step_result = await record_step(session_id=session_id, tool_name="navigate", args={"url": "http://localhost:3000"})
        assert step_result["status"] == "recorded"
        assert step_result["total_recorded"] == 1

        # 4. Close session — must disconnect, not close browser
        from mcp_tools.session import session_close
        close_result = await session_close(session_id)
        assert close_result["status"] == "closed"
        assert mock_cdp_browser._disconnected is True
        assert mock_cdp_browser._closed is False


@pytest.mark.asyncio
async def test_second_agent_sees_first_agent_cdp_session():
    """Agent B calling session_start with same email should get Agent A's session back (reused)."""
    from helpers.session_store import register_session, SessionData, unregister_session

    session = SessionData(
        session_id="sess_agent_a",
        email="shared@test.com",
        profile=None,
        context=MagicMock(),
        page=MockPage(),
        cdp_endpoint="ws://localhost:9222",
        connect_method="cdp",
    )
    register_session("sess_agent_a", session)

    try:
        from mcp_tools.session import session_start
        # Agent B tries to start a session with the same email
        result = await session_start(email="shared@test.com")
        assert result["session_id"] == "sess_agent_a"
        assert result["reused"] is True
        assert result["cdp_endpoint"] == "ws://localhost:9222"
    finally:
        unregister_session("sess_agent_a")
```

**Step 2: Run tests to verify they pass**

```bash
python -m pytest tests/test_cdp_integration.py -v
```

Expected: 2 PASS.

**Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: 19 PASS (all tasks combined).

**Step 4: Commit**

```bash
git add tests/test_cdp_integration.py
git commit -m "test: add end-to-end CDP flow integration test"
```

---

### Task 7: Manual verification — run with real Chrome

**Files:**
- None (verification only)

**Interfaces:**
- Consumes: All changes from Tasks 1-6
- Produces: Confirmed working CDP flow against a real Chrome instance

**Step 1: Start Chrome with CDP**

```bash
/opt/google/chrome/chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp-test &
sleep 2
# Verify CDP is responding
curl -s http://localhost:9222/json/version | python3 -m json.tool
```

Expected output includes `"webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/..."`.

**Step 2: Start the MCP server**

```bash
cd /home/web-h-063/Documents/office-beacon-fe/qa-automation-mcp-server
python -m main.py
```

**Step 3: Connect via CDP from the MCP**

In a separate terminal, use the MCP client to call:

```
session_start(email="manual@test.com", cdp_endpoint="ws://localhost:9222")
```

Expected: `{"session_id": "sess_...", "cdp_endpoint": "ws://localhost:9222", "connect_method": "cdp", "status": "ready", "reused": false}`

**Step 4: List sessions to verify CDP info is returned**

```
session_list
```

Expected: The session appears with `cdp_endpoint` and `connect_method: "cdp"`.

**Step 5: Record and close**

```
start_recording(session_id="<id>", name="manual-cdp-test")
navigate(session_id="<id>", url="http://localhost:3000")
stop_recording(session_id="<id>")
session_close(session_id="<id>")
```

Expected: Recording saved, session closed, Chrome still running (not killed by the MCP).

**Step 6: Verify Chrome is still running**

```bash
pgrep -f "chrome.*remote-debugging-port=9222" && echo "Chrome still running" || echo "Chrome was killed (BUG)"
```

Expected: Chrome still running. This confirms `session_close` correctly `disconnect()`ed instead of `close()`ing.

**Step 7: Clean up**

```bash
pkill -f "chrome.*remote-debugging-port=9222"
```

---

## Self-Review

**1. Spec coverage:**
- `cdp_endpoint` parameter in `session_start` → Task 2 ✓
- `cdp_endpoint` parameter in `start_recording` → Task 5 ✓
- `SessionData` stores CDP endpoint → Task 1 ✓
- `session_close` disconnects for CDP → Task 3 ✓
- `session_list` returns CDP endpoint → Task 4 ✓
- Multi-agent coordination via `session_list` discovery → Task 6 (integration test) ✓
- Recording works with CDP sessions → Task 5 (passthrough) ✓
- Manual verification with real Chrome → Task 7 ✓

**2. Placeholder scan:**
- No "TBD", "TODO", "implement later" found.
- No "similar to Task N" references — all code is self-contained.
- All steps include actual code blocks or commands.

**3. Type consistency:**
- `cdp_endpoint: str | None` defined in Task 1, used identically in Tasks 2-6.
- `connect_method: str` defined in Task 1 with values `"cdp" | "persistent" | "launch"`, used identically in Task 3.
- `session_start` signature: `(email, profile_name=None, cdp_endpoint=None)` — consistent across all references.
- `session_close` signature unchanged: `(session_id)` — no breaking change.
- `session_list` return value: adds `cdp_endpoint` and `connect_method` keys — all callers (including the new integration test) expect these keys.
- `start_recording` signature: `(session_id, name, cdp_endpoint=None)` — consistent.

**Gap check:** The spec mentions `tabs.py` doesn't need changes. Verified — no tabs.py tests added (correct). The spec mentions `replay.py` is a future concern. Correctly excluded from this plan.

---

## Execution

Plan complete and saved to `docs/superpowers/plans/2026-07-10-cdp-browser-sharing.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**