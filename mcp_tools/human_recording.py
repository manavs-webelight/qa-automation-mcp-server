"""Translate DOM events (from record_interactions.py) to automation JSON.

This module converts the raw DOM event stream captured by the recorder script
injected into pages (record_interactions.py's RECORDER_JS) into the same
automation JSON format that `stop_recording` produces.

Events come in as dicts with keys: type, time, url, element (with selector
and rect), value (for fill events), key (for keydown events), etc.
"""

import re
from datetime import datetime, timezone

# Event type -> MCP tool name mapping
EVENT_TO_TOOL = {
    "click": "click",
    "dblclick": "dblclick",
    "contextmenu": "contextmenu",
    "fill": "fill",
    "keydown": "press_key",
}

# Values that look like they've already been redacted
REDACTED_MARKERS = ("REDACTED", "***", "[hidden]")

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PASSWORD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$")


RECORDER_JS = r"""
(() => {
  if (window.__recorderInstalled) return;
  window.__recorderInstalled = true;

  function cssSelector(el) {
    if (!(el instanceof Element)) return null;
    if (el.id) return '#' + CSS.escape(el.id);
    const path = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && path.length < 8) {
      let part = node.nodeName.toLowerCase();
      if (node.id) {
        part = '#' + CSS.escape(node.id);
        path.unshift(part);
        break;
      }
      let sibling = node, nth = 1;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.nodeName === node.nodeName) nth++;
      }
      part += ':nth-of-type(' + nth + ')';
      path.unshift(part);
      node = node.parentElement;
    }
    return path.join(' > ');
  }

  function elInfo(el) {
    if (!el || !el.getBoundingClientRect) return null;
    const r = el.getBoundingClientRect();
    let classes = [];
    if (el.className && typeof el.className === 'string') {
      classes = el.className.split(/\s+/).filter(Boolean);
    }
    return {
      selector: cssSelector(el),
      tag: el.tagName ? el.tagName.toLowerCase() : null,
      id: el.id || null,
      classes: classes,
      name: el.getAttribute ? el.getAttribute('name') : null,
      text: (el.innerText || el.value || '').toString().slice(0, 120),
      rect: {
        x: Math.round(r.x), y: Math.round(r.y),
        width: Math.round(r.width), height: Math.round(r.height),
        top: Math.round(r.top), left: Math.round(r.left)
      }
    };
  }

  function send(type, data) {
    try {
      window.__record_event__(Object.assign({
        type: type,
        time: Date.now(),
        url: location.href
      }, data));
    } catch (e) { /* binding not ready yet, drop it */ }
  }

  let dragState = null;

  document.addEventListener('mousedown', (e) => {
    dragState = { startX: e.pageX, startY: e.pageY, target: e.target, button: e.button };
  }, true);

  document.addEventListener('mouseup', (e) => {
    if (!dragState) return;
    const dx = e.pageX - dragState.startX;
    const dy = e.pageY - dragState.startY;
    const dist = Math.hypot(dx, dy);
    if (dist > 5) {
      send('drag', {
        from: { x: dragState.startX, y: dragState.startY },
        to: { x: e.pageX, y: e.pageY },
        distance: Math.round(dist),
        element: elInfo(dragState.target),
        dropTarget: elInfo(e.target)
      });
    } else {
      send('click', {
        x: e.pageX, y: e.pageY,
        button: e.button,
        element: elInfo(e.target)
      });
    }
    dragState = null;
  }, true);

  document.addEventListener('dblclick', (e) => {
    send('dblclick', { x: e.pageX, y: e.pageY, element: elInfo(e.target) });
  }, true);

  document.addEventListener('contextmenu', (e) => {
    send('contextmenu', { x: e.pageX, y: e.pageY, element: elInfo(e.target) });
  }, true);

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      let value = el.value;
      if (el.type === 'checkbox' || el.type === 'radio') value = el.checked;
      send('fill', { element: elInfo(el), value: value, inputType: el.type || tag });
    }
  }, true);

  document.addEventListener('dragstart', (e) => {
    send('dragstart', { element: elInfo(e.target), x: e.pageX, y: e.pageY });
  }, true);

  document.addEventListener('drop', (e) => {
    send('drop', { element: elInfo(e.target), x: e.pageX, y: e.pageY });
  }, true);

  document.addEventListener('keydown', (e) => {
    if (['Enter', 'Tab', 'Escape'].includes(e.key)) {
      send('keydown', { key: e.key, element: elInfo(e.target) });
    }
  }, true);

  document.addEventListener('scroll', (() => {
    let timer = null;
    return () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        send('scroll', { x: Math.round(window.scrollX), y: Math.round(window.scrollY) });
      }, 250);
    };
  })(), true);
})();
"""


def _looks_redacted(value) -> bool:
    """Check if a string looks like a redacted value."""
    if not isinstance(value, str):
        return False
    return any(marker in value for marker in REDACTED_MARKERS)


def _extract_placeholders(events: list) -> tuple[list, dict]:
    """Find emails/passwords in fill values, replace with {{EMAIL}}/{{PASSWORD}}.

    Returns the filtered events list and a dict of {placeholder_name: original_value}.
    """
    extracted = {}
    var_counter = {}

    patterns = [
        ("EMAIL", EMAIL_RE),
        ("PASSWORD", PASSWORD_RE),
    ]

    def _get_var_name(name: str) -> str:
        count = var_counter.get(name, 0)
        var_counter[name] = count + 1
        if count == 0:
            return name
        return f"{name}{count + 1}"

    new_events = []
    for event in events:
        if event.get("type") != "fill":
            new_events.append(event)
            continue

        value = event.get("value")
        if not isinstance(value, str):
            new_events.append(event)
            continue

        # Skip if already a placeholder
        if value.startswith("{{") and value.endswith("}}"):
            new_events.append(event)
            continue

        # Skip if it looks redacted
        if _looks_redacted(value):
            new_events.append(event)
            continue

        for var_name, pattern in patterns:
            if pattern.match(value):
                safe_name = _get_var_name(var_name)
                extracted[safe_name] = value
                event = dict(event)  # copy to avoid mutating
                event["value"] = f"{{{{{safe_name}}}}}"
                break

        new_events.append(event)

    return new_events, extracted


def translate_events(events: list, profile: str | None) -> dict:
    """Convert DOM events into automation JSON.

    - Maps event type to MCP tool name
    - Extracts {{EMAIL}} / {{PASSWORD}} placeholders
    - Extracts {{BASE_URL}} from first navigate URL
    - Emits navigate tool calls when URL changes between events
    """
    tools = []
    variables = {}
    last_url = None
    seen_navigates = set()

    # Extract placeholders first
    clean_events, extracted = _extract_placeholders(events)

    # Check for URL changes
    for event in clean_events:
        event_url = event.get("url")
        event_type = event.get("type")

        # Track URL
        if event_url and event_url != last_url and not event_url.startswith("chrome://"):
            base_url = event_url.split("/", 2)[0:3]
            base_url = "/".join(base_url).rstrip("/")

            # Emit navigate tool if we haven't seen this URL before
            if base_url not in seen_navigates:
                seen_navigates.add(base_url)
                tools.append({
                    "tool": "navigate",
                    "args": {"url": event_url},
                })
                if "{{BASE_URL}}" not in variables:
                    # Extract base URL for variables
                    from urllib.parse import urlparse
                    parsed = urlparse(event_url)
                    variables["BASE_URL"] = f"{parsed.scheme}://{parsed.netloc}"

            last_url = event_url

        # Translate event to tool
        if event_type in EVENT_TO_TOOL:
            tool_name = EVENT_TO_TOOL[event_type]
            args = {}

            if event_type == "fill":
                elem = event.get("element", {})
                selector = elem.get("selector") if isinstance(elem, dict) else None
                if selector:
                    args["selector"] = selector
                args["value"] = event.get("value", "")
            elif event_type in ("click", "dblclick", "contextmenu"):
                elem = event.get("element", {})
                selector = elem.get("selector") if isinstance(elem, dict) else None
                if selector:
                    args["selector"] = selector
            elif event_type == "keydown":
                args["key"] = event.get("key", "")
                elem = event.get("element", {})
                selector = elem.get("selector") if isinstance(elem, dict) else None
                if selector:
                    args["selector"] = selector

            # Only emit if we have at least a key piece of info
            if args:
                tools.append({
                    "tool": tool_name,
                    "args": args,
                })

    # Add extracted variables (skip BASE_URL from user-provided variables, keep it)
    final_variables = {**extracted, **variables}

    automation = {
        "version": 1,
        "name": "",
        "description": "",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile or "",
        "cdp_endpoint": "",
        "reuse_session": True,
        "on_error": "continue",
        "max_retries": 1,
        "variables": final_variables,
        "tools": tools,
    }

    return automation