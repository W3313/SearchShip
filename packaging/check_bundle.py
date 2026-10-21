"""Check that a built SearchShip exe contains what it needs at runtime.

    python packaging/check_bundle.py [dist/SearchShip.exe]

Reads the PyInstaller archive (the exe is not run, so no window or WebView2
is needed) and exits 1 if a bundled file or module is missing. The Windows
entries (pythonnet and the WebView2 DLLs) are checked on Windows, where the
exe is built, or with --windows.
"""
import os
import re
import sys

from PyInstaller.archive.readers import pkg_archive_contents

# Files read at runtime: ours through tracker.paths.resource(), pywebview's bridge scripts.
FILES = [r"prep_rules\.md", r"skip_companies\.txt", r"tracker/ui/index\.html", r"webview/js/api\.js"]
MODULES = ["scan", "tracker.__main__", "tracker.api", "tracker.pipeline", "webview", "bottle"]

# The WinForms + Edge WebView2 backend: pythonnet (clr), its loader and the WebView2 interop DLLs.
WINDOWS_FILES = [
    r"webview/lib/Microsoft\.Web\.WebView2\.Core\.dll",
    r"webview/lib/Microsoft\.Web\.WebView2\.WinForms\.dll",
    r"webview/lib/runtimes/win-x64/native/WebView2Loader\.dll",
    r"pythonnet/runtime/Python\.Runtime\.dll",
    r"clr_loader/ffi/dlls/amd64/ClrLoader\.dll",
]
WINDOWS_MODULES = ["clr", "pythonnet", "clr_loader", "webview.platforms.winforms", "webview.platforms.edgechromium"]


def main(argv):
    args = [a for a in argv[1:] if a != "--windows"]
    windows = sys.platform == "win32" or "--windows" in argv
    path = args[0] if args else os.path.join("dist", "SearchShip.exe")
    names = {n.replace("\\", "/") for n in pkg_archive_contents(path)}

    files = FILES + (WINDOWS_FILES if windows else [])
    modules = MODULES + (WINDOWS_MODULES if windows else [])
    missing = [f.replace("\\", "") for f in files if not any(re.fullmatch(f, n) for n in names)]
    missing += [m for m in modules if m not in names]

    print(f"{path}: {len(names)} entries, checked {len(files)} files and {len(modules)} modules")
    for m in missing:
        print(f"MISSING  {m}")
    if missing:
        return 1
    print("bundle: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
