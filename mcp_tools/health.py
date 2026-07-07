from fastmcp.tools import tool


@tool
def ping() -> str:
    """Health check — returns 'pong'."""
    return "pong"
