from fastmcp.tools import tool

from mcp_tools.logging_utils import _log_action


@tool
@_log_action("ping")
def ping() -> str:
    """Health check — returns 'pong'."""
    return "pong"
