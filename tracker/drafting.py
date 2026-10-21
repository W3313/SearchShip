"""AI steps: drafting prep for postings, and finding extra postings by web search.

The AI never edits the tracker directly. It returns JSON, SearchShip checks
every entry against the prep rules, and only then writes accepted entries.
"""
import json

from . import sources
from .paths import resource
from .store import touched

DASHES = ("—", "–")  # em dash, en dash
PREP_FIELDS = ("fit", "project", "why")

DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["drafts"],
    "properties": {"drafts": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "status", "notes", "fit", "project", "why"],
        "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["ready", "skipped"]},
            "notes": {"type": "string"},
            "fit": {"type": "string"},
            "project": {"type": "string"},
            "why": {"type": "string"},
        },
    }}},
}

SEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["postings"],
    "properties": {"postings": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["company", "title", "locations", "url", "posted", "category"],
        "properties": {
            "company": {"type": "string"},
            "title": {"type": "string"},
            "locations": {"type": "array", "items": {"type": "string"}},
            "url": {"type": "string"},
            "posted": {"type": "string"},
            "category": {"type": "string", "enum": ["Software", "AI/ML/Data", "Quant"]},
        },
    }}},
}


def prep_rules() -> str:
    return resource("prep_rules.md").read_text(encoding="utf-8")


def draft_prompt(rows: dict, resume: str, rules: str) -> str:
    postings = [{"id": pid, "company": r.get("company"), "title": r.get("title"),
                 "locations": r.get("locations", []), "url": r.get("url"),
                 "category": r.get("category"), "posted": r.get("posted")}
                for pid, r in rows.items()]
    return (
        "Follow the prep rules below. For each posting, either write prep (fit, project, why) "
        "and set status \"ready\", or set status \"skipped\" with a short reason in notes. "
        "Return exactly one entry per posting, using the ids given. For skipped postings, "
        "leave fit, project and why as empty strings. Use notes for citizenship or clearance "
        "requirements on ready postings; otherwise leave notes empty. Never use em dashes or "
        "en dashes anywhere.\n\n"
        f"<rules>\n{rules.strip()}\n</rules>\n\n"
        f"<resume>\n{resume.strip()}\n</resume>\n\n"
        f"<postings>\n{json.dumps(postings, indent=1, ensure_ascii=False)}\n</postings>\n"
    )


def validate_drafts(result, ids) -> tuple:
    """Check the AI's answer. Returns ({id: changes}, [problems]).

    Each accepted entry is {"status", "notes", "prep"?}. An entry that breaks
    a rule is dropped (its row stays as it was) and reported.
    """
    accepted, problems = {}, []
    wanted = set(ids)
    entries = result.get("drafts") if isinstance(result, dict) else None
    if not isinstance(entries, list):
        return {}, ["The AI answer had no drafts list."]
    for e in entries:
        if not isinstance(e, dict):
            continue
        pid = str(e.get("id", ""))
        if pid not in wanted:
            problems.append(f"Ignored an entry for an id that was not asked for: {pid[:40]}")
            continue
        if pid in accepted:
            continue
        status = e.get("status")
        notes = str(e.get("notes") or "").strip()[:500]
        if status == "ready":
            prep = {f: str(e.get(f) or "").strip() for f in PREP_FIELDS}
            missing = [f for f in PREP_FIELDS if not prep[f]]
            if missing:
                problems.append(f"{pid}: prep is missing {', '.join(missing)}")
                continue
            if any(d in prep[f] for f in PREP_FIELDS for d in DASHES):
                problems.append(f"{pid}: prep used an em or en dash")
                continue
            accepted[pid] = {"status": "ready", "notes": notes, "prep": prep}
        elif status == "skipped":
            if not notes:
                problems.append(f"{pid}: skipped without a reason")
                continue
            accepted[pid] = {"status": "skipped", "notes": notes}
        else:
            problems.append(f"{pid}: unknown status {status!r}")
    for pid in ids:
        if pid not in accepted and not any(p.startswith(pid + ":") for p in problems):
            problems.append(f"{pid}: no answer")
    return accepted, problems


def merge_notes(old: str, new: str) -> str:
    """Add the AI's note below whatever is already there; never drop the user's."""
    old, new = str(old or "").strip(), str(new or "").strip()
    if not new or new in old:
        return old
    return f"{old}\n{new}" if old else new


def apply_drafts(store, accepted: dict, snapshot: dict) -> dict:
    """Write accepted drafts, but only to rows the user has not changed since
    the AI started (same status and `updated` as in `snapshot`).

    The AI's notes are appended, never replacing the user's. On a row the
    user has already acted on (touched), a "skipped" answer is only advice:
    the reason goes into notes and the status stays as the user left it.
    """
    counts = {"ready": 0, "skipped": 0, "advised": 0, "changedMeanwhile": 0}
    with store.lock:
        postings = store.load_postings()
        for pid, change in accepted.items():
            row = postings.get(pid)
            if row is None or (row.get("status"), row.get("updated")) != snapshot.get(pid):
                counts["changedMeanwhile"] += 1
                continue
            if change["status"] == "ready":
                row["status"] = "ready"
                row["prep"] = change["prep"]
                row["notes"] = merge_notes(row.get("notes"), change["notes"])
                counts["ready"] += 1
            elif touched(row):
                row["notes"] = merge_notes(row.get("notes"), "AI suggests skipping: " + change["notes"])
                counts["advised"] += 1
            else:
                row["status"] = "skipped"
                row["notes"] = merge_notes(row.get("notes"), change["notes"])
                counts["skipped"] += 1
        store.save_postings(postings)
    return counts


def draft_rows(store, provider, ids, log=print) -> dict:
    """Ask the AI for prep on `ids` and save what passes the checks."""
    resume = store.resume_text().strip()
    if not resume:
        raise ValueError("Add your resume in Settings before drafting prep.")
    postings = store.load_postings()
    rows = {pid: postings[pid] for pid in ids if pid in postings}
    if not rows:
        return {"ready": 0, "skipped": 0, "advised": 0, "changedMeanwhile": 0, "problems": []}
    snapshot = {pid: (r.get("status"), r.get("updated")) for pid, r in rows.items()}
    log(f"Asking {provider.label} to draft prep for {len(rows)} posting(s)…")
    result = provider.run_json(draft_prompt(rows, resume, prep_rules()), DRAFT_SCHEMA, web=True)
    accepted, problems = validate_drafts(result, list(rows))
    for p in problems:
        log("Draft check: " + p)
    counts = apply_drafts(store, accepted, snapshot)
    counts["problems"] = problems
    return counts


def search_prompt(known_companies) -> str:
    known = ", ".join(sorted(known_companies)[:300])
    return (
        "Search the web for internship postings that are open right now for Summer 2027, "
        "for undergraduates, in software engineering, AI/ML/data, or quantitative trading and research. "
        "Locations: the San Francisco Bay Area, the Sacramento area, or remote in the US.\n"
        "Find up to 15 postings you can confirm on the employer's own careers page or applicant "
        "tracking system (Greenhouse, Lever, Ashby, Workday and similar). Prefer companies that are "
        f"not already tracked. Already tracked: {known}.\n"
        "For each, give the direct posting URL, the locations as listed, the posted date as "
        "YYYY-MM-DD if the page shows one (otherwise an empty string), and a category. "
        "Never invent a posting; returning fewer is fine."
    )


def search_postings(provider, postings: dict, log=print) -> list:
    known = {str(r.get("company", "")).strip() for r in postings.values() if r.get("company")}
    log(f"Asking {provider.label} to search the web for more postings…")
    result = provider.run_json(search_prompt(known), SEARCH_SCHEMA, web=True)
    items = result.get("postings", []) if isinstance(result, dict) else []
    out = [l for l in (sources.web_listing(i) for i in items if isinstance(i, dict)) if l]
    log(f"Web search: {len(out)} posting(s) found")
    return out


def drafting_candidates(postings: dict, ids) -> list:
    """Rows the user may ask to draft (or redraft) by hand: new or ready."""
    return [pid for pid in ids if pid in postings and postings[pid].get("status") in ("new", "ready")]

