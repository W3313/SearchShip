"""actions: the approve / skip / applied / interview / restore / note buttons."""
import re
from datetime import datetime, timezone

import pytest

from helpers import listing, row
from tracker import actions, pipeline
from tracker.actions import ActionError, parse_command
from tracker.store import touched

STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")


@pytest.mark.parametrize("text, expected", [
    ("approve abc-123", ("approve", "abc-123", None)),
    ("skip 0315cabd-eed9-4fb0-bf72-2770478f6bb9", ("skip", "0315cabd-eed9-4fb0-bf72-2770478f6bb9", None)),
    ("applied greenhouse:stripe:123", ("applied", "greenhouse:stripe:123", None)),
    ("interview lever:plaid:5b1e-77aa", ("interview", "lever:plaid:5b1e-77aa", None)),
    ("restore web:0a1b2c3d4e5f6a7b", ("restore", "web:0a1b2c3d4e5f6a7b", None)),
    ("restore lead-alameda-itd", ("restore", "lead-alameda-itd", None)),
    ("  Approve   manual-bytedance-rdb  ", ("approve", "manual-bytedance-rdb", None)),
    ("APPLIED a.b_c", ("applied", "a.b_c", None)),
    ("note abc-123: call back Monday", ("note", "abc-123", "call back Monday")),
    ("note greenhouse:stripe:123: Referral: Sam, call at 3:30",
     ("note", "greenhouse:stripe:123", "Referral: Sam, call at 3:30")),
    ("note ashby:openai:9f8e:   spaced out   ", ("note", "ashby:openai:9f8e", "spaced out")),
    ("Note abc: line one\nline two", ("note", "abc", "line one\nline two")),
])
def test_parse_command(text, expected):
    assert parse_command(text) == expected


@pytest.mark.parametrize("text", [
    "", None, "approve", "approve ", "delete abc", "approve abc def", "approve a/b",
    "note abc", "note greenhouse:stripe:123", "approve abc: some text", "skip abc: why",
    "approveabc", "approve <script>",
])
def test_parse_command_rejects_bad_input(text):
    with pytest.raises(ActionError):
        parse_command(text)


@pytest.mark.parametrize("text", ["note abc-123:", "note abc-123:   ", "note greenhouse:stripe:1:"])
def test_parse_command_rejects_empty_note(text):
    with pytest.raises(ActionError):
        parse_command(text)


def test_action_error_is_a_value_error():
    assert issubclass(ActionError, ValueError)


@pytest.fixture
def postings():
    return {
        "a": row("Acme", status="new"),
        "b": row("Beta", status="ready", prep={"fit": "f", "project": "p", "why": "w"}),
        "c": row("Closed Co", status="closed"),
        "greenhouse:stripe:123": row("Stripe", status="new", source="Greenhouse"),
    }


@pytest.mark.parametrize("cmd, status", [
    ("approve", "ready"), ("skip", "skipped"), ("applied", "applied"),
    ("interview", "interview"), ("restore", "new"),
])
def test_status_actions(postings, cmd, status):
    r = actions.apply(postings, cmd, "a")
    assert r is postings["a"]
    assert r["status"] == status
    assert STAMP.match(r["updated"])
    assert touched(r)


def test_applied_sets_applied_on_to_today_utc(postings):
    before = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = actions.apply(postings, "applied", "greenhouse:stripe:123")
    after = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert r["appliedOn"] in {before, after}   # UTC date, even across midnight
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", r["appliedOn"])
    assert r["status"] == "applied"
    updated = datetime.fromisoformat(r["updated"].replace("Z", "+00:00"))
    assert abs((datetime.now(timezone.utc) - updated).total_seconds()) < 60


def test_other_actions_do_not_set_applied_on(postings):
    for cmd in ("approve", "skip", "interview", "restore"):
        assert "appliedOn" not in actions.apply(postings, cmd, "a")


def test_note_replaces_notes_and_keeps_status(postings):
    postings["b"]["notes"] = "old note"
    r = actions.apply(postings, "note", "b", "  New note: with a colon  ")
    assert r["notes"] == "New note: with a colon"
    assert r["status"] == "ready"
    assert r["prep"] == {"fit": "f", "project": "p", "why": "w"}
    assert STAMP.match(r["updated"])


def test_every_action_sets_updated(postings):
    for cmd in actions.COMMANDS:
        postings["a"].pop("updated", None)
        actions.apply(postings, cmd, "a", "text" if cmd == "note" else None)
        assert STAMP.match(postings["a"]["updated"]), cmd


def test_actions_leave_other_rows_alone(postings):
    import copy
    before = copy.deepcopy(postings)
    actions.apply(postings, "skip", "a")
    for pid in ("b", "c", "greenhouse:stripe:123"):
        assert postings[pid] == before[pid]


def test_unknown_id_raises(postings):
    with pytest.raises(ActionError):
        actions.apply(postings, "approve", "nope")


def test_unknown_command_raises(postings):
    with pytest.raises(ActionError):
        actions.apply(postings, "delete", "a")
    assert "updated" not in postings["a"]


def test_apply_to_store_persists(store, postings):
    store.save_postings(postings)
    r = actions.apply_to_store(store, "applied", "greenhouse:stripe:123")
    saved = store.load_postings()
    assert saved["greenhouse:stripe:123"] == r
    assert saved["greenhouse:stripe:123"]["status"] == "applied"
    with pytest.raises(ActionError):
        actions.apply_to_store(store, "approve", "missing")


def test_restored_row_is_not_closed_again_by_the_scan(postings):
    """restore marks the row touched, so housekeeping must not re-close it."""
    actions.apply(postings, "restore", "c")
    fetched = [pipeline.Fetched("SimplifyJobs", [listing("other", "Other")],
                                lambda pid, r: r.get("source") == "SimplifyJobs")]
    _, closed, _ = pipeline.merge(postings, fetched, set())
    assert "c" not in closed and "a" in closed
