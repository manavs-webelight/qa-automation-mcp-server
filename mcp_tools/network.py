"""Network interception tools — route, unroute, get_requests."""

import json
from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


async def _setup_request_listeners(session):
    """Attach request/response listeners to the page if not already attached."""
    if getattr(session, "_request_listeners_active", False):
        return

    captured_requests: list = []

    async def on_request(request):
        captured_requests.append({
            "url": request.url,
            "method": request.method,
            "headers": dict(request.headers),
        })

    async def on_response(response):
        # Find the matching request and attach the response info
        for req in captured_requests:
            if req["url"] == response.url and req.get("method") == response.request.method:
                req["status"] = response.status
                req["response"] = {
                    "headers": dict(response.headers),
                    "body": None,  # Body not available on response object
                }
                break

    session.page.on("request", on_request)
    session.page.on("response", on_response)

    # Store the captured_requests list on the session for access by get_requests
    session._captured_requests = captured_requests
    session._request_listeners_active = True


# @tool  # DISABLED
async def route(session_id: str, url_pattern: str, handler: dict) -> dict:
    """
    Register a network route to intercept and fulfill matching requests.

    Uses ``page.route(url_pattern, route => route.fulfill(...))`` to intercept
    requests matching the URL pattern and respond with the provided handler.

    Args:
        session_id: The session ID.
        url_pattern: A glob-style pattern (e.g. ``"**/api/user"``) or regex
            to match request URLs.
        handler: A dict describing how to fulfill matching requests:
            - ``status``: HTTP status code (default: 200)
            - ``body``: Response body string
            - ``headers``: Optional dict of response headers
            - ``contentType``: Optional Content-Type header override

    Returns:
        ``{"matched": <count>}`` where count is the number of requests
        matched so far on this route. Returns ``{"matched": 0}`` on first
        registration (no matches yet).

    Example::

        route(
            session_id="sess_abc123",
            url_pattern="**/api/user",
            handler={
                "status": 200,
                "body": '{"id": 1, "name": "Alice"}',
                "contentType": "application/json"
            }
        )
        → { "matched": 0 }
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    # Set up listeners to track matched requests
    await _setup_request_listeners(session)

    status = handler.get("status", 200)
    body = handler.get("body", "")
    headers = handler.get("headers", {})
    content_type = handler.get("contentType")

    if content_type:
        headers["content-type"] = content_type

    # Keep track of how many times this pattern has matched
    match_count = 0

    async def route_handler(route):
        nonlocal match_count
        match_count += 1
        await route.fulfill(status=status, body=body, headers=headers)

    await session.page.route(url_pattern, route_handler)

    # Track the pattern so we can unroute it later
    session.routes.append(url_pattern)

    return {"matched": 0}


# @tool  # DISABLED
async def unroute(session_id: str, url_pattern: str | None = None) -> dict:
    """
    Remove registered network route(s).

    If ``url_pattern`` is provided, removes only routes matching that pattern.
    If omitted, removes **all** registered routes for the session.

    Args:
        session_id: The session ID.
        url_pattern: Optional glob/regex pattern. If None, all routes are removed.

    Returns:
        ``{"unrouted": <count>}`` — number of routes removed.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if url_pattern is None:
        # Remove all routes
        count = len(session.routes)
        for pattern in session.routes:
            await session.page.unroute(pattern)
        session.routes.clear()
        return {"unrouted": count}

    # Remove routes matching the pattern
    removed = 0
    await session.page.unroute(url_pattern)
    if url_pattern in session.routes:
        session.routes.remove(url_pattern)
        removed = 1

    return {"unrouted": removed}


# @tool  # DISABLED
async def get_requests(session_id: str, filter: str | None = None) -> dict:
    """
    Return all network requests captured since the last call, then clear the buffer.

    Request listeners are attached automatically on first call to ``route``.
    Each request entry contains URL, method, status code, and response headers.

    Args:
        session_id: The session ID.
        filter: Optional URL pattern (glob/regex) to return only matching requests.

    Returns:
        ``{"requests": [...]}`` — list of request objects, oldest first.
        Each object has: ``url``, ``method``, ``status``, ``response.headers``.
        Returns ``{"requests": []}`` if no requests captured or listeners not active.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    captured = getattr(session, "_captured_requests", [])

    if filter:
        import fnmatch
        filtered = [
            req for req in captured
            if fnmatch.fnmatch(req["url"], filter)
        ]
    else:
        filtered = captured

    # Clear the buffer after reading
    captured.clear()

    return {"requests": filtered}
