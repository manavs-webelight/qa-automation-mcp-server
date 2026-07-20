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

    # Create a log file with one entry at the path that _get_log_file_path returns
    # for a session with base_dir=tmp_path (it creates logs/ subdir).
    log_file = tmp_path / "logs" / "sess_test_tools.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
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