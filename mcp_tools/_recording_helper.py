"""Helper to append recording reminder to action tool results."""

RECORDER_REMINDER = (
    "\n\n[automation: if this step succeeded, call record_step to record it]"
)


def add_recording_reminder(result: dict) -> dict:
    """Append recording reminder to action tool result dict.

    Returns a new dict with the original fields plus a 'recording_reminder' field
    containing the reminder text. The reminder is always appended to action tools
    so the agent sees it in the tool output and can decide to record.
    """
    result["recording_reminder"] = RECORDER_REMINDER
    return result