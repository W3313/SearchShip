"""The deterministic scan: fetch sources, run scan.py, merge new rows, close
stale ones, and pick which new rows get AI prep. No AI is involved here
except the optional web-search source, which only adds listings.
"""
from dataclasses import dataclass, field
from typing import Callable

import scan as scanner

from . import sources
from .store import now_iso, touched

DAYS = 14
TOP = 25
DRAFT_CAP = 8
DRAFT_MIN_SCORE = 4.5
WEB_SEARCH_EVERY_HOURS = 20


@dataclass
class Fetched:
    """One source's listings for this run.

    `owns(pid, row)` says which existing tracker rows this fetch is the
    authority on, so housekeeping can close the ones that disappeared.
    Sources that are not authoritative (web search) own nothing.
    """
    label: str
    listings: list
    owns: Callable[[str, dict], bool] = field(default=lambda pid, row: False)


def fetch_all(settings: dict, get=sources.http_get_json, log=print):
    """Fetch every enabled source. A failing source is reported and skipped;
    it never closes rows, because we cannot tell what disappeared."""
    fetched, errors = [], []
    src = settings.get("sources", {})
    if src.get("simplify", True):
        try:
            fetched.append(Fetched("SimplifyJobs", sources.fetch_simplify(get),
                                   lambda pid, row: row.get("source") == "SimplifyJobs"))
            log(f"SimplifyJobs: {len(fetched[-1].listings)} listings")
        except Exception as e:
            errors.append(f"SimplifyJobs: {e}")
    if src.get("vanshb03", True):
        try:
            fetched.append(Fetched("vanshb03", sources.fetch_vansh(get),
                                   lambda pid, row: row.get("source") == "vanshb03"))
            log(f"vanshb03: {len(fetched[-1].listings)} listings")
        except Exception as e:
            errors.append(f"vanshb03: {e}")
    if src.get("boards", True):
        for spec in settings.get("boards", []):
            try:
                ats, token, _ = sources.parse_board(spec)
                prefix = sources.board_prefix(ats, token)
                rows = sources.fetch_board(spec, get)
                fetched.append(Fetched(sources.ATS_LABELS[ats], rows,
                                       lambda pid, row, p=prefix: pid.lower().startswith(p)))
            except Exception as e:
                errors.append(f"{spec}: {e}")
        log(f"Career boards: {sum(len(f.listings) for f in fetched if f.label in sources.ATS_LABELS.values())} internship listings")
    return fetched, errors


def merge(postings: dict, fetched: list, skip_co: set, added_at: str = None,
          days: int = DAYS, top: int = TOP):
    """Pure merge step. Returns (new_rows, closed_ids, to_draft_ids); does not
    mutate `postings`.

    * scan.py picks the new listings (its filters, scoring, dedupe and caps).
    * Rows from sources other than SimplifyJobs are dropped when the same job
      is already tracked under another id (same URL or company + title).
    * Housekeeping: an untouched row with status new or ready whose
      authoritative source no longer lists it as active becomes closed.
    * Drafts: at most DRAFT_CAP of this run's new rows, highest score first,
      and only those scoring at least DRAFT_MIN_SCORE.
    """
    added_at = added_at or now_iso()
    known_urls = {sources.url_key(r.get("url", "")) for r in postings.values() if r.get("url")}
    known_pairs = {(str(r.get("company", "")).strip().lower(), str(r.get("title", "")).strip().lower())
                   for r in postings.values()}

    combined, source_of = [], {}
    for f in fetched:
        for x in f.listings:
            if not isinstance(x, dict) or "id" not in x or x["id"] in source_of:
                continue
            key = sources.url_key(x.get("url", ""))
            if f.label != "SimplifyJobs":
                pair = (str(x.get("company_name", "")).strip().lower(), str(x.get("title", "")).strip().lower())
                if key in known_urls or pair in known_pairs:
                    continue
            known_urls.add(key)
            source_of[x["id"]] = f.label
            combined.append(x)

    picked = scanner.scan(combined, set(postings), skip_co, days, top)
    new_rows = {}
    for r in picked:
        new_rows[r["id"]] = {
            "company": r["company"],
            "title": r["title"],
            "locations": r["locations"],
            "url": r["url"],
            "category": r["category"],
            "posted": r["posted"],
            "score": r["score"],
            "status": "new",
            "added": added_at,
            "source": source_of[r["id"]],
            "notes": "",
        }

    closed, active_by_source = [], {}
    for pid, row in postings.items():
        if row.get("status") not in ("new", "ready") or touched(row):
            continue
        owner = next((f for f in fetched if f.owns(pid, row)), None)
        if owner is None:
            continue
        if id(owner) not in active_by_source:
            active_by_source[id(owner)] = {x["id"]: bool(x.get("active"))
                                           for x in owner.listings if isinstance(x, dict) and "id" in x}
        if not active_by_source[id(owner)].get(pid, False):
            closed.append(pid)

    ranked = sorted((r for r in new_rows.items() if r[1]["score"] >= DRAFT_MIN_SCORE),
                    key=lambda kv: (kv[1]["score"], kv[1]["posted"]), reverse=True)
    to_draft = [pid for pid, _ in ranked[:DRAFT_CAP]]
    return new_rows, closed, to_draft


def summary_line(n_new: int, n_drafted: int, n_closed: int) -> str:
    return f"{n_new} new · {n_drafted} drafted · {n_closed} closed"


def run_scan(store, settings: dict, get=sources.http_get_json, web_listings=None,
             dry_run: bool = False, log=print) -> dict:
    """Fetch, merge and save. `web_listings` are extra listings from the AI
    web search (already fetched by the caller, which owns the AI)."""
    fetched, errors = fetch_all(settings, get, log)
    if web_listings:
        fetched.append(Fetched("Web search", web_listings))
    if not fetched:
        raise RuntimeError("No source could be read. " + "; ".join(errors))
    skip_co = scanner.load_skip_companies(str(store.skip_path))

    with store.lock:
        postings = store.load_postings()
        new_rows, closed, to_draft = merge(postings, fetched, skip_co)
        if not dry_run:
            postings.update(new_rows)
            for pid in closed:
                postings[pid]["status"] = "closed"
            store.save_postings(postings)
            store.save_state({
                "lastRun": now_iso(),
                "summary": summary_line(len(new_rows), 0, len(closed)),
                "new": len(new_rows),
                "drafted": 0,
                "closed": len(closed),
                "errors": errors,
            })
    return {"new": list(new_rows), "newRows": new_rows, "closed": closed,
            "toDraft": to_draft, "errors": errors}
