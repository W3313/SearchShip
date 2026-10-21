#!/usr/bin/env python3
"""Internship scanner for William Li's tracker.

Reads the public SimplifyJobs Summer 2027 listings, keeps roles that fit
(software / AI-ML / quant, Bay Area + Sacramento + remote, undergrad-eligible),
scores them, and prints new ones as JSON.

Usage:
  git clone --depth 1 https://github.com/SimplifyJobs/Summer2027-Internships.git s27
  python3 scan.py s27/.github/scripts/listings.json seen_ids.txt [--days 30] [--top 40] > new.json

seen_ids.txt: one tracker doc id per line (rows already in the tracker, any status).
Also skips companies listed in skip_companies.txt (one per line, case-insensitive) if present.
"""
import json, re, sys, time, os, argparse

ap = argparse.ArgumentParser()
ap.add_argument("listings")
ap.add_argument("seen")
ap.add_argument("--days", type=int, default=30)
ap.add_argument("--top", type=int, default=40)
ap.add_argument("--skip-companies", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "skip_companies.txt"))
a = ap.parse_args()

data = json.load(open(a.listings))
seen = set()
if os.path.exists(a.seen):
    seen = {l.strip() for l in open(a.seen) if l.strip()}
skip_co = set()
if os.path.exists(a.skip_companies):
    skip_co = {l.strip().lower() for l in open(a.skip_companies) if l.strip()}

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

now = time.time()
out = []
for x in data:
    if not x.get("active") or not x.get("is_visible", True): continue
    if "Summer 2027" not in x.get("terms", []): continue  # Winter/Spring 2027 co-ops are not summer roles
    if x.get("category") not in CATS: continue
    if not any(LOC.search(l) for l in x.get("locations", [])): continue
    if EXCLUDE.search(x["title"]): continue
    if now - x["date_posted"] > a.days * 86400: continue
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
out = final[: a.top]
json.dump(out, sys.stdout, indent=1)
