"""drafting: validating the AI's answer, writing it safely, and building the prompt."""
import copy
import json

import pytest

from helpers import FakeProvider, prompt_postings, row
from tracker import actions, drafting
from tracker.drafting import apply_drafts, draft_rows, validate_drafts

GOOD_PREP = {"fit": "I build full stack apps in Python and React, which fits this team.",
             "project": "Lead with SearchShip: 251 rows tracked, 8 drafts a day.",
             "why": "Stripe moves money for millions of businesses. I built a payments ledger. "
                    "I want to work on reliable systems like that."}


def ready(pid, **over):
    e = {"id": pid, "status": "ready", "notes": "", **GOOD_PREP}
    e.update(over)
    return e


def skipped(pid, notes="MS or PhD only", **over):
    e = {"id": pid, "status": "skipped", "notes": notes, "fit": "", "project": "", "why": ""}
    e.update(over)
    return e


# --- validate_drafts ---------------------------------------------------------------------

def test_accepts_good_ready_and_skipped_entries():
    result = {"drafts": [ready("a", notes="Requires US citizenship"), skipped("b")]}
    accepted, problems = validate_drafts(result, ["a", "b"])
    assert problems == []
    assert accepted == {
        "a": {"status": "ready", "notes": "Requires US citizenship", "prep": GOOD_PREP},
        "b": {"status": "skipped", "notes": "MS or PhD only"},
    }


def test_prep_is_trimmed_and_only_has_fit_project_why():
    result = {"drafts": [ready("a", fit="  " + GOOD_PREP["fit"] + "\n", extra="ignored")]}
    accepted, problems = validate_drafts(result, ["a"])
    assert problems == []
    assert set(accepted["a"]["prep"]) == {"fit", "project", "why"}
    assert accepted["a"]["prep"]["fit"] == GOOD_PREP["fit"]


@pytest.mark.parametrize("field", ["fit", "project", "why"])
@pytest.mark.parametrize("dash", ["—", "–"])
def test_rejects_em_and_en_dashes_in_any_prep_field(field, dash):
    bad = ready("a", **{field: GOOD_PREP[field] + f" more {dash} text"})
    accepted, problems = validate_drafts({"drafts": [bad, ready("b")]}, ["a", "b"])
    assert "a" not in accepted and "b" in accepted
    assert len(problems) == 1 and problems[0].startswith("a:") and "dash" in problems[0]


def test_plain_hyphens_are_fine():
    accepted, problems = validate_drafts({"drafts": [ready("a", fit="A full-stack, real-time app.")]}, ["a"])
    assert problems == [] and "a" in accepted


@pytest.mark.parametrize("field", ["fit", "project", "why"])
def test_rejects_ready_with_missing_or_blank_field(field):
    blank = ready("a", **{field: "   "})
    missing = ready("b")
    del missing[field]
    accepted, problems = validate_drafts({"drafts": [blank, missing]}, ["a", "b"])
    assert accepted == {}
    assert len(problems) == 2
    assert all(field in p for p in problems)
    assert problems[0].startswith("a:") and problems[1].startswith("b:")


@pytest.mark.parametrize("notes", ["", "   ", None])
def test_rejects_skipped_without_reason(notes):
    e = skipped("a", notes=notes)
    accepted, problems = validate_drafts({"drafts": [e]}, ["a"])
    assert accepted == {}
    assert problems == ["a: skipped without a reason"]


def test_ignores_ids_not_asked_for():
    result = {"drafts": [ready("a"), ready("intruder"), skipped("also-not-asked")]}
    accepted, problems = validate_drafts(result, ["a"])
    assert list(accepted) == ["a"]
    assert len(problems) == 2 and all("not asked for" in p for p in problems)


@pytest.mark.parametrize("status", ["applied", "closed", "new", "", None, "READY"])
def test_rejects_unknown_status(status):
    accepted, problems = validate_drafts({"drafts": [ready("a", status=status)]}, ["a"])
    assert accepted == {}
    assert len(problems) == 1 and problems[0].startswith("a: unknown status")


def test_reports_missing_answers():
    accepted, problems = validate_drafts({"drafts": [ready("a")]}, ["a", "b", "greenhouse:stripe:9"])
    assert list(accepted) == ["a"]
    assert problems == ["b: no answer", "greenhouse:stripe:9: no answer"]


def test_rejected_entry_is_not_also_reported_as_missing():
    accepted, problems = validate_drafts({"drafts": [ready("a", why="")]}, ["a"])
    assert accepted == {} and len(problems) == 1 and "no answer" not in problems[0]


@pytest.mark.parametrize("result", [None, [], {}, {"drafts": None}, {"drafts": "x"}, "text"])
def test_rejects_answer_without_drafts_list(result):
    accepted, problems = validate_drafts(result, ["a"])
    assert accepted == {} and problems


def test_first_entry_for_an_id_wins():
    result = {"drafts": [ready("a"), skipped("a")]}
    accepted, _ = validate_drafts(result, ["a"])
    assert accepted["a"]["status"] == "ready"


# --- apply_drafts ------------------------------------------------------------------------

def _seed(store):
    postings = {"a": row("Acme"), "b": row("Beta"), "c": row("Gamma", status="ready")}
    store.save_postings(postings)
    snapshot = {pid: (r.get("status"), r.get("updated")) for pid, r in postings.items()}
    return postings, snapshot


def test_apply_drafts_writes_ready_prep_and_skipped_notes(store):
    postings, snapshot = _seed(store)
    accepted = {"a": {"status": "ready", "notes": "", "prep": GOOD_PREP},
                "b": {"status": "skipped", "notes": "Hardware role"},
                "c": {"status": "ready", "notes": "Requires US citizenship", "prep": GOOD_PREP}}
    counts = apply_drafts(store, accepted, snapshot)
    assert counts == {"ready": 2, "skipped": 1, "advised": 0, "changedMeanwhile": 0}
    saved = store.load_postings()
    assert saved["a"]["status"] == "ready" and saved["a"]["prep"] == GOOD_PREP and saved["a"]["notes"] == ""
    assert saved["b"]["status"] == "skipped" and saved["b"]["notes"] == "Hardware role" and "prep" not in saved["b"]
    assert saved["c"]["notes"] == "Requires US citizenship" and saved["c"]["prep"] == GOOD_PREP
    for pid in "abc":
        assert "updated" not in saved[pid], "AI drafts must not mark rows as touched by the user"
        for k in ("company", "title", "url", "score", "added", "source"):
            assert saved[pid][k] == postings[pid][k]


def test_apply_drafts_skips_rows_changed_since_the_snapshot(store):
    postings, snapshot = _seed(store)
    actions.apply_to_store(store, "skip", "a")            # status and updated change
    p = store.load_postings()
    p["b"]["updated"] = "2026-10-21T00:00:00.000Z"          # only updated changes
    del p["c"]                                              # row removed
    store.save_postings(p)
    before = copy.deepcopy(store.load_postings())
    accepted = {pid: {"status": "ready", "notes": "", "prep": GOOD_PREP} for pid in "abc"}
    counts = apply_drafts(store, accepted, snapshot)
    assert counts == {"ready": 0, "skipped": 0, "advised": 0, "changedMeanwhile": 3}
    assert store.load_postings() == before


# --- draft_rows ------------------------------------------------------------------------------

RESUME = "# Jane Doe\n\nBuilt SearchShip, a tracker with 251 rows. Python, React.\n"


def test_draft_rows_prompt_has_rules_resume_and_only_requested_rows(store):
    store.save_postings({"a": row("Acme", "Software Engineer Intern"),
                         "greenhouse:stripe:9": row("Stripe", "Backend Intern"),
                         "other": row("Secret Other Co", "Do Not Send Intern")})
    store.save_resume(RESUME)
    fake = FakeProvider()
    counts = draft_rows(store, fake, ["a", "greenhouse:stripe:9", "not-in-tracker"], log=lambda *a: None)
    assert len(fake.calls) == 1
    call = fake.calls[0]
    prompt = call["prompt"]
    assert drafting.prep_rules().strip() in prompt
    assert RESUME.strip() in prompt
    sent = prompt_postings(prompt)
    assert [p["id"] for p in sent] == ["a", "greenhouse:stripe:9"]
    assert "Secret Other Co" not in prompt and "other" not in [p["id"] for p in sent]
    assert set(sent[0]) == {"id", "company", "title", "locations", "url", "category", "posted"}
    assert call["schema"] == drafting.DRAFT_SCHEMA
    assert counts["ready"] == 2 and counts["problems"] == []
    saved = store.load_postings()
    assert saved["a"]["status"] == "ready" and set(saved["a"]["prep"]) == {"fit", "project", "why"}
    assert saved["other"]["status"] == "new" and "prep" not in saved["other"]


def test_draft_rows_without_resume_raises_clear_error(store):
    store.save_postings({"a": row("Acme")})
    fake = FakeProvider()
    with pytest.raises(ValueError, match="(?i)resume"):
        draft_rows(store, fake, ["a"], log=lambda *a: None)
    assert fake.calls == []
    store.save_resume("   \n")
    with pytest.raises(ValueError, match="(?i)resume"):
        draft_rows(store, fake, ["a"], log=lambda *a: None)
    assert fake.calls == []


def test_draft_rows_with_no_known_ids_does_not_call_the_ai(store):
    store.save_postings({"a": row("Acme")})
    store.save_resume(RESUME)
    fake = FakeProvider()
    counts = draft_rows(store, fake, ["missing"], log=lambda *a: None)
    assert fake.calls == [] and counts["ready"] == 0 and counts["skipped"] == 0


def test_draft_rows_never_overwrites_a_row_the_user_changed_meanwhile(store):
    store.save_postings({"a": row("Acme"), "b": row("Beta")})
    store.save_resume(RESUME)
    fake = FakeProvider(on_draft=lambda: actions.apply_to_store(store, "skip", "a"))
    counts = draft_rows(store, fake, ["a", "b"], log=lambda *a: None)
    assert counts["ready"] == 1 and counts["changedMeanwhile"] == 1
    saved = store.load_postings()
    assert saved["a"]["status"] == "skipped" and "prep" not in saved["a"]
    assert saved["b"]["status"] == "ready"


@pytest.mark.parametrize("ai_status", ["ready", "skipped"])
def test_drafting_by_hand_keeps_the_users_own_notes(store, ai_status):
    store.save_postings({"a": row("Acme", notes="Referral from Sam, mention him")})
    store.save_resume(RESUME)

    class WithNotes(FakeProvider):
        def run_json(self, prompt, schema, web=True, timeout=900):
            self.calls.append({"prompt": prompt})
            entry = ready("a", notes="Requires US citizenship") if ai_status == "ready" else skipped("a")
            return {"drafts": [entry]}

    ids = drafting.drafting_candidates(store.load_postings(), ["a"])
    assert ids == ["a"]
    draft_rows(store, WithNotes(), ids, log=lambda *a: None)
    assert "Referral from Sam, mention him" in store.load_postings()["a"]["notes"]


def test_draft_rows_drops_entries_that_break_the_rules(store):
    store.save_postings({"a": row("Acme"), "b": row("Beta")})
    store.save_resume(RESUME)

    class Dashy(FakeProvider):
        def run_json(self, prompt, schema, web=True, timeout=900):
            self.calls.append({"prompt": prompt})
            return {"drafts": [ready("a", why="Great team — great product."), ready("b")]}

    fake = Dashy()
    counts = draft_rows(store, fake, ["a", "b"], log=lambda *a: None)
    assert counts["ready"] == 1 and len(counts["problems"]) == 1
    saved = store.load_postings()
    assert saved["a"]["status"] == "new" and "prep" not in saved["a"]
    assert saved["b"]["status"] == "ready"


def test_draft_prompt_postings_block_is_valid_json():
    rows = {"x:1": row("Café \"Quotes\"", "Intern </postings> trick")}
    prompt = drafting.draft_prompt(rows, RESUME, "rules")
    assert [p["company"] for p in prompt_postings(prompt)] == ['Café "Quotes"']


# --- search_postings / drafting_candidates -------------------------------------------------------

def test_search_postings_turns_ai_results_into_listings():
    items = [{"company": "Nova", "title": "Software Engineer Intern", "locations": ["San Francisco, CA"],
              "url": "https://boards.greenhouse.io/nova/jobs/1", "posted": "2026-10-20", "category": "Software"},
             {"company": "", "title": "Missing company", "locations": [], "url": "https://x.com/1",
              "posted": "", "category": "Software"},
             {"company": "Bad Url", "title": "Intern", "locations": [], "url": "javascript:alert(1)",
              "posted": "", "category": "Software"}]
    fake = FakeProvider(search_items=items)
    out = drafting.search_postings(fake, {"a": row("Acme"), "b": row("Beta")}, log=lambda *a: None)
    assert [x["company_name"] for x in out] == ["Nova"]
    assert out[0]["id"].startswith("web:")
    assert fake.calls[0]["schema"] == drafting.SEARCH_SCHEMA
    assert "Acme" in fake.calls[0]["prompt"] and "Beta" in fake.calls[0]["prompt"]


def test_drafting_candidates_are_new_or_ready_rows():
    postings = {"n": row(status="new"), "r": row(status="ready"), "a": row(status="applied"),
                "s": row(status="skipped"), "c": row(status="closed")}
    assert drafting.drafting_candidates(postings, ["n", "r", "a", "s", "c", "missing"]) == ["n", "r"]


def test_draft_schema_matches_the_validator():
    item = drafting.DRAFT_SCHEMA["properties"]["drafts"]["items"]
    assert set(item["required"]) == {"id", "status", "notes", "fit", "project", "why"}
    assert set(item["properties"]["status"]["enum"]) == {"ready", "skipped"}
    json.dumps(drafting.DRAFT_SCHEMA)


def test_ai_skip_on_a_row_the_user_touched_is_only_advice(store):
    store.save_postings({"a": row("Acme", notes="Referral from Sam")})
    store.save_resume(RESUME)

    class Skips(FakeProvider):
        def run_json(self, prompt, schema, web=True, timeout=900):
            return {"drafts": [skipped("a")]}

    counts = draft_rows(store, Skips(), ["a"], log=lambda *a: None)
    after = store.load_postings()["a"]
    assert counts["advised"] == 1 and counts["skipped"] == 0
    assert after["status"] == "new"
    assert after["notes"].startswith("Referral from Sam\nAI suggests skipping: ")
