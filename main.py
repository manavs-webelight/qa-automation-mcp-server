import os
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.providers import FileSystemProvider

load_dotenv()

HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "7000"))

# Use FileSystemProvider for automatic tool discovery - no manual registration needed
# Each tool file uses standalone @tool decorator from fastmcp.tools
mcp = FastMCP(
    "qa-automation",
    providers=[
        FileSystemProvider(
            root=Path(__file__).parent / "mcp_tools",
            reload=os.getenv("DEBUG", "false").lower() == "true",
        )
    ],
)

if __name__ == "__main__":
    mcp.run(transport="http", host=HOST, port=PORT)
