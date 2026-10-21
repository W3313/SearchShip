# PyInstaller spec for SearchShip.exe: one file, windowed (no console).
#
# Build from the repository root (packaging/build.ps1 does this):
#     python -m PyInstaller --noconfirm --clean packaging/SearchShip.spec
# Output: dist/SearchShip.exe
#
# pywebview ships its own PyInstaller hook (webview/__pyinstaller), which
# collects the WebView2 DLLs (webview/lib) and the bridge scripts (webview/js).
# pyinstaller-hooks-contrib covers pythonnet (clr + Python.Runtime.dll) and
# clr_loader. The Edge WebView2 Runtime itself comes with Windows.
import os
import re

from PyInstaller.compat import is_win

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821 (SPECPATH is set by PyInstaller)

with open(os.path.join(ROOT, "tracker", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', f.read()).group(1)
nums = tuple(int(x) for x in re.findall(r"\d+", VERSION)[:4])
nums = nums + (0,) * (4 - len(nums))

# Files read at runtime through tracker.paths.resource() (sys._MEIPASS).
datas = [
    (os.path.join(ROOT, "prep_rules.md"), "."),
    (os.path.join(ROOT, "skip_companies.txt"), "."),
    (os.path.join(ROOT, "tracker", "ui", "index.html"), os.path.join("tracker", "ui")),
]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "searchship.py")],
    pathex=[ROOT],
    datas=datas,
    # scan.py sits at the repository root and is imported by tracker.pipeline.
    hiddenimports=["scan"],
    # Other pywebview backends; never needed on Windows, and keeping them out
    # stops a dev machine that happens to have Qt or GTK from bloating the exe.
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi", "cefpython3"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821


def windows_version_info():
    """File properties shown by Explorer and Task Manager (Windows builds only)."""
    from PyInstaller.utils.win32 import versioninfo as vi  # needs pefile, which only Windows installs

    strings = {"CompanyName": "SearchShip", "FileDescription": "SearchShip", "FileVersion": VERSION,
               "InternalName": "SearchShip", "OriginalFilename": "SearchShip.exe",
               "ProductName": "SearchShip", "ProductVersion": VERSION}
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            vi.StringFileInfo([vi.StringTable("040904B0", [vi.StringStruct(k, v) for k, v in strings.items()])]),
            vi.VarFileInfo([vi.VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SearchShip",
    icon=os.path.join(ROOT, "packaging", "searchship.ico"),
    version=windows_version_info() if is_win else None,
    console=False,          # windowed: no console window
    debug=False,
    strip=False,
    upx=False,              # UPX-packed exes trip antivirus heuristics more often
    runtime_tmpdir=None,
)
