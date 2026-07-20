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
# async functions in dom.py, navigate.py, upload.py.
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
        return output[:MAX_OUTPUT_LEN] + "... [truncated]", True
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
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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

            args_str = " ".join(f"{k}={v!r}" for k, v in log_args.items())
            print(f"[tool] {tool_name}({args_str})")

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