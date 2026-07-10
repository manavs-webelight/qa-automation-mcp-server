"""Recording tools — manually record automation sequences as JSON.

Tools for recording tool calls into a structured automation that can later be
saved to disk as a reusable JSON playbook.
"""

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = await get_session_by_id(session_id)
    if session is None:
        return (
            {"status": "error", "message": f"Session {session_id} not found"},
            None,
        )
    return (None, session)


def _get_automations_dir(profile: str = None) -> Path:
    """Return the automations directory, creating it if needed.

    Args:
        profile: Profile name. If provided, path is automations/{profile}/.
                 If None, uses 'default'.
    """
    base = Path(__file__).parent.parent / "automations"
    if profile:
        automations_dir = base / profile
    else:
        automations_dir = base / "default"
    automations_dir.mkdir(parents=True, exist_ok=True)
    return automations_dir




def _extract_placeholders(tools: list[dict]) -> dict:
    """Scan recorded tools for literal values that look like variables.

    Only scans 'fill' tool args for the 'value' field. Replaces them with
    {{VARIABLE}} placeholders and returns the extracted values as a variables dict.

    This lets the agent record naturally — no knowledge of placeholders needed.
    """
    extracted = {}
    var_counter = {}  # track how many times each variable has been used

    # Known patterns: (name, regex)
    # Must be specific enough to not match selectors/URLs
    patterns = [
        ("EMAIL", re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")),
        ("PASSWORD", re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=]).{8,}$")),
    ]

    def _get_var_name(name: str) -> str:
        """Return variable name with counter if seen before."""
        count = var_counter.get(name, 0)
        var_counter[name] = count + 1
        if count == 0:
            return name
        return f"{name}{count + 1}"

    def _process_fill_args(args: dict) -> dict:
        """Only process 'fill' tool args — specifically the 'value' field."""
        if "value" not in args:
            return args
        value = args["value"]
        if not isinstance(value, str):
            return args
        # Skip if already a placeholder
        if value.startswith("{{") and value.endswith("}}"):
            return args
        # Check against patterns
        for var_name, pattern in patterns:
            if pattern.match(value):
                safe_name = _get_var_name(var_name)
                extracted[safe_name] = value
                args = dict(args)  # copy to avoid mutating session data
                args["value"] = f"{{{{{safe_name}}}}}"
                return args
        return args

    def _process_tool(entry: dict) -> dict:
        """Process a single tool entry."""
        tool_name = entry.get("tool", "")
        args = entry.get("args", {})
        # Only process 'fill' tool args
        if tool_name == "fill" and isinstance(args, dict):
            args = _process_fill_args(args)
        return {"tool": tool_name, "args": args}

    # Process each recorded step
    new_tools = [_process_tool(entry) for entry in tools]

    return new_tools, extracted


@tool
async def start_recording(session_id: str, recording_name: str, cdp_endpoint: str | None = None) -> dict:
    """Start a new recording session.

    Only one recording can be active per session. Subsequent calls to
    ``start_recording`` without first stopping the current recording will
    return an error.

    **Call this once before any browser actions.** Then call ``record_step``
    after every tool call you make.

    Args:
        session_id: The browser session ID to record in.
        recording_name: A short identifier for this recording (e.g. ``"login-flow"``).
            This is used as the output filename when the recording is saved.
        cdp_endpoint: Optional Chrome DevTools Protocol endpoint. If no session
            exists yet, a new session is auto-created via CDP.

    Returns:
        ``{"status": "started", "name": str, "recorded": 0}`` on success.
        ``{"status": "error", "error": "already_recording"}`` if a recording is
        already active in this session.

    Example::

        start_recording(session_id="sess_abc", recording_name="login-flow")
    """
    err, session = await _resolve_session(session_id)
    if err:
        # If CDP endpoint is provided and no session exists, auto-create one
        if cdp_endpoint is not None:
            from mcp_tools.session import session_start
            auto_session = await session_start(
                email=f"auto_{uuid.uuid4().hex[:8]}@auto",
                profile_name="",
                cdp_endpoint=cdp_endpoint,
            )
            if auto_session.get("status") != "ready":
                return {"status": "error", "message": "Failed to auto-create session via CDP"}
            session_id = auto_session["session_id"]
            err, session = await _resolve_session(session_id)
            if err:
                return err
        else:
            return err

    if session.is_recording:
        return {"status": "error", "error": "already_recording"}

    session.is_recording = True
    session.recording_name = recording_name
    session.recording_tools = []

    return {"status": "started", "name": recording_name, "recorded": 0}


@tool
async def record_step(session_id: str, tool_name: str, args: dict) -> dict:
    """Record a successful tool call to the current recording.

    **Call this after every browser action.** The MCP server does NOT auto-capture
    tool calls — you must explicitly record each one.

    The ``args`` parameter receives the tool's arguments as a flat dict. Do NOT
    nest them inside another ``args`` field.

    Args:
        session_id: The browser session ID (same one used for the tool call).
        tool_name: The MCP tool name that was just called (e.g. ``"navigate"``,
            ``"fill"``, ``"click"``).
        args: The arguments passed to the tool, as a plain dict. Include
            ``session_id`` inside this dict if the tool requires it.

    Returns:
        ``{"status": "recorded", "tool": str, "args": dict,
        "total_recorded": int}`` on success.
        ``{"status": "error", "error": "not_recording"}`` if no active recording.

    Examples::

        # Record a navigate call
        record_step(
            session_id="sess_abc",
            tool_name="navigate",
            args={"url": "http://localhost:3000", "session_id": "sess_abc"}
        )

        # Record a fill call
        record_step(
            session_id="sess_abc",
            tool_name="fill",
            args={
                "selector": "input[type='email']",
                "value": "user@example.com",
                "session_id": "sess_abc"
            }
        )
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if not session.is_recording:
        return {"status": "error", "error": "not_recording"}

    session.recording_tools.append({"tool": tool_name, "args": args})
    total = len(session.recording_tools)

    return {"status": "recorded", "tool": tool_name, "args": args, "total_recorded": total}


@tool
async def remove_last_step(session_id: str) -> dict:
    """Remove the last recorded step (undo).

    Args:
        session_id: The session ID.

    Returns:
        ``{"status": "removed", "tool": str, "remaining": int}`` on success.
        ``{"status": "error", "error": "not_recording"}`` if no active recording.
        ``{"status": "error", "error": "empty"}`` if no steps to remove.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if not session.is_recording:
        return {"status": "error", "error": "not_recording"}

    if not session.recording_tools:
        return {"status": "error", "error": "empty"}

    removed = session.recording_tools.pop()

    return {"status": "removed", "tool": removed["tool"], "remaining": len(session.recording_tools)}


@tool
async def list_recording(session_id: str) -> dict:
    """View all currently recorded steps.

    Args:
        session_id: The session ID.

    Returns:
        ``{"name": str, "steps": int, "tools": [{"tool": str, "args": dict}, ...]}``
        on success.
        ``{"status": "error", "error": "not_recording"}`` if no active recording.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if not session.is_recording:
        return {"status": "error", "error": "not_recording"}

    return {
        "name": session.recording_name,
        "steps": len(session.recording_tools),
        "tools": list(session.recording_tools),
    }


@tool
async def stop_recording(
    session_id: str,
    variables: dict | None = None,
) -> dict:
    """Stop recording and save as JSON file.

    Preserves ``{{VARIABLE}}`` placeholders as-is in the tools list so they
    can be substituted at replay time. Writes the automation to
    ``automations/{name}.json`` with a ``variables`` section containing the
    default values.

    **Must be called after recording at least one step.** Calling this on an
    empty recording returns ``{"status": "error", "error": "empty"}``.

    Args:
        session_id: The browser session ID.
        variables: Optional mapping of variable names to default values
            (e.g. ``{"EMAIL": "user@example.com"}``). These override any
            auto-extracted placeholders. Stored in the JSON
            ``variables`` section for replay-time substitution.

    Returns:
        ``{"status": "saved", "path": str, "steps": int}`` on success.
        ``{"status": "error", "error": "not_recording"}`` if no active recording.
        ``{"status": "error", "error": "empty"}`` if no steps were recorded.
        ``{"status": "error", "error": "already_recording"}`` if ``start_recording``
        was not called first.

    Example::

        stop_recording(
            session_id="sess_abc",
            variables={"EMAIL": "user@example.com", "PASSWORD": "secure123!"}
        )
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if not session.is_recording:
        return {"status": "error", "error": "not_recording"}

    if not session.recording_tools:
        return {"status": "error", "error": "empty"}

    automations_dir = _get_automations_dir(session.profile)
    filepath = automations_dir / f"{session.recording_name}.json"

    # Auto-extract literal values into {{VARIABLE}} placeholders
    # If the agent recorded real credentials (e.g. "manav@email.com"), this
    # replaces them with {{EMAIL}}, {{PASSWORD}}, etc.
    extracted_tools, extracted_vars = _extract_placeholders(session.recording_tools)

    # Agent-provided variables override auto-extracted ones (agent knows better)
    final_vars = {**extracted_vars, **(variables or {})}

    # Build the tools list, preserving placeholders as-is for replay-time substitution
    # (see replay.py for runtime substitution logic)
    tools = list(extracted_tools)

    # Assemble the automation JSON
    automation = {
        "version": 1,
        "name": session.recording_name,
        "description": "",
        "recorded_at": datetime.utcnow().isoformat() + "Z",
        "profile": session.profile,
        "cdp_endpoint": session.cdp_endpoint or "",
        "reuse_session": True,
        "on_error": "stop",
        "max_retries": 1,
        "variables": final_vars,
        "tools": tools,
    }
    json_str = json.dumps(automation, indent=2)
    filepath.write_text(json_str)

    # Reset recording state
    session.is_recording = False
    session.recording_name = None
    session.recording_tools = []

    return {
        "status": "saved",
        "path": str(filepath),
        "steps": len(tools),
        "extracted_variables": extracted_vars,
    }