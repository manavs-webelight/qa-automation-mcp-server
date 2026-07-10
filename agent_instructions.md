# Recording Workflow — Required Steps

The recording tools have been updated. **Follow this exact sequence.** Deviations will fail.

## The 3-Step Pattern

```
1. start_recording(session_id="...", recording_name="login-flow")
2. record_step(session_id="...", tool_name="navigate", args={"url": "...", "session_id": "..."})
3. stop_recording(session_id="...")
```

## Critical Rules

### 1. `start_recording` — parameter is `recording_name`, NOT `description`

```
# CORRECT
start_recording(session_id="sess_abc", recording_name="login-flow")

# WRONG — this parameter doesn't exist
start_recording(session_id="sess_abc", description="login-flow")
```

### 2. `record_step` — call AFTER every browser action

The server does NOT auto-capture tool calls. **You must explicitly record each one.**

The `args` parameter receives the tool's arguments as a flat dict. Do NOT nest them.

```
# CORRECT — navigate
record_step(
    session_id="sess_abc",
    tool_name="navigate",
    args={"url": "http://localhost:3000", "session_id": "sess_abc"}
)

# CORRECT — fill
record_step(
    session_id="sess_abc",
    tool_name="fill",
    args={
        "selector": "input[type='email']",
        "value": "user@example.com",
        "session_id": "sess_abc"
    }
)

# CORRECT — click
record_step(
    session_id="sess_abc",
    tool_name="click",
    args={"selector": "button:has-text('Sign In')", "session_id": "sess_abc"}
)
```

### 3. `stop_recording` — requires at least one recorded step

Calling this on an empty recording returns `{"status": "error", "error": "empty"}`.

```
# CORRECT
stop_recording(session_id="sess_abc")

# WITH variable overrides (optional)
stop_recording(
    session_id="sess_abc",
    variables={"EMAIL": "user@example.com", "PASSWORD": "secure123!"}
)
```

## What Changed

- `name` parameter renamed to `recording_name` — unambiguous
- `description` parameter removed from `stop_recording` — not needed
- `on_error` default fixed from `"screenshot_and_stop"` to `"stop"`
- Debug print statements removed
- Docstrings updated with explicit call examples

## What Was Your Fault

- **Empty recording:** You didn't call `record_step` after browser actions. The server can't auto-capture.
- **`description` confusion:** The parameter was `name`, not `description`. The function signature is clear — `start_recording(session_id, name, cdp_endpoint?)`.
- **`record_step` format:** The MCP framework passes arguments correctly. Calling `record_step(session_id="...", tool_name="...", args={...})` works as expected. The "wrapping" issue you described doesn't exist.

## Next Time

Follow the 3-step pattern exactly. Don't guess the API — read the docstring. If you're unsure about a parameter name or shape, check the tool definition before calling it.