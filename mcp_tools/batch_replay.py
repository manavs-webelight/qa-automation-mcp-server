"""Batch replay tools — execute multiple recordings in dependency-resolved order.

Three tools:

- ``batch_replay_all`` — Run ALL recordings across all modules, fresh session per recording
- ``batch_replay_all_standalone_session`` — Run ALL recordings in ONE session (no dep replay)
- ``replay_specific`` — Run specific module or recordings WITH dependency replay

All tools load the manifest, resolve dependencies via topological sort, and generate reports.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id, register_session
from mcp_tools.manifest import _load_manifest
from mcp_tools.replay import replay_automation, replay_interactions
from mcp_tools.logging_utils import _log_action


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_failed_events(
    explicit_failed: list[dict] | None, raw_results: list[dict] | None
) -> list[dict]:
    """Normalize failed events to {event_index, event_type, error} shape."""
    if explicit_failed:
        normalized = []
        for i, ev in enumerate(explicit_failed):
            normalized.append(
                {
                    "event_index": ev.get("event_index", i),
                    "event_type": ev.get("event_type", ev.get("tool", "unknown")),
                    "error": ev.get("error", "Step failed"),
                }
            )
        return normalized

    failed = []
    if raw_results:
        for i, r in enumerate(raw_results):
            if r.get("success") is False:
                failed.append(
                    {
                        "event_index": i,
                        "event_type": r.get("tool", "unknown"),
                        "error": r.get("error")
                        or ("Element not found"
                            if r.get("result", {}).get("found") is False
                            else r.get("result", {}).get("message")
                            or f"{r.get('tool', 'unknown')} failed"),
                    }
                )
    return failed


async def _run_single_recording(
    session_id: str,
    recording: dict,
    wiki_root: Path,
) -> dict:
    """Execute a single recording in an existing session (no dependency traversal)."""
    import time

    start_time = time.time()
    recording_path = wiki_root / recording["path"]

    try:
        if recording["type"] == "auto":
            result = await replay_automation(
                automation_path=str(recording_path),
                session_id=session_id,
            )
        else:  # human
            result = await replay_interactions(
                input_path=str(recording_path),
                session_id=session_id,
            )

        duration = time.time() - start_time

        if recording["type"] == "auto":
            selectors = _extract_selectors(recording_path)
            steps = _build_steps_array(result.get("results", []), selectors)
            return {
                "name": recording["name"],
                "module": recording["module"],
                "status": "passed" if result.get("status") == "success" else "failed",
                "duration_seconds": round(duration, 2),
                "steps_total": result.get("total", 0),
                "steps_successful": result.get("successful", 0),
                "steps_failed": result.get("failed", 0),
                "failed_events": _build_failed_events(
                    result.get("failed_events"), result.get("results")
                ),
                "executed_deps": [],
                "steps": steps,
            }
        else:
            return {
                "name": recording["name"],
                "module": recording["module"],
                "status": "passed" if result.get("status") == "success" else "failed",
                "duration_seconds": round(duration, 2),
                "steps_total": result.get("total", 0),
                "steps_successful": result.get("successful", 0),
                "steps_failed": result.get("failed", 0),
                "failed_events": result.get("failed_events", []),
                "executed_deps": [],
                "steps": [],
            }

    except Exception as e:
        duration = time.time() - start_time
        return {
            "name": recording["name"],
            "module": recording["module"],
            "status": "failed",
            "duration_seconds": round(duration, 2),
            "steps_total": 0,
            "steps_successful": 0,
            "steps_failed": 0,
            "failed_events": [{"event_index": 0, "event_type": "unknown", "error": str(e)}],
            "executed_deps": [],
            "steps": [],
        }


def _build_dep_graph(recordings: list[dict]) -> tuple[dict, dict]:
    """Build dependency graph from recordings."""
    graph = {r["name"]: [] for r in recordings}
    in_degree = {r["name"]: 0 for r in recordings}

    for recording in recordings:
        name = recording["name"]
        for dep in recording.get("deps", []):
            if dep in graph:
                graph[dep].append(name)
                in_degree[name] += 1

    return graph, in_degree


def _expand_module_deps(recordings: list[dict], modules: dict) -> list[dict]:
    """Expand module-level deps to recording-level deps."""
    module_to_recordings: dict[str, list[str]] = {}
    for module_name, module in modules.items():
        module_to_recordings[module_name] = [r["name"] for r in module.get("recordings", [])]

    recording_names = {r["name"] for r in recordings}

    expanded = []
    for recording in recordings:
        deps = recording.get("deps", [])
        resolved = []
        for dep in deps:
            if dep in recording_names:
                resolved.append(dep)
            elif dep in module_to_recordings:
                resolved.extend(module_to_recordings[dep])
        expanded.append({**recording, "deps": resolved})
    return expanded


def _topological_sort_rounds(recordings: list[dict], modules: dict | None = None) -> list[list[dict]]:
    """Sort recordings into rounds based on dependencies."""
    if modules:
        recordings = _expand_module_deps(recordings, modules)

    graph, in_degree = _build_dep_graph(recordings)
    recording_map = {r["name"]: r for r in recordings}
    rounds = []
    remaining = set(r["name"] for r in recordings)

    while remaining:
        current_round = [
            recording_map[name]
            for name in remaining
            if in_degree[name] == 0
        ]

        if not current_round:
            raise ValueError(f"Circular dependency detected among: {remaining}")

        rounds.append(current_round)
        remaining -= {r["name"] for r in current_round}

        for recording in current_round:
            for dependent in graph[recording["name"]]:
                in_degree[dependent] -= 1

    return rounds


def _extract_selectors(recording_path: Path) -> list[str]:
    """Extract selectors from automation JSON file."""
    try:
        with open(recording_path) as f:
            rec_data = json.load(f)
        tools = rec_data.get("tools", [])
        return [tool.get("args", {}).get("selector", "") for tool in tools]
    except (OSError, json.JSONDecodeError):
        return []


def _build_steps_array(results: list[dict], selectors: list[str]) -> list[dict]:
    """Build full steps array from replay results and selectors."""
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
    return steps


async def _execute_recording(
    session_id: str,
    recording: dict,
    wiki_root: Path,
    modules: dict | None = None,
    _executed: set | None = None,
) -> dict:
    """Execute a single recording, running dependencies first in the same session."""
    import time

    if _executed is None:
        _executed = set()

    if recording["name"] in _executed:
        return {
            "name": recording["name"],
            "module": recording["module"],
            "status": "passed",
            "round": None,
            "duration_seconds": 0,
            "steps_total": 0,
            "steps_successful": 0,
            "steps_failed": 0,
            "failed_events": None,
            "executed_deps": [],
        }

    # Find dependency recordings
    dep_recordings = []
    if modules:
        for dep_name in recording.get("deps", []):
            dep_recording = None
            for mod_name, mod in modules.items():
                for r in mod.get("recordings", []):
                    if r["name"] == dep_name:
                        dep_recording = r
                        break
                if dep_recording:
                    break
            if dep_recording:
                dep_recordings.append(dep_recording)

    print(f"REPLAY: Recording '{recording['name']}' (module={recording.get('module')}) deps={recording.get('deps', [])} -> {len(dep_recordings)} dep(s) found")

    # Execute dependencies first
    for dep_recording in dep_recordings:
        print(f"REPLAY: Running dep '{dep_recording['name']}' before '{recording['name']}'")
        await _execute_recording(session_id, dep_recording, wiki_root, modules, _executed)

    _executed.add(recording["name"])

    if dep_recordings:
        await asyncio.sleep(1.0)

    result = await _run_single_recording(session_id, recording, wiki_root)
    result["executed_deps"] = [d["name"] for d in dep_recordings]
    return result


async def _execute_round(
    round_recordings: list[dict],
    wiki_root: Path,
    modules: dict | None = None,
) -> tuple[list[dict], bool]:
    """Execute a round of recordings (each gets a fresh session)."""
    results = []

    for recording in round_recordings:
        from mcp_tools.session import session_start

        session_result = await session_start(
            email=f"batch_{recording['name']}_{datetime.utcnow().timestamp()}",
            base_dir=str(wiki_root),
        )
        if session_result.get("status") != "ready":
            results.append({
                "name": recording["name"],
                "module": recording["module"],
                "status": "failed",
                "round": None,
                "duration_seconds": 0,
                "steps_total": 0,
                "steps_successful": 0,
                "steps_failed": 0,
                "failed_events": [{"event_index": 0, "event_type": "unknown", "error": "Failed to start session"}],
            })
            continue

        try:
            result = await _execute_recording(session_result["session_id"], recording, wiki_root, modules)
            results.append(result)
        except Exception as e:
            results.append({
                "name": recording["name"],
                "module": recording["module"],
                "status": "failed",
                "round": None,
                "duration_seconds": 0,
                "steps_total": 0,
                "steps_successful": 0,
                "steps_failed": 0,
                "failed_events": [{"event_index": 0, "event_type": "unknown", "error": str(e)}],
            })
        finally:
            from mcp_tools.session import session_close
            await session_close(session_id=session_result["session_id"])

    all_passed = all(r["status"] == "passed" for r in results)
    return results, all_passed


def _export_report(wiki_root_path: Path, report: dict, prefix: str = "report") -> dict:
    """Export HTML report and update manifest."""
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path as P

    template_dir = P(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("report.html")

    failed_details = [d for d in report["details"] if d.get("status") == "failed"]
    skipped_details = []
    for d in report["details"]:
        if d.get("status") == "skipped":
            deps = d.get("deps", [])
            skip_reason = f"Dependency failed: {', '.join(deps)}" if deps else "Dependency failed"
            skipped_details.append({**d, "skip_reason": skip_reason})

    template_ctx = {
        "timestamp": report.get("timestamp", "Unknown"),
        "wiki_root": str(wiki_root_path),
        "duration_human": f"{report['duration_seconds']:.0f}s",
        "total": report["total"],
        "passed": report["passed"],
        "failed": report["failed"],
        "skipped": report["skipped"],
        "pass_rate": (report["passed"] / report["total"] * 100) if report["total"] > 0 else 0,
        "modules": report.get("modules", {}),
        "execution_plan": report.get("execution_plan", []),
        "all_details": report["details"],
        "failed_details": failed_details,
        "skipped_details": skipped_details,
        "exported_at": datetime.utcnow().isoformat() + "Z",
    }

    html_content = template.render(**template_ctx)

    reports_dir = wiki_root_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = report.get("timestamp", "unknown").replace("T", "_").replace("Z", "").replace(":", "-")
    output_path = reports_dir / f"{prefix}-{timestamp_str}.html"
    output_path.write_text(html_content, encoding="utf-8")

    report["exported_path"] = str(output_path)
    report["export_status"] = "success"
    return report


def _write_manifest(manifest: dict, manifest_path: Path, report: dict) -> None:
    """Write run results to manifest."""
    manifest["last_run_results"] = report
    manifest["last_run_at"] = datetime.utcnow().isoformat() + "Z"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Tool 1: batch_replay_all (was batch_replay)
# ---------------------------------------------------------------------------

@tool
@_log_action("batch_replay_all")
async def batch_replay_all(
    wiki_root: str,
    export: bool = True,
) -> dict:
    """Run ALL recordings across all modules in dependency-resolved order.

    Each recording gets its own fresh browser session. Dependencies are replayed
    first in the same session before the target recording runs.

    Args:
        wiki_root: Path to the automation-wiki folder.
        export: If True, export report to HTML after completion.

    Returns:
        Report with module-level and recording-level results.

    Example::

        batch_replay_all(wiki_root="/path/to/automation-wiki")
        batch_replay_all(wiki_root="/path/to/automation-wiki", export=False)
    """
    print(f"\n[batch_replay_all] Called with params:")
    print(f"  wiki_root: {wiki_root}")
    print(f"  export: {export}")

    wiki_root_path = Path(wiki_root).resolve()

    manifest, manifest_path = await _load_manifest(wiki_root_path)
    if manifest is None:
        return {"status": "error", "error": "no_manifest", "message": "No manifest.json found"}
    if hasattr(manifest, "status") and manifest.get("status") == "error":
        return manifest

    # Collect ALL recordings
    all_recordings = []
    for mod in manifest["modules"].values():
        all_recordings.extend(mod.get("recordings", []))

    if not all_recordings:
        return {"status": "error", "error": "no_recordings", "message": "No recordings found in manifest"}

    try:
        rounds = _topological_sort_rounds(all_recordings, manifest.get("modules", {}))
    except ValueError as e:
        return {"status": "error", "error": "circular_dependency", "message": str(e)}

    # Execute rounds
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    module_results = {}
    execution_plan = []
    details = []
    all_start_time = datetime.utcnow()

    for round_num, round_recordings in enumerate(rounds, 1):
        round_start = datetime.utcnow()
        round_results, all_passed = await _execute_round(round_recordings, wiki_root_path, manifest.get("modules", {}))
        round_duration = (datetime.utcnow() - round_start).total_seconds()

        for result in round_results:
            result["round"] = round_num

        all_deps = set()
        for result in round_results:
            if result.get("executed_deps"):
                all_deps.update(result["executed_deps"])
        dep_names_display = sorted(all_deps) if all_deps else ["—"]

        execution_plan.append({
            "round": round_num,
            "recordings": [r["name"] for r in round_recordings],
            "duration": round_duration,
            "deps_executed": dep_names_display,
        })

        failed_names = {r["name"] for r in round_results if r["status"] == "failed"}

        for recording in all_recordings:
            if recording["name"] in failed_names:
                continue
            if any(dep in failed_names for dep in recording.get("deps", [])):
                skip_result = {
                    "name": recording["name"],
                    "module": recording["module"],
                    "status": "skipped",
                    "round": None,
                    "duration_seconds": 0,
                    "steps_total": 0,
                    "steps_successful": 0,
                    "steps_failed": 0,
                    "failed_events": None,
                    "executed_deps": [],
                }
                round_results.append(skip_result)

        for result in round_results:
            if result["status"] == "passed":
                total_passed += 1
            elif result["status"] == "failed":
                total_failed += 1
            elif result["status"] == "skipped":
                total_skipped += 1

            mod = result["module"]
            if mod not in module_results:
                module_results[mod] = {"passed": 0, "failed": 0, "skipped": 0}
            module_results[mod][result["status"]] += 1

            if result["status"] != "skipped":
                details.append(result)

    total = total_passed + total_failed + total_skipped
    report = {
        "timestamp": all_start_time.isoformat() + "Z",
        "total": total,
        "passed": total_passed,
        "failed": total_failed,
        "skipped": total_skipped,
        "duration_seconds": round((datetime.utcnow() - all_start_time).total_seconds(), 2),
        "modules": module_results,
        "execution_plan": execution_plan,
        "details": details,
    }

    _write_manifest(manifest, manifest_path, report)

    if export:
        _export_report(wiki_root_path, report, prefix="report")

    return report


# ---------------------------------------------------------------------------
# Tool 2: batch_replay_all_standalone_session (was batch_replay_standalone)
# ---------------------------------------------------------------------------

@tool
@_log_action("batch_replay_all_standalone_session")
async def batch_replay_all_standalone_session(
    wiki_root: str,
    export: bool = True,
) -> dict:
    """Run ALL recordings sequentially in ONE browser session (no dependency replay).

    Loads the manifest, topologically sorts recordings by dependencies
    (deps run first as ordering heuristic but are not replayed), creates
    ONE browser session for the entire batch, and runs each recording
    directly. If a recording fails, dependents are skipped.

    Args:
        wiki_root: Path to the automation-wiki folder.
        export: If True, export report to HTML after completion.

    Returns:
        Report with module-level and recording-level results.

    Example::

        batch_replay_all_standalone_session(wiki_root="/path/to/automation-wiki")
        batch_replay_all_standalone_session(wiki_root="/path/to/automation-wiki", export=False)
    """
    print(f"\n[batch_replay_all_standalone_session] Called with params:")
    print(f"  wiki_root: {wiki_root}")
    print(f"  export: {export}")

    wiki_root_path = Path(wiki_root).resolve()

    manifest, manifest_path = await _load_manifest(wiki_root_path)
    if manifest is None:
        return {"status": "error", "error": "no_manifest", "message": "No manifest.json found"}
    if hasattr(manifest, "status") and manifest.get("status") == "error":
        return manifest

    # Collect ALL recordings
    all_recordings = []
    for mod in manifest["modules"].values():
        all_recordings.extend(mod.get("recordings", []))

    if not all_recordings:
        return {"status": "error", "error": "no_recordings", "message": "No recordings found in manifest"}

    try:
        rounds = _topological_sort_rounds(all_recordings, manifest.get("modules", {}))
    except ValueError as e:
        return {"status": "error", "error": "circular_dependency", "message": str(e)}

    # Flatten rounds into a single execution list
    flattened: list[dict] = []
    for round_recordings in rounds:
        for r in round_recordings:
            flattened.append(r)

    from mcp_tools.session import session_close, session_start

    session_result = await session_start(
        email=f"standalone_batch_{datetime.utcnow().timestamp()}",
        base_dir=str(wiki_root_path),
    )

    all_start_time = datetime.utcnow()
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    module_results: dict[str, dict[str, int]] = {}
    execution_plan: list[dict] = []
    details: list[dict] = []

    if session_result.get("status") == "ready":
        session_id = session_result["session_id"]
        failed_names: set[str] = set()

        for idx, recording in enumerate(flattened, 1):
            if any(dep in failed_names for dep in recording.get("deps", [])):
                skip_result = {
                    "name": recording["name"],
                    "module": recording["module"],
                    "status": "skipped",
                    "round": None,
                    "duration_seconds": 0,
                    "steps_total": 0,
                    "steps_successful": 0,
                    "steps_failed": 0,
                    "failed_events": None,
                    "executed_deps": [],
                    "steps": [],
                }
                details.append(skip_result)
                print(f"  [standalone:{idx}] SKIPPED '{recording['name']}' — dependency failed")
                continue

            print(f"  [standalone:{idx}] Running '{recording['name']}'")
            result = await _run_single_recording(session_id, recording, wiki_root_path)
            details.append(result)

            try:
                from mcp_tools.wait import wait_for_load_state
                await wait_for_load_state(state="networkidle", session_id=session_id, timeout=5000)
                await asyncio.sleep(0.5)
            except Exception:
                pass

            if result["status"] == "failed":
                failed_names.add(recording["name"])

        await session_close(session_id=session_id)
    else:
        for idx, recording in enumerate(flattened, 1):
            result = {
                "name": recording["name"],
                "module": recording["module"],
                "status": "failed",
                "duration_seconds": 0,
                "steps_total": 0,
                "steps_successful": 0,
                "steps_failed": 0,
                "failed_events": [{"event_index": 0, "event_type": "unknown", "error": "Failed to start session"}],
                "executed_deps": [],
                "steps": [],
            }
            details.append(result)
            failed_names.add(recording["name"])
        print(f"  [standalone] FAILED to create session: {session_result.get('message', 'unknown')}")

    for detail in details:
        if detail["status"] == "passed":
            total_passed += 1
        elif detail["status"] == "failed":
            total_failed += 1
        elif detail["status"] == "skipped":
            total_skipped += 1

        mod = detail["module"]
        if mod not in module_results:
            module_results[mod] = {"passed": 0, "failed": 0, "skipped": 0}
        module_results[mod][detail["status"]] += 1

    execution_plan.append({
        "round": 1,
        "recordings": [d["name"] for d in details],
        "duration": round((datetime.utcnow() - all_start_time).total_seconds(), 2),
        "deps_executed": ["—"],
    })

    total = total_passed + total_failed + total_skipped
    report = {
        "timestamp": all_start_time.isoformat() + "Z",
        "total": total,
        "passed": total_passed,
        "failed": total_failed,
        "skipped": total_skipped,
        "duration_seconds": round((datetime.utcnow() - all_start_time).total_seconds(), 2),
        "modules": module_results,
        "execution_plan": execution_plan,
        "details": details,
    }

    _write_manifest(manifest, manifest_path, report)

    if export:
        _export_report(wiki_root_path, report, prefix="standalone-report")

    return report


# ---------------------------------------------------------------------------
# Tool 3: replay_specific (NEW)
# ---------------------------------------------------------------------------

@tool
@_log_action("replay_specific")
async def replay_specific(
    wiki_root: str,
    module: str,
    export: bool = True,
) -> dict:
    """Run a specific module WITH dependency replay.

    Collects all recordings in the specified module and their dependencies
    (even from other modules), topologically sorts them, creates ONE browser
    session, and runs each recording with its dependencies replayed first.

    Args:
        wiki_root: Path to the automation-wiki folder.
        module: Module name to replay (runs all recordings in that module + deps).
        export: If True, export report to HTML after completion.

    Returns:
        Report with module-level and recording-level results.

    Example::

        replay_specific(wiki_root="/path/to/automation-wiki", module="performance")
        replay_specific(wiki_root="/path/to/automation-wiki", module="auth", export=False)
    """
    print(f"\n[replay_specific] Called with params:")
    print(f"  wiki_root: {wiki_root}")
    print(f"  module: {module}")
    print(f"  export: {export}")

    wiki_root_path = Path(wiki_root).resolve()

    manifest, manifest_path = await _load_manifest(wiki_root_path)
    if manifest is None:
        return {"status": "error", "error": "no_manifest", "message": "No manifest.json found"}
    if hasattr(manifest, "status") and manifest.get("status") == "error":
        return manifest

    # Collect target recordings for this module
    all_recordings = []
    for mod in manifest["modules"].values():
        all_recordings.extend(mod.get("recordings", []))

    target_recordings = [r for r in all_recordings if r["module"] == module]
    if not target_recordings:
        return {"status": "error", "error": "module_not_found", "message": f"Module '{module}' not found"}

    # Build full list including dependencies (from all modules)
    recording_names = {r["name"] for r in target_recordings}
    all_with_deps = list(target_recordings)

    # Find all dependencies recursively
    for recording in target_recordings:
        for dep_name in recording.get("deps", []):
            # Find dep recording in any module
            for mod in manifest["modules"].values():
                for r in mod.get("recordings", []):
                    if r["name"] == dep_name and r["name"] not in recording_names:
                        all_with_deps.append(r)
                        recording_names.add(r["name"])

    try:
        rounds = _topological_sort_rounds(all_with_deps, manifest.get("modules", {}))
    except ValueError as e:
        return {"status": "error", "error": "circular_dependency", "message": str(e)}

    # Flatten rounds
    flattened: list[dict] = []
    for round_recordings in rounds:
        for r in round_recordings:
            flattened.append(r)

    from mcp_tools.session import session_start, session_close

    session_result = await session_start(
        email=f"specific_replay_{datetime.utcnow().timestamp()}",
        base_dir=str(wiki_root_path),
    )

    all_start_time = datetime.utcnow()
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    module_results: dict[str, dict[str, int]] = {}
    details: list[dict] = []

    if session_result.get("status") == "ready":
        session_id = session_result["session_id"]

        for idx, recording in enumerate(flattened, 1):
            print(f"  [specific:{idx}] Running '{recording['name']}'")
            result = await _execute_recording(session_id, recording, wiki_root_path, manifest.get("modules", {}))
            details.append(result)

            try:
                from mcp_tools.wait import wait_for_load_state
                await wait_for_load_state(state="networkidle", session_id=session_id, timeout=5000)
                await asyncio.sleep(0.5)
            except Exception:
                pass

            if result["status"] == "failed":
                print(f"  [specific:{idx}] FAILED '{recording['name']}'")
            else:
                print(f"  [specific:{idx}] PASSED '{recording['name']}'")

        await session_close(session_id=session_id)
    else:
        for idx, recording in enumerate(flattened, 1):
            result = {
                "name": recording["name"],
                "module": recording["module"],
                "status": "failed",
                "duration_seconds": 0,
                "steps_total": 0,
                "steps_successful": 0,
                "steps_failed": 0,
                "failed_events": [{"event_index": 0, "event_type": "unknown", "error": "Failed to start session"}],
                "executed_deps": [],
                "steps": [],
            }
            details.append(result)
        print(f"  [specific] FAILED to create session: {session_result.get('message', 'unknown')}")

    for detail in details:
        if detail["status"] == "passed":
            total_passed += 1
        elif detail["status"] == "failed":
            total_failed += 1
        elif detail["status"] == "skipped":
            total_skipped += 1

        mod = detail["module"]
        if mod not in module_results:
            module_results[mod] = {"passed": 0, "failed": 0, "skipped": 0}
        module_results[mod][detail["status"]] += 1

    report = {
        "timestamp": all_start_time.isoformat() + "Z",
        "total": total_passed + total_failed + total_skipped,
        "passed": total_passed,
        "failed": total_failed,
        "skipped": total_skipped,
        "duration_seconds": round((datetime.utcnow() - all_start_time).total_seconds(), 2),
        "modules": module_results,
        "execution_plan": [{
            "round": 1,
            "recordings": [d["name"] for d in details],
            "duration": round((datetime.utcnow() - all_start_time).total_seconds(), 2),
            "deps_executed": ["—"],
        }],
        "details": details,
    }

    _write_manifest(manifest, manifest_path, report)

    if export:
        _export_report(wiki_root_path, report, prefix="specific-report")

    return report