# SearchShip

SearchShip is a Windows desktop app that tracks Summer 2027 internships. Once a day it checks
community job lists, company career boards and (optionally) the web, keeps the postings that fit,
ranks them, and drafts application prep for the best ones using your own Claude or ChatGPT plan.
Your tracker lives on your computer, and SearchShip never applies to anything for you.

## Download and install

1. Get `SearchShip.exe` from the [Releases](https://github.com/W3313/SearchShip/releases) page
   (published when a `v*` tag is pushed), or from the latest run of the
   [Windows build](https://github.com/W3313/SearchShip/actions/workflows/windows.yml) workflow:
   open the run, download the **SearchShip-windows** artifact (a zip) and unzip it.
   Artifacts need a GitHub sign-in and expire after the repository's retention period.
2. Put `SearchShip.exe` wherever you like and double-click it. There is no installer.
3. The exe is not code-signed, so Windows SmartScreen may say "Windows protected your PC".
   Choose **More info**, then **Run anyway**. Some antivirus tools also distrust unsigned
   PyInstaller apps; if yours blocks it, you can [run it from source](#run-from-source) instead.
4. The window uses Microsoft Edge WebView2, which comes with Windows 11 and up-to-date Windows 10.
   If the window stays blank, install the "Evergreen" WebView2 Runtime from Microsoft.

Your data is stored in `%APPDATA%\SearchShip`. To uninstall, delete the exe and that folder.

## First run

1. **Landing.** Click **Get started**.
2. **Connect your AI.** Pick Claude or ChatGPT. The **Install** and **Sign in** buttons run the
   official command-line apps for you in a visible console window, and SearchShip notices when
   you are done. SearchShip never sees your password or token.

   | Account | Needs | Install (PowerShell) | Sign in |
   |---|---|---|---|
   | Claude Pro or Max | Claude Code | `irm https://claude.ai/install.ps1 \| iex` | `claude auth login` |
   | ChatGPT Plus or Pro | Codex CLI and [Node.js](https://nodejs.org) | `npm install -g @openai/codex` | `codex login` |

   Claude Code on Windows may ask you to install [Git for Windows](https://git-scm.com/downloads/win)
   first. You can skip this step: scanning works without AI, and drafting stays off until you
   connect in Settings.
3. **Resume and import.** Paste your resume or open a `.md`/`.txt` file. It is the only source
   of facts about you when drafting. Optionally import the `postings.json` from the old tracker;
   rows you already have are kept as they are.
4. **Finish.** The tracker opens and the first scan starts.

## Using the tracker

Tabs: **Ready**, **New**, **Applied/Interview**, **Skipped/Closed** and **All**, each with a count,
plus a search box for company or title. **Scan now** runs a scan; **Settings** holds the AI
account, sources, career boards, companies to skip, your resume, auto-scan and the data folder.

| Status | Meaning | Card buttons |
|---|---|---|
| New | Found by a scan, not reviewed yet | Draft*, Approve, Skip |
| Ready | Worth applying to. Once drafted, fit, project and why show with copy buttons | Applied, Skip, Redraft* |
| Applied | You applied (the date is recorded) | Interview |
| Interview | You have an interview | |
| Skipped | Ruled out by you, or by the AI with its reason in the notes | Restore |
| Closed | Its source no longer lists it | Restore |

Every card also has **Open** (the posting in your browser) and **Note**. *Draft and Redraft
appear once an AI account is connected. Approve, Skip, Applied, Interview, Restore and Note mark
the row as touched, and automatic scans never change a touched row again.

## AI usage and terms

Drafting and web search count against your Claude or ChatGPT plan's usage limits, the same as
using Claude Code or Codex yourself. SearchShip runs the official CLIs on your own computer, for
your own use. Check Anthropic's and OpenAI's current terms before sharing the app with other
people: Anthropic does not allow third-party products to offer Claude.ai subscription login
without approval, so for anyone else the safe route is an API key, in Claude Code
(`claude auth login --console`) or in Codex.

## How it works

- **Sources** (each can be turned off in Settings):
  - the [SimplifyJobs](https://github.com/SimplifyJobs/Summer2027-Internships) Summer 2027 list,
  - the [vanshb03](https://github.com/vanshb03/Summer2027-Internships) Summer 2027 list,
  - Greenhouse, Lever and Ashby career boards you list in Settings
    (`greenhouse:stripe = Stripe`, one per line; a starter set is included),
  - AI web search for postings the lists miss, at most once every 20 hours, when an AI account
    is connected.
- **Filtering and scoring** come from `scan.py`, unchanged from the original tracker. Every source
  is converted to the SimplifyJobs listing format so the same filters apply: Summer 2027 software,
  AI/ML/data and quant roles in the Bay Area, the Sacramento area or remote, that undergraduates
  can take, posted in the last 14 days, at most 25 new rows per scan. A job that arrives from a
  second source (same URL, or same company and title) is not added twice.
- **Auto-scan.** While SearchShip is open it scans when the last scan is 20 or more hours old,
  checking at launch and every 30 minutes. There is no background service: if the app is closed,
  nothing runs.
- **Housekeeping.** A New or Ready row you have not touched becomes Closed when its source (the
  SimplifyJobs list, the vanshb03 list or its career board) no longer lists it as active. A source
  that fails to load never closes anything, and web search results are never closed automatically.
- **Drafting.** After each scan, up to 8 of the new rows with a score of 4.5 or more (highest first)
  go to the AI. The rules it follows are in [`prep_rules.md`](prep_rules.md): prep is
  `{fit, project, why}` in your voice, using only facts from your resume, and roles that are
  MS/PhD-only, outside the US or hardware-focused get skipped with a reason. You can also draft
  any New or Ready card by hand.
- **Checks before saving.** The AI runs headless in an empty temporary folder (`claude -p` with
  only web search and web fetch, or `codex exec` in its read-only sandbox with web search) and
  must answer with JSON. SearchShip validates every entry before saving it: only the ids it asked
  about, all three prep fields present, no em or en dashes in the prep, and a reason for every skip. Entries that fail are dropped and
  listed as problems, and a row you changed while the AI was working is left alone.
- **Nothing is ever submitted to employers.**

## Your data

Everything is plain files in `%APPDATA%\SearchShip` (Settings > Data > **Open data folder**):

| File | Contents |
|---|---|
| `postings.json` | Your tracker: `{id: {company, title, locations, url, status, notes, prep, ...}}` |
| `state.json` | Last scan time, summary line and problems |
| `settings.json` | AI account choice, sources, career boards, auto-scan |
| `resume.md` | Your resume |
| `skip_companies.txt` | Employers to ignore, one per line |
| `searchship.log` | Log written by the exe (it has no console) |
| `webview\` | The window's browser storage |

What is sent to the AI: the prep rules, your resume and the postings being drafted (company,
title, locations, link, category, posted date). Web search also sends the names of companies
already in your tracker so it can look for new ones. Apart from that, SearchShip itself only
downloads the job lists and career boards; the Claude Code or Codex CLI talks to Anthropic or
OpenAI as it normally does. To back up, copy the folder. To keep the data somewhere else, set the
`SEARCHSHIP_HOME` environment variable to another folder.

## Run from source

Requires Python 3.12 on Windows (the browser mode and the CLI also run on macOS and Linux).

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m tracker                     # the desktop window
python -m tracker serve               # the same UI in your browser, on http://127.0.0.1:8765
python -m tracker scan --dry-run      # print the postings a scan would add; saves nothing
python -m tracker check-boards        # try every career board listed in Settings
```

`scan` options: `--dry-run` saves nothing and never calls the AI; `--listings path\to\listings.json`
uses a local copy of the SimplifyJobs list; `--no-ai` skips web search and drafting on a real scan.
Set `SEARCHSHIP_FAKE_AI=1` to use canned AI answers while working on the UI, so no real Claude or
Codex call is made.

Tests (they never touch the network or a real AI):

```powershell
pip install pytest
python -m pytest -q
```

Build the exe on Windows (writes `dist\SearchShip.exe`):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# which installs requirements-dev.txt and then runs:
python -m PyInstaller --noconfirm --clean packaging\SearchShip.spec
```

`packaging/SearchShip.spec` builds one windowed exe and bundles `prep_rules.md`,
`skip_companies.txt` and `tracker/ui/index.html`. `packaging/make_icon.py` redraws the icon.
To check a build, run `dist\SearchShip.exe selftest --out selftest.txt` and read the file
(the exe has no console), and `python packaging\check_bundle.py` to list anything missing from it.

**Continuous integration** (`.github/workflows/windows.yml`) runs the tests on Linux and Windows,
builds the exe, runs its self-test and a window smoke test, and uploads the
**SearchShip-windows** artifact. Pushing a tag such as `v0.1.0` also publishes a GitHub Release
with `SearchShip.exe` and its SHA-256 checksum:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

## Changes from the original spec

The first version of this project was specified as a GitHub Actions + GitHub Pages tracker.
SearchShip keeps its data model, statuses, tabs, prep rules and `scan.py`, with these changes:

- It is a desktop app that runs on your computer, instead of a scheduled workflow and a Pages site.
- Actions are buttons on each card instead of GitHub issues. `parse_command` in
  `tracker/actions.py` still understands the old issue titles (`approve <id>`, `note <id>: text`).
- Prep rules live in `prep_rules.md` instead of `CLAUDE.md`, so Claude Code sessions working on
  this repository do not load them as their own instructions.
- The AI returns JSON that SearchShip validates, instead of editing `postings.json` itself.
- Your resume and the old tracker's `postings.json` are not committed, because this repository
  is public. You add them on first run.
- **Approve** moves a row to Ready; **Draft** asks the AI for prep.
- New actions: **Interview** and **Restore** (undo a skip or an automatic close).
- More sources: the vanshb03 list, company career boards and AI web search.
- The scan summary appears in the app header instead of a "Drafts ready" issue.
