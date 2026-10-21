"""Job sources. Each one returns listings in the SimplifyJobs listings.json shape
so scan.py's filters and scoring apply to every source the same way:

  {id, company_name, title, locations, url, category, terms, date_posted,
   active, is_visible, sponsorship}
"""
import gzip
import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit, parse_qsl, urlencode

from . import __version__

SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
VANSH_URL = "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json"

ATS_LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}

INTERN = re.compile(r"\bintern(ship)?s?\b", re.I)
QUANT = re.compile(r"Quant|Trading|Trader", re.I)
AIML = re.compile(r"Machine Learning|\bML\b|\bAI\b|Data|Applied Scien|Research Scien|Deep Learning|LLM|"
                  r"Computer Vision|\bNLP\b", re.I)
SOFTWARE = re.compile(r"Software|SWE|Engineer|Developer|Full[- ]?Stack|Front[- ]?end|Back[- ]?end|Web|"
                      r"Platform|Infrastructure|Systems|Mobile|iOS|Android|Cloud|Security|DevOps|\bSRE\b|"
                      r"Programmer", re.I)
OFF_SEASON = re.compile(r"\b(Fall|Autumn|Spring|Winter)\b", re.I)


class SourceError(Exception):
    pass


def http_get_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"SearchShip/{__version__} (+https://github.com/W3313/SearchShip)",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        raise SourceError("not found (check the board name)" if e.code == 404 else f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise SourceError(f"could not connect ({e.reason})") from e
    except Exception as e:  # timeouts, bad gzip, dropped connections
        raise SourceError(str(e) or e.__class__.__name__) from e
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise SourceError("the response was not JSON") from e


def infer_category(title: str) -> str:
    if QUANT.search(title):
        return "Quant"
    if AIML.search(title):
        return "AI/ML/Data"
    if SOFTWARE.search(title):
        return "Software"
    return "Other"


def infer_terms(title: str) -> list:
    """Career boards rarely tag a term, so read it from the title.

    An internship posted now with no season or year named is assumed to be
    for Summer 2027; one naming another year or an off-season term is not.
    """
    years = re.findall(r"\b20\d\d\b", title)
    if years and "2027" not in years:
        return [f"Other {years[0]}"]
    if OFF_SEASON.search(title) and not re.search(r"\bSummer\b", title, re.I):
        return [OFF_SEASON.search(title).group(1).title()]
    return ["Summer 2027"]


def to_epoch(value) -> float:
    if value is None or value == "":
        return datetime.now(timezone.utc).timestamp()
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e11 else float(value)  # Lever uses milliseconds
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).timestamp()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def listing(id, company, title, locations, url, date_posted, category=None, terms=None):
    return {
        "id": id,
        "company_name": company,
        "title": title,
        "locations": [l for l in locations if l],
        "url": url,
        "category": category or infer_category(title),
        "terms": terms or infer_terms(title),
        "date_posted": date_posted,
        "active": True,
        "is_visible": True,
        "sponsorship": "Other",
    }


def url_key(url: str) -> str:
    """Loose URL identity used to spot the same job arriving from two sources."""
    parts = urlsplit((url or "").strip().lower())
    path = re.sub(r"/(apply|application)/?$", "", parts.path).rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not k.startswith("utm_")))
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    return f"{host}{path}?{query}" if query else f"{host}{path}"


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NOISE_PARAMS = {"embed", "ref", "source", "src", "gh_src", "lever-source", "lever-origin"}


def job_key(url: str) -> str:
    """Identity of the job behind a URL, used to spot one posting arriving
    from two sources. The same job reached through the company site, an
    embed or the ATS itself gives the same key:

      greenhouse.io/.../jobs/123, ...?gh_jid=123, embed/job_app?token=123 -> greenhouse:123
      jobs.ashbyhq.com/<org>/<uuid>[/application], jobs.lever.co/<org>/<uuid> -> uuid:<uuid>
      <tenant>.wd5.myworkdayjobs.com/.../Title_JR-123 -> workday:<tenant>:jr-123
    Anything else falls back to url_key without tracking parameters.
    """
    parts = urlsplit((url or "").strip().lower())
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    query = dict(parse_qsl(parts.query))
    gh = query.get("gh_jid") or (query.get("token") if "greenhouse" in host else None)
    if not (gh and gh.isdigit()) and "greenhouse" in host:
        m = re.search(r"/jobs/(\d+)", parts.path)
        gh = m.group(1) if m else None
    if gh and gh.isdigit():
        return f"greenhouse:{gh}"
    if host.endswith(("ashbyhq.com", "lever.co")):
        m = UUID.search(parts.path)
        if m:
            return f"uuid:{m.group(0)}"
    path = parts.path.rstrip("/")
    if host.endswith("myworkdayjobs.com"):
        m = re.search(r"_([a-z]{0,3}-?\d{4,}[a-z0-9-]*)$", path)
        if m:
            return f"workday:{host.split('.')[0]}:{m.group(1)}"
        path = re.sub(r"^/[a-z]{2}-[a-z]{2}(?=/)", "", path)
    path = re.sub(r"/(apply|application)$", "", path)
    q = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query)
                         if not k.startswith("utm_") and k not in NOISE_PARAMS))
    return f"{host}{path}?{q}" if q else f"{host}{path}"


# --- community lists -----------------------------------------------------------

def fetch_simplify(get=http_get_json) -> list:
    data = get(SIMPLIFY_URL)
    if not isinstance(data, list):
        raise SourceError("SimplifyJobs listings.json was not a list")
    return data


def fetch_vansh(get=http_get_json) -> list:
    """vanshb03's list has no category or terms, only a season, so fill them in."""
    data = get(VANSH_URL)
    if not isinstance(data, list):
        raise SourceError("vanshb03 listings.json was not a list")
    out = []
    for x in data:
        if not isinstance(x, dict) or not x.get("id") or not x.get("title"):
            continue
        x = dict(x)
        if not x.get("terms"):
            season = str(x.get("season") or "Summer")
            x["terms"] = ["Summer 2027"] if season.lower() == "summer" else [season]
        x.setdefault("category", None)
        if not x["category"]:
            x["category"] = infer_category(x["title"])
        out.append(x)
    return out


# --- career boards -------------------------------------------------------------

def parse_board(spec: str):
    """'greenhouse:stripe = Stripe' -> ('greenhouse', 'stripe', 'Stripe')."""
    spec = (spec or "").strip()
    name = None
    if "=" in spec:
        spec, name = (p.strip() for p in spec.split("=", 1))
    if ":" not in spec:
        raise SourceError(f"Board '{spec}' should look like greenhouse:company")
    ats, token = (p.strip() for p in spec.split(":", 1))
    ats = ats.lower()
    if ats not in ATS_LABELS or not re.fullmatch(r"[A-Za-z0-9._-]+", token):
        raise SourceError(f"Board '{spec}': use greenhouse:, lever: or ashby: and a board name")
    return ats, token, name or token.replace("-", " ").replace("_", " ").title()


def board_prefix(ats: str, token: str) -> str:
    return f"{ats}:{token.lower()}:"


def fetch_board(spec: str, get=http_get_json) -> list:
    ats, token, name = parse_board(spec)
    prefix = board_prefix(ats, token)
    out = []
    if ats == "greenhouse":
        data = get(f"https://boards-api.greenhouse.io/v1/boards/{quote(token)}/jobs")
        for j in data.get("jobs", []):
            title = j.get("title") or ""
            if not INTERN.search(title):
                continue
            locs = re.split(r"\s*[;|]\s*", (j.get("location") or {}).get("name") or "")
            out.append(listing(prefix + str(j["id"]), j.get("company_name") or name, title, locs,
                               j.get("absolute_url") or "", to_epoch(j.get("first_published") or j.get("updated_at"))))
    elif ats == "lever":
        data = get(f"https://api.lever.co/v0/postings/{quote(token)}?mode=json")
        for j in data if isinstance(data, list) else []:
            title = j.get("text") or ""
            cats = j.get("categories") or {}
            if not (INTERN.search(title) or INTERN.search(str(cats.get("commitment") or ""))):
                continue
            locs = list(cats.get("allLocations") or []) or [cats.get("location") or ""]
            if str(j.get("workplaceType") or "").lower() == "remote" and "Remote" not in locs:
                locs.append("Remote")
            out.append(listing(prefix + str(j["id"]), name, title, locs,
                               j.get("hostedUrl") or "", to_epoch(j.get("createdAt"))))
    else:  # ashby
        data = get(f"https://api.ashbyhq.com/posting-api/job-board/{quote(token)}")
        for j in data.get("jobs", []):
            title = j.get("title") or ""
            is_intern = INTERN.search(title) or str(j.get("employmentType") or "").lower() == "intern"
            if not is_intern or j.get("isListed") is False:
                continue
            locs = [j.get("location") or ""] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
            if j.get("isRemote") and "Remote" not in locs:
                locs.append("Remote")
            out.append(listing(prefix + str(j["id"]), name, title, locs,
                               j.get("jobUrl") or "", to_epoch(j.get("publishedAt"))))
    return out


# --- AI web search results -------------------------------------------------------

def web_listing(item: dict):
    """Turn one AI web-search result into a listing, or None if it is unusable."""
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or "").strip()
    company = str(item.get("company") or "").strip()
    if not (url.startswith("http") and title and company):
        return None
    category = item.get("category")
    if category not in ("Software", "AI/ML/Data", "Quant"):
        category = infer_category(title)
    locs = [str(l).strip() for l in item.get("locations") or [] if str(l).strip()]
    pid = "web:" + hashlib.sha1(url_key(url).encode()).hexdigest()[:16]
    return listing(pid, company, title, locs, url, to_epoch(item.get("posted") or None),
                   category=category, terms=["Summer 2027"])
