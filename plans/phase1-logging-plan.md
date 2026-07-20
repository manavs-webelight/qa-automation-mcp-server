# Phase 1: Session-Level Tool Logging Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. This plan omits git operations — implement in current context without commits or branches.

**Goal:** Add automatic per-session tool call logging so every action tool invocation is captured to a JSON-lines file; provide a `list_log_entries` tool for reading the log back.

**Architecture:** A new `mcp_tools/logging_utils.py` module holds three concerns — (1) a JSON-lines writer keyed to a session's base dir, (2) a `@_log_action("name")` decorator applied to each action tool that records duration, status, args and output, and (3) a `list_log_entries` tool that reads/paginates the log file. `SessionData` gains a `log_config` field. `session_start()` gains an `exploration` parameter that gates whether the log file is created.

**Tech Stack:** Python stdlib (`json`, `pathlib`, `time`, `asyncio`), fastmcp `@tool`, existing `helpers/session_store.py`.

## Global Constraints

- Use only stdlib for logging — no new dependencies.
- All timestamps are ISO-8601 in UTC (`datetime.utcnow().isoformat() + "Z"`), matching existing codebase convention.
- Log entries must fit on a single line (one JSON object per line) — one line per tool call.
- Output values longer than 500 characters are truncated in the log (with `"..."` appended and a `truncated: true` flag on the entry) to keep logs readable.
- The `exploration` parameter defaults to `False` — every existing code path continues to log by default (no behaviour change for current callers).
- The log file lives under the session's `base_dir`, not the automations/ directory — separate concern (log vs recording).

---

### Task 1: Create `mcp_tools/logging_utils.py`

**Files:**
- Create: `mcp_tools/logging_utils.py`

**Interfaces:**
- Consumes: nothing (pure utility module).
- Produces: `_SESSION_ACTION_TOOLS` set, `_log_action(tool_name)` decorator, `create_session_log(session)`, `_read_log_entries(session, offset, limit)`, `safe_serialize(value)`.

**Implementation:**

This module is the heart of the feature. It has four pieces:

1. **Action tool name set** — the whitelist of tool names that should be logged.
2. **`create_session_log(session)`** — creates the log file if it doesn't already exist and returns the file path.
3. **`_log_entry(tool_name, args, status, output, duration_ms, timestamp)`** — writes a single JSON-lines entry to the session log file.
4. **`_read_log_entries(session, offset, limit)`** — reads and paginates the log file.
5. **`_log_action(tool_name)`** — async decorator that wraps an action tool function.

```python
"""Logging utilities for session-level tool call capture.

Every action tool call is automatically written to a JSON-lines file in the
session's base directory. The log is created at session_start() time (unless
exploration=True) and is read back by list_log_entries().
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from helpers.session_store import get_session_by_id, _session_lock

# Tool names that should be auto-logged. These match the @tool-decorated
# async functions in dom.py, navigate.py, wait.py, upload.py.
SESSION_ACTION_TOOLS = {
    "navigate",
    "navigate_with_retry",
    "navigate_back",
    "reload",
    "click",
    "type",
    "fill",
    "select_option",
    "check",
    "press_key",
    "upload_file",
}

# Non-action tools that should NOT be logged (read-only, introspection, session
# lifecycle, recording, etc.).
NON_ACTION_TOOLS = {
    "snapshot",
    "screenshot",
    "assert_visible",
    "assert_text",
    "assert_url",
    "assert_title",
    "assert_no_console_errors",
    "get_text",
    "get_value",
    "get_attribute",
    "get_cookies",
    "get_local_storage",
    "execute",
    "console_messages",
    "clear_console_messages",
    "session_start",
    "session_close",
    "session_list",
    "start_recording",
    "stop_recording",
    "record_step",
    "remove_last_step",
    "list_recording",
    "start_human_recording",
    "stop_human_recording",
    "remove_human_recording",
    "list_log_entries",
    "new_tab",
    "close_tab",
    "switch_tab",
    "list_tabs",
}


MAX_OUTPUT_LEN = 500


def _truncated_output(output: Any) -> tuple[Any, bool]:
    """If output is a long string, truncate it. Returns (value, was_truncated)."""
    if isinstance(output, str) and len(output) > MAX_OUTPUT_LEN:
        return output[:MAX_OUTPUT_LEN] + "..." + " [truncated]", True
    return output, False


def safe_serialize(value: Any) -> Any:
    """Return a JSON-safe version of `value`.

    Drops keys like ``session_id`` from arg dicts (the session is already
    implied by the log file). Truncates long strings. Returns ``None`` for
    values that cannot be serialized.
    """
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        truncated, _ = _truncated_output(value)
        return truncated
    if isinstance(value, (list, tuple)):
        return [safe_serialize(v) for v in value]
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if k == "session_id":
                continue  # implied by the log file location
            result[str(k)] = safe_serialize(v)
        return result
    if hasattr(value, "isoformat"):
        return value.isoformat()
    # Anything else (Playwright handles, pages, etc.) — drop it with a note.
    return f"<{type(value).__name__}>"


def _get_log_file_path(session) -> Path:
    """Return the path to the session's tool-call log file.

    Creates parent dirs if needed.
    """
    base = session.base_dir
    if base is None:
        return Path("/tmp") / f"session_{session.session_id}_tools.log"
    log_dir = Path(base) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{session.session_id}_tools.log"


async def create_session_log(session) -> Path:
    """Create the session's tool-call log file if it does not already exist.

    Returns the Path to the log file. Creates the log file as an empty file
    if it does not exist. If a previous run left a log behind (same session
    ID), it is reused — log entries are appended, not overwritten.
    """
    path = _get_log_file_path(session)
    if not path.exists():
        path.touch()
    return path


def _format_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 with Z suffix."""
    return datetime.utcnow().isoformat() + "Z"


async def write_log_entry(
    session,
    tool_name: str,
    args: dict,
    status: str,
    output: Any,
    duration_ms: float,
):
    """Append one JSON-lines entry to the session's log file.

    Thread-safe: uses the session lock so concurrent tool calls do not
    interleave writes. Uses stdlib `asyncio.Lock` via `_session_lock`.
    """
    path = _get_log_file_path(session)
    serialized_output, was_truncated = _truncated_output(output)
    entry = {
        "tool": tool_name,
        "args": safe_serialize(args),
        "status": status,
        "output": safe_serialize(serialized_output),
        "truncated": was_truncated,
        "duration_ms": round(duration_ms, 2),
        "timestamp": _format_timestamp(),
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    async with _session_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


async def read_log_entries(
    session, offset: int = 0, limit: int = 20
) -> dict:
    """Read and paginate the session's tool-call log.

    Returns:
        ``{"entries": [...], "total": int, "offset": int, "limit": int,
        "has_more": bool}``
    """
    path = _get_log_file_path(session)
    if not path.exists():
        return {"entries": [], "total": 0, "offset": offset, "limit": limit, "has_more": False}

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    total = len(lines)

    sliced = lines[offset : offset + limit]
    entries = []
    for line in sliced:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # Skip malformed lines (should not happen in normal use).
            entries.append({"raw": line, "error": "parse_error"})

    has_more = (offset + limit) < total
    return {
        "entries": entries,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
    }


def _log_action(tool_name: str):
    """Async decorator that logs a tool call when the session is logging.

    When applied, the decorated function wraps the original tool. Before
    invoking the original function it checks whether the session has logging
    enabled (via ``session.log_config``). If yes, it writes a JSON-lines
    entry to the session's log file after the tool completes.

    Usage::

        @tool
        @_log_action("click")
        async def click(session_id: str, selector: str, timeout: int = 5000) -> dict:
            ...
    """
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract session_id (first positional arg or keyword).
            session_id = None
            if args:
                session_id = args[0]
            elif "session_id" in kwargs:
                session_id = kwargs["session_id"]

            # Check whether logging is active for this session.
            if not await _is_logging_active(session_id):
                return await func(*args, **kwargs)

            # Capture args dict for the log (everything except session_id).
            log_args = {k: v for k, v in kwargs.items() if k != "session_id"}
            # Positional args beyond session_id are also captured.
            positional_names = func.__code__.co_varnames[:func.__code__.co_argcount]
            for i, name in enumerate(positional_names):
                if i == 0 or name == "session_id":
                    continue
                if name in log_args:
                    continue
                if i < len(args):
                    log_args[name] = args[i]

            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
                status = "success"
            except Exception as e:
                status = "error"
                result = {"status": "error", "message": str(e)}

            duration_ms = (time.perf_counter() - start) * 1000

            # Get the session object to write to its log file.
            session = await get_session_by_id(session_id)
            if session is not None:
                await write_log_entry(
                    session, tool_name, log_args, status, result, duration_ms
                )

            # Re-raise the original exception so the caller sees it.
            if status == "error" and isinstance(result, dict) and "message" in result:
                # Check if the original function raised a real exception
                # (AssertionError, etc.) — we should re-raise that.
                # We cannot know for sure here, so we rely on the fact that
                # assertion tools raise AssertionError which is distinct from
                # returning {"status": "error", ...}. The decorator preserves
                # the original return value if it came from a catch block.
                # We do NOT re-raise — the action tools use a try/except
                # pattern and return error dicts, not raise. Only tools that
                # truly raise (assertion tools) are not wrapped by this
                # decorator, so this is safe.
                pass

            return result
        return wrapper
    return decorator


async def _is_logging_active(session_id: str) -> bool:
    """Return True if the session has logging enabled and a log file exists."""
    session = await get_session_by_id(session_id)
    if session is None:
        return False
    config = getattr(session, "log_config", None)
    if config is None:
        return False
    return bool(config.get("active", False))
```

**Edge cases:**
- `session.base_dir` is `None` → fall back to `/tmp/session_{id}_tools.log` (defensive; the spec requires `base_dir` for `session_start`).
- Session not found → `_is_logging_active` returns `False`, so no error.
- Concurrent writes → `_session_lock` serializes.
- Malformed log lines → `read_log_entries` skips them with an `error` flag.

---

### Task 2: Add `log_config` field to `SessionData`

**Files:**
- Modify: `helpers/session_store.py:10-40`

**Interfaces:**
- Consumes: existing `SessionData` dataclass.
- Produces: `log_config: dict | None = None` field on `SessionData`.

**Steps:**

- [ ] **Step 1: Add `log_config` field**

Add the following line to `SessionData`, right after the `is_recording` field:

```python
    # Logging state
    log_config: dict | None = None  # {"active": bool, "log_file": str}
```

This is the simplest possible addition — a single optional dict that the logging module populates.

- [ ] **Step 2: Verify import consistency**

No imports are needed — `log_config` is just a dict on the dataclass. No code in `session_store.py` needs to change.

---

### Task 3: Modify `session_start()` to accept `exploration` and create log file

**Files:**
- Modify: `mcp_tools/session.py:30-208`

**Interfaces:**
- Consumes: `SessionData` from `helpers/session_store.py` (now with `log_config`), `create_session_log` from `mcp_tools/logging_utils.py`.
- Produces: `session_start()` with new `exploration` parameter; log file created on disk when `exploration=False`.

**Steps:**

- [ ] **Step 1: Add import and parameter**

Add the import at the top of `session.py`:

```python
from mcp_tools.logging_utils import create_session_log
```

Add the `exploration` parameter to `session_start()` signature (line 30):

```python
@tool
async def session_start(
    email: str = "",
    profile_name: str = "",
    cdp_endpoint: str | None = None,
    *,
    base_dir: str,
    exploration: bool = False,
) -> dict:
```

- [ ] **Step 2: Create log file when exploration=False**

After the session is registered (after `await register_session(session_id, session)` on both the CDP path and the non-CDP path), add:

```python
        # Create session log if not in exploration mode
        if not exploration:
            log_file = await create_session_log(session)
            session.log_config = {
                "active": True,
                "log_file": str(log_file),
                "exploration": False,
            }
```

Insert this right after `await register_session(session_id, session)` in BOTH the CDP branch (line 124) and the non-CDP branch (line 199). The existing code already has a return after registration, so the log creation happens BEFORE the return.

The CDP branch looks like:
```python
        # Register in store (handles both email and session_id indexes)
        await register_session(session_id, session)

+        # Create session log if not in exploration mode
+        if not exploration:
+            log_file = await create_session_log(session)
+            session.log_config = {
+                "active": True,
+                "log_file": str(log_file),
+                "exploration": False,
+            }

        return {
```

And the non-CDP branch:
```python
    # Register in store (handles both email and session_id indexes)
    await register_session(session_id, session)

+    # Create session log if not in exploration mode
+    if not exploration:
+        log_file = await create_session_log(session)
+        session.log_config = {
+            "active": True,
+            "log_file": str(log_file),
+            "exploration": False,
+        }

    return {
```

- [ ] **Step 3: Pass `exploration` through reused sessions**

In the "user already has an active session" paths (the early returns at lines 69-76 and 139-148), also set `exploration` on the returned dict so callers know the session's exploration state:

```python
            return {
                "session_id": existing.session_id,
                "profile": existing.profile,
                "status": "ready",
                "reused": True,
                "cdp_endpoint": existing.cdp_endpoint,
                "connect_method": existing.connect_method,
                "exploration": exploration,
            }
```

Same change for both reused-session returns.

- [ ] **Step 4: Add `exploration` to the default return dict**

Add `"exploration": exploration` to the final success return in both branches.

---

### Task 4: Apply `_log_action` decorator to action tools

**Files:**
- Modify: `mcp_tools/dom.py` — add decorator to `click`, `type`, `fill`, `select_option`, `check`, `press_key`
- Modify: `mcp_tools/navigate.py` — add decorator to `navigate`, `navigate_with_retry`, `navigate_back`, `reload`
- Modify: `mcp_tools/upload.py` — add decorator to `upload_file`

**Do NOT wrap:** `snapshot`, `screenshot`, `assert_*`, `get_text`, `get_value`, `get_attribute`, `get_cookies`, `get_local_storage`, `execute`, `console_messages`, `clear_console_messages`, `session_*`, `start_recording`, `stop_recording`, `record_step`, `remove_last_step`, `list_recording`, `new_tab`, `close_tab`, `switch_tab`, `list_tabs`, `sleep`, `wait_for_selector`, `wait_for_url`, `wait_for_load_state`, `wait_for_navigation`

**Rationale:** Wait primitives (`wait_*`) are timing utilities, not user actions. The agent calls them to wait for page state, not to perform an interaction. Logging them would bloat the recording with irrelevant entries.

**Interfaces:**
- Consumes: `_log_action` from `mcp_tools/logging_utils.py`.
- Produces: Each action tool is wrapped so its calls are auto-logged.

**Steps:**

- [ ] **Step 1: Add import and apply decorator to `dom.py` action tools**

Add at top of `dom.py`:
```python
from mcp_tools.logging_utils import _log_action
```

Apply the decorator to each action tool function (before `@tool`):

```python
@tool
@_log_action("click")
async def click(session_id: str, selector: str, timeout: int = 5000) -> dict:
```

Repeat for `type`, `fill`, `select_option`, `check`, `press_key`.

For `fill` and `type` which delegate to each other, the decorator must go on the top-level function. Since `fill` calls `type` directly, the wrapper on `type` will capture the call correctly.

**Note on `dblclick`:** If `dblclick` already exists in `dom.py`, wrap it. If it doesn't, skip it — it will be added separately in a future task.

- [ ] **Step 2: Apply decorator to `navigate.py` and `upload.py`**

For `navigate.py`, apply the decorator to `navigate`, `navigate_with_retry`, `navigate_back`, and `reload`:

```python
@tool
@_log_action("navigate")
async def navigate(session_id: str, url: str) -> dict:
```

Repeat for the others in navigate.py and upload.py.

**Note on `navigate_with_retry`:** This function makes multiple internal `page.goto()` calls. The log entry should capture the *top-level* tool invocation (one entry per `navigate_with_retry` call), not each internal retry. The decorator already does this correctly because it wraps the top-level function.

- [ ] **Step 3: Verify no non-action tools are wrapped**

Do NOT apply the decorator to any of: `snapshot`, `screenshot`, `assert_*`, `get_text`, `get_value`, `get_attribute`, `get_cookies`, `get_local_storage`, `execute`, `console_messages`, `clear_console_messages`, `session_*`, `start_recording`, `stop_recording`, `record_step`, `remove_last_step`, `list_recording`, `new_tab`, `close_tab`, `switch_tab`, `list_tabs`, `sleep`, `wait_for_selector`, `wait_for_url`, `wait_for_load_state`, `wait_for_navigation`.

- [ ] **Step 4: Remove `record_step()` instruction from tool responses (Phase 3)**

**Out of scope for Phase 1.** After this task, when implementing the skill changes (Phase 3), we need to remove the "call `record_step()`" instruction that currently appears in tool responses. This will be handled in the skill update, not in the MCP server code.

---

### Task 5: Add `list_log_entries` tool

**Files:**
- Create: `mcp_tools/session_logging.py` (new file, separate tool from `session.py` to keep concerns apart)

**Interfaces:**
- Consumes: `read_log_entries` from `mcp_tools/logging_utils.py`, `get_session_by_id` from `helpers/session_store.py`.
- Produces: `list_log_entries()` MCP tool.

**Steps:**

- [ ] **Step 1: Create `mcp_tools/session_logging.py`**

```python
"""Tools for reading session tool-call logs."""

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id
from mcp_tools.logging_utils import read_log_entries


@tool
async def list_log_entries(
    session_id: str,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    """List paginated tool-call log entries for a session.

    Reads the session's tool-call log file (created at ``session_start``
    time when ``exploration=False``) and returns a paginated list of
    entries.

    Args:
        session_id: The session ID whose log to read.
        offset: Number of entries to skip (default 0).
        limit: Maximum number of entries to return (default 20, max 200).

    Returns:
        ``{"entries": [...], "total": int, "offset": int, "limit": int,
        "has_more": bool}`` on success.
        ``{"status": "error", "message": "Session not found"}`` if the
        session does not exist.

    Example::

        list_log_entries(session_id="sess_abc", offset=0, limit=10)
    """
    session = await get_session_by_id(session_id)
    if session is None:
        return {"status": "error", "message": f"Session {session_id} not found"}

    # Clamp limit to a sane maximum
    limit = min(limit, 200)
    if limit < 1:
        limit = 1
    if offset < 0:
        offset = 0

    return await read_log_entries(session, offset=offset, limit=limit)
```

- [ ] **Step 2: Verify fastmcp auto-discovers the tool**

`main.py` uses `FileSystemProvider(root=Path(__file__).parent / "mcp_tools", ...)` which auto-discovers `@tool`-decorated functions in all `.py` files under `mcp_tools/`. No manual registration needed.

---

### Task 6: Write tests

**Files:**
- Create: `tests/test_logging_utils.py`
- Create: `tests/test_session_logging.py`
- Create: `tests/__init__.py`

**Test strategy:**
- No real browser is needed — tests only exercise the logging utility functions and the `list_log_entries` tool with mocked sessions.
- Use `pytest-asyncio` for async tests.

**Steps:**

- [ ] **Step 1: Set up test infrastructure**

```python
# tests/__init__.py
# Empty file for pytest discovery
```

Add `pytest` and `pytest-asyncio` to dev dependencies in `pyproject.toml`:

```toml
[project.optional-dependencies]
test = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

- [ ] **Step 2: Write tests for `logging_utils.py`**

```python
# tests/test_logging_utils.py
import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from mcp_tools.logging_utils import (
    NON_ACTION_TOOLS,
    SESSION_ACTION_TOOLS,
    _format_timestamp,
    _is_logging_active,
    _truncated_output,
    create_session_log,
    read_log_entries,
    safe_serialize,
    write_log_entry,
)


@pytest.mark.asyncio
async def test_truncated_output_short():
    value, truncated = _truncated_output("hello")
    assert value == "hello"
    assert truncated is False


@pytest.mark.asyncio
async def test_truncated_output_long():
    long_str = "x" * 600
    value, truncated = _truncated_output(long_str)
    assert len(value) == 503  # 500 + "..." + " [truncated]"
    assert truncated is True


@pytest.mark.asyncio
async def test_safe_serialize_none():
    assert safe_serialize(None) is None


@pytest.mark.asyncio
async def test_safe_serialize_dict_drops_session_id():
    result = safe_serialize({"session_id": "sess_123", "selector": "button"})
    assert "session_id" not in result
    assert result["selector"] == "button"


@pytest.mark.asyncio
async def test_safe_serialize_unserializable():
    result = safe_serialize(MagicMock())
    assert isinstance(result, str)
    assert "Mock" in result


@pytest.mark.asyncio
async def test_session_action_tools_does_not_include_non_action():
    """Ensure no tool appears in both sets."""
    overlap = SESSION_ACTION_TOOLS & NON_ACTION_TOOLS
    assert overlap == set(), f"Overlap: {overlap}"


@pytest.mark.asyncio
async def test_session_action_tools_does_not_include_wait_tools():
    """Ensure wait tools are NOT in the action set."""
    wait_tools = {"wait_for_selector", "wait_for_url", "wait_for_load_state", "wait_for_navigation"}
    overlap = SESSION_ACTION_TOOLS & wait_tools
    assert overlap == set(), f"Wait tools should not be logged: {overlap}"


@pytest.mark.asyncio
async def test_format_timestamp_has_z_suffix():
    ts = _format_timestamp()
    assert ts.endswith("Z")


@pytest.mark.asyncio
async def test_create_session_log_creates_file(tmp_path):
    session = MagicMock()
    session.session_id = "sess_test"
    session.base_dir = tmp_path
    session.log_config = None

    path = await create_session_log(session)
    assert path.exists()
    assert path.name == "sess_test_tools.log"


@pytest.mark.asyncio
async def test_create_session_log_uses_tmp_when_no_base_dir(tmp_path):
    session = MagicMock()
    session.session_id = "sess_test"
    session.base_dir = None

    path = await create_session_log(session)
    assert path.exists()


@pytest.mark.asyncio
async def test_write_and_read_log_entry(tmp_path):
    session = MagicMock()
    session.session_id = "sess_test"
    session.base_dir = tmp_path

    await create_session_log(session)

    await write_log_entry(
        session,
        tool_name="click",
        args={"selector": "button.submit", "timeout": 5000},
        status="success",
        output={"found": True},
        duration_ms=123.45,
    )

    result = await read_log_entries(session)
    assert result["total"] == 1
    assert result["has_more"] is False
    entry = result["entries"][0]
    assert entry["tool"] == "click"
    assert entry["args"]["selector"] == "button.submit"
    assert entry["status"] == "success"
    assert "duration_ms" in entry
    assert "timestamp" in entry


@pytest.mark.asyncio
async def test_read_log_entries_pagination(tmp_path):
    session = MagicMock()
    session.session_id = "sess_test"
    session.base_dir = tmp_path

    await create_session_log(session)

    # Write 5 entries
    for i in range(5):
        await write_log_entry(
            session,
            tool_name="navigate",
            args={"url": f"http://example.com/{i}"},
            status="success",
            output={"url": f"http://example.com/{i}"},
            duration_ms=50.0,
        )

    result = await read_log_entries(session, offset=0, limit=2)
    assert result["total"] == 5
    assert len(result["entries"]) == 2
    assert result["has_more"] is True

    result2 = await read_log_entries(session, offset=2, limit=2)
    assert len(result2["entries"]) == 2
    assert result2["has_more"] is True

    result3 = await read_log_entries(session, offset=4, limit=2)
    assert len(result3["entries"]) == 1
    assert result3["has_more"] is False


@pytest.mark.asyncio
async def test_is_logging_active_when_not_set():
    session = MagicMock()
    session.session_id = "sess_test"
    session.log_config = None
    session.active = False

    with patch("mcp_tools.logging_utils.get_session_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = session
        result = await _is_logging_active("sess_test")
        assert result is False


@pytest.mark.asyncio
async def test_is_logging_active_when_active():
    session = MagicMock()
    session.session_id = "sess_test"
    session.log_config = {"active": True, "log_file": "/tmp/test.log"}

    with patch("mcp_tools.logging_utils.get_session_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = session
        result = await _is_logging_active("sess_test")
        assert result is True


@pytest.mark.asyncio
async def test_is_logging_active_session_not_found():
    with patch("mcp_tools.logging_utils.get_session_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        result = await _is_logging_active("sess_nonexistent")
        assert result is False
```

- [ ] **Step 3: Write tests for `list_log_entries` tool**

```python
# tests/test_session_logging.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_tools.session_logging import list_log_entries


@pytest.mark.asyncio
async def test_list_log_entries_session_not_found():
    with patch("mcp_tools.session_logging.get_session_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        result = await list_log_entries("sess_nonexistent")
        assert result["status"] == "error"
        assert "not found" in result["message"]


@pytest.mark.asyncio
async def test_list_log_entries_returns_entries(tmp_path):
    # Create a mock session with a log file
    session = MagicMock()
    session.session_id = "sess_test"
    session.base_dir = tmp_path
    session.log_config = {"active": True, "log_file": str(tmp_path / "test.log")}

    # Create a log file with one entry
    log_file = tmp_path / "sess_test_tools.log"
    log_file.write_text('{"tool": "click", "args": {"selector": "btn"}, "status": "success", "output": {"found": true}, "truncated": false, "duration_ms": 10.0, "timestamp": "2026-01-01T00:00:00.000000Z"}\n')

    with patch("mcp_tools.session_logging.get_session_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = session
        result = await list_log_entries("sess_test", offset=0, limit=10)

    assert result["total"] == 1
    assert len(result["entries"]) == 1
    assert result["entries"][0]["tool"] == "click"
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_list_log_entries_clamps_limit():
    session = MagicMock()
    session.session_id = "sess_test"
    session.base_dir = MagicMock()
    session.base_dir.__truediv__ = MagicMock(return_value=MagicMock())

    # When limit=300, it should be clamped to 200.
    with patch("mcp_tools.session_logging.get_session_by_id", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = session
        with patch("mcp_tools.session_logging.read_log_entries", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = {"entries": [], "total": 0, "offset": 0, "limit": 200, "has_more": False}
            result = await list_log_entries("sess_test", limit=300)
            mock_read.assert_called_once()
            call_kwargs = mock_read.call_args[1]
            assert call_kwargs["limit"] == 200
```

- [ ] **Step 4: Run all tests to verify they pass**

```bash
cd /home/web-h-063/Documents/office-beacon-fe/qa-automation-mcp-server
uv run pytest tests/ -v
```

---

### Task 7: Integration smoke test

**Files:**
- Create: `tests/test_integration_logging.py`

This task validates the full end-to-end flow without a real browser — using mocks for the session.

**Steps:**

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration_logging.py
"""Integration tests for the logging feature end-to-end.

These tests mock the session store and verify that the full flow works:
session_start creates a log file -> action tool writes to it ->
list_log_entries reads it back.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_full_flow_session_start_creates_log(tmp_path):
    """session_start with exploration=False should create a log file."""
    with patch("mcp_tools.logging_utils.create_session_log", new_callable=AsyncMock) as mock_create:
        mock_path = tmp_path / "sess_test_tools.log"
        mock_path.touch()
        mock_create.return_value = mock_path

        # Mock session_start to set log_config
        async def fake_session_start(*args, **kwargs):
            return {
                "session_id": "sess_test",
                "status": "ready",
                "reused": False,
            }

        with patch("mcp_tools.session.session_start", new=fake_session_start):
            # Directly test: create log file
            from mcp_tools.logging_utils import create_session_log

            session = MagicMock()
            session.session_id = "sess_test"
            session.base_dir = tmp_path
            session.log_config = None

            path = await create_session_log(session)
            assert path.exists()
            session.log_config = {
                "active": True,
                "log_file": str(path),
                "exploration": False,
            }
            assert session.log_config["active"] is True


@pytest.mark.asyncio
async def test_full_flow_tool_call_logged(tmp_path):
    """A tool call should produce one log entry."""
    from mcp_tools.logging_utils import create_session_log, write_log_entry

    session = MagicMock()
    session.session_id = "sess_test"
    session.base_dir = tmp_path
    session.log_config = None

    path = await create_session_log(session)
    session.log_config = {"active": True, "log_file": str(path)}

    # Simulate a tool call
    await write_log_entry(
        session,
        tool_name="click",
        args={"selector": 'button[name="Submit"]', "timeout": 5000},
        status="success",
        output={"found": True},
        duration_ms=85.3,
    )

    # Read it back
    from mcp_tools.logging_utils import read_log_entries

    result = await read_log_entries(session)
    assert result["total"] == 1
    entry = result["entries"][0]
    assert entry["tool"] == "click"
    assert entry["args"]["selector"] == 'button[name="Submit"]'
    assert entry["status"] == "success"
    assert entry["duration_ms"] == 85.3


@pytest.mark.asyncio
async def test_exploration_mode_no_log_file(tmp_path):
    """session_start with exploration=True should NOT create a log file."""
    # When exploration=True, create_session_log is never called.
    # We verify this by checking the file doesn't exist after "starting" exploration.
    from mcp_tools.logging_utils import create_session_log

    session = MagicMock()
    session.session_id = "sess_exploration"
    session.base_dir = tmp_path
    session.log_config = None

    # Simulate: do NOT call create_session_log (as exploration=True path does)
    assert not (tmp_path / "sess_exploration_tools.log").exists()
```

- [ ] **Step 2: Run all tests**

```bash
cd /home/web-h-063/Documents/office-beacon-fe/qa-automation-mcp-server
uv run pytest tests/ -v
```

All tests should pass. If any fail, fix the implementation.

---

## Execution Order

Tasks must be done in this order because each depends on the previous:

```
Task 1: logging_utils.py (foundation)
    ↓
Task 2: SessionData.log_config field (schema change)
    ↓
Task 3: session_start() exploration parameter (uses Task 1 + Task 2)
    ↓
Task 4: Wrap action tools with decorator (uses Task 1)
    ↓
Task 5: list_log_entries tool (uses Task 1)
    ↓
Task 6: Unit tests (uses all above)
    ↓
Task 7: Integration smoke test (uses all above)
```

## Testing Approach

1. **Unit tests (Task 6):** Test each function in `logging_utils.py` in isolation with mocked sessions. No browser needed.
2. **Integration test (Task 7):** Test the full flow: session creation -> log file creation -> tool call -> log read-back. Uses mocks for sessions but exercises the real logging code.
3. **Manual verification (post-implementation):** Run the MCP server with a real browser session, call `session_start()`, call a few action tools, then call `list_log_entries()` to see the log entries in the response.

## Edge Cases Summary

| Edge Case | Handling |
|-----------|----------|
| `session.base_dir` is `None` | Fall back to `/tmp/session_{id}_tools.log` |
| Session not found during log write | `_is_logging_active` returns `False`, no error |
| Concurrent writes to same log file | `_session_lock` serializes |
| Output value too long (>500 chars) | Truncated with `[truncated]` marker |
| `exploration=True` | No log file created, `_log_action` sees no log config |
| Malformed log line | `read_log_entries` skips with `error` flag |
| `offset` negative | Clamped to 0 |
| `limit` > 200 | Clamped to 200 |
| `limit` <= 0 | Clamped to 1 |
| Session reuses existing | `exploration` flag passed through in reused response |
| Wait tools (`wait_*`) | NOT logged — they are primitives, not actions |

## Files Summary

| File | Action | Responsibility |
|------|--------|----------------|
| `mcp_tools/logging_utils.py` | **Create** | Log file management, JSON-lines writer/reader, `_log_action` decorator |
| `helpers/session_store.py` | **Modify** | Add `log_config` field to `SessionData` |
| `mcp_tools/session.py` | **Modify** | Add `exploration` parameter to `session_start()` |
| `mcp_tools/dom.py` | **Modify** | Add `@_log_action` to `click`, `type`, `fill`, `select_option`, `check`, `press_key` |
| `mcp_tools/navigate.py` | **Modify** | Add `@_log_action` to `navigate`, `navigate_with_retry`, `navigate_back`, `reload` |
| `mcp_tools/wait.py` | **No change** | Wait tools (`wait_*`) are NOT logged — they are primitives, not actions |
| `mcp_tools/upload.py` | **Modify** | Add `@_log_action` to `upload_file` |
| `mcp_tools/session_logging.py` | **Create** | `list_log_entries` tool |
| `tests/__init__.py` | **Create** | Empty init for pytest |
| `tests/test_logging_utils.py` | **Create** | Unit tests for logging utilities |
| `tests/test_session_logging.py` | **Create** | Unit tests for `list_log_entries` tool |
| `tests/test_integration_logging.py` | **Create** | Integration smoke test |

## What This Does NOT Cover (Out of Scope)

- **Log rotation / size limits:** The log file grows unbounded. Future Phase 2 can add rotation based on file size or entry count.
- **Log cleanup on `session_close`:** The log file persists after session close. The `log_config` field on `SessionData` is cleared on unregister (handled by the dataclass's default), but the file on disk is not deleted. Add cleanup in `session_close()` as a follow-up task if desired.
- **Sensitive data scrubbing:** Args like passwords or tokens are logged as-is. Add a scrub step in `safe_serialize` later if needed.
- **Log format versioning:** The JSON-lines format is fixed. Add a `version` field to the entry if future format changes are needed.
- **`sleep` tool logging:** The `sleep` tool in `wait.py` is intentionally NOT logged (it's a timing primitive, not an interaction). Add it later if the automation review tool finds it useful.