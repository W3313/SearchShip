"""scan.py must keep the user's original filters and scoring exactly.

The original scanner (tests/fixtures/scan_original.py) and the current
scan.py run as subprocesses on the same fixture listings, back to back,
and their output must be byte-identical.
"""
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

import scan as scanner
from helpers import listing

REPO = Path(__file__).resolve().parent.parent
ORIGINAL = REPO / "tests" / "fixtures" / "scan_original.py"
CURRENT = REPO / "scan.py"


def fixture_listings(now):
    """Listings that exercise every filter and every scoring rule."""
    L = []
    add = L.append
    # kept, with different scores
    add(listing("swe-sf", "Acme", "Software Engineer Intern", ["San Francisco, CA"], 0.5, now=now))
    add(listing("ml-sv", "Brightly", "Machine Learning Engineer Intern", ["Sunnyvale, CA"], 3,
                category="AI/ML/Data", now=now))
    add(listing("quant-remote", "QuantCo", "Quantitative Trader Intern", ["Remote in USA"], 6, category="Quant", now=now))
    add(listing("ds-sac", "Capitol Data", "Data Science Intern", ["Sacramento, CA"], 10,
                category="Data Science, AI & Machine Learning", now=now))
    add(listing("research-pa", "LabCorp AI", "AI Research Intern", ["Palo Alto, CA"], 2, category="AI/ML/Data", now=now))
    add(listing("analyst-sj", "Numbers Inc", "Data Analyst Intern", ["San Jose, CA"], 4, category="AI/ML/Data", now=now))
    add(listing("citizen", "Defense Soft", "Software Developer Intern", ["Mountain View, CA"], 1,
                sponsorship="U.S. Citizenship is Required", now=now))
    add(listing("sf-abbrev", "Shorty", "Backend Engineer Intern", ["SF"], 7, now=now))
    add(listing("many-locs", "Wide", "Full Stack Developer Intern",
                ["NYC", "Austin, TX", "Remote", "Seattle, WA", "Boston, MA", "Denver, CO"], 5, now=now))
    add(listing("legacy-cat", "OldCat", "Platform Engineer Intern", ["Oakland, CA"], 8,
                category="Software Engineering", now=now))
    add(listing("quant-cat2", "Fin Quant", "Quant Developer Intern", ["Berkeley, CA"], 9,
                category="Quantitative Finance", now=now))
    add(listing("two-terms", "Seasons", "iOS Engineer Intern", ["Cupertino, CA"], 3,
                terms=["Summer 2027", "Fall 2027"], now=now))
    add(listing("old-20", "Slowpoke", "Cloud Engineer Intern", ["Fremont, CA"], 20, now=now))
    add(listing("no-visible-key", "Hidden Key", "Web Developer Intern", ["Davis, CA"], 2, now=now))
    del L[-1]["is_visible"]
    add(listing("support", "Helpdesk", "IT Support Intern", ["Folsom, CA"], 2, now=now))
    # dropped by a filter
    add(listing("inactive", "Gone", "Software Engineer Intern", ["San Francisco, CA"], 1, active=False,
                now=now))
    add(listing("invisible", "Ghost", "Software Engineer Intern", ["San Francisco, CA"], 1, is_visible=False, now=now))
    add(listing("fall", "Autumn", "Software Engineer Intern", ["San Francisco, CA"], 1, terms=["Fall 2026"], now=now))
    add(listing("summer26", "LastYear", "Software Engineer Intern", ["San Francisco, CA"], 1,
                terms=["Summer 2026"], now=now))
    add(listing("hardware-cat", "Chips", "Software Engineer Intern", ["San Jose, CA"], 1, category="Hardware", now=now))
    add(listing("nyc", "Gotham", "Software Engineer Intern", ["New York, NY"], 1, now=now))
    add(listing("phd", "Deep", "PhD Software Engineer Intern", ["San Francisco, CA"], 1, now=now))
    add(listing("coop", "Coop Co", "Software Engineer Co-op", ["San Francisco, CA"], 1, now=now))
    add(listing("ms", "Masters", "MS Machine Learning Intern", ["San Francisco, CA"], 1, category="AI/ML/Data", now=now))
    add(listing("firmware", "Firm", "Firmware Engineer Intern", ["San Jose, CA"], 1, now=now))
    add(listing("too-old", "Ancient", "Software Engineer Intern", ["San Francisco, CA"], 45, now=now))
    add(listing("seen-1", "Seen Co", "Software Engineer Intern", ["San Francisco, CA"], 1, now=now))
    add(listing("seen-2", "Seen Two", "Software Engineer Intern", ["San Francisco, CA"], 1, now=now))
    add(listing("skipped-co", "ByteDance", "Software Engineer Intern", ["San Jose, CA"], 1, now=now))
    add(listing("skipped-co-case", "  visa ", "Software Engineer Intern", ["San Jose, CA"], 1, now=now))
    # duplicate company + title (case differs), and a company with too many roles
    add(listing("dup-a", "Twin", "Software Engineer Intern", ["San Francisco, CA"], 1, now=now))
    add(listing("dup-b", "twin", "software engineer intern", ["San Francisco, CA"], 2, now=now))
    for i, t in enumerate(["Software Engineer Intern", "Backend Engineer Intern", "Frontend Engineer Intern",
                           "Mobile Engineer Intern", "Infrastructure Engineer Intern"]):
        add(listing(f"big-{i}", "BigCo", t, ["Menlo Park, CA"], 1 + i, now=now))
    return L


@pytest.fixture
def scan_files(tmp_path):
    now = time.time()
    listings = tmp_path / "listings.json"
    listings.write_text(json.dumps(fixture_listings(now)), encoding="utf-8")
    skip = tmp_path / "skip_companies.txt"
    skip.write_text("ByteDance\nVisa\n", encoding="utf-8")
    return tmp_path, listings, skip


def run(script, *args, cwd):
    r = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
                       cwd=str(cwd), timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.parametrize("extra", [[], ["--days", "14", "--top", "25"], ["--days", "30", "--top", "5"]])
def test_output_identical_to_original_with_seen_ids_txt(scan_files, extra):
    tmp, listings, skip = scan_files
    seen = tmp / "seen_ids.txt"
    seen.write_text("seen-1\n\nseen-2\n", encoding="utf-8")
    original = run(ORIGINAL, listings, seen, "--skip-companies", skip, *extra, cwd=tmp)
    current = run(CURRENT, listings, seen, "--skip-companies", skip, *extra, cwd=tmp)
    assert current == original
    ids = [r["id"] for r in json.loads(current)]
    assert ids, "fixture should produce some output"
    assert not {"seen-1", "seen-2", "skipped-co", "skipped-co-case"} & set(ids)


def test_fixture_exercises_the_filters(scan_files):
    """Guard against a vacuous comparison: check what the original keeps."""
    tmp, listings, skip = scan_files
    seen = tmp / "seen_ids.txt"
    seen.write_text("seen-1\nseen-2\n", encoding="utf-8")
    out = json.loads(run(ORIGINAL, listings, seen, "--skip-companies", skip, cwd=tmp))
    ids = {r["id"] for r in out}
    for kept in ("swe-sf", "ml-sv", "quant-remote", "ds-sac", "research-pa", "citizen", "sf-abbrev",
                 "many-locs", "legacy-cat", "quant-cat2", "two-terms", "old-20", "no-visible-key", "support"):
        assert kept in ids, kept
    for dropped in ("inactive", "invisible", "fall", "summer26", "hardware-cat", "nyc", "phd", "coop", "ms",
                    "firmware", "too-old", "seen-1", "seen-2", "skipped-co", "skipped-co-case"):
        assert dropped not in ids, dropped
    assert len(ids & {"dup-a", "dup-b"}) == 1
    assert len([i for i in ids if i.startswith("big-")]) == 3
    by_id = {r["id"]: r for r in out}
    assert len(by_id["many-locs"]["locations"]) == 4
    assert set(by_id["swe-sf"]) == {"id", "company", "title", "locations", "url", "category", "posted", "score", "terms"}


def test_seen_json_counts_every_key_as_seen(scan_files):
    tmp, listings, skip = scan_files
    postings = tmp / "postings.json"
    postings.write_text(json.dumps({"seen-1": {"status": "applied"}, "swe-sf": {"status": "new"}}), encoding="utf-8")
    txt = tmp / "equivalent.txt"
    txt.write_text("seen-1\nswe-sf\n", encoding="utf-8")
    original = run(ORIGINAL, listings, txt, "--skip-companies", skip, cwd=tmp)
    current = run(CURRENT, listings, "--seen-json", postings, "--skip-companies", skip, cwd=tmp)
    assert current == original
    ids = {r["id"] for r in json.loads(current)}
    assert "swe-sf" not in ids and "seen-1" not in ids and "seen-2" in ids


def test_seen_txt_and_seen_json_together_are_combined(scan_files):
    tmp, listings, skip = scan_files
    seen = tmp / "seen_ids.txt"
    seen.write_text("seen-1\n", encoding="utf-8")
    postings = tmp / "postings.json"
    postings.write_text(json.dumps({"seen-2": {}, "ml-sv": {}}), encoding="utf-8")
    union = tmp / "union.txt"
    union.write_text("seen-1\nseen-2\nml-sv\n", encoding="utf-8")
    original = run(ORIGINAL, listings, union, "--skip-companies", skip, "--days", "14", cwd=tmp)
    current = run(CURRENT, listings, seen, "--seen-json", postings, "--skip-companies", skip, "--days", "14", cwd=tmp)
    assert current == original
    ids = {r["id"] for r in json.loads(current)}
    assert not {"seen-1", "seen-2", "ml-sv"} & ids


def test_missing_seen_file_means_nothing_seen(scan_files):
    tmp, listings, skip = scan_files
    original = run(ORIGINAL, listings, tmp / "does-not-exist.txt", "--skip-companies", skip, cwd=tmp)
    current = run(CURRENT, listings, tmp / "does-not-exist.txt", "--skip-companies", skip, cwd=tmp)
    assert current == original
    without_any = run(CURRENT, listings, "--skip-companies", skip, cwd=tmp)
    assert without_any == original
    assert {"seen-1", "seen-2"} <= {r["id"] for r in json.loads(original)}


def test_in_process_scan_matches_cli(scan_files):
    """The desktop app imports scan.scan(); it must agree with the CLI."""
    tmp, listings, skip = scan_files
    cli = json.loads(run(CURRENT, listings, "--skip-companies", skip, "--days", "14", "--top", "25", cwd=tmp))
    data = json.loads(listings.read_text(encoding="utf-8"))
    inproc = scanner.scan(data, set(), scanner.load_skip_companies(str(skip)), 14, 25)
    assert [r["id"] for r in inproc] == [r["id"] for r in cli]
    for a, b in zip(inproc, cli):
        assert {k: v for k, v in a.items() if k != "score"} == {k: v for k, v in b.items() if k != "score"}
        assert a["score"] == pytest.approx(b["score"], abs=0.01)


def _segments(path):
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("CATS", "LOC", "EXCLUDE"):
            found[node.targets[0].id] = ast.dump(node.value)
        if isinstance(node, ast.FunctionDef) and node.name == "score":
            found["score"] = ast.dump(node)
    return found


def test_filters_and_scoring_source_unchanged():
    original, current = _segments(ORIGINAL), _segments(CURRENT)
    assert set(original) == {"CATS", "LOC", "EXCLUDE", "score"}
    for name in original:
        assert current.get(name) == original[name], f"{name} differs from the original scanner"
