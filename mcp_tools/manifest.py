"""Manifest tools — register and list recordings in the automation wiki.

Tools for managing the manifest.json file that tracks recordings, modules,
and dependencies.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.tools import tool


async def _load_manifest(wiki_root: Path) -> tuple[dict | None, Path]:
    """Load manifest.json from wiki_root.

    Args:
        wiki_root: Path to the automation-wiki folder.

    Returns:
        Tuple of (manifest_dict, manifest_path).
        If manifest doesn't exist, returns (None, manifest_path).
    """
    manifest_path = wiki_root / "manifest.json"
    if not manifest_path.exists():
        return None, manifest_path

    try:
        manifest = json.loads(manifest_path.read_text())
        return manifest, manifest_path
    except (json.JSONDecodeError, OSError) as e:
        return {"status": "error", "error": "invalid_manifest", "message": str(e)}, manifest_path


async def _save_manifest(manifest: dict, manifest_path: Path) -> None:
    """Save manifest to disk.

    Args:
        manifest: The manifest dict to save.
        manifest_path: Path to manifest.json.
    """
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def _validate_recording_name(manifest: dict, name: str) -> dict | None:
    """Check if recording name already exists in manifest.

    Args:
        manifest: The manifest dict.
        name: Recording name to check.

    Returns:
        None if name is unique, error dict if duplicate.
    """
    for module in manifest.get("modules", {}).values():
        for recording in module.get("recordings", []):
            if recording["name"] == name:
                return {"status": "error", "error": "duplicate_name", "message": f"Recording '{name}' already exists"}
    return None


def _validate_deps(manifest: dict, deps: list[str]) -> dict | None:
    """Validate that all dependency names exist in manifest.

    Accepts either recording names OR module names as deps.

    Args:
        manifest: The manifest dict.
        deps: List of dependency names (recording names or module names).

    Returns:
        None if all deps exist, error dict if any dep is missing.
    """
    all_names = set()
    all_modules = set()
    for module_name, module in manifest.get("modules", {}).items():
        all_modules.add(module_name)
        for recording in module.get("recordings", []):
            all_names.add(recording["name"])

    for dep in deps:
        if dep not in all_names and dep not in all_modules:
            return {"status": "error", "error": "dep_not_found",
                    "message": f"Dependency '{dep}' not found in manifest. "
                    f"Available: {', '.join(sorted(all_names))} or modules: {', '.join(sorted(all_modules))}"}
    return None


@tool
async def register_recording(
    wiki_root: str,
    module_name: str,
    recording_path: str,
    type: str,
    deps: list[str] = [],
    label: str | None = None,
    name: str | None = None,
) -> dict:
    """Register a recording in the manifest.

    Args:
        wiki_root: Path to the automation-wiki folder.
        module_name: Module name (e.g. "auth", "hr").
        recording_path: Relative path to recording JSON from wiki_root.
        type: "auto" or "human".
        deps: Array of recording names this depends on.
        label: Optional human-readable module label.
        name: Optional explicit recording name. If not provided, derived from filename.

    Returns:
        {"status": "registered", "entry": {...}} on success.
        {"status": "error", "error": "...", "message": "..."} on failure.

    Example::

        register_recording(
            wiki_root="/path/to/automation-wiki",
            module_name="auth",
            recording_path="recordings/login-flow_xxx.json",
            type="auto",
            deps=[],
            name="login-flow",
            label="Authentication"
        )
    """
    # Validate type
    if type not in ("auto", "human"):
        return {"status": "error", "error": "invalid_type", "message": "Type must be 'auto' or 'human'"}

    # Resolve paths
    wiki_root_path = Path(wiki_root).resolve()
    recording_path_abs = wiki_root_path / recording_path

    # Check if recording file exists
    if not recording_path_abs.exists():
        return {"status": "error", "error": "file_not_found", "message": f"Recording file not found: {recording_path_abs}"}

    # Determine recording name
    recording_name = name or recording_path.split("/")[-1].split(".")[0]

    # Load or create manifest
    manifest, manifest_path = await _load_manifest(wiki_root_path)

    if manifest is None:
        # Create new manifest
        manifest = {
            "name": wiki_root_path.name,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "modules": {},
            "last_run_results": None,
        }
    elif hasattr(manifest, "status") and manifest.get("status") == "error":
        # Manifest exists but is invalid
        return manifest

    # Check recording name uniqueness
    name_error = _validate_recording_name(manifest, recording_name)
    if name_error:
        return name_error

    # Validate deps exist
    deps_error = _validate_deps(manifest, deps)
    if deps_error:
        return deps_error

    # Get or create module
    if module_name not in manifest["modules"]:
        manifest["modules"][module_name] = {
            "label": label or module_name,
            "recordings": [],
        }

    # Add recording entry
    recording_entry = {
        "name": recording_name,
        "path": recording_path,
        "type": type,
        "deps": deps,
        "module": module_name,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    manifest["modules"][module_name]["recordings"].append(recording_entry)

    # Save manifest
    await _save_manifest(manifest, manifest_path)

    return {"status": "registered", "entry": recording_entry}


@tool
async def list_recordings(wiki_root: str) -> dict:
    """List all recordings in the manifest.

    Args:
        wiki_root: Path to the automation-wiki folder.

    Returns:
        {"modules": {...}, "total": int} on success.
        {"status": "error", "error": "...", "message": "..."} on failure.

    Example::

        list_recordings(wiki_root="/path/to/automation-wiki")
    """
    wiki_root_path = Path(wiki_root).resolve()

    manifest, manifest_path = await _load_manifest(wiki_root_path)

    if manifest is None:
        return {"status": "error", "error": "no_manifest", "message": "No manifest.json found"}
    if hasattr(manifest, "status") and manifest.get("status") == "error":
        return manifest

    # Count total recordings
    total = 0
    for module in manifest["modules"].values():
        total += len(module.get("recordings", []))

    return {"modules": manifest["modules"], "total": total}