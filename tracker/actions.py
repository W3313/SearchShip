"""User actions on a single row (the buttons on each card).

The old GitHub tracker took these as issue titles ("approve <id>",
"note <id>: text"); parse_command() still understands that format.
"""
import re

from .store import now_stamp, today

COMMANDS = ("approve", "skip", "applied", "interview", "restore", "note")
COMMAND_RE = re.compile(r"^\s*(approve|skip|applied|interview|restore|note)\s+([A-Za-z0-9:._-]+)\s*(?::\s*(.*))?$",
                        re.I | re.S)


class ActionError(ValueError):
    pass


def parse_command(text: str):
    """'approve <id>' / 'note <id>: text' -> (command, id, text or None)."""
    m = COMMAND_RE.match(text or "")
    if not m:
        raise ActionError("Use one of: approve <id>, skip <id>, applied <id>, interview <id>, "
                          "restore <id>, note <id>: text")
    cmd, pid, note = m.group(1).lower(), m.group(2), m.group(3)
    if cmd == "note" and note is None:
        raise ActionError("A note needs text after a colon: note <id>: text")
    if cmd != "note" and note:
        raise ActionError(f"'{cmd}' does not take text")
    return cmd, pid, (note.strip() if note is not None else None)


def apply(postings: dict, cmd: str, pid: str, text: str = None) -> dict:
    """Apply one action in place and return the updated row.

    approve   -> ready (worth applying to; draft prep from the card if you like)
    skip      -> skipped
    applied   -> applied, appliedOn = today (UTC)
    interview -> interview
    restore   -> new (undo a skip or an automatic close)
    note      -> replaces the row's notes
    Every action stamps `updated`, which marks the row as touched so the
    automatic scan never changes it again.
    """
    if cmd not in COMMANDS:
        raise ActionError(f"Unknown action '{cmd}'")
    row = postings.get(pid)
    if row is None:
        raise ActionError(f"No posting with id {pid}")
    if cmd == "note":
        row["notes"] = (text or "").strip()
    elif cmd == "approve":
        row["status"] = "ready"
    elif cmd == "skip":
        row["status"] = "skipped"
    elif cmd == "applied":
        row["status"] = "applied"
        row["appliedOn"] = today()
    elif cmd == "interview":
        row["status"] = "interview"
    elif cmd == "restore":
        row["status"] = "new"
    row["updated"] = now_stamp()
    return row


def apply_to_store(store, cmd: str, pid: str, text: str = None) -> dict:
    with store.lock:
        postings = store.load_postings()
        row = apply(postings, cmd, pid, text)
        store.save_postings(postings)
        return row
