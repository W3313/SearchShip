"""sources: career boards, vanshb03, web-search results and URL identity."""
import time
from datetime import datetime, timezone

import pytest

import scan as scanner
from helpers import FakeGet, vansh_listing
from tracker import sources
from tracker.sources import SourceError, fetch_board, infer_terms, parse_board, url_key, web_listing

LISTING_KEYS = {"id", "company_name", "title", "locations", "url", "category", "terms", "date_posted",
                "active", "is_visible", "sponsorship"}


def iso(days_ago, tz="+00:00"):
    t = datetime.fromtimestamp(time.time() - days_ago * 86400, timezone.utc).replace(microsecond=0)
    return t.isoformat().replace("+00:00", tz) if tz != "+00:00" else t.isoformat()


# --- parse_board ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec, expected", [
    ("greenhouse:stripe = Stripe", ("greenhouse", "stripe", "Stripe")),
    ("  greenhouse:stripe=Stripe Inc.  ", ("greenhouse", "stripe", "Stripe Inc.")),
    ("Lever:plaid", ("lever", "plaid", "Plaid")),
    ("ashby:my-company", ("ashby", "my-company", "My Company")),
    ("ashby:some_co", ("ashby", "some_co", "Some Co")),
    ("greenhouse : databricks", ("greenhouse", "databricks", "Databricks")),
])
def test_parse_board_good(spec, expected):
    assert parse_board(spec) == expected


@pytest.mark.parametrize("spec", [
    "", None, "stripe", "workday:acme", "greenhouse:", "greenhouse:two words", "greenhouse:a/b",
    "greenhouse:acme?x=1", "= Stripe", "https://boards.greenhouse.io/stripe",
])
def test_parse_board_bad(spec):
    with pytest.raises(SourceError):
        parse_board(spec)


def test_default_boards_all_parse():
    from tracker.store import DEFAULT_BOARDS
    for spec in DEFAULT_BOARDS:
        parse_board(spec)


# --- fetch_board -------------------------------------------------------------------------------

def test_greenhouse_board():
    url = "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"
    published = iso(2, "-04:00")
    get = FakeGet({url: {"jobs": [
        {"id": 123, "title": "Software Engineer Intern, Summer 2027", "company_name": "Stripe",
         "location": {"name": "San Francisco, CA; Remote in US"},
         "absolute_url": "https://stripe.com/jobs/123", "first_published": published,
         "updated_at": iso(0)},
        {"id": 124, "title": "Senior Software Engineer", "location": {"name": "San Francisco, CA"},
         "absolute_url": "https://stripe.com/jobs/124", "first_published": iso(1)},
        {"id": 125, "title": "International Payments Engineer", "location": {"name": "Remote"},
         "absolute_url": "https://stripe.com/jobs/125"},
        {"id": 126, "title": "Data Science Internship", "location": {"name": "New York | Remote"},
         "absolute_url": "https://stripe.com/jobs/126", "updated_at": iso(3)},
    ]}})
    out = fetch_board("greenhouse:stripe = Stripe", get)
    assert get.calls == [url]
    assert [x["id"] for x in out] == ["greenhouse:stripe:123", "greenhouse:stripe:126"]
    a, b = out
    assert set(a) == LISTING_KEYS
    assert a["company_name"] == "Stripe" and a["title"] == "Software Engineer Intern, Summer 2027"
    assert a["locations"] == ["San Francisco, CA", "Remote in US"]
    assert a["url"] == "https://stripe.com/jobs/123"
    assert a["date_posted"] == pytest.approx(datetime.fromisoformat(published).timestamp())
    assert a["terms"] == ["Summer 2027"] and a["category"] == "Software"
    assert a["active"] is True and a["is_visible"] is True
    assert b["locations"] == ["New York", "Remote"]
    assert b["category"] == "AI/ML/Data"
    assert b["date_posted"] == pytest.approx(time.time() - 3 * 86400, abs=5)  # falls back to updated_at


def test_lever_board():
    url = "https://api.lever.co/v0/postings/plaid?mode=json"
    created_ms = int((time.time() - 4 * 86400) * 1000)
    get = FakeGet({url: [
        {"id": "aaaa-1111", "text": "Software Engineer Intern", "hostedUrl": "https://jobs.lever.co/plaid/aaaa-1111",
         "categories": {"location": "San Francisco", "allLocations": ["San Francisco", "New York"]},
         "workplaceType": "remote", "createdAt": created_ms},
        {"id": "bbbb-2222", "text": "Backend Intern", "hostedUrl": "https://jobs.lever.co/plaid/bbbb-2222",
         "categories": {"location": "New York"}, "workplaceType": "onsite", "createdAt": created_ms},
        {"id": "cccc-3333", "text": "Engineering Manager", "categories": {}, "createdAt": created_ms},
    ]})
    out = fetch_board("lever:plaid = Plaid", get)
    assert [x["id"] for x in out] == ["lever:plaid:aaaa-1111", "lever:plaid:bbbb-2222"]
    a, b = out
    assert a["company_name"] == "Plaid"
    assert a["locations"] == ["San Francisco", "New York", "Remote"]
    assert b["locations"] == ["New York"]
    assert a["date_posted"] == pytest.approx(created_ms / 1000)
    assert a["url"] == "https://jobs.lever.co/plaid/aaaa-1111"


def test_lever_board_non_list_payload_gives_nothing():
    get = FakeGet({"https://api.lever.co/v0/postings/plaid?mode=json": {"ok": False}})
    assert fetch_board("lever:plaid", get) == []


def test_ashby_board():
    url = "https://api.ashbyhq.com/posting-api/job-board/OpenAI"
    published = iso(1)
    get = FakeGet({url: {"jobs": [
        {"id": "u-1", "title": "Software Engineer Intern", "location": "San Francisco",
         "secondaryLocations": [{"location": "Seattle"}], "isRemote": True, "isListed": True,
         "jobUrl": "https://jobs.ashbyhq.com/openai/u-1", "publishedAt": published},
        {"id": "u-2", "title": "Research Intern", "location": "San Francisco", "isListed": False,
         "jobUrl": "https://jobs.ashbyhq.com/openai/u-2", "publishedAt": published},
        {"id": "u-3", "title": "Member of Technical Staff", "location": "Remote", "isRemote": True,
         "jobUrl": "https://jobs.ashbyhq.com/openai/u-3", "publishedAt": published},
        {"id": "u-4", "title": "ML Intern (Fall 2027)", "location": "Remote", "isRemote": True,
         "jobUrl": "https://jobs.ashbyhq.com/openai/u-4"},
    ]}})
    out = fetch_board("ashby:OpenAI = OpenAI", get)
    assert get.calls == [url]
    assert [x["id"] for x in out] == ["ashby:openai:u-1", "ashby:openai:u-4"]
    a, b = out
    assert a["company_name"] == "OpenAI"
    assert a["locations"] == ["San Francisco", "Seattle", "Remote"]
    assert b["locations"] == ["Remote"]  # no duplicate "Remote"
    assert a["date_posted"] == pytest.approx(datetime.fromisoformat(published).timestamp())
    assert b["terms"] == ["Fall"]
    assert b["date_posted"] == pytest.approx(time.time(), abs=5)  # no date: treated as now


def test_board_listings_flow_through_scan():
    """A board listing in Bay Area / remote must pass scan.py like a SimplifyJobs one."""
    url = "https://boards-api.greenhouse.io/v1/boards/figma/jobs"
    get = FakeGet({url: {"jobs": [
        {"id": 1, "title": "Software Engineer Intern", "location": {"name": "San Francisco, CA"},
         "absolute_url": "https://figma.com/1", "first_published": iso(1)},
        {"id": 2, "title": "Software Engineer Intern (Summer 2026)", "location": {"name": "San Francisco, CA"},
         "absolute_url": "https://figma.com/2", "first_published": iso(1)},
    ]}})
    out = scanner.scan(fetch_board("greenhouse:figma = Figma", get), set(), set(), 14, 25)
    assert [r["id"] for r in out] == ["greenhouse:figma:1"]


# --- vanshb03 ------------------------------------------------------------------------------------

def test_fetch_vansh_fills_terms_and_category():
    raw = [vansh_listing("v1", "Acme", "Software Engineer Intern"),
           vansh_listing("v2", "Acme", "Machine Learning Intern", season="Fall"),
           vansh_listing("v3", "QuantCo", "Quant Trader Intern"),
           vansh_listing("v4", "Acme", "Product Designer Intern", season="Winter"),
           {"id": "", "title": "no id"}, {"id": "v6"}, "junk"]
    raw[0]["season"] = None
    get = FakeGet({sources.VANSH_URL: raw})
    out = sources.fetch_vansh(get)
    assert get.calls == [sources.VANSH_URL]
    by = {x["id"]: x for x in out}
    assert list(by) == ["v1", "v2", "v3", "v4"]
    assert by["v1"]["terms"] == ["Summer 2027"] and by["v1"]["category"] == "Software"
    assert by["v2"]["terms"] == ["Fall"] and by["v2"]["category"] == "AI/ML/Data"
    assert by["v3"]["terms"] == ["Summer 2027"] and by["v3"]["category"] == "Quant"
    assert by["v4"]["terms"] == ["Winter"] and by["v4"]["category"] == "Other"
    assert raw[1].get("terms") is None, "fetch_vansh must not mutate the downloaded data"


def test_fetch_vansh_keeps_existing_terms_and_category():
    x = vansh_listing("v1")
    x["terms"] = ["Summer 2027", "Fall 2027"]
    x["category"] = "Quant"
    out = sources.fetch_vansh(FakeGet({sources.VANSH_URL: [x]}))
    assert out[0]["terms"] == ["Summer 2027", "Fall 2027"] and out[0]["category"] == "Quant"


@pytest.mark.parametrize("payload", [{"jobs": []}, None, "x"])
def test_list_sources_reject_non_lists(payload):
    with pytest.raises(SourceError):
        sources.fetch_vansh(FakeGet({sources.VANSH_URL: payload}))
    with pytest.raises(SourceError):
        sources.fetch_simplify(FakeGet({sources.SIMPLIFY_URL: payload}))


# --- terms / categories -------------------------------------------------------------------------

@pytest.mark.parametrize("title, terms", [
    ("Software Engineer Intern", ["Summer 2027"]),
    ("Software Engineer Intern, Summer 2027", ["Summer 2027"]),
    ("Software Engineer Intern - Summer 2026", ["Other 2026"]),
    ("2028 Software Engineering Internship", ["Other 2028"]),
    ("Software Engineer Intern (Fall 2027)", ["Fall"]),
    ("Winter Software Intern", ["Winter"]),
    ("Spring 2027 Data Intern", ["Spring"]),
    ("Autumn ML Intern", ["Autumn"]),
    ("Summer/Fall 2027 Software Intern", ["Summer 2027"]),
    ("Software Engineer Intern, 2026-2027", ["Summer 2027"]),
])
def test_infer_terms(title, terms):
    assert infer_terms(title) == terms


@pytest.mark.parametrize("title, category", [
    ("Quantitative Trader Intern", "Quant"),
    ("Machine Learning Engineer Intern", "AI/ML/Data"),
    ("Data Engineering Intern", "AI/ML/Data"),
    ("Software Engineer Intern", "Software"),
    ("iOS Developer Intern", "Software"),
    ("Product Designer Intern", "Other"),
    ("Marketing Intern", "Other"),
])
def test_infer_category(title, category):
    assert sources.infer_category(title) == category


# --- url_key -------------------------------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ("https://example.com/jobs/1", "http://example.com/jobs/1"),
    ("https://www.example.com/jobs/1", "https://example.com/jobs/1"),
    ("https://example.com/jobs/1/", "https://example.com/jobs/1"),
    ("https://example.com/jobs/1/apply", "https://example.com/jobs/1"),
    ("https://example.com/jobs/1/apply/", "https://example.com/jobs/1"),
    ("https://example.com/jobs/1/application", "https://example.com/jobs/1"),
    ("https://example.com/jobs/1?utm_source=simplify&utm_medium=x", "https://example.com/jobs/1"),
    ("https://example.com/jobs?gh_jid=5&utm_source=a", "https://example.com/jobs?gh_jid=5"),
    ("https://example.com/jobs?b=2&a=1", "https://example.com/jobs?a=1&b=2"),
    ("  HTTPS://Example.COM/Jobs/1  ", "https://example.com/jobs/1"),
    ("https://example.com/jobs/1#apply", "https://example.com/jobs/1"),
])
def test_url_key_same_job(a, b):
    assert url_key(a) == url_key(b)


@pytest.mark.parametrize("a, b", [
    ("https://example.com/jobs/1", "https://example.com/jobs/2"),
    ("https://example.com/jobs?gh_jid=1", "https://example.com/jobs?gh_jid=2"),
    ("https://a.example.com/jobs/1", "https://b.example.com/jobs/1"),
])
def test_url_key_different_jobs(a, b):
    assert url_key(a) != url_key(b)


def test_url_key_handles_empty():
    assert url_key("") == url_key(None) == ""


# --- web_listing ----------------------------------------------------------------------------------

GOOD_WEB = {"company": "Nova", "title": "Software Engineer Intern", "locations": ["San Francisco, CA", " "],
            "url": "https://boards.greenhouse.io/nova/jobs/1", "posted": "2026-10-20", "category": "Software"}


def test_web_listing_good():
    x = web_listing(dict(GOOD_WEB))
    assert set(x) == LISTING_KEYS
    assert x["id"].startswith("web:") and len(x["id"]) == len("web:") + 16
    assert x["company_name"] == "Nova" and x["locations"] == ["San Francisco, CA"]
    assert x["terms"] == ["Summer 2027"] and x["category"] == "Software"
    assert x["date_posted"] == datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    assert x["active"] is True


def test_web_listing_id_is_stable_across_url_variants():
    a = web_listing(dict(GOOD_WEB))
    b = web_listing(dict(GOOD_WEB, url="http://www.boards.greenhouse.io/nova/jobs/1/?utm_source=x"))
    assert a["id"] == b["id"]


def test_web_listing_bad_category_is_inferred_and_missing_date_is_now():
    x = web_listing(dict(GOOD_WEB, category="Hardware", title="Machine Learning Intern", posted=""))
    assert x["category"] == "AI/ML/Data"
    assert x["date_posted"] == pytest.approx(time.time(), abs=5)


@pytest.mark.parametrize("change", [
    {"url": ""}, {"url": None}, {"url": "ftp://x.com/1"}, {"url": "javascript:alert(1)"},
    {"url": "boards.greenhouse.io/nova/1"}, {"title": ""}, {"title": "   "}, {"company": ""},
    {"company": None},
])
def test_web_listing_rejects_incomplete_items(change):
    assert web_listing(dict(GOOD_WEB, **change)) is None


def test_web_listing_rejects_missing_keys():
    for key in ("url", "title", "company"):
        item = dict(GOOD_WEB)
        del item[key]
        assert web_listing(item) is None


# --- to_epoch ------------------------------------------------------------------------------------

def test_to_epoch():
    assert sources.to_epoch(1_760_000_000) == 1_760_000_000
    assert sources.to_epoch(1_760_000_000_000) == 1_760_000_000
    assert sources.to_epoch("2026-10-20T00:00:00Z") == datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    assert sources.to_epoch("2026-10-20") == datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    for empty in (None, "", "not a date"):
        assert sources.to_epoch(empty) == pytest.approx(time.time(), abs=5)
