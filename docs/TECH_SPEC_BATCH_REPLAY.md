# Tech Spec: Batch Replay & Recording Manifest

## Overview

Add a manifest-based recording registry and batch replay capability to the QA automation MCP server. This enables:

1. **Recording registration** — Track recordings in a structured manifest with module grouping and dependency tracking
2. **Batch replay** — Execute multiple recordings in dependency-resolved order with parallel execution where possible
3. **Reporting** — Generate pass/fail reports with module-level and recording-level granularity

## Current State

### Existing Infrastructure

| Component | Location | Purpose |
|---|---|---|
| Recording | `mcp_tools/recording.py` | `start_recording`, `record_step`, `stop_recording`, `start_human_recording`, `stop_human_recording` |
| Replay | `mcp_tools/replay.py` | `replay_automation`, `replay_interactions` |
| Session | `mcp_tools/session.py` | `session_start`, `session_close`, `session_list` |
| Session Store | `helpers/session_store.py` | `SessionData` dataclass, async registry |
| Tool Registration | `main.py` | FastMCP with `FileSystemProvider` auto-discovery |

### Existing Output Schemas

**stop_recording output:**
```python
{
    "status": "saved",
    "path": str(filepath),
    "steps": int,
    "extracted_variables": dict
}
```

**Automation JSON format (stop_recording writes):**
```json
{
  "version": 1,
  "name": "login-flow",
  "description": "",
  "recorded_at": "2026-07-14T10:00:00Z",
  "profile": "default",
  "cdp_endpoint": "",
  "reuse_session": true,
  "on_error": "stop",
  "max_retries": 1,
  "variables": { "EMAIL": "user@example.com" },
  "tools": [
    { "tool": "navigate", "args": { "url": "...", "session_id": "..." } }
  ]
}
```

**replay_automation output:**
```python
{
    "name": "login-flow",
    "total": int,
    "completed": int,
    "successful": int,
    "failed": int,
    "status": "success" | "partial_failure",
    "results": [
        { "tool": "navigate", "success": True, "result": {...} },
        { "tool": "click", "success": False, "error": "..." }
    ]
}
```

**replay_interactions output:**
```python
{
    "total": int,
    "successful": int,
    "failed": int,
    "status": "success" | "partial_failure"
}
```

**SessionData fields (session_store.py):**
- `session_id`, `email`, `profile`, `context`, `page`, `current_tab_index`, `tabs`
- `active_frame`, `viewport`, `started_at`
- `console_errors`, `console_messages`, `is_tracing`
- `routes`, `request_history`
- `is_recording`, `recording_name`, `recording_tools`
- `is_human_recording`, `human_recording_name`, `human_recording_events`, `human_recording_cdp_playwright`
- `cdp_endpoint`, `connect_method`, `playwright`, `base_dir`

### Coding Patterns

- **Tool registration:** `@tool` decorator on `async def`, auto-discovered via `FileSystemProvider`
- **Session resolution:** Local `_resolve_session(session_id)` helper, returns `(err, session)` tuple
- **Imports:** `from fastmcp.tools import tool`, `from helpers.session_store import get_session_by_id`, `from pathlib import Path`
- **Return style:** Success returns dict with documented keys, error returns `{"status": "error", "error": "..."}`
- **File paths:** Use `mkdir(parents=True, exist_ok=True)`, filenames include `uuid4().hex[:8]`
- **Variable substitution:** `{{VARIABLE}}` placeholders replaced at replay time via `substitute_placeholders()`

## New Components

### 1. Manifest File

**Location:** `{wiki_root}/manifest.json`

**Schema:**
```json
{
  "name": "login-automation",
  "created_at": "2026-07-14T10:00:00Z",
  "modules": {
    "auth": {
      "label": "Authentication",
      "recordings": [
        {
          "name": "login-flow",
          "path": "recordings/login-flow_a8148726.json",
          "type": "auto",
          "deps": [],
          "module": "auth",
          "created_at": "2026-07-14T10:05:00Z"
        },
        {
          "name": "logout",
          "path": "recordings/logout_b4e8ae28.json",
          "type": "auto",
          "deps": ["login-flow"],
          "module": "auth",
          "created_at": "2026-07-14T10:10:00Z"
        }
      ]
    },
    "hr": {
      "label": "HR Module",
      "recordings": [
        {
          "name": "employees",
          "path": "recordings/employees_c3f1a2b1.json",
          "type": "auto",
          "deps": ["login-flow"],
          "module": "hr",
          "created_at": "2026-07-14T10:15:00Z"
        }
      ]
    }
  },
  "last_run_results": null
}
```

**Field Definitions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Manifest name (wiki folder name) |
| `created_at` | ISO datetime | Yes | When manifest was created |
| `modules` | object | Yes | Map of module_name → module config |
| `modules[].label` | string | No | Human-readable label (auto-filled from module name) |
| `modules[].recordings[]` | array | Yes | List of recording entries |
| `recordings[].name` | string | Yes | Recording name (must be unique across manifest) |
| `recordings[].path` | string | Yes | Relative path from wiki_root to recording JSON |
| `recordings[].type` | enum | Yes | `"auto"` or `"human"` |
| `recordings[].deps` | array | Yes | Array of recording names this depends on |
| `recordings[].module` | string | Yes | Module name this recording belongs to |
| `recordings[].created_at` | ISO datetime | Yes | When recording was registered |
| `last_run_results` | object | No | Latest batch replay results (null if never run) |

### 2. New MCP Tools

#### `register_recording`

**File:** `mcp_tools/manifest.py`

**Signature:**
```python
@tool
async def register_recording(
    wiki_root: str,
    module_name: str,
    recording_path: str,
    type: str,
    deps: list[str] = [],
    label: str | None = None
) -> dict:
    """Register a recording in the manifest.

    Args:
        wiki_root: Path to the automation-wiki folder.
        module_name: Module name (e.g. "auth", "hr").
        recording_path: Relative path to recording JSON from wiki_root.
        type: "auto" or "human".
        deps: Array of recording names this depends on.
        label: Optional human-readable module label.

    Returns:
        {"status": "registered", "entry": {...}} or error.
    """
```

**Logic:**
1. Resolve `wiki_root` to absolute path
2. Check if `manifest.json` exists:
   - If not, create with `{"name": ..., "created_at": now, "modules": {}, "last_run_results": null}`
3. Validate module exists or create it:
   - If module doesn't exist, add `{module_name: {"label": label or module_name, "recordings": []}}`
4. Validate recording name uniqueness (across entire manifest)
5. Validate deps exist (all names in `deps` must be registered)
6. Validate recording file exists at `recording_path`
7. Add entry to `modules[module_name].recordings[]` with `created_at = now`
8. Write manifest back to disk
9. Return `{"status": "registered", "entry": {...}}`

**Error cases:**
- Manifest path invalid → `{"status": "error", "error": "invalid_path"}`
- Recording name already exists → `{"status": "error", "error": "duplicate_name"}`
- Dep reference doesn't exist → `{"status": "error", "error": "dep_not_found"}`
- Recording file doesn't exist → `{"status": "error", "error": "file_not_found"}`

#### `batch_replay`

**File:** `mcp_tools/batch_replay.py`

**Signature:**
```python
@tool
async def batch_replay(
    wiki_root: str,
    module: str | None = None,
    recordings: list[str] | None = None
) -> dict:
    """Run all recordings in dependency-resolved order.

    Args:
        wiki_root: Path to the automation-wiki folder.
        module: Optional module name to replay (if None, run all).
        recordings: Optional list of recording names to replay (if None, run all).

    Returns:
        Report with module-level and recording-level results.
    """
```

**Logic:**
1. Read `manifest.json`
2. Filter recordings:
   - If `module` specified, filter to that module only
   - If `recordings` specified, filter to those names only
3. Build dependency graph:
   - Collect all recordings (filtered)
   - For each recording, collect its `deps`
4. Topological sort with parallel scheduling:
   - Calculate in-degree for each recording
   - Find all recordings with in-degree 0 → Round 1
   - As each recording completes, decrement in-degree of dependents
   - When a recording's in-degree reaches 0, add to next round
   - If a recording fails, mark all its dependents as "skipped"
5. Execute recordings round-by-round:
   - For each round, execute recordings in parallel (or sequentially for simplicity)
   - For each recording:
     - Start fresh browser session (new launch, no CDP)
     - Call `replay_automation` or `replay_interactions` based on `type`
     - Capture duration, pass/fail, detailed results
     - Close session
   - Track round-level duration
6. Write `last_run_results` to manifest
7. Return report

**Report schema:**
```python
{
    "timestamp": "2026-07-14T11:00:00Z",
    "total": int,
    "passed": int,
    "failed": int,
    "skipped": int,
    "duration_seconds": int,
    "modules": {
        "auth": {"passed": 2, "failed": 0, "skipped": 0},
        "hr": {"passed": 2, "failed": 1, "skipped": 1}
    },
    "execution_plan": [
        { "round": 1, "recordings": ["login-flow", "logout"], "duration": 10 },
        { "round": 2, "recordings": ["employees"], "duration": 15 }
    ],
    "details": [
        {
            "name": "login-flow",
            "module": "auth",
            "status": "passed",
            "round": 1,
            "duration_seconds": 5,
            "steps_total": 4,
            "steps_successful": 4,
            "steps_failed": 0
        },
        {
            "name": "employees",
            "module": "hr",
            "status": "failed",
            "round": 2,
            "duration_seconds": 15,
            "steps_total": 8,
            "steps_successful": 7,
            "steps_failed": 1,
            "failed_events": [
                { "event_index": 6, "event_type": "click", "error": "Selector not found: .employee-list" }
            ]
        }
    ]
}
```

#### `list_recordings`

**File:** `mcp_tools/manifest.py`

**Signature:**
```python
@tool
async def list_recordings(wiki_root: str) -> dict:
    """List all recordings in the manifest.

    Args:
        wiki_root: Path to the automation-wiki folder.

    Returns:
        {"modules": {...}, "total": int} or error.
    """
```

**Logic:**
1. Read `manifest.json`
2. Return `{modules: ..., total: len(all_recordings)}` or error

### 3. Enhanced `replay_interactions` Output

**File:** `mcp_tools/replay.py`

**Current output:**
```python
{
    "total": int,
    "successful": int,
    "failed": int,
    "status": "success" | "partial_failure"
}
```

**New output (backward compatible):**
```python
{
    "total": int,
    "successful": int,
    "failed": int,
    "status": "success" | "partial_failure",
    "failed_events": [  # NEW
        { "event_index": 6, "event_type": "click", "error": "..." }
    ]
}
```

**Changes:**
- Add `failed_events` array to output
- Track which event index failed and what the error was
- Backward compatible: existing callers still get `failed` count

### 4. Skill Update

**File:** `~/.claude/skills/intelligent-automation-v2-new/references/step_5_recording.md`

**Add after `stop_recording`:**
```
5b. Register Recording (Optional)

After `stop_recording()` succeeds, ask the user: "Want to register this recording?"

- If yes → call `register_recording(wiki_root, module_name, recording_path, type, deps)`
  - Agent infers module from context (e.g., "auth" for login, "hr" for employees)
  - Agent infers deps from manifest (e.g., if recording requires login, dep on "login-flow")
  - Agent presents inferred deps to user for confirmation
- If no → skip

Wait for user confirmation before proceeding.
```

## Implementation Plan

### Phase 1: Core Infrastructure

1. **Create `mcp_tools/manifest.py`**
   - `register_recording()` function
   - `list_recordings()` function
   - Helper functions: `_load_manifest()`, `_save_manifest()`, `_validate_recording()`

2. **Create `mcp_tools/batch_replay.py`**
   - `batch_replay()` function
   - Helper functions: `_build_dep_graph()`, `_topological_sort()`, `_execute_round()`

3. **Update `mcp_tools/replay.py`**
   - Add `failed_events` field to `replay_interactions` output
   - Track event-level failures in `_do_replay_interactions()`

### Phase 2: Testing

1. **Unit tests for manifest operations**
   - Register recording (valid)
   - Register recording with duplicate name
   - Register recording with invalid dep reference
   - Register recording with missing file

2. **Unit tests for batch replay**
   - Empty manifest
   - Single recording
   - Multiple recordings with deps
   - Recording with failed dep (should skip)
   - Human recording with event-level failures

3. **Integration test**
   - Full flow: register → batch_replay → verify report

### Phase 3: Skill Update

1. **Update `step_5_recording.md`**
   - Add "Register Recording" step after `stop_recording`
   - Document agent's role in inferring module and deps

## File Structure

```
qa-automation-mcp-server/
  mcp_tools/
    __init__.py
    manifest.py          # NEW: register_recording, list_recordings
    batch_replay.py      # NEW: batch_replay
    recording.py         # Existing (unchanged)
    replay.py            # Modified: add failed_events to human replay output
    session.py           # Existing (unchanged)
  helpers/
    session_store.py     # Existing (unchanged)
  docs/
    TECH_SPEC_BATCH_REPLAY.md  # This document
```

## Dependencies

- Python stdlib only: `json`, `pathlib`, `datetime`, `collections.defaultdict`
- Existing: `fastmcp`, `playwright`, `helpers.session_store`
- No new external dependencies

## Edge Cases

1. **Empty manifest** — `batch_replay` should handle gracefully (return empty report)
2. **Circular dependencies** — Topological sort will detect and raise error
3. **Missing recording file** — `register_recording` validates file exists
4. **Missing dep reference** — `register_recording` validates all deps exist
5. **Concurrent manifest writes** — Use file locking or single-threaded writes (simple)
6. **Fresh browser per recording** — Use `session_start` with `connect_method="launch"`, close after each
7. **Variable substitution in batch mode** — Use defaults from recording's `variables` field, no user overrides
8. **Human recording event-level errors** — Capture `event_index`, `event_type`, `error_message`
9. **Manifest file corruption** — Catch JSON parse errors, return `{"status": "error", "error": "invalid_manifest"}`
10. **Large manifest (100+ recordings)** — Sequential execution is fine, parallel within rounds if needed

## Success Criteria

1. `register_recording` correctly adds entries to manifest
2. `batch_replay` executes recordings in dependency-resolved order
3. Failed dependencies correctly skip dependent recordings
4. Report includes module-level and recording-level results
5. Human recording failures include event-level detail
6. Fresh browser per recording (no state leakage)
7. Manifest auto-created if missing
8. Skill agent asks user after `stop_recording` whether to register

## Open Questions

1. **Selective replay** — Should `batch_replay` support `module` and `recordings` filters? (Yes, included in spec)
2. **Manifest editing** — Should we add `update_recording` and `remove_recording` tools? (Out of scope for now)
3. **Replay history** — Should we keep multiple runs or just `last_run_results`? (Just `last_run_results` for now)
4. **Credential prompting** — Should batch replay prompt for credentials if recording has variables? (Use defaults from recording, no prompting)
5. **Manual manifest editing** — Should users be able to edit manifest.json directly? (Not supported, use tools)
6. **Cleanup** — Should we add `purge_missing` to remove entries where file doesn't exist? (Out of scope for now)



Implementation Plan
Phase 1: Manifest Infrastructure (3 files)
Step 1: Create mcp_tools/manifest.py

register_recording(wiki_root, module_name, recording_path, type, deps, label?)
list_recordings(wiki_root)
Helpers: _load_manifest(), _save_manifest(), _validate_recording()
Step 2: Create test for manifest operations

Register valid recording
Register duplicate name (should fail)
Register with missing dep (should fail)
Register with missing file (should fail)
Phase 2: Enhanced Replay (1 file)
Step 3: Update mcp_tools/replay.py

Add failed_events array to _do_replay_interactions() output
Track which event index failed, event type, error message
Keep existing failed count for backward compatibility
Step 4: Test human replay with failures

Verify failed_events captures event-level detail
Phase 3: Batch Replay (1 file)
Step 5: Create mcp_tools/batch_replay.py

batch_replay(wiki_root, module?, recordings?)
Helpers: _build_dep_graph(), _topological_sort(), _execute_round()
Step 6: Implement dependency resolution

Calculate in-degree for each recording
Find recordings with in-degree 0 → Round 1
Execute round, track pass/fail
Skip recordings if any dep failed
Step 7: Implement fresh browser per recording

Use session_start with connect_method="launch"
Close session after each recording
Step 8: Generate report

Module-level pass/fail counts
Recording-level details with durations
failed_events for human recordings
Write to last_run_results in manifest
Phase 4: Skill Update (1 file)
Step 9: Update references/step_5_recording.md

Add "Register Recording" step after stop_recording
Document agent's role in inferring module/deps
Ask user "Want to register this recording?"
Phase 5: Integration Testing
Step 10: End-to-end test

Record login flow → register
Record employees → register (with dep on login)
Run batch_replay → verify report
Step 11: Edge case tests

Empty manifest
Circular dependencies (should error)
Failed dependency (dependent should skip)
Human recording with event failures
Order of Execution

Step 1 → Step 2 → Step 3 → Step 4 → Step 5 → Step 6 → Step 7 → Step 8 → Step 9 → Step 10 → Step 11
Each step builds on the previous one. No parallelization needed.

Time Estimate
Steps 1-2: ~30 min (manifest logic + tests)
Steps 3-4: ~20 min (replay enhancement)
Steps 5-8: ~60 min (batch replay core)
Step 9: ~10 min (skill update)
Steps 10-11: ~30 min (integration tests)
Total: ~2.5 hours

Want me to start with Step 1?