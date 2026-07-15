# Batch Replay Report - Expandable Cards Implementation

## Overview
Transform the batch replay report from a simple table to professional expandable cards with detailed step-by-step execution breakdown.

## Current State
- Report shows basic stats (total/passed/failed/skipped)
- Module breakdown table
- Failed recordings section with only error messages
- No per-step visibility into what actually happened

## Target State
- Summary cards for ALL recordings (passed + failed)
- Expandable step-by-step execution table per recording
- Color-coded pass/fail indicators
- Per-step timing data
- Selector information for each step

## Implementation Plan

### Phase 1: MCP Side - Data Enrichment

#### 1.1 Add Per-Step Timing to replay.py
**File:** `mcp_tools/replay.py`
**Change:** Wrap `_call_tool_with_retry` calls with timing

```python
# Before tool call:
start_time = time.time()
result, err_msg = await _call_tool_with_retry(session, tool_name, tool_args, max_retries=max_retries)
duration = time.time() - start_time

# Add duration to result
result["duration"] = round(duration, 3)
```

This captures how long each step takes.

#### 1.2 Extract Selectors from Replay Config
**File:** `mcp_tools/batch_replay.py`
**Change:** Parse the recording JSON to extract selectors

```python
# The replay config has structure:
# {"tools": [{"tool": "click", "args": {"selector": "#btn"}}, ...]}
# Extract selectors for each step
tools = recording.get("tools", [])
selectors = [tool.get("args", {}).get("selector", "") for tool in tools]
```

#### 1.3 Build Full Steps Array in batch_replay.py
**File:** `mcp_tools/batch_replay.py`
**Change:** Enrich recording details with full steps array

```python
# After replay completes, build steps array:
steps = []
for i, r in enumerate(results):
    step = {
        "step": i + 1,
        "tool": r.get("tool", "unknown"),
        "selector": selectors[i] if i < len(selectors) else "",
        "status": "passed" if r.get("success") else "failed",
        "duration": r.get("duration", 0),
        "error": r.get("error") if not r.get("success") else None,
    }
    steps.append(step)

# Add to recording detail
recording_detail["steps"] = steps
```

#### 1.4 Update Template Context
**File:** `mcp_tools/batch_replay.py`
**Change:** Pass `all_details` (all recordings) to template

```python
template_ctx = {
    ...
    "all_details": report["details"],  # All recordings, not just failed
    ...
}
```

### Phase 2: UI Side - Expandable Cards

#### 2.1 Replace Failed Recordings Section with All Recordings Cards
**File:** `templates/report.html`
**Change:** New section showing all recordings as cards

```html
<h2>📊 Recording Details</h2>
{% for detail in all_details %}
<div class="recording-card {% if detail.status == 'passed' %}card-passed{% else %}card-failed{% endif %}">
    <!-- Card header with summary -->
    <div class="card-header">
        <h3>{{ detail.name }}</h3>
        <span class="module-badge">{{ detail.module }}</span>
        <span class="status-badge {% if detail.status == 'passed' %}status-passed{% else %}status-failed{% endif %}">
            {{ detail.status|capitalize }}
        </span>
        <span class="duration">{{ detail.duration_seconds }}s</span>
    </div>

    <!-- Progress bar -->
    <div class="progress-bar">
        <div class="progress-fill" style="width: {{ (detail.steps_successful / detail.steps_total * 100) if detail.steps_total > 0 else 0 }}%"></div>
        <span class="progress-text">{{ detail.steps_successful }}/{{ detail.steps_total }} steps</span>
    </div>

    <!-- Expand/collapse button -->
    <button class="expand-btn" onclick="toggleSteps(this)">
        📋 View Steps ▾
    </button>

    <!-- Hidden step details -->
    <div class="steps-container" style="display: none;">
        <table class="steps-table">
            <thead>
                <tr>
                    <th>Step</th>
                    <th>Tool</th>
                    <th>Selector</th>
                    <th>Status</th>
                    <th>Time</th>
                </tr>
            </thead>
            <tbody>
                {% for step in detail.steps %}
                <tr class="step-row {% if step.status == 'passed' %}step-passed{% else %}step-failed{% endif %}">
                    <td>{{ step.step }}</td>
                    <td>{{ step.tool }}</td>
                    <td class="selector">{{ step.selector or '-' }}</td>
                    <td>
                        {% if step.status == 'passed' %}
                            <span class="status-icon">✓</span>
                        {% else %}
                            <span class="status-icon">✗</span>
                        {% endif %}
                    </td>
                    <td>{{ step.duration }}s</td>
                </tr>
                {% if step.error %}
                <tr class="error-row">
                    <td colspan="5">
                        <span class="error-message">{{ step.error }}</span>
                    </td>
                </tr>
                {% endif %}
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
{% endfor %}
```

#### 2.2 Add JavaScript for Expand/Collapse
**File:** `templates/report.html`
**Change:** Add toggle function

```javascript
<script>
function toggleSteps(btn) {
    const container = btn.nextElementSibling;
    const isHidden = container.style.display === 'none';
    container.style.display = isHidden ? 'block' : 'none';
    btn.innerHTML = isHidden ? '📋 Hide Steps ▴' : '📋 View Steps ▾';
}
</script>
```

#### 2.3 Add CSS for Cards and Steps
**File:** `templates/report.html`
**Change:** Professional styling for cards and step tables

```css
.recording-card {
    background: white;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    border-left: 4px solid #ddd;
}

.card-passed { border-left-color: #28a745; }
.card-failed { border-left-color: #dc3545; }

.card-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
}

.card-header h3 { margin: 0; font-size: 1.1em; }

.module-badge {
    background: #e9ecef;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.85em;
}

.status-badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: bold;
    font-size: 0.85em;
}

.status-passed { background: #d4edda; color: #155724; }
.status-failed { background: #f8d7da; color: #721c24; }

.progress-bar {
    background: #e9ecef;
    border-radius: 4px;
    height: 24px;
    position: relative;
    margin-bottom: 12px;
}

.progress-fill {
    background: #28a745;
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s ease;
}

.progress-text {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 0.85em;
    font-weight: bold;
}

.expand-btn {
    background: #f8f9fa;
    border: 1px solid #dee2e6;
    padding: 8px 16px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9em;
    width: 100%;
    text-align: left;
}

.expand-btn:hover { background: #e9ecef; }

.steps-table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 12px;
    font-size: 0.9em;
}

.steps-table th, .steps-table td {
    padding: 8px;
    border-bottom: 1px solid #dee2e6;
    text-align: left;
}

.steps-table th {
    background: #f8f9fa;
    font-weight: 600;
}

.step-passed { background: #f8f9fa; }
.step-failed { background: #fff5f5; }

.error-row td {
    padding: 8px 16px;
    background: #fff5f5;
    color: #721c24;
    font-style: italic;
}

.selector {
    font-family: monospace;
    font-size: 0.85em;
    color: #6c757d;
}
```

### Phase 3: Testing

#### 3.1 Verify Data Structure
- Ensure `steps` array is present in all recording details
- Check that timing data is captured correctly
- Verify selectors are extracted from replay configs

#### 3.2 Test UI Rendering
- Check card layout for passed vs failed recordings
- Verify expand/collapse functionality
- Confirm progress bar displays correctly
- Test with multiple recordings in different modules

#### 3.3 Edge Cases
- Recording with 0 steps (should not divide by zero)
- Recording with all steps failed
- Recording with no selectors (some tools don't have selectors)
- Very long step lists (scrolling behavior)

## Files to Modify
1. `mcp_tools/replay.py` - Add timing to step execution
2. `mcp_tools/batch_replay.py` - Enrich recording details with steps array
3. `templates/report.html` - New expandable card UI with CSS and JS

## Success Criteria
- [ ] All recordings show as expandable cards (not just failed)
- [ ] Each card shows summary with progress bar
- [ ] Clicking "View Steps" expands to show step-by-step table
- [ ] Step table shows tool, selector, status, and duration
- [ ] Failed steps highlighted in red with error message
- [ ] Passed steps highlighted in green/gray
- [ ] No errors in browser console
- [ ] Report still renders correctly for passed recordings