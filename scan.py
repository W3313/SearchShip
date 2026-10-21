#!/usr/bin/env python3
"""Internship scanner for William Li's tracker.

Reads the public SimplifyJobs Summer 2027 listings, keeps roles that fit
(software / AI-ML / quant, Bay Area + Sacramento + remote, undergrad-eligible),
scores them, and prints new ones as JSON.

Usage:
  git clone --depth 1 https://github.com/SimplifyJobs/Summer2027-Internships.git s27
  python3 scan.py s27/.github/scripts/listings.json --seen-json postings.json [--days 14] [--top 25] > new.json
  python3 scan.py s27/.github/scripts/listings.json seen_ids.txt [--days 30] [--top 40] > new.json

seen_ids.txt: one tracker doc id per line (rows already in the tracker, any status).
--seen-json: the tracker's postings.json; every key in it counts as seen.
Also skips companies listed in skip_companies.txt (one per line, case-insensitive) if present.

The SearchShip desktop app imports scan() from this file, so the filters and
scoring below are the single source of truth for every job source.
"""
import json, re, sys, time, os, argparse

CATS = {"Software", "Software Engineering", "Quant", "Quantitative Finance",
        "AI/ML/Data", "Data Science, AI & Machine Learning"}
LOC = re.compile(r"San Francisco|\bSF\b|San Jose|Santa Clara|Sunnyvale|Mountain View|Palo Alto|"
                 r"Menlo Park|Redwood City|Foster City|San Mateo|Oakland|Berkeley|Fremont|Cupertino|"
                 r"Milpitas|Emeryville|Burlingame|San Bruno|Sacramento|Remote|Bay Area|Los Gatos|"
                 r"Pleasanton|Livermore|Folsom|Roseville|Rancho Cordova|Davis|Newark, CA|Hayward|"
                 r"San Carlos|Belmont|Los Altos|Campbell|Walnut Creek|San Ramon|Dublin, CA", re.I)
EXCLUDE = re.compile(r"\bPh\.?D|Doctoral|Master'?s|\bMS\b|\bMBA\b|Graduate Student|Postdoc|"
                     r"Hardware|ASIC|RTL|Analog|Mechanical|Electrical Eng|Firmware|Silicon|"
                     r"Sales|Marketing|Recruit|Legal|Accounting|Finance Intern|HR\b|"
                     r"Co-?op|Fall 20|Spring 20|Winter 20", re.I)

def score(x):
    t = x["title"]
    s = 0.0
    if re.search(r"Software|SWE|Full[- ]?Stack|Front[- ]?end|Back[- ]?end|Web|Platform|Infrastructure|"
                 r"Developer|Systems|Product Engineer|Mobile|iOS|Android|Cloud|Database", t, re.I): s += 3
    if re.search(r"Machine Learning|\bML\b|\bAI\b|Applied Scien|Deep Learning|LLM", t, re.I): s += 2
    if re.search(r"Quant|Trading|Trader", t, re.I): s += 2
    if re.search(r"Data Scien|Data Eng|Analytics", t, re.I): s += 1
    if re.search(r"Research", t, re.I): s -= 1.5
    if re.search(r"Analyst|Business|Operations|Support|Technician|IT Intern|QA|Test", t, re.I): s -= 1.5
    locs = " ".join(x.get("locations", []))
    if re.search(r"San Francisco|San Jose|Santa Clara|Sunnyvale|Mountain View|Palo Alto|Menlo Park|"
                 r"Redwood City|Foster City|San Mateo|Berkeley|Oakland|Emeryville|Cupertino", locs, re.I): s += 1
    if re.search(r"Sacramento|Folsom|Roseville|Rancho Cordova|Davis", locs, re.I): s += 1
    if re.search(r"Remote", locs, re.I): s += 0.5
    age_days = (time.time() - x["date_posted"]) / 86400
    s += max(0.0, 2.0 - age_days / 7)          # fresh postings get up to +2
    if x.get("sponsorship") == "U.S. Citizenship is Required": s -= 0.5
    return round(s, 2)

def load_seen(seen_txt=None, seen_json=None):
    seen = set()
    if seen_txt and os.path.exists(seen_txt):
        seen |= {l.strip() for l in open(seen_txt) if l.strip()}
    if seen_json and os.path.exists(seen_json):
        seen |= set(json.load(open(seen_json, encoding="utf-8")))
    return seen

def load_skip_companies(path):
    if path and os.path.exists(path):
        return {l.strip().lower() for l in open(path, encoding="utf-8") if l.strip()}
    return set()

def scan(data, seen, skip_co, days=30, top=40):
    now = time.time()
    out = []
    for x in data:
        if not x.get("active") or not x.get("is_visible", True): continue
        if "Summer 2027" not in x.get("terms", []): continue  # Winter/Spring 2027 co-ops are not summer roles
        if x.get("category") not in CATS: continue
        if not any(LOC.search(l) for l in x.get("locations", [])): continue
        if EXCLUDE.search(x["title"]): continue
        if now - x["date_posted"] > days * 86400: continue
        if x["id"] in seen: continue
        if x["company_name"].strip().lower() in skip_co: continue
        out.append({
            "id": x["id"],
            "company": x["company_name"],
            "title": x["title"],
            "locations": x.get("locations", [])[:4],
            "url": x["url"],
            "category": x.get("category"),
            "posted": time.strftime("%Y-%m-%d", time.gmtime(x["date_posted"])),
            "score": score(x),
            "terms": x.get("terms", []),
        })

    out.sort(key=lambda r: (r["score"], r["posted"]), reverse=True)
    # dedupe identical company+title, cap 3 per company so one employer doesn't flood the list
    pairs, per_co, final = set(), {}, []
    for r in out:
        k = (r["company"].lower(), r["title"].lower())
        if k in pairs: continue
        pairs.add(k)
        c = r["company"].lower()
        if per_co.get(c, 0) >= 3: continue
        per_co[c] = per_co.get(c, 0) + 1
        final.append(r)
    return final[: top]

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("listings")
    ap.add_argument("seen", nargs="?", help="seen_ids.txt, one tracker id per line")
    ap.add_argument("--seen-json", help="postings.json; every key counts as seen")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--skip-companies", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "skip_companies.txt"))
    a = ap.parse_args(argv)

    data = json.load(open(a.listings, encoding="utf-8"))
    seen = load_seen(a.seen, a.seen_json)
    skip_co = load_skip_companies(a.skip_companies)
    out = scan(data, seen, skip_co, a.days, a.top)
    json.dump(out, sys.stdout, indent=1)

if __name__ == "__main__":
    main()
