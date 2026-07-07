"""Dialog handling tools — browser alert, confirm, and prompt dialogs."""

from typing import Any

from fastmcp.tools import tool

from helpers.session_store import get_session_by_id


async def _resolve_session(session_id: str) -> tuple[dict | None, Any]:
    """Return (error, session). If error is set, session is None."""
    session = get_session_by_id(session_id)
    if session is None:
        return ({"status": "error", "message": f"Session {session_id} not found"}, None)
    return (None, session)


# @tool  # DISABLED
async def handle_dialog(
    session_id: str,
    action: str,
    prompt_text: str | None = None,
) -> dict:
    """
    Handle the next browser dialog (alert, confirm, or prompt).

    Sets up a one-shot dialog listener, waits for the dialog to fire, handles it
    based on ``action``, and returns. Subsequent dialogs are not affected —
    call this tool again for each dialog you need to handle.

    Args:
        session_id: The session ID.
        action: Either ``"accept"`` or ``"dismiss"``.
        prompt_text: Required when ``action = "accept"`` and the dialog has a
            text input field (prompt). Ignored for alerts and confirms.

    Returns:
        ``{"handled": true, "dialog_type": "<type>"}`` where type is
        ``"alert"``, ``"confirm"``, or ``"prompt"``.

    Raises:
        ValueError: If ``action`` is not ``"accept"`` or ``"dismiss"``.
    """
    err, session = await _resolve_session(session_id)
    if err:
        return err

    if action not in ("accept", "dismiss"):
        raise ValueError(f"Invalid action '{action}'. Must be 'accept' or 'dismiss'.")

    dialog_type = None

    def handler(dialog):
        nonlocal dialog_type
        dialog_type = dialog.type

        if action == "accept":
            if dialog.type == "prompt" and prompt_text:
                dialog.accept_with_prompt(prompt_text)
            else:
                dialog.accept()
        else:
            dialog.dismiss()

    # Set up a one-shot listener — dialog fires once then this handler runs
    session.page.once("dialog", handler)
    return {"handled": True, "dialog_type": dialog_type}
