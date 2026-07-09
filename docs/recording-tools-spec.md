# Recording Tools Spec

## Overview

Tools for manually recording automation sequences as JSON. Agent controls which tool calls get recorded — only the tools needed to replay the automation are included.

## Tools

### `start_recording`

Start a new recording session.

**Args:**
- `session_id` (required): The session to record in
- `name` (required): Name for the automation (e.g., "login-flow")

**Response:**
```json
{
  "status": "started",
  "name": "login-flow",
  "recorded": 0
}
```

**Errors:**
- `already_recording`: A recording is already active in this session

---

### `record_step`

Record a successful tool call to the current recording.

**Args:**
- `session_id` (required): The session ID
- `tool` (required): Tool name (e.g., "navigate", "click", "fill")
- `args` (required): Object with tool arguments

**Response:**
```json
{
  "status": "recorded",
  "tool": "click",
  "args": {"selector": "button:has-text('Continue')"},
  "total_recorded": 5
}
```

**Errors:**
- `not_recording`: No active recording in this session

---

### `remove_last_step`

Remove the last recorded step (undo).

**Args:**
- `session_id` (required): The session ID

**Response:**
```json
{
  "status": "removed",
  "tool": "click",
  "remaining": 4
}
```

**Errors:**
- `not_recording`: No active recording
- `empty`: No steps to remove

---

### `list_recording`

View all currently recorded steps.

**Args:**
- `session_id` (required): The session ID

**Response:**
```json
{
  "name": "login-flow",
  "steps": 5,
  "tools": [
    {"tool": "navigate", "args": {"url": "https://..."}},
    {"tool": "click", "args": {"selector": "button:has-text('Continue')"}},
    {"tool": "fill", "args": {"selector": "#username", "value": "{{EMAIL}}"}},
    ...
  ]
}
```

**Errors:**
- `not_recording`: No active recording

---

### `stop_recording`

Stop recording and save as JSON file.

**Args:**
- `session_id` (required): The session ID
- `description` (optional): Human-readable description
- `variables` (optional): Object mapping variable names to default values

**Variables Format:**
```json
{
  "EMAIL": "ekta@webelight.co.in",
  "PASSWORD": "Test@123",
  "BASE_URL": "https://ob-lms.replit.app"
}
```

**Full JSON Output (`automations/{profile}/{name}.json`):**
```json
{
  "version": 1,
  "name": "ekta5-ob-auth-login",
  "description": "Logs into Office Beacon via Auth0",
  "recorded_at": "2026-07-09T12:00:00Z",
  "profile": "ekta5",
  "reuse_session": true,
  "on_error": "screenshot_and_stop",
  "max_retries": 1,
  "variables": {
    "EMAIL": "ekta@webelight.co.in",
    "PASSWORD": "Test@123",
    "BASE_URL": "https://ob-lms.replit.app"
  },
  "tools": [
    {"tool": "navigate", "args": {"url": "{{BASE_URL}}/auth"}},
    {"tool": "click", "args": {"selector": "button:has-text('Continue with Auth0')"}},
    {"tool": "fill", "args": {"selector": "#username", "value": "{{EMAIL}}"}},
    {"tool": "fill", "args": {"selector": "#password", "value": "{{PASSWORD}}"}},
    {"tool": "execute", "args": {"script": "document.querySelector('button[type=\"submit\"]').click()"}},
    {"tool": "wait_for_url", "args": {"pattern": "{{BASE_URL}}"}}
  ]
}
```

**Response:**
```json
{
  "status": "saved",
  "path": "automations/ekta5/ekta5-ob-auth-login.json",
  "steps": 6
}
```

**Errors:**
- `not_recording`: No active recording
- `empty`: No steps recorded

---

## Agent Workflow

```
1. [PLAN FIRST] Analyze task → Title automation → Present plan → Wait for approval

2. User approves:
   start_recording(session_id, "ekta5-ob-auth-login")
   → Recording started

3. [Run automation tools normally]

4. record_step(session_id, "navigate", {"url": "{{BASE_URL}}/auth"})
   → Step recorded (1/6)

5. record_step(session_id, "click", {"selector": "button:has-text('Continue')"})
   → Step recorded (2/6)

6. [Continue recording only successful needed steps]

7. list_recording(session_id)
   → Review all recorded steps

8. remove_last_step(session_id)  [if mistakes made]
   → Removes last step

9. stop_recording(session_id, "Logs into Office Beacon via Auth0", {"EMAIL": "ekta@...", "PASSWORD": "..."})
   → JSON saved to automations/ekta5/ekta5-ob-auth-login.json
```

## Session Store Changes

```python
class Session:
    def __init__(self, ...):
        self.is_recording = False
        self.recording_name = None
        self.recording_tools = []  # list of {"tool": "...", "args": {...}}
```

## File Output

- Location: `{project_root}/automations/{profile}/{name}.json`
  - Example: `automations/ekta5/ekta5-ob-auth-login.json`
  - If no profile: uses `automations/default/{name}.json`
- Directory created if not exists
- Overwrites existing file with same name
