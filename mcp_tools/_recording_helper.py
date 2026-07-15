"""Helper to append recording reminder to action tool results."""

RECORDER_REMINDER = (
    "\n\n[automation: if this step succeeded, call record_step to record it.\n"
    "If the element only appears sometimes (modal, banner, popup), mark it optional:\n"
    "  record_step(session_id, tool_name, args, optional=true)\n"
    "Transient UI = optional. Core flow = compulsory.]"
)


def add_recording_reminder(result: dict) -> dict:
    """Append recording reminder to action tool result dict.

    Returns a new dict with the original fields plus a 'recording_reminder' field
    containing the reminder text. The reminder is always appended to action tools
    so the agent sees it in the tool output and can decide to record.
    """
    result["recording_reminder"] = RECORDER_REMINDER
    return result