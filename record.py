#!/usr/bin/env python3
"""Standalone recorder — records human interactions to JSON.

Usage:
  python3 record.py [output_name]

Example:
  python3 record.py my-login-flow
"""

import json
import sys
import time
import threading
import signal
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


# ── HTTP server to receive events from content script ────────────────────────

events = []
events_lock = threading.Lock()


class EventHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(size)
        try:
            data = json.loads(body)
            with events_lock:
                events.append(data)
        except json.JSONDecodeError:
            pass
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def log_message(self, *args):
        pass  # silence logs

    def do_GET(self):
        if self.path == '/debug':
            with events_lock:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"count": len(events), "events": events[-5:]}).encode())
        else:
            self.send_response(404)
            self.end_headers()


# ── Content script ──────────────────────────────────────────────────────────

CONTENT_SCRIPT = """
(function () {
  if (window.__recorder_injected) return;
  window.__recorder_injected = true;

  const SERVER = 'http://localhost:9223';

  function post(event, data) {
    navigator.sendBeacon && navigator.sendBeacon(SERVER + '/events', JSON.stringify({
      session_id: '',
      event: event,
      timestamp: Date.now(),
      url: location.href,
      data: data || {}
    }));
  }

  // Use console.log for events — CDP captures these via Log.entryAdded
  function postEvent(event, data) {
    console.log('[REC]' + JSON.stringify({ session_id: '', event: event, timestamp: Date.now(), url: location.href, data: data || {} }));
  }

  document.addEventListener('click', e => {
    postEvent('click', { selector: e.target.outerHTML.slice(0, 200), tag: e.target.tagName, text: e.target.textContent.trim().slice(0, 100) });
  }, true);

  document.addEventListener('input', e => {
    const el = e.target;
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {
      postEvent('input', { selector: el.outerHTML.slice(0, 200), tag: el.tagName, type: el.type || '', name: el.name || '', id: el.id || '', value: el.value });
    }
  }, true);

  document.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      postEvent('keydown', { key: 'Enter', selector: e.target.outerHTML.slice(0, 200) });
    }
  }, true);

  window.addEventListener('hashchange', () => postEvent('navigate', { url: location.href }));
  history.pushState && history.replaceState && ['pushState', 'replaceState'].forEach(fn => {
    const orig = history[fn];
    history[fn] = function() { orig.apply(this, arguments); setTimeout(() => postEvent('navigate', { url: location.href }), 100); };
  });

  console.log('[recorder] injected');
})();
"""


# ── CDP injection ────────────────────────────────────────────────────────────

def inject_script(cdp_url: str) -> bool:
    try:
        import websocket
    except ImportError:
        print("Install: pip install websocket-client")
        return False

    # Get pages list via HTTP first
    import urllib.request
    req = urllib.request.urlopen(f"{cdp_url}/json")
    pages = json.loads(req.read())
    if not pages:
        print("No pages found")
        return False

    print(f"Found page: {pages[0]['url']}")

    # Connect to first page via WebSocket using the debugger URL
    ws = websocket.WebSocket()
    ws.connect(pages[0]["webSocketDebuggerUrl"])
    print(f"Connected to: {pages[0]['url']}")

    msg = json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": CONTENT_SCRIPT, "returnByValue": False},
    })
    ws.send(msg)
    resp = json.loads(ws.recv())
    ws.close()

    print("Content script injected")
    print("NOTE: Navigate to an http:// or https:// page in Chrome to start recording")
    return True


# ── CDP event listener ────────────────────────────────────────────────────────

def listen_cdp_events(page_ws_url: str, stop_event: threading.Event):
    """Listen to CDP Log.entryAdded events and capture recorder logs."""
    import websocket

    ws = websocket.WebSocket()
    ws.settimeout(0.5)

    try:
        ws.connect(page_ws_url)
    except Exception as e:
        print(f"CDP listener connect failed: {e}")
        return

    # Enable Console domain (not Log — console.log appears here)
    ws.send(json.dumps({"id": 1, "method": "Console.enable"}))
    try:
        ws.recv()
    except:
        pass

    msg_id = 2
    while not stop_event.is_set():
        try:
            data = ws.recv()
            msg = json.loads(data)

            # Look for Console.messageAdded events
            if msg.get("method") == "Console.messageAdded":
                msg_obj = msg.get("params", {}).get("message", {})
                args = msg_obj.get("args", [])
                text = args[0].get("value", "") if args else ""
                if text.startswith("[REC]"):
                    try:
                        event_data = json.loads(text[5:])
                        with events_lock:
                            events.append(event_data)
                        print(f"  [captured: {event_data.get('event', '?')}]")
                    except json.JSONDecodeError:
                        pass
        except websocket.WebSocketTimeoutException:
            continue
        except Exception:
            break

    ws.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    output_name = sys.argv[1] if len(sys.argv) > 1 else "recording"
    cdp_url = "http://localhost:9222"

    # Get page URL
    import urllib.request
    req = urllib.request.urlopen(f"{cdp_url}/json")
    pages = json.loads(req.read())
    if not pages:
        print("No pages found")
        return

    page_url = pages[0]["url"]
    page_ws_url = pages[0]["webSocketDebuggerUrl"]
    page_id = pages[0]["id"]

    print(f"Found page: {page_url}")

    # Start HTTP server on port 9223
    server = HTTPServer(('localhost', 9223), EventHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"HTTP server running on port 9223")

    # Inject content script
    if not inject_script(cdp_url):
        server.shutdown()
        return

    # Start CDP listener thread
    stop_event = threading.Event()
    cdp_thread = threading.Thread(target=listen_cdp_events, args=(page_ws_url, stop_event))
    cdp_thread.start()
    print(f"\nRecording to: {output_name}.json")
    print("Interact with Chrome. Press Ctrl+C to stop and save.\n")

    # Wait for Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nSaving...")

    stop_event.set()
    server.shutdown()

    # Save events
    with events_lock:
        saved = list(events)

    if not saved:
        print("No events recorded")
        return

    out_dir = Path(__file__).parent / "automations" / "default"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{output_name}.json"

    result = {
        "recorded_at": datetime.utcnow().isoformat() + "Z",
        "events": saved,
    }
    out_path.write_text(json.dumps(result, indent=2))

    print(f"Saved {len(saved)} events to: {out_path}")


if __name__ == "__main__":
    main()
