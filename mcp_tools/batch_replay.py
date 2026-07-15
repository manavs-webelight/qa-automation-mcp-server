"""Batch replay tool — execute multiple recordings in dependency-resolved order.

Orchestrates batch replay of recordings from a manifest with dependency resolution,
fresh browser sessions, and report generation.
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


def _build_dep_graph(recordings: list[dict]) -> tuple[dict, dict]:
    """Build dependency graph from recordings.

    Args:
        recordings: List of recording entries from manifest.

    Returns:
        Tuple of (graph, in_degree) where:
        - graph: dict mapping recording_name -> list of dependent names
        - in_degree: dict mapping recording_name -> number of dependencies
    """
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
    """Expand module-level deps to recording-level deps.

    Args:
        recordings: List of recording entries from manifest.
        modules: Module definitions from manifest.

    Returns:
        New list of recording entries with deps resolved to recording names.
    """
    # Build module_name -> list of recording names
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
            # else: leave unresolved, will be filtered out by graph builder
        expanded.append({**recording, "deps": resolved})
    return expanded


def _topological_sort_rounds(recordings: list[dict], modules: dict | None = None) -> list[list[dict]]:
    """Sort recordings into rounds based on dependencies.

    Args:
        recordings: List of recording entries from manifest.
        modules: Optional module definitions for expanding module-level deps.

    Returns:
        List of rounds, where each round is a list of recordings that can run
        in parallel. Recordings with no unsatisfied deps go in Round 1.
    """
    if modules:
        recordings = _expand_module_deps(recordings, modules)

    graph, in_degree = _build_dep_graph(recordings)
    recording_map = {r["name"]: r for r in recordings}
    rounds = []
    remaining = set(r["name"] for r in recordings)

    while remaining:
        # Find all recordings with in_degree 0
        current_round = [
            recording_map[name]
            for name in remaining
            if in_degree[name] == 0
        ]

        if not current_round:
            # Circular dependency detected
            raise ValueError(f"Circular dependency detected among: {remaining}")

        rounds.append(current_round)
        remaining -= {r["name"] for r in current_round}

        # Update in_degrees for dependents
        for recording in current_round:
            for dependent in graph[recording["name"]]:
                in_degree[dependent] -= 1

    return rounds


async def _execute_recording(
    session_id: str,
    recording: dict,
    wiki_root: Path,
    modules: dict | None = None,
    _executed: set | None = None,
) -> dict:
    """Execute a single recording, running dependencies first in the same session.

    Args:
        session_id: Browser session ID (created by _execute_round).
        recording: Recording entry from manifest.
        wiki_root: Path to wiki root.
        modules: Module definitions for resolving module-level deps.
        _executed: Set of already-executed recording names (for cycle detection).

    Returns:
        Result dict with status, duration, details, and executed_deps.
    """
    import time

    # Initialize tracking set
    if _executed is None:
        _executed = set()

    # Check if already executed (cycle detection)
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

    # Find the dependency recordings by name (deps are already expanded by topological sort)
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

    # First, execute all dependencies in the same session
    for dep_recording in dep_recordings:
        print(f"REPLAY: Running dep '{dep_recording['name']}' before '{recording['name']}'")
        await _execute_recording(session_id, dep_recording, wiki_root, modules, _executed)

    _executed.add(recording["name"])

    # Wait briefly for state to propagate after dependencies
    if dep_recordings:
        await asyncio.sleep(1.0)

    # Now execute the actual recording
    start_time = time.time()
    recording_path = wiki_root / recording["path"]
    executed_deps = [d["name"] for d in dep_recordings]

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

        # Extract details based on result type
        if recording["type"] == "auto":
            # replay_automation returns results with per-step detail
            status_str = result.get("status", "")
            return {
                "name": recording["name"],
                "module": recording["module"],
                "status": "passed" if status_str == "success" else "failed",
                "round": None,  # Set later
                "duration_seconds": round(duration, 2),
                "steps_total": result.get("total", 0),
                "steps_successful": result.get("successful", 0),
                "steps_failed": result.get("failed", 0),
                "failed_events": result.get("failed_events") or result.get("results"),
                "executed_deps": executed_deps,
            }
        else:
            # replay_interactions returns summary with optional failed_events
            return {
                "name": recording["name"],
                "module": recording["module"],
                "status": "passed" if result.get("status") == "success" else "failed",
                "round": None,
                "duration_seconds": round(duration, 2),
                "steps_total": result.get("total", 0),
                "steps_successful": result.get("successful", 0),
                "steps_failed": result.get("failed", 0),
                "failed_events": result.get("failed_events", []),
                "executed_deps": executed_deps,
            }

    except Exception as e:
        duration = time.time() - start_time
        return {
            "name": recording["name"],
            "module": recording["module"],
            "status": "failed",
            "round": None,
            "duration_seconds": round(duration, 2),
            "steps_total": 0,
            "steps_successful": 0,
            "steps_failed": 0,
            "failed_events": [{"event_index": 0, "event_type": "unknown", "error": str(e)}],
            "executed_deps": executed_deps,
        }


async def _execute_round(
    round_recordings: list[dict],
    wiki_root: Path,
    modules: dict | None = None,
) -> tuple[list[dict], bool]:
    """Execute a round of recordings.

    Args:
        round_recordings: List of recordings to run in this round.
        wiki_root: Path to wiki root.

    Returns:
        Tuple of (results, all_passed) where results is list of result dicts
        and all_passed is True if all recordings in this round passed.
    """
    results = []

    for recording in round_recordings:
        # Create fresh session for this recording
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
            # Close session
            from mcp_tools.session import session_close
            await session_close(session_id=session_result["session_id"])

    all_passed = all(r["status"] == "passed" for r in results)
    return results, all_passed


@tool
async def batch_replay(
    wiki_root: str,
    module: str | None = None,
    recordings: list[str] | None = None,
    export: bool = False,
) -> dict:
    """Run all recordings in dependency-resolved order.

    Args:
        wiki_root: Path to the automation-wiki folder.
        module: Optional module name to replay (if None, run all).
        recordings: Optional list of recording names to replay (if None, run all).
        export: If True, export report to HTML after completion.

    Returns:
        Report with module-level and recording-level results.

    Example::

        batch_replay(wiki_root="/path/to/automation-wiki")
        batch_replay(wiki_root="/path/to/automation-wiki", module="auth")
        batch_replay(wiki_root="/path/to/automation-wiki", recordings=["login-flow"])
        batch_replay(wiki_root="/path/to/automation-wiki", export=True)
    """
    wiki_root_path = Path(wiki_root).resolve()

    # Load manifest
    manifest, manifest_path = await _load_manifest(wiki_root_path)

    if manifest is None:
        return {"status": "error", "error": "no_manifest", "message": "No manifest.json found"}
    if hasattr(manifest, "status") and manifest.get("status") == "error":
        return manifest

    # Collect all recordings
    all_recordings = []
    for mod in manifest["modules"].values():
        all_recordings.extend(mod.get("recordings", []))

    # Filter by module
    if module:
        all_recordings = [r for r in all_recordings if r["module"] == module]
        if not all_recordings:
            return {"status": "error", "error": "module_not_found", "message": f"Module '{module}' not found"}

    # Filter by recordings list
    if recordings:
        all_recordings = [r for r in all_recordings if r["name"] in recordings]
        if not all_recordings:
            return {"status": "error", "error": "recordings_not_found", "message": "None of the specified recordings found"}

    # Sort into rounds
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

        # Update round numbers in results
        for result in round_results:
            result["round"] = round_num

        # Collect deps executed by all recordings in this round
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

        # Track which recordings failed in this round
        failed_names = {r["name"] for r in round_results if r["status"] == "failed"}

        # Propagate skips to dependents
        for recording in all_recordings:
            if recording["name"] in failed_names:
                continue
            if any(dep in failed_names for dep in recording.get("deps", [])):
                # This recording should be skipped
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
                total_skipped += 1
                details.append(skip_result)

        # Aggregate results
        for result in round_results:
            if result["status"] == "passed":
                total_passed += 1
            elif result["status"] == "failed":
                total_failed += 1
            elif result["status"] == "skipped":
                total_skipped += 1

            # Module-level aggregation
            mod = result["module"]
            if mod not in module_results:
                module_results[mod] = {"passed": 0, "failed": 0, "skipped": 0}
            module_results[mod][result["status"]] += 1

            # Add to details (only non-skipped for now)
            if result["status"] != "skipped":
                details.append(result)

    # Build report
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

    # Write results to manifest
    manifest["last_run_results"] = report
    manifest["last_run_at"] = all_start_time.isoformat() + "Z"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    # Export report if requested
    if export:
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
            "failed_details": failed_details,
            "skipped_details": skipped_details,
            "exported_at": datetime.utcnow().isoformat() + "Z",
        }

        html_content = template.render(**template_ctx)

        reports_dir = wiki_root_path / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp_str = report.get("timestamp", "unknown").replace("T", "_").replace("Z", "").replace(":", "-")
        output_path = reports_dir / f"report-{timestamp_str}.html"
        output_path.write_text(html_content, encoding="utf-8")

        report["exported_path"] = str(output_path)
        report["export_status"] = "success"

    return report