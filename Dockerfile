# syntax=docker/dockerfile:1

# ---------- builder ----------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Python dependencies into /app/.venv (cached on pyproject changes only)
COPY pyproject.toml ./
RUN uv sync --no-install-project

# Fetch the Chromium browser binary (OS deps handled in the runner stage)
RUN uv run playwright install chromium

# ---------- runner ----------
FROM python:3.13-slim AS runner

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Bring over the resolved venv and the browser binary
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /ms-playwright /ms-playwright

# Install only the OS-level libraries Chromium needs at runtime,
# plus Xvfb so Chromium can run with headless=False inside the container
RUN playwright install-deps chromium \
 && apt-get update \
 && apt-get install -y --no-install-recommends xvfb \
 && rm -rf /var/lib/apt/lists/*

# App source
COPY main.py ./
COPY mcp_tools ./mcp_tools
COPY helpers ./helpers
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

EXPOSE 7000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
