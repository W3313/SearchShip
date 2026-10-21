"""Where bundled resources and the user's data live."""
import os
import sys
from pathlib import Path

from . import APP_NAME


def resource_dir() -> Path:
    """Folder holding scan.py, prep_rules.md, skip_companies.txt and tracker/ui.

    In a PyInstaller build that is the unpacked bundle (sys._MEIPASS);
    in a source checkout it is the repository root.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def resource(*parts) -> Path:
    return resource_dir().joinpath(*parts)


def data_dir() -> Path:
    """Per-user data folder: %APPDATA%\\SearchShip on Windows.

    SEARCHSHIP_HOME overrides it (tests, dev runs, portable installs).
    """
    override = os.environ.get("SEARCHSHIP_HOME")
    if override:
        d = Path(override)
    elif os.name == "nt":
        d = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d
