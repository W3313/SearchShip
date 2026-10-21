"""Builders for listings, tracker rows and fake sources used across the tests.

Dates are always relative to time.time(), so the suite does not depend on
the day it runs.
"""
import json
import re
import time

DAY = 86400


def listing(id, company="Acme", title="Software Engineer Intern", locations=("San Francisco, CA",),
            days_ago=1.0, active=True, category="Software", terms=("Summer 2027",), url=None,
            is_visible=True, sponsorship="Other", now=None, **extra):
    """One listing in the SimplifyJobs listings.json shape."""
    now = time.time() if now is None else now
    x = {
        "id": id,
        "company_name": company,
        "title": title,
        "locations": list(locations),
        "url": url or f"https://jobs.example.com/{re.sub(r'[^a-z0-9]+', '-', company.lower())}/{id}",
        "category": category,
        "terms": list(terms),
        "date_posted": int(now - days_ago * DAY),
        "active": active,
        "is_visible": is_visible,
        "sponsorship": sponsorship,
    }
    x.update(extra)
    return x


def vansh_listing(id, company="Acme", title="Software Engineer Intern", locations=("San Francisco, CA",),
                  days_ago=1.0, active=True, season="Summer", url=None, now=None):
    """One listing in vanshb03's shape: a season, but no category or terms."""
    now = time.time() if now is None else now
    return {
        "id": id,
        "company_name": company,
        "title": title,
        "locations": list(locations),
        "url": url or f"https://vansh.example.com/{re.sub(r'[^a-z0-9]+', '-', company.lower())}/{id}",
        "date_posted": int(now - days_ago * DAY),
        "date_updated": int(now - days_ago * DAY),
        "active": active,
        "is_visible": True,
        "sponsorship": "Other",
        "season": season,
        "source": "vanshb03",
        "company_url": "",
    }


def row(company="Acme", title="Software Engineer Intern", status="new", source="SimplifyJobs", **extra):
    """One tracker row as stored in postings.json."""
    r = {
        "company": company,
        "title": title,
        "locations": ["San Francisco, CA"],
        "url": "https://old.example.com/" + re.sub(r"[^a-z0-9]+", "-", f"{company} {title}".lower()),
        "category": "Software",
        "posted": "2026-10-01",
        "score": 5.0,
        "status": status,
        "added": "2026-10-01T15:00:00+00:00",
        "source": source,
        "notes": "",
    }
    r.update(extra)
    return r


class FakeGet:
    """A fake `get(url)` mapping URLs to payloads. Records every URL asked for.
    A payload that is an Exception instance is raised instead."""

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.calls = []

    def __call__(self, url, timeout=60):
        self.calls.append(url)
        if url not in self.mapping:
            raise AssertionError(f"unexpected URL fetched: {url}")
        value = self.mapping[url]
        if isinstance(value, BaseException):
            raise value
        return json.loads(json.dumps(value))  # a fresh copy, like a real download


def prompt_postings(prompt):
    """The postings JSON block from a draft prompt."""
    m = re.search(r"<postings>\n(.*)\n</postings>", prompt, re.S)
    assert m, "draft prompt has no <postings> block"
    return json.loads(m.group(1))


class FakeProvider:
    """Stands in for Claude/Codex. Answers draft requests with valid prep for
    every posting it is shown, and web-search requests with `search_items`."""

    name = "fake"
    label = "FakeAI"

    def __init__(self, search_items=None, draft_status="ready", on_draft=None):
        self.calls = []
        self.search_items = list(search_items or [])
        self.draft_status = draft_status
        self.on_draft = on_draft

    def status(self):
        return {"loggedIn": True, "installed": True, "detail": "fake"}

    def run_json(self, prompt, schema, web=True, timeout=900):
        kind = "draft" if "drafts" in schema.get("properties", {}) else "search"
        self.calls.append({"kind": kind, "prompt": prompt, "schema": schema, "web": web})
        if kind == "search":
            return {"postings": list(self.search_items)}
        if self.on_draft:
            self.on_draft()
        drafts = []
        for p in prompt_postings(prompt):
            if self.draft_status == "ready":
                drafts.append({"id": p["id"], "status": "ready", "notes": "",
                               "fit": f"I build web apps, which fits {p['company']}.",
                               "project": "Lead with the tracker project, 251 rows and 8 drafts a day.",
                               "why": "I like the product. It solves a real problem. I want to help build it."})
            else:
                drafts.append({"id": p["id"], "status": "skipped", "notes": "MS only",
                               "fit": "", "project": "", "why": ""})
        return {"drafts": drafts}

    @property
    def draft_calls(self):
        return [c for c in self.calls if c["kind"] == "draft"]

    @property
    def search_calls(self):
        return [c for c in self.calls if c["kind"] == "search"]
