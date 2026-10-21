"""jobs.run_daily (scan + draft) and the one-job-at-a-time JobRunner."""
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from helpers import FakeGet, FakeProvider, listing, prompt_postings, row, vansh_listing
from tracker import ai, jobs, sources
from tracker.jobs import JobRunner, run_daily

RESUME = "# Jane Doe\n\nBuilt a tracker with 251 rows in Python.\n"
SUMMARY = re.compile(r"^(\d+) new · (\d+) drafted · (\d+) closed$")


def daily_get():
    simplify = [listing(f"s{i}", f"Company {i}", "Software Engineer Intern", ["San Francisco, CA"], i + 0.3)
                for i in range(11)]                       # 11 score >= 4.5, the draft cap is 8
    simplify.append(listing("low", "Low Co", "Data Analyst Intern", ["San Jose, CA"], 1, category="AI/ML/Data"))
    simplify.append(listing("alive", "Alive Co"))
    return FakeGet({sources.SIMPLIFY_URL: simplify,
                    sources.VANSH_URL: [vansh_listing("v1", "Vega", "Backend Engineer Intern", ["Palo Alto, CA"], 0.5)]})


def seed(store, provider=None):
    store.save_postings({"stale": row("Stale Co"), "alive": row("Alive Co"),
                         "mine": row("Mine Co", status="applied", appliedOn="2026-10-02")})
    store.save_resume(RESUME)
    store.save_settings({"sources": {"boards": False}, "provider": provider})


def test_run_daily_drafts_the_picked_ids(store):
    seed(store)
    fake = FakeProvider()
    result = run_daily(store, log=lambda *a: None, get=daily_get(), provider=fake)
    assert len(result["new"]) == 13
    assert result["closed"] == ["stale"]
    assert len(result["toDraft"]) == 8
    assert len(fake.draft_calls) == 1
    asked = re.findall(r'"id": "([^"]+)"', fake.draft_calls[0]["prompt"])
    assert asked == result["toDraft"]
    assert result["drafted"] == 8
    assert result["summary"] == "13 new · 8 drafted · 1 closed"
    assert SUMMARY.match(result["summary"])

    saved = store.load_postings()
    for pid in result["toDraft"]:
        assert saved[pid]["status"] == "ready" and set(saved[pid]["prep"]) == {"fit", "project", "why"}
    for pid in set(result["new"]) - set(result["toDraft"]):
        assert saved[pid]["status"] == "new" and "prep" not in saved[pid]
    assert saved["mine"] == row("Mine Co", status="applied", appliedOn="2026-10-02")

    state = store.load_state()
    assert state["summary"] == result["summary"]
    assert (state["new"], state["drafted"], state["closed"]) == (13, 8, 1)


def test_run_daily_web_search_adds_listings_once_a_day(store):
    seed(store)
    item = {"company": "Nova", "title": "Platform Engineer Intern", "locations": ["Remote"],
            "url": "https://jobs.ashbyhq.com/nova/1", "posted": "", "category": "Software"}
    fake = FakeProvider(search_items=[item])
    result = run_daily(store, log=lambda *a: None, get=daily_get(), provider=fake)
    web_ids = [pid for pid in result["new"] if pid.startswith("web:")]
    assert len(web_ids) == 1
    assert store.load_postings()[web_ids[0]]["source"] == "Web search"
    assert len(fake.search_calls) == 1
    assert store.load_settings()["lastWebSearch"]
    run_daily(store, log=lambda *a: None, get=daily_get(), provider=fake)
    assert len(fake.search_calls) == 1, "web search runs at most once every WEB_SEARCH_EVERY_HOURS"


def test_run_daily_respects_websearch_off(store):
    seed(store)
    store.save_settings({"sources": {"websearch": False}})
    fake = FakeProvider(search_items=[])
    run_daily(store, log=lambda *a: None, get=daily_get(), provider=fake)
    assert fake.search_calls == []


def test_run_daily_drafting_failure_is_reported_not_raised(store):
    seed(store)

    class Broken(FakeProvider):
        def run_json(self, prompt, schema, web=True, timeout=900):
            if "drafts" in schema["properties"]:
                raise ai.AIError("Claude took too long and was stopped.")
            return {"postings": []}

    result = run_daily(store, log=lambda *a: None, get=daily_get(), provider=Broken())
    assert result["drafted"] == 0
    assert any("Drafting" in e for e in result["errors"])
    assert result["summary"] == "13 new · 0 drafted · 1 closed"
    assert store.load_state()["errors"] == result["errors"]


def test_run_daily_without_provider_saves_new_rows_undrafted(store):
    seed(store)
    result = run_daily(store, log=lambda *a: None, get=daily_get(), provider=None)
    assert result["drafted"] == 0 and len(result["toDraft"]) == 8
    assert all(store.load_postings()[pid]["status"] == "new" for pid in result["new"])


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_dry_run_with_auto_provider_never_calls_the_ai(store, monkeypatch):
    seed(store, provider="claude")
    spy = FakeProvider()
    statuses = []
    spy.status = lambda: statuses.append(1) or {"loggedIn": True}
    monkeypatch.setitem(ai.PROVIDERS, "claude", spy)
    assert jobs.connected_provider(store.load_settings()) is spy
    statuses.clear()
    before = _snapshot(store.root)
    result = run_daily(store, log=lambda *a: None, dry_run=True, get=daily_get())
    assert spy.calls == [] and statuses == []
    assert result["drafted"] == 0 and len(result["toDraft"]) == 8
    assert result["summary"] == "13 new · 0 drafted · 1 closed"
    assert _snapshot(store.root) == before


def test_dry_run_with_explicit_provider_never_calls_the_ai(store):
    seed(store)
    fake = FakeProvider()
    before = _snapshot(store.root)
    run_daily(store, log=lambda *a: None, dry_run=True, get=daily_get(), provider=fake)
    assert fake.calls == []
    assert _snapshot(store.root) == before


def test_dry_run_with_explicit_provider_never_drafts(store):
    seed(store)
    fake = FakeProvider()
    run_daily(store, log=lambda *a: None, dry_run=True, get=daily_get(), provider=fake)
    assert fake.draft_calls == []
    assert all(r["status"] != "ready" for r in store.load_postings().values())


def test_connected_provider(store, monkeypatch):
    assert jobs.connected_provider({"provider": None}) is None
    assert jobs.connected_provider({"provider": "gemini"}) is None
    assert jobs.connected_provider({"provider": "claude"}) is ai.PROVIDERS["claude"]   # fake: signed in
    assert jobs.connected_provider({"provider": "codex"}) is None                       # fake: not installed


# --- JobRunner -----------------------------------------------------------------------------------

def wait_until(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_job_runner_runs_one_job_at_a_time(store):
    runner = JobRunner(store)
    gate = threading.Event()

    def work(log):
        log("working")
        gate.wait(5)
        return {"done": True}

    assert runner.start("scan", work) is True
    assert wait_until(lambda: "working" in runner.status()["log"])
    s = runner.status()
    assert s["running"] is True and s["kind"] == "scan"
    assert runner.start("draft", lambda log: None) is False
    gate.set()
    assert wait_until(lambda: not runner.status()["running"])
    s = runner.status()
    assert s["result"] == {"done": True} and s["error"] is None and s["finished"]
    json.dumps(s)


def test_job_runner_reports_errors(store):
    runner = JobRunner(store)

    def boom(log):
        raise ValueError("Only New or Ready postings can be drafted.")

    seq = runner.status()["seq"]
    assert runner.start("draft", boom)
    assert wait_until(lambda: not runner.status()["running"])
    s = runner.status()
    assert s["error"] == "Only New or Ready postings can be drafted." and s["seq"] == seq + 1
    assert any("Error:" in line for line in s["log"])


def test_job_runner_draft_needs_a_connected_provider(store):
    store.save_postings({"a": row("Acme")})
    runner = JobRunner(store)
    assert runner.draft(["a"])
    assert wait_until(lambda: not runner.status()["running"])
    assert "Connect" in runner.status()["error"]


def test_scan_if_due(store, monkeypatch):
    started = []
    runner = JobRunner(store)
    monkeypatch.setattr(runner, "scan", lambda: started.append(1) or True)
    assert runner.scan_if_due() is False                      # not onboarded yet
    store.save_settings({"onboarded": True, "autoScan": False})
    assert runner.scan_if_due() is False                      # auto scan turned off
    store.save_settings({"autoScan": True})
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.save_state({"lastRun": recent, "summary": ""})
    assert runner.scan_if_due() is False                      # ran recently
    old = (datetime.now(timezone.utc) - timedelta(hours=jobs.AUTO_SCAN_HOURS + 1)).isoformat()
    store.save_state({"lastRun": old, "summary": ""})
    assert runner.scan_if_due() is True and started == [1]
    store.save_state({"lastRun": None, "summary": ""})
    assert runner.scan_if_due() is True and started == [1, 1]


def test_job_runner_draft_only_drafts_new_or_ready_rows(store, monkeypatch):
    store.save_postings({"a": row("Acme"), "r": row("Ready Co", status="ready"),
                         "x": row("Applied Co", status="applied", appliedOn="2026-10-02")})
    store.save_resume(RESUME)
    store.save_settings({"provider": "claude"})
    fake = FakeProvider()
    monkeypatch.setitem(ai.PROVIDERS, "claude", fake)
    runner = JobRunner(store)
    assert runner.draft(["a", "r", "x", "missing"])
    assert wait_until(lambda: not runner.status()["running"])
    s = runner.status()
    assert s["error"] is None and s["result"]["ready"] == 2
    asked = [p["id"] for p in prompt_postings(fake.draft_calls[0]["prompt"])]
    assert asked == ["a", "r"]
    saved = store.load_postings()
    assert saved["a"]["status"] == "ready" and saved["r"]["status"] == "ready"
    assert saved["x"] == row("Applied Co", status="applied", appliedOn="2026-10-02")


def test_job_runner_draft_refuses_rows_that_are_not_new_or_ready(store, monkeypatch):
    store.save_postings({"x": row("Applied Co", status="applied")})
    store.save_resume(RESUME)
    store.save_settings({"provider": "claude"})
    fake = FakeProvider()
    monkeypatch.setitem(ai.PROVIDERS, "claude", fake)
    runner = JobRunner(store)
    assert runner.draft(["x"])
    assert wait_until(lambda: not runner.status()["running"])
    assert runner.status()["error"] and fake.calls == []
