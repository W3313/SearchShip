"""pipeline.merge (pure merge, housekeeping, draft picks), fetch_all and run_scan."""
import copy
import json
import re
from datetime import datetime, timezone

import pytest

import scan as scanner
from helpers import FakeGet, listing, row, vansh_listing
from tracker import pipeline, sources
from tracker.pipeline import Fetched, merge

ADDED = "2026-10-21T15:00:00+00:00"
NEW_ROW_KEYS = {"company", "title", "locations", "url", "category", "posted", "score",
                "status", "added", "source", "notes"}


def simplify(listings):
    return Fetched("SimplifyJobs", listings, lambda pid, r: r.get("source") == "SimplifyJobs")


def vansh(listings):
    return Fetched("vanshb03", listings, lambda pid, r: r.get("source") == "vanshb03")


# --- new rows ---------------------------------------------------------------------

def test_new_rows_have_status_new_added_source_and_scan_fields():
    lst = [listing("a1", "Acme", "Software Engineer Intern",
                   ["San Francisco, CA", "Remote", "NYC", "Austin, TX", "Seattle, WA"], 1)]
    new, closed, to_draft = merge({}, [simplify(lst)], set(), added_at=ADDED)
    assert list(new) == ["a1"] and closed == []
    r = new["a1"]
    assert set(r) == NEW_ROW_KEYS
    assert "terms" not in r and "id" not in r
    assert r["status"] == "new"
    assert r["added"] == ADDED
    assert r["source"] == "SimplifyJobs"
    assert r["notes"] == ""
    expected = scanner.scan(lst, set(), set(), pipeline.DAYS, pipeline.TOP)[0]
    for k in ("company", "title", "locations", "url", "category", "posted"):
        assert r[k] == expected[k], k
    assert r["locations"] == lst[0]["locations"][:4]
    assert r["score"] == pytest.approx(expected["score"], abs=0.01)


def test_added_defaults_to_now_utc_iso():
    new, _, _ = merge({}, [simplify([listing("a1")])], set())
    added = datetime.fromisoformat(new["a1"]["added"])
    assert added.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - added).total_seconds()) < 60


def test_each_new_row_keeps_its_own_source_label():
    fetched = [simplify([listing("s1", "Alpha")]),
               vansh(sources.fetch_vansh(lambda url: [vansh_listing("v1", "Beta")])),
               Fetched("Greenhouse", [listing("greenhouse:gamma:1", "Gamma")],
                       lambda pid, r: pid.startswith("greenhouse:gamma:")),
               Fetched("Web search", [listing("web:abc", "Delta")])]
    new, _, _ = merge({}, fetched, set(), added_at=ADDED)
    assert {pid: r["source"] for pid, r in new.items()} == {
        "s1": "SimplifyJobs", "v1": "vanshb03", "greenhouse:gamma:1": "Greenhouse", "web:abc": "Web search"}


def test_existing_ids_are_never_re_added_or_overwritten():
    postings = {"a1": row("Acme", status="applied", appliedOn="2026-10-01"),
                "a2": row("Acme2", status="closed")}
    before = copy.deepcopy(postings)
    lst = [listing("a1", "Acme"), listing("a2", "Acme2"), listing("a3", "Acme3")]
    new, _, _ = merge(postings, [simplify(lst)], set(), added_at=ADDED)
    assert list(new) == ["a3"]
    assert postings == before


def test_skip_companies_are_respected():
    lst = [listing("a1", "ByteDance"), listing("a2", "Acme")]
    new, _, _ = merge({}, [simplify(lst)], {"bytedance"}, added_at=ADDED)
    assert list(new) == ["a2"]


def test_merge_does_not_mutate_inputs():
    postings = {"old": row("Old Co")}
    lst = [listing("a1", "Acme")]
    before_p, before_l = copy.deepcopy(postings), copy.deepcopy(lst)
    merge(postings, [simplify(lst)], set(), added_at=ADDED)
    assert postings == before_p and lst == before_l


# --- housekeeping -------------------------------------------------------------------

TOUCHED = {
    "t-applied-on": dict(appliedOn="2026-10-02"),
    "t-updated": dict(updated="2026-10-02T05:02:00.581Z"),
    "t-notes": dict(notes="Recruiter said apply after Nov 1"),
    "t-status-applied": dict(status="applied"),
    "t-status-interview": dict(status="interview"),
    "t-status-skipped": dict(status="skipped", notes=""),
    "t-ready-updated": dict(status="ready", updated="2026-10-03T00:00:00.000Z"),
}


@pytest.mark.parametrize("pid", sorted(TOUCHED))
@pytest.mark.parametrize("presence", ["inactive", "missing"])
def test_touched_rows_are_never_changed(pid, presence):
    postings = {pid: row(company=pid, **TOUCHED[pid])}
    before = copy.deepcopy(postings)
    lst = [listing("other", "Other Co")]
    if presence == "inactive":
        lst.append(listing(pid, pid, active=False))
    new, closed, to_draft = merge(postings, [simplify(lst)], set(), added_at=ADDED)
    assert pid not in closed
    assert pid not in new
    assert postings == before


@pytest.mark.parametrize("status", ["new", "ready"])
def test_untouched_simplify_rows_close_when_missing(status):
    postings = {"gone": row("Gone Co", status=status), "here": row("Here Co", status=status)}
    lst = [listing("here", "Here Co")]
    _, closed, _ = merge(postings, [simplify(lst)], set(), added_at=ADDED)
    assert closed == ["gone"]


@pytest.mark.parametrize("status", ["new", "ready"])
def test_untouched_simplify_rows_close_when_inactive(status):
    postings = {"dead": row("Dead Co", status=status)}
    lst = [listing("dead", "Dead Co", active=False)]
    _, closed, _ = merge(postings, [simplify(lst)], set(), added_at=ADDED)
    assert closed == ["dead"]


def test_already_closed_rows_are_not_reported_again():
    postings = {"c": row("C", status="closed")}
    _, closed, _ = merge(postings, [simplify([])], set(), added_at=ADDED)
    assert closed == []


def test_rows_from_other_sources_are_left_alone():
    postings = {
        "manual-acme": row("Acme", source="manual"),
        "lead-indeed": row("Indeed lead", source="lead"),
        "v-1": row("Vansh Co", source="vanshb03"),
        "web:abc": row("Web Co", source="Web search"),
        "s-1": row("Simplify Co", source="SimplifyJobs"),
    }
    _, closed, _ = merge(postings, [simplify([listing("x", "X")])], set(), added_at=ADDED)
    assert closed == ["s-1"]


def test_vanshb03_rows_close_only_when_vanshb03_was_fetched():
    postings = {"v-gone": row("Vansh Gone", source="vanshb03"),
                "v-here": row("Vansh Here", source="vanshb03"),
                "v-touched": row("Vansh Touched", source="vanshb03", notes="keep")}
    vl = sources.fetch_vansh(lambda url: [vansh_listing("v-here", "Vansh Here")])
    _, closed, _ = merge(postings, [simplify([])], set(), added_at=ADDED)
    assert closed == []
    _, closed, _ = merge(postings, [simplify([]), vansh(vl)], set(), added_at=ADDED)
    assert closed == ["v-gone"]


def test_failed_source_closes_nothing():
    postings = {"s-1": row("S1"), "s-2": row("S2", status="ready")}
    # SimplifyJobs failed, so only vanshb03 is in the fetched list
    vl = sources.fetch_vansh(lambda url: [vansh_listing("v1", "V1")])
    _, closed, _ = merge(postings, [vansh(vl)], set(), added_at=ADDED)
    assert closed == []
    _, closed, _ = merge(postings, [], set(), added_at=ADDED)
    assert closed == []


def test_web_search_rows_never_close():
    postings = {"web:1": row("Web One", source="Web search")}
    _, closed, _ = merge(postings, [Fetched("Web search", [])], set(), added_at=ADDED)
    assert closed == []


def gh_payload(*jobs):
    return {"jobs": [{"id": jid, "title": title, "location": {"name": "San Francisco, CA"},
                      "absolute_url": f"https://boards.greenhouse.io/x/jobs/{jid}",
                      "first_published": datetime.now(timezone.utc).isoformat()} for jid, title in jobs]}


def test_board_rows_close_only_when_their_own_board_was_fetched():
    settings = {"sources": {"simplify": False, "vanshb03": False, "boards": True},
                "boards": ["greenhouse:stripe = Stripe", "greenhouse:figma = Figma", "lever:plaid = Plaid"]}
    get = FakeGet({
        "https://boards-api.greenhouse.io/v1/boards/stripe/jobs": gh_payload((2, "Software Engineer Intern")),
        "https://boards-api.greenhouse.io/v1/boards/figma/jobs": sources.SourceError("HTTP 503"),
        "https://api.lever.co/v0/postings/plaid?mode=json": [],
    })
    fetched, errors = pipeline.fetch_all(settings, get, log=lambda *a: None)
    assert len(errors) == 1 and "figma" in errors[0].lower()
    postings = {
        "greenhouse:stripe:1": row("Stripe", "Old Intern", source="Greenhouse"),
        "greenhouse:stripe:2": row("Stripe", "Software Engineer Intern", source="Greenhouse"),
        "greenhouse:stripe:3": row("Stripe", "Touched Intern", source="Greenhouse", notes="mine"),
        "greenhouse:figma:9": row("Figma", "Design Eng Intern", source="Greenhouse"),
        "greenhouse:stripe-labs:4": row("Stripe Labs", "Intern", source="Greenhouse"),
        "lever:plaid:abc": row("Plaid", "SWE Intern", source="Lever"),
        "ashby:openai:x": row("OpenAI", "SWE Intern", source="Ashby"),
        "s-1": row("Simplify Co"),
    }
    _, closed, _ = merge(postings, fetched, set(), added_at=ADDED)
    assert sorted(closed) == ["greenhouse:stripe:1", "lever:plaid:abc"]


# --- duplicates across sources ---------------------------------------------------------

def test_non_simplify_duplicates_of_existing_rows_are_dropped():
    postings = {"s-old": row("Acme", "Software Engineer Intern", url="https://www.acme.com/jobs/1/")}
    fetched = [
        vansh(sources.fetch_vansh(lambda url: [
            vansh_listing("v-same-url", "Acme Corp", "Backend Intern",
                          url="http://acme.com/jobs/1?utm_source=simplify"),
            vansh_listing("v-same-pair", "  ACME ", "software engineer intern",
                          url="https://other.example.com/1"),
            vansh_listing("v-fresh", "Fresh Co", "Software Engineer Intern"),
        ])),
        Fetched("Greenhouse", [listing("greenhouse:acme:7", "Acme", "Software Engineer Intern",
                                       url="https://boards.greenhouse.io/acme/jobs/7")],
                lambda pid, r: pid.startswith("greenhouse:acme:")),
        Fetched("Web search", [listing("web:1", "Acme", "Software Engineer Intern",
                                       url="https://acme.com/jobs/1/apply")]),
    ]
    new, _, _ = merge(postings, fetched, set(), added_at=ADDED)
    assert set(new) == {"v-fresh"}


def test_simplify_listing_is_kept_even_if_company_and_title_match_an_existing_row():
    """SimplifyJobs ids are the tracker's own ids, so only the id decides (as in the original)."""
    postings = {"s-old": row("Acme", "Software Engineer Intern", status="closed")}
    new, _, _ = merge(postings, [simplify([listing("s-new", "Acme", "Software Engineer Intern")])], set(),
                      added_at=ADDED)
    assert set(new) == {"s-new"}


def test_same_job_from_two_sources_in_one_run_is_added_once_as_simplify():
    url = "https://jobs.example.com/acme/123"
    fetched = [simplify([listing("s1", "Acme", "Software Engineer Intern", url=url)]),
               vansh(sources.fetch_vansh(lambda u: [vansh_listing("v1", "Acme", "SWE Intern", url=url + "/")]))]
    new, _, _ = merge({}, fetched, set(), added_at=ADDED)
    assert list(new) == ["s1"] and new["s1"]["source"] == "SimplifyJobs"


def test_same_job_on_simplify_and_a_board_is_tracked_once_across_runs():
    simplify_copy = listing("s-stripe", "Stripe", "Software Engineer Intern", ["San Francisco, CA"], 5,
                            url="https://job-boards.greenhouse.io/stripe/jobs/123")
    board_copy = listing("greenhouse:stripe:123", "Stripe", "Software Engineer Intern",
                         ["San Francisco, CA", "Remote"], 1, url="https://stripe.com/jobs/search?gh_jid=123")

    def fetched():
        return [simplify([dict(simplify_copy)]),
                Fetched("Greenhouse", [dict(board_copy)], lambda pid, r: pid.startswith("greenhouse:stripe:"))]

    postings = {}
    for _ in range(2):
        new, _, _ = merge(postings, fetched(), set(), added_at=ADDED)
        postings.update(new)
    same = [pid for pid, r in postings.items()
            if (r["company"], r["title"]) == ("Stripe", "Software Engineer Intern")]
    assert len(same) == 1, same


# --- draft picks ----------------------------------------------------------------------

def test_draft_list_cap_threshold_and_order():
    # 11 listings score >= 4.5 (SF software, posted 0.3 .. 10.3 days ago), 3 score below.
    lst = [listing(f"d{i}", f"Company {i}", "Software Engineer Intern", ["San Francisco, CA"], i + 0.3)
           for i in range(14)]
    new, _, to_draft = merge({}, [simplify(lst)], set(), added_at=ADDED)
    assert len(new) == 14
    assert len(to_draft) == pipeline.DRAFT_CAP == 8
    assert all(pid in new and new[pid]["score"] >= 4.5 for pid in to_draft)
    scores = [new[pid]["score"] for pid in to_draft]
    assert scores == sorted(scores, reverse=True)
    eligible = sorted((p for p in new if new[p]["score"] >= 4.5), key=lambda p: new[p]["score"], reverse=True)
    assert len(eligible) == 11
    assert to_draft == eligible[:8]


def test_draft_list_only_scores_at_least_4_5():
    # posted 20 days ago -> no freshness bonus, so scores are exact: 3 + 1 + 0.5 = 4.5 and 3 + 1 = 4.0
    lst = [listing("exact", "Exact", "Software Engineer Intern", ["San Francisco, CA", "Remote"], 20),
           listing("below", "Below", "Software Engineer Intern", ["San Francisco, CA"], 20),
           listing("low", "Low", "Data Analyst Intern", ["San Jose, CA"], 1, category="AI/ML/Data")]
    new, _, to_draft = merge({}, [simplify(lst)], set(), added_at=ADDED, days=30)
    assert new["exact"]["score"] == 4.5 and new["below"]["score"] == 4.0
    assert to_draft == ["exact"]


def test_draft_list_empty_when_nothing_qualifies():
    lst = [listing("low", "Low", "Data Analyst Intern", ["San Jose, CA"], 1, category="AI/ML/Data")]
    new, _, to_draft = merge({}, [simplify(lst)], set(), added_at=ADDED)
    assert list(new) == ["low"] and to_draft == []


def test_draft_list_only_holds_rows_added_this_run():
    postings = {"old-high": row("Old High", score=9.0), "old-ready": row("Old Ready", status="ready", score=8.0)}
    lst = [listing("old-high", "Old High"), listing("old-ready", "Old Ready"),
           listing("fresh", "Fresh Co", "Software Engineer Intern", ["San Francisco, CA"], 0.5)]
    new, _, to_draft = merge(postings, [simplify(lst)], set(), added_at=ADDED)
    assert to_draft == ["fresh"]


# --- fetch_all -------------------------------------------------------------------------

def test_fetch_all_skips_disabled_sources_and_reports_failures():
    settings = {"sources": {"simplify": True, "vanshb03": True, "boards": False}, "boards": ["greenhouse:x"]}
    get = FakeGet({sources.SIMPLIFY_URL: sources.SourceError("boom"),
                   sources.VANSH_URL: [vansh_listing("v1")]})
    fetched, errors = pipeline.fetch_all(settings, get, log=lambda *a: None)
    assert [f.label for f in fetched] == ["vanshb03"]
    assert len(errors) == 1 and errors[0].startswith("SimplifyJobs")
    assert sorted(get.calls) == sorted([sources.SIMPLIFY_URL, sources.VANSH_URL])

    settings = {"sources": {"simplify": False, "vanshb03": False, "boards": False}, "boards": []}
    get = FakeGet({})
    assert pipeline.fetch_all(settings, get, log=lambda *a: None) == ([], [])
    assert get.calls == []


def test_fetch_all_reports_bad_board_spec():
    settings = {"sources": {"simplify": False, "vanshb03": False, "boards": True}, "boards": ["not a board"]}
    fetched, errors = pipeline.fetch_all(settings, FakeGet({}), log=lambda *a: None)
    assert fetched == [] and len(errors) == 1


# --- run_scan end to end ------------------------------------------------------------------

def seed_store(store):
    postings = {
        "s-touched": row("Touched Co", status="new", notes="Asked a friend for a referral"),
        "s-applied": row("Applied Co", status="applied", appliedOn="2026-10-02", updated="2026-10-02T05:00:00.000Z"),
        "s-stale": row("Stale Co", status="new"),
        "s-stale-ready": row("Stale Ready Co", status="ready",
                             prep={"fit": "a", "project": "b", "why": "c"}),
        "s-alive": row("Alive Co", status="new"),
        "manual-x": row("Manual Co", source="manual", status="new"),
    }
    store.save_postings(postings)
    return postings


def scan_get():
    simplify_listings = [
        listing("s-alive", "Alive Co"),
        listing("s-stale-ready", "Stale Ready Co", active=False),
        listing("s-applied", "Applied Co", active=False),
        listing("n1", "Nova", "Software Engineer Intern", ["San Francisco, CA"], 0.5),
        listing("n2", "Orbit", "Machine Learning Intern", ["Remote"], 2, category="AI/ML/Data"),
        listing("n3", "Pulse", "Data Analyst Intern", ["San Jose, CA"], 3, category="AI/ML/Data"),
        listing("n-nyc", "Gotham", "Software Engineer Intern", ["New York, NY"], 1),
    ]
    vansh_listings = [vansh_listing("v1", "Vega", "Backend Engineer Intern", ["Palo Alto, CA"], 1),
                      vansh_listing("v-fall", "Vega", "Frontend Engineer Intern", ["Palo Alto, CA"], 1, season="Fall")]
    return FakeGet({sources.SIMPLIFY_URL: simplify_listings, sources.VANSH_URL: vansh_listings})


def scan_settings(store):
    settings = store.load_settings()
    settings["sources"]["boards"] = False
    return settings


def test_run_scan_writes_postings_and_state(store):
    before = seed_store(store)
    get = scan_get()
    result = pipeline.run_scan(store, scan_settings(store), get=get, log=lambda *a: None)
    assert sorted(get.calls) == sorted([sources.SIMPLIFY_URL, sources.VANSH_URL])
    assert sorted(result["new"]) == ["n1", "n2", "n3", "v1"]
    assert sorted(result["closed"]) == ["s-stale", "s-stale-ready"]
    assert result["errors"] == []
    assert set(result["toDraft"]) <= set(result["new"])

    text = store.postings_path.read_text(encoding="utf-8")
    saved = json.loads(text)
    assert text == json.dumps(saved, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    assert text.startswith('{\n "')

    for pid in ("n1", "n2", "n3", "v1"):
        assert saved[pid]["status"] == "new" and saved[pid]["notes"] == ""
        assert "terms" not in saved[pid] and "id" not in saved[pid]
    assert saved["v1"]["source"] == "vanshb03" and saved["n1"]["source"] == "SimplifyJobs"
    assert saved["s-stale"]["status"] == "closed"
    assert saved["s-stale-ready"]["status"] == "closed"
    assert saved["s-stale-ready"]["prep"] == before["s-stale-ready"]["prep"]
    for pid in ("s-touched", "s-applied", "s-alive", "manual-x"):
        assert saved[pid] == before[pid], pid
    assert "v-fall" not in saved and "n-nyc" not in saved

    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert {"lastRun", "summary", "new", "drafted", "closed"} <= set(state)
    assert state["new"] == 4 and state["drafted"] == 0 and state["closed"] == 2
    assert state["summary"] == "4 new · 0 drafted · 2 closed"
    last = datetime.fromisoformat(state["lastRun"])
    assert abs((datetime.now(timezone.utc) - last).total_seconds()) < 60


def test_run_scan_second_run_adds_nothing_new(store):
    seed_store(store)
    pipeline.run_scan(store, scan_settings(store), get=scan_get(), log=lambda *a: None)
    first = json.loads(store.postings_path.read_text(encoding="utf-8"))
    result = pipeline.run_scan(store, scan_settings(store), get=scan_get(), log=lambda *a: None)
    assert result["new"] == [] and result["closed"] == [] and result["toDraft"] == []
    assert json.loads(store.postings_path.read_text(encoding="utf-8")) == first


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_run_scan_dry_run_writes_nothing(store):
    seed_store(store)
    store.save_state({"lastRun": "2026-10-01T15:00:00+00:00", "summary": "old"})
    before = _snapshot(store.root)
    result = pipeline.run_scan(store, scan_settings(store), get=scan_get(), dry_run=True, log=lambda *a: None)
    assert sorted(result["new"]) == ["n1", "n2", "n3", "v1"]
    assert sorted(result["closed"]) == ["s-stale", "s-stale-ready"]
    assert _snapshot(store.root) == before


def test_run_scan_dry_run_on_empty_store_creates_no_files(store):
    result = pipeline.run_scan(store, scan_settings(store), get=scan_get(), dry_run=True, log=lambda *a: None)
    assert result["new"]
    assert not store.postings_path.exists()
    assert not store.state_path.exists()


def test_run_scan_with_failed_simplify_keeps_simplify_rows(store):
    seed_store(store)
    get = FakeGet({sources.SIMPLIFY_URL: sources.SourceError("timed out"),
                   sources.VANSH_URL: [vansh_listing("v1", "Vega", "Backend Engineer Intern", ["Palo Alto, CA"], 1)]})
    result = pipeline.run_scan(store, scan_settings(store), get=get, log=lambda *a: None)
    assert result["closed"] == []
    assert result["new"] == ["v1"]
    assert result["errors"] and result["errors"][0].startswith("SimplifyJobs")
    saved = store.load_postings()
    assert saved["s-stale"]["status"] == "new"
    state = store.load_state()
    assert state["errors"] == result["errors"]


def test_run_scan_raises_when_no_source_could_be_read(store):
    seed_store(store)
    before = _snapshot(store.root)
    get = FakeGet({sources.SIMPLIFY_URL: sources.SourceError("a"), sources.VANSH_URL: sources.SourceError("b")})
    with pytest.raises(RuntimeError):
        pipeline.run_scan(store, scan_settings(store), get=get, log=lambda *a: None)
    assert _snapshot(store.root) == before


def test_run_scan_uses_the_data_folder_skip_list(store):
    store.save_skip("Nova\n")
    result = pipeline.run_scan(store, scan_settings(store), get=scan_get(), log=lambda *a: None)
    assert "n1" not in result["new"]


def test_summary_line_format():
    assert pipeline.summary_line(3, 2, 1) == "3 new · 2 drafted · 1 closed"
    assert re.fullmatch(r"\d+ new · \d+ drafted · \d+ closed", pipeline.summary_line(0, 0, 0))


@pytest.mark.parametrize("simplify_url, vansh_url", [
    # Ashby, with and without the embed flag and /application suffix
    ("https://jobs.ashbyhq.com/armadin/571d7673-0a1b-4c2d-8e3f-123456789abc/application?embed=true",
     "https://jobs.ashbyhq.com/armadin/571d7673-0a1b-4c2d-8e3f-123456789abc"),
    # Greenhouse embed token vs. the company site's gh_jid
    ("https://boards.greenhouse.io/embed/job_app?token=6865687002",
     "https://www.databricks.com/company/careers/open-positions/job?gh_jid=6865687002"),
    # Workday with and without a locale segment
    ("https://generalmotors.wd5.myworkdayjobs.com/en-CA/Careers_GM/job/Sunnyvale/ML-Intern_JR-202621695",
     "https://generalmotors.wd5.myworkdayjobs.com/Careers_GM/job/Sunnyvale-CA/Machine-Learning-Intern_JR-202621695"),
])
def test_same_job_under_different_urls_and_titles_is_added_once(simplify_url, vansh_url):
    fetched = [simplify([listing("s1", "Acme", "Frontend Engineer Intern", url=simplify_url, days_ago=3)]),
               vansh(sources.fetch_vansh(lambda u: [vansh_listing("v1", "Acme", "Member of Technical Staff Intern, Frontend",
                                                                   url=vansh_url)]))]
    new, _, _ = merge({}, fetched, set(), added_at=ADDED)
    assert list(new) == ["s1"]


def test_inactive_simplify_copy_does_not_hide_a_live_copy_elsewhere():
    url = "https://jobs.lever.co/acme/0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
    fetched = [simplify([listing("s-old", "Acme", "Software Engineer Intern", url=url, active=False)]),
               vansh(sources.fetch_vansh(lambda u: [vansh_listing("v1", "Acme", "SWE Intern", url=url)]))]
    new, _, _ = merge({}, fetched, set(), added_at=ADDED)
    assert list(new) == ["v1"]
