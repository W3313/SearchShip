"""Background work (scans and AI drafting) so the window never freezes.

One job runs at a time. The UI polls `JobRunner.status()` for progress.
"""
import threading
import traceback
from datetime import datetime, timezone

from . import ai, drafting, pipeline, sources
from .store import now_iso

AUTO_SCAN_HOURS = 20


def _hours_since(iso):
    if not iso:
        return float("inf")
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600


def connected_provider(settings):
    """The chosen provider if it is installed and signed in, else None."""
    name = settings.get("provider")
    if name not in ai.PROVIDERS:
        return None
    p = ai.PROVIDERS[name]
    return p if p.status().get("loggedIn") else None


def run_daily(store, log=print, dry_run=False, get=sources.http_get_json, provider="auto") -> dict:
    """Scan every source, then draft prep for this run's best new rows."""
    settings = store.load_settings()
    if dry_run:
        provider = None
    elif provider == "auto":
        provider = connected_provider(settings)
    errors, web = [], []

    if (provider and settings["sources"].get("websearch")
            and _hours_since(settings.get("lastWebSearch")) >= pipeline.WEB_SEARCH_EVERY_HOURS):
        try:
            web = drafting.search_postings(provider, store.load_postings(), log)
            store.save_settings({"lastWebSearch": now_iso()})
        except Exception as e:
            errors.append(f"Web search: {e}")
            log(f"Web search failed: {e}")

    result = pipeline.run_scan(store, settings, get=get, web_listings=web, dry_run=dry_run, log=log)
    errors += result["errors"]
    drafted, problems = 0, []
    if result["toDraft"] and provider and not dry_run:
        try:
            counts = drafting.draft_rows(store, provider, result["toDraft"], log)
            drafted, problems = counts["ready"], counts["problems"]
        except Exception as e:
            errors.append(f"Drafting: {e}")
            log(f"Drafting failed: {e}")
    elif result["toDraft"] and not provider and not dry_run:
        log("No AI account connected, so new postings were not drafted.")

    summary = pipeline.summary_line(len(result["new"]), drafted, len(result["closed"]))
    if not dry_run:
        store.save_state({"lastRun": now_iso(), "summary": summary, "new": len(result["new"]),
                          "drafted": drafted, "closed": len(result["closed"]),
                          "errors": errors, "draftProblems": problems})
    for e in errors:
        log("Problem: " + e)
    log(summary + (" (dry run, nothing saved)" if dry_run else ""))
    return {"summary": summary, "new": result["new"], "closed": result["closed"],
            "toDraft": result["toDraft"], "drafted": drafted, "errors": errors, "newRows": result["newRows"]}


class JobRunner:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._thread = None
        self._state = {"running": False, "kind": None, "log": [], "error": None, "result": None,
                       "finished": None, "seq": 0}

    def status(self) -> dict:
        with self._lock:
            s = dict(self._state)
            s["log"] = list(s["log"][-200:])
            return s

    def _log(self, msg):
        with self._lock:
            self._state["log"].append(str(msg))

    def start(self, kind, fn) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
            self._state = {"running": True, "kind": kind, "log": [], "error": None, "result": None,
                           "finished": None, "seq": self._state["seq"] + 1}

        def target():
            result = error = None
            try:
                result = fn(self._log)
            except Exception as e:
                error = str(e) or e.__class__.__name__
                self._log("Error: " + error)
                traceback.print_exc()
            with self._lock:
                self._state.update(running=False, result=result, error=error, finished=now_iso())

        self._thread = threading.Thread(target=target, name=f"searchship-{kind}", daemon=True)
        self._thread.start()
        return True

    def scan(self) -> bool:
        return self.start("scan", lambda log: run_daily(self.store, log))

    def draft(self, ids) -> bool:
        def work(log):
            settings = self.store.load_settings()
            provider = connected_provider(settings)
            if not provider:
                raise ai.AIError("Connect Claude or ChatGPT in Settings first.")
            ids_ok = drafting.drafting_candidates(self.store.load_postings(), ids)
            if not ids_ok:
                raise ValueError("Only New or Ready postings can be drafted.")
            counts = drafting.draft_rows(self.store, provider, ids_ok, log)
            msg = f"Drafted {counts['ready']}, skipped {counts['skipped']}"
            if counts.get("advised"):
                msg += f", {counts['advised']} kept as you left them (the AI's reason is in notes)"
            log(msg)
            return counts
        return self.start("draft", work)

    def scan_if_due(self) -> bool:
        settings = self.store.load_settings()
        if not settings.get("onboarded") or not settings.get("autoScan"):
            return False
        if _hours_since(self.store.load_state().get("lastRun")) < AUTO_SCAN_HOURS:
            return False
        return self.scan()
