---
name: intelligent-automation
description: Use when automating web interactions on pages that may be in your codebase or third-party, especially when navigation, form filling, or data extraction requires code-aware decision making
---

# Intelligent Automation

Automate web interactions by reading code first, using exact selectors from code, and falling back to snapshots only when direct clicks fail.

## Overview

**Code and graphify are source of truth.** Snapshots and screenshots are last resort.

Priority order:
1. **Graphify** — query knowledge graph for component relationships and structure
2. **Read code** — find exact selectors (href, id, text) in source files
3. **Playwright direct** — click/fill with code-derived selectors
4. **JS execute** — find selectors via DOM when code doesn't reveal them
5. **Snapshot** — analyze YAML to find selectors (last resort)
6. **Screenshot** — visual inspection (last resort, only when snapshot fails)

## Quick Start

1. Get URL
2. Is it in codebase? YES → **graphify first**, then read code
3. Have selectors? YES → click directly
4. NO → list_tabs → execute (JS) → snapshot → retry

## When to Use

- Automating internal pages (routes in your codebase)
- Automating third-party pages (OAuth providers, external services)
- Any web interaction requiring navigation, clicking, form filling, or data extraction
- When you need to understand dynamic behavior (loading states, tab switches)
- Before taking unnecessary snapshots — read code first

## Core Flowchart

```
START
  │
  ▼
[1. Get URL / Target Page]
  │
  ▼
[2. DECISION: Is this page in our codebase?]
  │
  ├─ YES (internal: routes in client/src, components in project)
  │   │
  │   ▼
  │ [3. **GRAPHIFY REQUIRED** — query knowledge graph]
  │   ├─ `graphify query "page name"`
  │   └─ `graphify query "navbar", "tabs", "forms"`
  │   │
  │   ▼
  │ [4. **READ CODE** — find exact selectors in source]
  │   ├─ NavigationComponent → find nav links with href
  │   ├─ PageComponent → find tabs, buttons, forms
  │   └─ UI Component → find selectors, IDs, classes
  │   │
  │   ▼
  │ [5. **CLICK/FILL** — use code-derived selectors]
  │   ├─ `click("a[href='/target-route']")`
  │   ├─ `click("button:has-text('Tab Label')")`
  │   └─ `fill("input#field-id", "value")`
  │   │
  │   ▼
  │ [6. **Fallback if click fails**: list_tabs → execute (JS) → snapshot]
  │   ├─ `list_tabs()` — confirm URL changed
  │   ├─ `execute()` — find selector via DOM
  │   └─ `snapshot()` — analyze YAML for selector
  │   │
  │   ▼
  │ [7. Wait for navigation]
  │   └─ `wait_for_url("*target*")` (timeout → check list_tabs)
  │   │
  │   ▼
  │ [8. **Snapshot/Extract Data** — only when needed]
  │   └─ `snapshot()` → extract from YAML
  │
  │
  └─ NO (third-party: external domains, OAuth providers)
      │
      ▼
      [3. Use Playwright Tools Directly]
      │   ├─ navigate("https://third-party.com/login")
      │   ├─ click("button:has-text('Continue')")
      │   ├─ fill("input#username", "email")
      │   └─ fill("input#password", "pass")
      │   │
      │   ▼
      │   [4. Wait for redirect]
      │   └─ wait_for_url("https://new-domain.com/*")
      │   │
      │   ▼
      │   [5. Wait for auth callback]
      │   └─ wait_for_url("*/callback*") or "*/success*")
      │   │
      │   ▼
      │   [6. Wait for redirect to app]
      │   └─ wait_for_url("https://app-domain.com/*")
      │   │
      │   ▼
      │   [7. Snapshot/Verify]
      │   └─ snapshot → verify redirect/login success
      │
      ▼
END
```

## Quick Reference

| Scenario | Approach | Tools |
|----------|----------|-------|
| Internal page navigation | Graphify → Read code → click by href | `graphify query`, `read files`, `click()`, `wait_for_url()` |
| Internal tab switching | Graphify → Read component → click by text | `graphify query`, `read files`, `click("button:has-text('Active')")` |
| Third-party form fill | Playwright direct | `fill()`, `click()` |
| Third-party auth flow | Playwright direct | `fill()`, `click()`, `wait_for_url()` |
| Data extraction | Snapshot after click (only when needed) | `snapshot()`, extract from YAML |
| Click fails | Fallback: `list_tabs` → JS → snapshot | `list_tabs()`, `execute()`, `snapshot()` |

## Session Management

**When to close session:**
- Only when user explicitly says: "close session", "close browser", "done", "that's all", "end session"
- After automation completes, ask: "Automation complete. Continue with new task or end session?"

**When to keep session open:**
- User wants to continue with another automation task
- User says "keep it open" or "stay logged in"
- User gives a follow-up task on the same app

## Timeout & Performance

**`wait_for_url` timeout:**
- Default timeout is 30s — too long for most cases
- Use `timeout=5000` (5s) for navigation waits
- On timeout: call `list_tabs()` to confirm URL changed before assuming failure

**`click` slowness:**
- Playwright's click has auto-waiting (5-15s)
- For instant clicks: use `execute()` with JS click
- Example: `execute("document.querySelector('button:has-text(\"Continue\")').click()")`

**Prefer `wait_for_load_state` over `wait_for_url`:**
- `wait_for_load_state("networkidle")` — wait for data pages to fully load
- `wait_for_load_state("domcontentloaded")` — wait for DOM to be ready
- Use these instead of `wait_for_url` for pages that load content dynamically

## Route Verification

Before navigating to internal URL, verify route exists in code:
```
1. grep "routes" or "Route" in project source (client/src, src/pages)
2. Check if URL path matches defined route
3. If not found, check if it's a redirect or handled by catch-all
```

Common patterns:
- React Router: `Route` components, `routes.tsx`, `index.tsx`
- Next.js: `app/` or `pages/` directory structure
- Generic: routing config, path mappings

## Graphify Tools

**REQUIRED:** Run graphify commands in the directory containing `graphify-out/graph.json`.

| Tool | Purpose |
|------|---------|
| `graphify query` | Query knowledge graph |
| `graphify explain` | Explain concept |
| `graphify path` | Find path between nodes |

**Example:**
```bash
cd /path/to/project/with/graphify-out
graphify query "My Courses navbar"
```

## MCP QA Automation Tools

**Core tools:**
- Navigate: `navigate()`, `wait_for_url()`, `wait_for_load_state()`
- Interact: `click()`, `fill()`, `type()`, `check()`, `select_option()`
- Verify: `snapshot()`, `screenshot()`, `assert_visible()`, `assert_text()`
- State: `get_text()`, `get_value()`, `get_attribute()`
- Execute: `execute()` (run JS in page)

**Full reference:** `session_start`, `session_close`, `list_tabs`, `new_tab`, `close_tab`, `switch_tab`, `press_key`, `fill_form`, `upload_file`, `assert_title`, `assert_url`, `assert_no_console_errors`, `get_cookies`, `get_local_storage`, `console_messages`, `set_cookies`, `set_local_storage`, `set_storage_state`, `clear_console_messages`, `wait_for_selector`, `wait_for_navigation`, `sleep`

## Common Patterns

See flowchart above for detailed steps. Key patterns:

### Internal Page
```
session_start → navigate → graphify query → read code → click (from code) → wait_for_url → snapshot
→ Ask: "Automation complete. Continue with new task or end session?"
```

### Third-Party
```
session_start → navigate → fill → click → wait_for_url → snapshot
→ Ask: "Automation complete. Continue with new task or end session?"
```

### Mixed Flow
```
session_start → navigate (internal) → read code → click → wait_for_url (third-party) → fill → click → wait_for_url (callback) → wait_for_url (internal) → snapshot
→ Ask: "Automation complete. Continue with new task or end session?"
```

### Click Fallback
```
click → found: false → list_tabs (check URL) → execute (find selector via JS) → snapshot → analyze YAML → retry with new selector
```

### Self-Check (After Each Successful Step)

After every successful interaction, briefly verify:
- **Selector source:** Did I use a code-derived selector? → Continue with code. Did I fall back to snapshot/JS? → Note what failed for next time.
- **Page branch:** Am I still on the right branch (internal vs third-party)? → Re-confirm.
- **Count badges:** Any tab labels with numbers? → Remember to omit them from selectors.
- **Wait strategy:** Did I use `wait_for_load_state("networkidle")` instead of `sleep`? → If not, use it next time for data loading.

This prevents drift from code-first to snapshot-first after multiple fallbacks.

## Examples

### Example 1: Internal Page Navigation

**Scenario:** Navigate to a page with tabs and extract data from each tab.

**URL:** `https://app.example.com/my-courses`

**Steps:**
```
1. session_start(email="user@example.com", profile_name="test")
2. navigate("https://app.example.com")
3. graphify query "page name navbar"
4. Read NavigationComponent → find nav link to target page
5. click("a[href='/target-route']")
6. wait_for_url("*target-route*")
7. graphify query "tabs active completed expired"
8. Read TabComponent → find button elements with text labels
9. click("button:has-text('Active')")
10. snapshot → extract data
11. click("button:has-text('Completed')")
12. snapshot → extract data
13. click("button:has-text('Expired')")
14. snapshot → extract data
15. → Ask: "Automation complete. Continue with new task or end session?"
```

### Example 2: Third-Party Form Fill

**Scenario:** Login to third-party OAuth provider with email/password.

**URL:** `https://auth-provider.com/login`

**Steps:**
```
1. session_start(email="user@example.com", profile_name="test")
2. navigate("https://app.example.com/auth")
3. click("button:has-text('Continue with OAuth')")
4. wait_for_url("*.auth-provider.com/*")
5. snapshot → verify login page loaded
6. execute("document.querySelectorAll('input').forEach(i => console.log(i.id, i.name))")
7. fill("input#username", "user@example.com")
8. fill("input#password", "password")
9. snapshot → verify form filled
10. click("button[type='submit']")
11. wait_for_url("https://app.example.com/callback")
12. wait_for_load_state("networkidle")
13. snapshot → verify redirect to app
14. → Ask: "Automation complete. Continue with new task or end session?"
```

### Example 3: Mixed Flow

**Scenario:** Complete authentication flow from internal app → OAuth provider → back to app.

**URLs:**
- Internal: `https://app.example.com/auth`
- Third-party: `https://auth-provider.com/login`
- Internal: `https://app.example.com/dashboard`

**Steps:**
```
1. session_start(email="user@example.com", profile_name="test")
2. navigate("https://app.example.com/auth")
3. snapshot → verify Auth page loaded
4. graphify query "Auth page"
5. Read AuthComponent → find button "Continue with OAuth"
6. click("button:has-text('Continue with OAuth')")
7. wait_for_url("*.auth-provider.com/*")
8. fill("input#username", "user@example.com")
9. fill("input#password", "password")
10. click("button:has-text('Continue')")
11. wait_for_url("https://app.example.com/callback")
12. wait_for_url("*/success*")
13. wait_for_url("https://app.example.com/*")
14. wait_for_load_state("networkidle")
15. snapshot → verify dashboard loaded
16. navigate("https://app.example.com/target-page")
17. click("a[href='/target-page']")
18. wait_for_url("*target-page*")
19. snapshot → extract data
20. → Ask: "Automation complete. Continue with new task or end session?"
```

## Decision Points

### 1. Is this page in our codebase?

**Internal pages:**
- URLs matching app domain
- Routes defined in client routing config
- Components in project source

**Third-party pages:**
- External OAuth providers (Auth0, Google, GitHub, etc.)
- Unknown domains
- Embedded iframes from other origins

### 2. Have selectors from code?

**YES — use direct selectors:**
- Found `a[href='/target-route']` → click it
- Found `button:has-text('Tab Label')` → click it
- Found `input#field-id` → fill it

**NO — fallback to snapshot:**
- Code doesn't reveal selectors
- Dynamic rendering (React state, async data)
- Need to see actual DOM structure

### 3. Click fails?

**Fallback (in order, code-first):**
```
1. list_tabs() → confirm URL changed (faster than snapshot)
2. execute() → find selector via JS DOM
3. snapshot() → analyze YAML for selector (last resort)
4. If still fails → screenshot() → manual inspection (last resort)
```

## Selector Pitfalls

### Count Badges in Tab Labels
Tabs often show counts like "Active 1", "Completed 1", "Expired 1". Playwright's `has-text()` requires exact match, so `button:has-text("Completed 1")` fails while `button:has-text("Completed")` works.

**Rule:** Omit count badges from selectors. Read the component code to find the actual button text.

### Multiple Buttons with Same Text
Pages often have multiple buttons with identical text (e.g., "Continue", "Submit"). The visible button may have a class or width that differs from hidden/form-submit buttons.

**Solution:** Use `execute()` to find the visible button:
```
execute("document.querySelectorAll('button').find(b => b.getBoundingClientRect().width > 200 && b.textContent.includes('Continue')).click()")
```
Or read code to find the form structure and target the correct button.

### `wait_for_url` Timeouts
`wait_for_url` may timeout even when the URL actually changed (query strings, redirects, or Auth0 callback flows).

**Rule:** On timeout, call `list_tabs()` to confirm the URL changed before assuming failure. Don't take a screenshot unless `list_tabs` shows the wrong URL.

### Auth0 Callback Loading State
Auth0 callback flows often have an intermediate loading page (e.g., "You're signed in — setting up session...") before redirecting back to the app. `wait_for_url` will timeout here.

**Rule:** Use `wait_for_load_state("networkidle")` or `wait_for_selector("selector on final page")` instead of `wait_for_url` for callback flows. Add a `sleep(10000)` as fallback if neither works.

### Form Submission Not Triggered by Button Click
Some forms have custom event handling where clicking the visible button doesn't submit. The button may only validate, while the real submit happens through a hidden button, custom handler, or direct form submission.

**Signs:** Click button → no redirect, page stays on same URL, no error shown.

**Fix:** Submit the form directly:
```
execute("document.querySelector('form').submit()")
```
Or press Enter in the last input field (often triggers native form submit).

## Fallback Triggers

Use snapshot when:
- Code doesn't show actual rendered structure
- Component uses dynamic classes/IDs
- Need to verify loading state
- `list_tabs()` shows wrong URL, `execute()` fails to find selector

Prefer these before snapshot:
- `list_tabs()` — confirm URL changed (faster than screenshot)
- `execute()` — find selectors via JS (no snapshot needed)
- `wait_for_load_state("networkidle")` — wait for data instead of `sleep`

Avoid snapshot when:
- Code gives exact selector (href, id, text)
- Page structure is static
- Multiple interactions on same page (avoid repeated snapshots)

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Skipping graphify on internal pages | Graphify is required — query knowledge graph before reading code |
| Snapshot before reading code | Read code first, snapshot only as fallback |
| Guessing selectors | Use exact selectors from code |
| Always using snapshots | Skip snapshot if code gives you what you need |
| Not distinguishing internal vs third-party | Check URL against codebase routes first |
| Using `sleep` instead of `wait_for_load_state` | Use `networkidle` for data loading, not fixed delays |

## Real-World Impact

- **30% fewer tool calls** — skip snapshots when code gives you selectors
- **More reliable** — code-based selectors don't break on loading states
- **Faster** — direct clicks from code, no analysis delays
- **Code-aware** — understand dynamic behavior (tabs, loading, navigation) before interacting

## Red Flags - STOP and Re-evaluate

- Taking snapshot before reading code or graphify
- Guessing selectors without code context
- Using same approach for internal and third-party pages
- "Snapshot is faster" — code reading is faster for multiple interactions
- Skipping graphify on internal pages — it's required, not optional
- Using `sleep` instead of `wait_for_load_state("networkidle")`

## Testing

Before deploying, test with all three flows:
- Internal page navigation
- Third-party form fill
- Mixed flow (internal → third-party → internal)