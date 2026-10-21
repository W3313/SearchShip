"""The user's tracker data: postings, run state, settings, resume.

Everything is plain JSON/Markdown in the data folder so it can be backed up,
edited by hand, or imported from the old GitHub tracker.
"""
import copy
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .paths import data_dir, resource

STATUSES = ("new", "ready", "applied", "interview", "skipped", "closed")
TOUCHED_STATUSES = {"applied", "interview", "skipped"}

# Public career boards checked when the "Company career pages" source is on.
# Format: "<ats>:<board token>" with an optional " = Display Name".
DEFAULT_BOARDS = [
    "greenhouse:stripe = Stripe",
    "greenhouse:databricks = Databricks",
    "greenhouse:figma = Figma",
    "greenhouse:airbnb = Airbnb",
    "greenhouse:robinhood = Robinhood",
    "greenhouse:discord = Discord",
    "greenhouse:cloudflare = Cloudflare",
    "greenhouse:pinterest = Pinterest",
    "greenhouse:anthropic = Anthropic",
    "greenhouse:samsara = Samsara",
    "ashby:openai = OpenAI",
    "ashby:notion = Notion",
    "ashby:ramp = Ramp",
    "lever:plaid = Plaid",
]

DEFAULT_SETTINGS = {
    "provider": None,          # "claude" | "codex" | None
    "onboarded": False,
    "autoScan": True,          # scan on launch and once a day while open
    "sources": {
        "simplify": True,      # SimplifyJobs Summer 2027 list
        "vanshb03": True,      # vanshb03 Summer 2027 list
        "boards": True,        # Greenhouse / Lever / Ashby boards below
        "websearch": True,     # AI web search (needs a connected provider)
    },
    "boards": DEFAULT_BOARDS,
    "lastWebSearch": None,
}


def now_iso() -> str:
    """Timestamp for `added` and `lastRun`, e.g. 2026-10-21T15:00:03+00:00."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def now_stamp() -> str:
    """Timestamp for `updated`, matching the old tracker: 2026-10-14T05:02:00.581Z."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def today() -> str:
    """Local date for `appliedOn`: the day you applied where you are."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def touched(row: dict) -> bool:
    """True when the user has acted on a row, so automation must leave it alone."""
    return bool(
        row.get("appliedOn")
        or row.get("updated")
        or str(row.get("notes") or "").strip()
        or row.get("status") in TOUCHED_STATUSES
    )


def dump_json(obj) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: file briefly held by an indexer or antivirus
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return copy.deepcopy(default)


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.postings_path = self.root / "postings.json"
        self.state_path = self.root / "state.json"
        self.settings_path = self.root / "settings.json"
        self.resume_path = self.root / "resume.md"
        self.skip_path = self.root / "skip_companies.txt"
        if not self.skip_path.exists():
            bundled = resource("skip_companies.txt")
            if bundled.exists():
                shutil.copyfile(bundled, self.skip_path)
            else:
                self.skip_path.write_text("", encoding="utf-8")

    # postings ---------------------------------------------------------------
    def load_postings(self) -> dict:
        return read_json(self.postings_path, {})

    def save_postings(self, postings: dict) -> None:
        with self.lock:
            write_atomic(self.postings_path, dump_json(postings))

    def import_postings(self, incoming) -> dict:
        """Merge rows exported from the old tracker.

        New ids are added. For an id already here, a row you have acted on in
        SearchShip is kept; a row SearchShip only added automatically is
        replaced when the imported copy carries your history (status, notes,
        appliedOn) or prep.
        """
        if not isinstance(incoming, dict):
            raise ValueError("Expected a JSON object of {id: row}.")
        good = {}
        for pid, row in incoming.items():
            if not isinstance(row, dict) or not row.get("company") or not row.get("title"):
                continue
            row = dict(row)
            row.setdefault("status", "new")
            row.setdefault("notes", "")
            row.setdefault("locations", [])
            row.setdefault("source", "manual")
            row.setdefault("added", now_iso())
            good[str(pid)] = row
        if incoming and not good:
            raise ValueError("No rows with a company and title were found.")
        with self.lock:
            postings = self.load_postings()
            added = [pid for pid in good if pid not in postings]
            replaced = [pid for pid in good if pid in postings and not touched(postings[pid])
                        and (touched(good[pid]) or (good[pid].get("prep") and not postings[pid].get("prep")))]
            for pid in added + replaced:
                postings[pid] = good[pid]
            self.save_postings(postings)
        return {"added": len(added), "replaced": len(replaced),
                "kept": len(good) - len(added) - len(replaced), "total": len(postings)}

    # state ------------------------------------------------------------------
    def load_state(self) -> dict:
        return read_json(self.state_path, {"lastRun": None, "summary": ""})

    def save_state(self, state: dict) -> None:
        with self.lock:
            write_atomic(self.state_path, dump_json(state))

    # settings ---------------------------------------------------------------
    def load_settings(self) -> dict:
        saved = read_json(self.settings_path, {})
        merged = copy.deepcopy(DEFAULT_SETTINGS)
        for key, value in saved.items():
            if key == "sources" and isinstance(value, dict):
                merged["sources"].update({k: bool(v) for k, v in value.items() if k in merged["sources"]})
            elif key in merged:
                merged[key] = value
        return merged

    def save_settings(self, updates: dict) -> dict:
        with self.lock:
            settings = self.load_settings()
            for key, value in (updates or {}).items():
                if key == "sources" and isinstance(value, dict):
                    settings["sources"].update({k: bool(v) for k, v in value.items() if k in settings["sources"]})
                elif key == "boards":
                    settings["boards"] = [str(b).strip() for b in value if str(b).strip()]
                elif key == "provider":
                    settings["provider"] = value if value in ("claude", "codex") else None
                elif key in DEFAULT_SETTINGS:
                    settings[key] = value
            write_atomic(self.settings_path, dump_json(settings))
            return settings

    # resume / skip list -----------------------------------------------------
    def resume_text(self) -> str:
        try:
            return self.resume_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def save_resume(self, text: str) -> None:
        text = (text or "").strip()
        with self.lock:
            write_atomic(self.resume_path, text + "\n" if text else "")

    def skip_text(self) -> str:
        return self.skip_path.read_text(encoding="utf-8")

    def save_skip(self, text: str) -> None:
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        with self.lock:
            write_atomic(self.skip_path, "\n".join(lines) + ("\n" if lines else ""))
