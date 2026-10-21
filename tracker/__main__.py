"""SearchShip entry point.

  SearchShip.exe / python -m tracker          open the desktop app
  python -m tracker serve [--port N]          same UI in your browser
  python -m tracker scan --dry-run            scan once and print the summary
  python -m tracker check-boards              try every configured career board
  python -m tracker selftest [--out FILE]     smoke test used by the Windows build
"""
import argparse
import json
import sys
import threading
import time

from . import APP_NAME, __version__


def _redirect_output_if_windowed(store_root):
    # A windowed .exe has no console, so keep a log file for troubleshooting.
    if sys.stdout is None or sys.stderr is None:
        path = store_root / "searchship.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(store_root / "searchship.old.log")
        log = open(path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _windows_prefers_dark() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def run_gui(store):
    import webview  # imported here so CLI commands work without pywebview

    from .api import Api
    from .jobs import JobRunner
    from .paths import resource

    jobs = JobRunner(store)
    api = Api(store, jobs)
    window = webview.create_window(
        APP_NAME, url=str(resource("tracker", "ui", "index.html")), js_api=api,
        width=1120, height=800, min_size=(400, 600),
        background_color="#151515" if _windows_prefers_dark() else "#FAFAF8",
        text_select=True,
    )

    def auto_scan():
        # Scan on launch if the last run is old, then re-check every 30 minutes.
        while True:
            try:
                jobs.scan_if_due()
            except Exception as e:
                print("auto-scan:", e)
            time.sleep(1800)

    def on_start():
        threading.Thread(target=auto_scan, name="searchship-auto", daemon=True).start()

    webview.start(on_start, private_mode=False, storage_path=str(store.root / "webview"))
    return window


def cmd_scan(store, args):
    from . import sources
    from .jobs import run_daily

    get = sources.http_get_json
    if args.listings:
        def get(url, timeout=60, _real=sources.http_get_json):
            if url == sources.SIMPLIFY_URL:
                with open(args.listings, encoding="utf-8") as f:
                    return json.load(f)
            return _real(url, timeout)
    result = run_daily(store, log=print, dry_run=args.dry_run, get=get,
                       provider=None if args.no_ai else "auto")
    if args.dry_run:
        for pid in result["new"]:
            r = result["newRows"][pid]
            mark = "*" if pid in result["toDraft"] else " "
            print(f" {mark} {r['score']:>5}  {r['company']} | {r['title']}  [{r['source']}]")
        if result["new"]:
            print(" * = would be drafted")
    return 0 if not result["errors"] else 1


def cmd_check_boards(store):
    from . import sources
    ok = True
    for spec in store.load_settings()["boards"]:
        try:
            rows = sources.fetch_board(spec)
            print(f"ok    {spec}: {len(rows)} internship posting(s)")
        except Exception as e:
            ok = False
            print(f"FAIL  {spec}: {e}")
    return 0 if ok else 1


def cmd_selftest(store, out):
    """Import every module, find bundled files, and run one merge offline."""
    import scan as scanner
    from . import actions, ai, api, drafting, jobs, pipeline, sources  # noqa: F401
    from .paths import resource

    lines = [f"{APP_NAME} {__version__}", f"python {sys.version.split()[0]}"]
    for rel in (("prep_rules.md",), ("skip_companies.txt",), ("tracker", "ui", "index.html")):
        p = resource(*rel)
        lines.append(f"{'/'.join(rel)}: {'ok' if p.exists() else 'MISSING'}")
        if not p.exists():
            raise SystemExit("\n".join(lines))
    now = time.time()
    listing = {"id": "t1", "company_name": "Acme", "title": "Software Engineer Intern",
               "locations": ["San Jose, CA"], "url": "https://example.com/1", "category": "Software",
               "terms": ["Summer 2027"], "date_posted": now, "active": True, "is_visible": True}
    new, closed, to_draft = pipeline.merge({}, [pipeline.Fetched("SimplifyJobs", [listing])], set())
    assert list(new) == ["t1"] and to_draft == ["t1"], (new, to_draft)
    assert scanner.score(listing) >= 4.5
    for name, p in ai.PROVIDERS.items():
        lines.append(f"{name}: found={bool(p.find())}")
    try:
        import webview  # noqa: F401
        lines.append("pywebview: ok")
    except Exception as e:
        lines.append(f"pywebview: {e}")
        raise SystemExit("\n".join(lines))
    lines.append("selftest: ok")
    text = "\n".join(lines)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


def main(argv=None):
    from .store import Store

    ap = argparse.ArgumentParser(prog=APP_NAME)
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = ap.add_subparsers(dest="command")
    s = sub.add_parser("serve", help="open the UI in a browser instead of a window")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s = sub.add_parser("scan", help="scan once and print the summary")
    s.add_argument("--dry-run", action="store_true", help="show what would change without saving")
    s.add_argument("--listings", help="use a local SimplifyJobs listings.json instead of downloading")
    s.add_argument("--no-ai", action="store_true", help="skip web search and drafting")
    sub.add_parser("check-boards", help="fetch every configured career board")
    s = sub.add_parser("selftest", help="offline smoke test")
    s.add_argument("--out")
    args = ap.parse_args(argv)

    store = Store()
    _redirect_output_if_windowed(store.root)
    if args.command == "serve":
        from .api import Api
        from .devserver import serve
        from .jobs import JobRunner
        serve(Api(store, JobRunner(store)), args.port, not args.no_browser)
        return 0
    if args.command == "scan":
        return cmd_scan(store, args)
    if args.command == "check-boards":
        return cmd_check_boards(store)
    if args.command == "selftest":
        return cmd_selftest(store, args.out)
    run_gui(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
