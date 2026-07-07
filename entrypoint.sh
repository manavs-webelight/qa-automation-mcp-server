#!/bin/sh
# Start Xvfb on :99, then exec the MCP server in the foreground.
# When python exits, Xvfb is killed by PID tracking.

set -e

XVFB_DISPLAY="${DISPLAY:-:99}"
XVFB_SCREEN="${XVFB_SCREEN:-1920x1080x24}"

echo "[entrypoint] starting Xvfb on ${XVFB_DISPLAY} (${XVFB_SCREEN})"
Xvfb "${XVFB_DISPLAY}" -screen 0 "${XVFB_SCREEN}" -nolisten tcp &
XVFB_PID=$!

# Give Xvfb a moment to come up
sleep 1

# If Xvfb died immediately, fail loudly instead of letting Chromium hang
if ! kill -0 "${XVFB_PID}" 2>/dev/null; then
  echo "[entrypoint] Xvfb failed to start" >&2
  exit 1
fi

# Make sure Xvfb dies when python does (or when this script is interrupted)
trap 'echo "[entrypoint] stopping Xvfb"; kill "${XVFB_PID}" 2>/dev/null || true' EXIT INT TERM

echo "[entrypoint] starting MCP server"
exec python main.py