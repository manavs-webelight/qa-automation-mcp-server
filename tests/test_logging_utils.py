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
    assert len(value) == 515  # 500 + "... [truncated]" (3 + 12 chars)
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
    # Use a plain class without isoformat so hasattr(value, "isoformat") is False.
    class _Opaque:
        pass
    result = safe_serialize(_Opaque())
    assert isinstance(result, str)
    assert "Opaque" in result


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