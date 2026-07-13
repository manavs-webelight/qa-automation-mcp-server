"""Tracing tools — Playwright trace recording with screenshots."""

import os
import time
from pathlib import Path
from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = await get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


async def _get_trace_dir(base_dir: Path = None) -> Path:
    """Return the trace directory, creating it if necessary."""
    trace_dir = Path(os.getenv("TRACE_DIR", "automations/traces"))
    # Resolve relative paths from base_dir or cwd
    if not trace_dir.is_absolute():
        trace_dir = (base_dir / trace_dir) if base_dir else Path.cwd() / trace_dir
    trace_dir.mkdir(parents=True, exist_ok=True)
    return trace_dir


# @tool  # DISABLED
async def start_tracing(session_id: str, name: str | None = None) -> dict:
    """
    Start recording a Playwright trace with screenshots for the session.

    Uses ``context.tracing.start(screenshots=True)`` — the trace is attached
    to the browser context, not a single page, so it captures all pages in the
    session.

    Args:
        session_id: The session ID.
        name: Optional name for the trace. Used as the base filename when
            ``stop_tracing`` is called.

    Returns:
        ``{"status": "started"}`` on success.
        ``{"status": "error", "error": "already_tracing"}`` if tracing is
        already active for this session.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if session.is_tracing:
        return {"status": "error", "error": "already_tracing"}

    await session.context.tracing.start(screenshots=True)
    session.is_tracing = True
    # Store the optional name for use in stop_tracing
    session._trace_name = name

    return {"status": "started"}


# @tool  # DISABLED
async def stop_tracing(session_id: str, name: str | None = None) -> dict:
    """
    Stop the active trace recording and save it to disk.

    Uses ``context.tracing.stop()`` which returns the trace as bytes, then
    writes to ``{TRACE_DIR}/{name}_{timestamp}.zip``.

    Args:
        session_id: The session ID.
        name: Optional name override. If omitted, uses the name passed to
            ``start_tracing``. Defaults to ``"trace"`` if neither is set.

    Returns:
        ``{"path": "..."}`` with the path to the saved trace file.
        ``{"status": "error", "error": "not_tracing"}`` if no trace is active.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if not session.is_tracing:
        return {"status": "error", "error": "not_tracing"}

    # Determine the trace name
    trace_name = name or getattr(session, "_trace_name", None) or "trace"

    # Stop tracing and get the binary data
    trace_dir = await _get_trace_dir(session.base_dir)
    timestamp = int(time.time() * 1000)
    filename = f"{trace_name}_{timestamp}.zip"
    filepath = trace_dir / filename

    # In Playwright >=1.40, tracing.stop() writes to the output path and returns None.
    # In older versions, it returned bytes directly.
    trace_buffer = await session.context.tracing.stop(path=str(filepath))
    if trace_buffer is not None:
        # Old-style: bytes returned directly
        Path(filepath).write_bytes(trace_buffer)
    # else: stop(output=...) already wrote the file

    session.is_tracing = False

    return {"path": str(filepath)}
