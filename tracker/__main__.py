"""SearchShip entry point.

  SearchShip.exe / python -m tracker          open the desktop app
  python -m tracker serve [--port N]          same UI in your browser
  python -m tracker scan --dry-run            scan once and print the summary
  python -m tracker check-boards              try every configured career board
  python -m tracker selftest [--out FILE]     smoke test used by the Windows build
"""
import argparse
import json
import os
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


_INSTANCE_MUTEX = []


def _focus_running_copy() -> bool:
    """Windows: if SearchShip is already open, bring it forward and return True.
    Two copies would write the same data files over each other."""
    if os.name != "nt":
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\SearchShip.SingleInstance")
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        _INSTANCE_MUTEX.append(handle)  # held until the process exits
        return False
    user32 = ctypes.WinDLL("user32")
    hwnd = user32.FindWindowW(None, APP_NAME)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    else:
        user32.MessageBoxW(None, f"{APP_NAME} is already open.", APP_NAME, 0x40)
    return True


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

    if _focus_running_copy():
        return None
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

    # Private mode gives each launch its own local port; the UI only keeps the
    # last tab in localStorage, so nothing important is lost between launches.
    webview.start(on_start, private_mode=True)
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


def cmd_selftest(store, out, gui=False):
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
    code = 0
    if gui:
        code = _selftest_gui(store, lines, out)
    text = "\n".join(lines)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return code


def _selftest_gui(store, lines, out, timeout=90):
    """Hidden `selftest --gui` used by the Windows build: open the real window,
    wait for the page and the JS bridge (bootstrap) to answer, then close.
    Appends result lines; returns 0 on success, 1 on failure."""
    import os
    import webview

    from .api import Api
    from .jobs import JobRunner
    from .paths import resource

    done = threading.Event()
    failed = []

    def fail(msg):
        failed.append(msg)
        lines.append(f"gui: FAIL {msg}")

    def watchdog():
        # A wedged GUI loop must not hang the build: record the failure and exit.
        if not done.wait(timeout * 2 + 30):
            fail("timed out")
            if out:
                with open(out, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            print("\n".join(lines))
            os._exit(1)

    window = webview.create_window(
        f"{APP_NAME} selftest", url=str(resource("tracker", "ui", "index.html")),
        js_api=Api(store, JobRunner(store)), width=900, height=700,
    )

    def wait_for(script, deadline):
        while time.time() < deadline:
            value = window.evaluate_js(script)
            if value:
                return value
            time.sleep(0.5)
        return None

    def probe():
        try:
            deadline = time.time() + timeout
            if not window.events.loaded.wait(timeout):
                raise RuntimeError("the page did not load")
            lines.append(f"webview renderer: {webview.renderer}")
            if os.name == "nt" and webview.renderer != "edgechromium":
                raise RuntimeError("Microsoft Edge WebView2 is not in use")
            window.evaluate_js(
                "window.__searchshipSelftest = null;"
                "window.pywebview.api.bootstrap().then("
                "function (r) { window.__searchshipSelftest = r || {error: 'empty answer'}; },"
                "function (e) { window.__searchshipSelftest = {error: String(e)}; }); true")
            boot = wait_for("window.__searchshipSelftest", deadline)
            if not isinstance(boot, dict):
                raise RuntimeError("the JS bridge did not answer bootstrap()")
            if boot.get("error"):
                raise RuntimeError(f"bootstrap(): {boot['error']}")
            lines.append(f"bridge: bootstrap() -> version {boot.get('version')}")
            # The page's own start-up also calls bootstrap, then draws its first screen.
            screen = wait_for(
                "(function () { var a = document.getElementById('app');"
                " if (!a || a.querySelector('.boot')) return '';"
                " var h = a.querySelector('h1, h2');"
                " return (h ? h.textContent : a.textContent || '').trim().slice(0, 80); })()", deadline)
            if not screen:
                raise RuntimeError("the page did not draw its first screen")
            lines.append(f"ui: {screen}")
            if "could not start" in screen:
                raise RuntimeError("the page showed its start-up error")
            lines.append("gui: ok")
        except Exception as e:
            fail(str(e) or e.__class__.__name__)
        finally:
            done.set()
            window.destroy()

    threading.Thread(target=watchdog, name="searchship-selftest-watchdog", daemon=True).start()
    try:
        webview.start(probe, private_mode=True)
    except Exception as e:
        fail(f"webview.start: {e}")
    if not failed and not done.wait(15):
        fail("the window closed before the check finished")
    done.set()
    return 1 if failed or "gui: ok" not in lines else 0


def main(argv=None):
    from .paths import data_dir
    from .store import Store

    _redirect_output_if_windowed(data_dir())  # before argparse, so --help reaches the log
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
    s.add_argument("--gui", action="store_true", help=argparse.SUPPRESS)  # window smoke test (CI)
    args = ap.parse_args(argv)

    store = Store()
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
        return cmd_selftest(store, args.out, args.gui)
    run_gui(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
