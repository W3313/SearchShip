"""store: postings import, settings, touched(), and where data lives."""
import json
import os
from pathlib import Path

import pytest

from helpers import row
from tracker import store as store_mod
from tracker.paths import data_dir
from tracker.store import DEFAULT_SETTINGS, Store, touched


def test_store_uses_searchship_home_when_no_root_given(monkeypatch, tmp_path):
    home = tmp_path / "custom-home"
    monkeypatch.setenv("SEARCHSHIP_HOME", str(home))
    s = Store()
    assert s.root == home and home.is_dir()
    assert data_dir() == home


def test_new_store_gets_a_skip_list(store):
    assert store.skip_path.exists()
    bundled = (Path(__file__).resolve().parent.parent / "skip_companies.txt").read_text(encoding="utf-8")
    assert store.skip_text() == bundled


def test_empty_store_defaults(store):
    assert store.load_postings() == {}
    assert store.load_state() == {"lastRun": None, "summary": ""}
    assert store.resume_text() == ""


def test_save_postings_format(store):
    store.save_postings({"b": {"z": 1, "a": "caf\u00e9"}, "a": {"y": [1, 2]}})
    text = store.postings_path.read_text(encoding="utf-8")
    assert text == '{\n "a": {\n  "y": [\n   1,\n   2\n  ]\n },\n "b": {\n  "a": "caf\u00e9",\n  "z": 1\n }\n}\n'
    assert not list(store.root.glob("*.tmp"))


# --- import_postings --------------------------------------------------------------------------

def test_import_keeps_existing_rows_and_adds_new_ones(store):
    existing = row("Acme", status="applied", appliedOn="2026-10-02", notes="mine")
    store.save_postings({"a": existing})
    incoming = {"a": row("Acme", status="new", notes=""), "b": row("Beta", status="ready"),
                "c": {"company": "Gamma", "title": "SWE Intern"}}
    result = store.import_postings(incoming)
    assert result == {"added": 2, "replaced": 0, "kept": 1, "total": 3}
    saved = store.load_postings()
    assert saved["a"] == existing
    assert saved["b"] == incoming["b"]
    c = saved["c"]
    assert c["company"] == "Gamma" and c["status"] == "new" and c["notes"] == ""
    assert c["locations"] == [] and c["source"] == "manual" and c["added"]


def test_import_skips_junk_rows(store):
    result = store.import_postings({"ok": row("Acme"), "no-title": {"company": "X"},
                                    "no-company": {"title": "Y"}, "list": [1, 2], "str": "row",
                                    "empty": {}, "blank": {"company": "", "title": ""}})
    assert result["added"] == 1
    assert list(store.load_postings()) == ["ok"]


@pytest.mark.parametrize("junk", [[], [row()], "text", 3, None])
def test_import_rejects_non_objects(store, junk):
    with pytest.raises(ValueError):
        store.import_postings(junk)
    assert not store.postings_path.exists()


def test_import_rejects_all_junk(store):
    store.save_postings({"a": row("Acme")})
    with pytest.raises(ValueError):
        store.import_postings({"x": {"foo": 1}, "y": "bar"})
    assert list(store.load_postings()) == ["a"]


def test_import_of_the_seed_shape_round_trips(store):
    seed = {f"id-{i}": row(f"Co {i}", status=s) for i, s in
            enumerate(["new", "ready", "applied", "interview", "skipped", "closed"])}
    seed["id-2"]["prep"] = {"fit": "f", "project": "p", "why": "w"}
    assert store.import_postings(json.loads(json.dumps(seed)))["added"] == 6
    assert store.load_postings() == seed


# --- settings --------------------------------------------------------------------------------------

def test_settings_default(store):
    s = store.load_settings()
    assert s == DEFAULT_SETTINGS
    s["sources"]["simplify"] = False
    s["boards"].append("greenhouse:x")
    assert DEFAULT_SETTINGS["sources"]["simplify"] is True, "defaults must not be shared/mutated"
    assert "greenhouse:x" not in DEFAULT_SETTINGS["boards"]
    assert store.load_settings() == DEFAULT_SETTINGS


def test_settings_merge_with_defaults(store):
    store.settings_path.write_text(json.dumps({
        "bogus": 1, "autoScan": False, "provider": "codex",
        "sources": {"vanshb03": False, "nope": True},
    }), encoding="utf-8")
    s = store.load_settings()
    assert "bogus" not in s
    assert s["autoScan"] is False and s["provider"] == "codex"
    assert s["sources"] == {"simplify": True, "vanshb03": False, "boards": True, "websearch": True}
    assert s["boards"] == DEFAULT_SETTINGS["boards"]
    assert s["onboarded"] is False


def test_save_settings_merges_and_validates(store):
    s = store.save_settings({"sources": {"boards": False, "bogus": True}, "provider": "claude", "junk": 5})
    assert s["sources"] == {"simplify": True, "vanshb03": True, "boards": False, "websearch": True}
    assert s["provider"] == "claude" and "junk" not in s
    s = store.save_settings({"sources": {"websearch": 0}})
    assert s["sources"]["boards"] is False and s["sources"]["websearch"] is False
    assert json.loads(store.settings_path.read_text(encoding="utf-8")) == store.load_settings() == s


@pytest.mark.parametrize("value", ["gpt", "Claude", "", 1, {"x": 1}])
def test_save_settings_rejects_unknown_provider(store, value):
    store.save_settings({"provider": "codex"})
    assert store.save_settings({"provider": value})["provider"] is None


def test_save_settings_cleans_boards(store):
    s = store.save_settings({"boards": ["  greenhouse:stripe = Stripe ", "", "   ", "lever:plaid"]})
    assert s["boards"] == ["greenhouse:stripe = Stripe", "lever:plaid"]


def test_save_resume_and_skip_list(store):
    store.save_resume("  # Me\n\nPython  \n\n")
    assert store.resume_text() == "# Me\n\nPython\n"
    store.save_skip("  Acme \n\n Beta\n")
    assert store.skip_text() == "Acme\nBeta\n"
    store.save_skip("")
    assert store.skip_text() == ""


# --- touched -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra, expected", [
    ({}, False),
    ({"status": "ready"}, False),
    ({"status": "closed"}, False),
    ({"notes": ""}, False),
    ({"notes": None}, False),
    ({"prep": {"fit": "a", "project": "b", "why": "c"}, "status": "ready"}, False),
    ({"appliedOn": "2026-10-02"}, True),
    ({"updated": "2026-10-02T05:02:00.581Z"}, True),
    ({"notes": "call recruiter"}, True),
    ({"status": "applied"}, True),
    ({"status": "interview"}, True),
    ({"status": "skipped"}, True),
])
def test_touched(extra, expected):
    assert touched(row(**extra)) is expected


def test_statuses():
    assert set(store_mod.STATUSES) == {"new", "ready", "applied", "interview", "skipped", "closed"}


def test_timestamps():
    import re
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", store_mod.now_iso())
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", store_mod.now_stamp())
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", store_mod.today())


def test_write_atomic_replaces_file(tmp_path):
    p = tmp_path / "x.json"
    store_mod.write_atomic(p, "one")
    store_mod.write_atomic(p, "two")
    assert p.read_text(encoding="utf-8") == "two"
    assert sorted(os.listdir(tmp_path)) == ["x.json"]


def test_import_restores_history_onto_rows_the_scan_added(tmp_path):
    store = Store(root=tmp_path)
    # The first scan added these before the user imported the old tracker.
    store.save_postings({
        "gm": {"company": "GM", "title": "ML Intern", "status": "new", "notes": "", "source": "SimplifyJobs"},
        "mine": {"company": "Acme", "title": "SWE Intern", "status": "skipped", "notes": "",
                 "updated": "2026-10-21T01:00:00.000Z", "source": "SimplifyJobs"},
        "plain": {"company": "Zed", "title": "SWE Intern", "status": "new", "notes": "", "source": "SimplifyJobs"},
    })
    result = store.import_postings({
        "gm": {"company": "GM", "title": "ML Intern", "status": "applied", "appliedOn": "2026-10-15",
               "notes": "Referred by Sam", "prep": {"fit": "f", "project": "p", "why": "w"}},
        "mine": {"company": "Acme", "title": "SWE Intern", "status": "applied", "appliedOn": "2026-10-01"},
        "plain": {"company": "Zed", "title": "SWE Intern", "status": "new", "notes": ""},
    })
    rows = store.load_postings()
    assert result == {"added": 0, "replaced": 1, "kept": 2, "total": 3}
    assert rows["gm"]["status"] == "applied" and rows["gm"]["notes"] == "Referred by Sam"
    assert rows["mine"]["status"] == "skipped"   # acted on in SearchShip: kept
    assert rows["plain"]["source"] == "SimplifyJobs"  # nothing to restore: kept
