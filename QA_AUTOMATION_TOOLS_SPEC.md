# QA Automation Tool Spec — Custom Playwright Wrapper

## Overview

A lightweight Playwright-based browser automation layer for QA testing. One persistent browser context per user session. All tools operate on that same context — no re-launching, no new browsers per call.

---

## Design Principles

1. **One browser per user session** — stored in server memory, keyed by session ID
2. **Favor JS for DOM work** — `page.evaluate()` for clicks, fills, reads; Playwright only for what JS can't do
3. **Fail fast** — every action should assert what it just did
4. **Screenshots on every step** — optional, configurable, for reports
5. **No tool call spawns a new browser** — session lifecycle is explicit

---

## Session Management

### `session_start(profile_name?)`
- Launch a persistent Chromium context using Playwright's `userDataDir`
- If `profile_name` is provided, use that Chrome profile path
- If omitted, launch a temporary incognito context
- Store `{ context, page, current_tab_index, tabs[] }` in a `Map<sessionId, Context>`
- Returns `{ session_id, profile }`

```
POST /session_start
{ "profile": "alice" }

→ { "session_id": "sess_abc123", "profile": "alice", "status": "ready" }
```

### `session_close(session_id)`
- Gracefully close all open tabs in the context, then close the context
- Remove from the sessions map
- Returns `{ status: "closed" }`

```
POST /session_close
{ "session_id": "sess_abc123" }

→ { "status": "closed" }
```

### `session_list`
- Return all active sessions (session_id + profile)
- Useful for debugging / admin

```
→ { "sessions": [{ "session_id": "sess_abc123", "profile": "alice" }] }
```

---

## Navigation

### `navigate(session_id, url)`
- Calls `page.goto(url)` on the stored session
- Waits for `domcontentloaded` by default
- Returns `{ url, title, final_url }`
- Catches page load errors (404, 500, network failure, timeout) — returns `{ status: "error", ... }` instead of throwing
- For automatic retry, use `navigate_with_retry`

```
POST /navigate
{ "session_id": "sess_abc123", "url": "https://app.example.com/login" }

→ { "url": "https://app.example.com/login", "title": "Login", "final_url": "https://app.example.com/login" }
```

### `navigate_back(session_id)`
- `page.goBack()`
- Returns `{ url, title }`

### `reload(session_id)`
- `page.reload()`
- Returns `{ url, title }`

---

## DOM Interaction (via JS)

### `execute(session_id, script)`
- Run arbitrary JS in the page via `page.evaluate(script)`
- Script runs in browser context, can return a value
- Use this for: reads, DOM mutations, complex interactions JS handles better than Playwright actions
- Returns `{ result }` — whatever JS returned

```
POST /execute
{
  "session_id": "sess_abc123",
  "script": "document.querySelector('.price').textContent"
}

→ { "result": "$19.99" }
```

### `click(session_id, selector)`
- JS `document.querySelector(selector).click()`
- Returns `{ found: true|false }` — fails if element not found

### `type(session_id, selector, text)`
- JS `focus()` + `value = text` + `input` event
- Returns `{ found: true|false }`

### `fill(session_id, selector, value)`
- Same as `type`, shorthand for text inputs

### `select_option(session_id, selector, value)`
- JS to set `<select>` value and dispatch `change`

### `check(session_id, selector)`
- JS to set `input[type=checkbox].checked = true`

### `press_key(session_id, selector, key)`
- JS `element.focus()` + `KeyboardEvent` — Enter, Tab, Escape, arrow keys, etc.

### `get_text(session_id, selector)`
- `document.querySelector(selector).textContent`
- Returns `{ text }`

### `get_value(session_id, selector)`
- `document.querySelector(selector).value`
- Returns `{ value }`

### `get_attribute(session_id, selector, attr)`
- `document.querySelector(selector).getAttribute(attr)`
- Returns `{ value }`

---

## Form Filling

### `fill_form(session_id, fields[])`
- Accept an array of field descriptors, execute each via JS in sequence
- Useful for multi-step forms without N tool calls

```
POST /fill_form
{
  "session_id": "sess_abc123",
  "fields": [
    { "selector": "input[name=email]", "type": "textbox", "value": "alice@example.com" },
    { "selector": "input[name=password]", "type": "password", "value": "secret123" },
    { "selector": "button[type=submit]", "type": "submit" }
  ]
}
→ { "filled": 3 }
```

---

## Waiting

### `wait_for_selector(session_id, selector, options?)`
- `page.waitForSelector(selector, { timeout?, state: 'visible'|'hidden'|'attached'|'detached' })`
- Returns `{ found: true }` or throws on timeout

```
POST /wait_for_selector
{
  "session_id": "sess_abc123",
  "selector": ".success-message",
  "options": { "timeout": 10000 }
}

→ { "found": true }
```

### `wait_for_url(session_id, pattern, timeout?)`
- `page.waitForURL(pattern)` — glob or regex pattern
- Returns `{ url }` when matched

### `wait_for_load_state(session_id, state)`
- `page.waitForLoadState('networkidle'|'domcontentloaded'|'load')`
- Returns `{ state }`

### `sleep(session_id, ms)`
- Plain `setTimeout` via JS, for cases where human wait is needed
- Returns `{ slept: ms }`

### `wait_for_navigation(session_id, options?)`
- `page.waitForNavigation({ timeout?, waitUntil: 'load'|'domcontentloaded'|'networkidle' })`
- Use after an action that triggers a navigation (click → redirect, form submit, etc.)
- Returns `{ url, title }` when navigation completes

```
POST /wait_for_navigation
{
  "session_id": "sess_abc123",
  "options": { "waitUntil": "networkidle", "timeout": 15000 }
}

→ { "url": "https://app.example.com/dashboard", "title": "Dashboard" }
```

---

## Tab Management

Tabs are tracked per session. Each session has a `current_page` and a `pages[]` array.
All tools use `current_page` unless a tab is specified.

### `new_tab(session_id, url?)`
- `context.newPage()` — opens a new tab in the same browser
- If `url` provided, navigates the new tab immediately
- Switches `current_page` to the new tab
- Returns `{ tab_index, url }`

```
POST /new_tab
{ "session_id": "sess_abc123", "url": "https://app.example.com/settings" }

→ { "tab_index": 2, "url": "https://app.example.com/settings" }
```

### `close_tab(session_id, index?)`
- Closes the tab at `index`. If omitted, closes the current tab
- If closing the current tab, automatically switches `current_page` to the next available tab
- Returns `{ closed: true }`

```
POST /close_tab
{ "session_id": "sess_abc123" }

→ { "closed": true }
```

### `switch_tab(session_id, index)`
- Switches `current_page` to the tab at `index`
- Returns `{ current_url, current_title, tab_index }`

```
POST /switch_tab
{ "session_id": "sess_abc123", "index": 0 }

→ { "current_url": "https://app.example.com/dashboard", "current_title": "Dashboard", "tab_index": 0 }
```

### `list_tabs(session_id)`
- Returns all tabs in the session with their index, URL, and title
- Useful for debugging and finding tabs by content

```
POST /list_tabs
{ "session_id": "sess_abc123" }

→ { "tabs": [
    { "index": 0, "url": "https://app.example.com/dashboard", "title": "Dashboard" },
    { "index": 1, "url": "https://app.example.com/settings", "title": "Settings" }
  ],
  "current": 0
}
```

---

## Iframe Handling

Iframes require switching the active frame context before interacting with elements inside them.

### `switch_to_frame(session_id, selector)`
- `page.frameLocator(selector).frame({ name: ... })` — switches to the iframe at the given selector
- Subsequent tools operate on the iframe context until `switch_to_main` is called
- Returns `{ frame_index, frame_name }`

```
POST /switch_to_frame
{ "session_id": "sess_abc123", "selector": "iframe[name='editor']" }

→ { "frame_index": 0, "frame_name": "editor" }
```

### `switch_to_main(session_id)`
- `page.mainFrame()` — switches back to the main page frame
- Subsequent tools operate on the main page context

```
POST /switch_to_main
{ "session_id": "sess_abc123" }

→ { "switched": true }
```

---

## Page Load Error Handling

All navigation tools catch and return page load errors gracefully instead of throwing unhandled exceptions.

### `navigate(session_id, url)` — error cases

| Scenario | Behavior |
|----------|----------|
| 404/500 response | Returns `{ status: "error", error: "page_error", http_status: 404 }` |
| Network failure | Returns `{ status: "error", error: "network_error", message: "..." }` |
| Navigation timeout | Returns `{ status: "error", error: "timeout", message: "Navigation timed out" }` |
| Redirect loop | Returns `{ status: "error", error: "redirect_loop" }` |

```
POST /navigate
{ "session_id": "sess_abc123", "url": "https://app.example.com/nonexistent" }

→ { "status": "error", "error": "page_error", "http_status": 404, "message": "Page not found" }
```

### `navigate_with_retry(session_id, url, options?)`
- Navigates with retry on failure. Useful for flaky networks or slow servers
- Options: `{ retries: number (default 3), retry_delay_ms: number (default 2000) }`
- Returns same as `navigate`, or after all retries exhausted: `{ status: "error", error: "all_retries_exhausted", attempts: N }`

```
POST /navigate_with_retry
{ "session_id": "sess_abc123", "url": "https://app.example.com/dashboard", "options": { "retries": 3 } }

→ { "url": "...", "title": "Dashboard", "attempts": 1 }  // succeeded on first try
```

---

## Assertions

### `assert_visible(session_id, selector, message?)`
- Check element exists and is visible via Playwright
- Throws if not found, includes optional custom message in error

### `assert_text(session_id, selector, expected, message?)`
- Fetch text, assert equals expected
- Throws on mismatch with diff shown

### `assert_url(session_id, pattern, message?)`
- Assert current URL matches pattern

### `assert_title(session_id, expected, message?)`
- Assert page title equals expected

### `assert_no_console_errors(session_id)`
- Check that no error-level console messages fired since last call
- Returns `{ errors: [] }`

---

## Screenshots & Recording

### `screenshot(session_id, name?, options?)`
- `page.screenshot({ fullPage?, type: 'png'|'jpeg' })`
- If `name` provided, save to `{screenshot_dir}/{name}_{timestamp}.png`
- Returns `{ path, base64 }`
- **Viewport support**: set `viewport` as `{ width, height }` or use a named `preset`
- **Available presets**: `desktop-1080p`, `desktop-720p`, `desktop-1440p`, `iphone-14-pro`, `iphone-se`, `pixel-7`, `galaxy-s24`, `ipad-pro-12`, `ipad-mini`, `surface-pro`, or pass explicit `{ width, height }`

```
POST /screenshot
{ "session_id": "sess_abc123", "name": "login-success" }

→ { "path": "./screenshots/login-success_1751459200000.png", "base64": "..." }
```

**Viewport presets:**

| Preset | Width | Height |
|--------|-------|--------|
| `desktop-1080p` | 1920 | 1080 |
| `desktop-720p` | 1280 | 720 |
| `desktop-1440p` | 2560 | 1440 |
| `iphone-14-pro` | 393 | 852 |
| `iphone-se` | 375 | 667 |
| `pixel-7` | 412 | 915 |
| `galaxy-s24` | 360 | 780 |
| `ipad-pro-12` | 1024 | 1366 |
| `ipad-mini` | 768 | 1024 |
| `surface-pro` | 1024 | 1336 |

```
POST /screenshot
{ "session_id": "sess_abc123", "name": "login-mobile", "viewport": { "preset": "iphone-14-pro" } }

→ { "path": "./screenshots/login-mobile_1751459200000.png", "base64": "..." }
```

### `start_tracing(session_id, name?)`
- `page.tracing.start({ screenshots: true })` — Playwright trace with screenshots
- Returns `{ "status": "started" }`

### `stop_tracing(session_id, name?)`
- `page.tracing.stop()`
- Save trace to `{trace_dir}/{name}_{timestamp}.zip`
- Returns `{ path }` — can open with `playwright show-trace`

---

## Network Interception

### `route(session_id, url_pattern, handler)`
- `page.route(url_pattern, route => route.fulfill({ ... })})`
- Handler: `{ status, body, headers?, contentType? }`
- Returns `{ matched: true }`

```
POST /route
{
  "session_id": "sess_abc123",
  "url_pattern": "**/api/user",
  "handler": { "status": 200, "body": "{\"id\": 1, \"name\": \"Alice\"}", "contentType": "application/json" }
}

→ { "matched": 0 }  // number of requests matched so far
```

### `unroute(session_id, url_pattern?)`
- Remove a specific route or all routes if no pattern

### `get_requests(session_id, filter?)`
- Return list of network requests since last call
- `filter` can narrow by URL pattern
- Returns `{ requests: [{ url, method, status, response }] }`

---

## Console & Logs

### `console_messages(session_id, level?)`
- `page.on('console', ...)` — capture since last call
- `level`: `'error'|'warning'|'info'|'debug'` — defaults to `'info'`
- Returns `{ messages: [{ type, text, location }] }`

### `clear_console_messages(session_id)`
- Discard buffered console messages without returning them

---

## Cookies & Storage

### `get_cookies(session_id)`
- `context.cookies()`
- Returns `{ cookies: [{ name, value, domain, path, httpOnly, secure, sameSite, expires }] }`

### `set_cookies(session_id, cookies[])`
- `context.addCookies(cookies[])`
- Useful for injecting a known session without using a profile

### `delete_cookie(session_id, name)`
- `context.deleteCookies(name)`

### `set_storage_state(session_id, file_path)`
- `context.setStorageFromFile(file)` — Playwright's storage state format
- Returns `{ "status": "restored" }`

### `get_local_storage(session_id)`
- `page.evaluate(() => Object.entries(localStorage))`

### `set_local_storage(session_id, key, value)`
- `page.evaluate(() => { localStorage.setItem(key, value) })`

---

## File Upload

### `upload_file(session_id, selector, file_path)`
- Playwright's `page.setInputFiles(selector, file_path)` — the one thing JS can't do
- Returns `{ uploaded: true }`

---

## Dialog Handling

### `handle_dialog(session_id, action, promptText?)`
- `page.on('dialog', dialog => dialog.accept(promptText?)|dialog.dismiss())`
- `action`: `'accept'|'dismiss'`
- If `action = 'accept'` and dialog has a prompt, pass `promptText`
- Returns `{ handled: true, dialog_type }`

---

## Error Handling

All tools return:
```json
{ "status": "ok", ...result }
```

On error:
```json
{ "status": "error", "message": "...", "tool": "...", "session_id": "..." }
```

Session not found: `404`
Invalid selector: `400` with details

---

## Multi-User / Concurrency

- Sessions stored in a `Map<session_id, { context, current_page, tabs[], profile, started_at }>`
- No limit enforced by spec — server resources set the limit
- Sessions do NOT auto-expire — caller must call `session_close` or implement TTL outside

---

## Tool Summary

| Category | Tools |
|----------|-------|
| Lifecycle | `session_start`, `session_close`, `session_list` |
| Navigation | `navigate`, `navigate_with_retry`, `navigate_back`, `reload` |
| Tabs | `new_tab`, `close_tab`, `switch_tab`, `list_tabs` |
| Iframes | `switch_to_frame`, `switch_to_main` |
| DOM | `execute`, `click`, `type`, `fill`, `select_option`, `check`, `press_key`, `get_text`, `get_value`, `get_attribute`, `fill_form` |
| Waiting | `wait_for_selector`, `wait_for_url`, `wait_for_load_state`, `wait_for_navigation`, `sleep` |
| Assertions | `assert_visible`, `assert_text`, `assert_url`, `assert_title`, `assert_no_console_errors` |
| Screenshots | `screenshot`, `start_tracing`, `stop_tracing` |
| Network | `route`, `unroute`, `get_requests` |
| Console | `console_messages`, `clear_console_messages` |
| Storage | `get_cookies`, `set_cookies`, `delete_cookie`, `set_storage_state`, `get_local_storage`, `set_local_storage` |
| Upload | `upload_file` |
| Dialog | `handle_dialog` |

---

## Implementation Notes

- **Runtime**: Node.js with `@playwright/test` or `playwright` package
- **Transport**: HTTP REST API (Express/Fastify) or WebSocket for bidirectional communication
- **Session storage**: In-memory `Map` — suitable for single-server deployments
- **Profile path convention**: `profiles/{profile_name}` — Playwright uses this as `userDataDir`
- **Screenshot dir**: `./screenshots/` — configurable via env `SCREENSHOT_DIR`
- **Trace dir**: `./traces/` — configurable via env `TRACE_DIR`
- **Playwright auto-waiting**: Leveraged by default — all Playwright actions already wait for element readiness, so explicit waits are only needed for non-action scenarios (page state changes not triggered by our own calls)
