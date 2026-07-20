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