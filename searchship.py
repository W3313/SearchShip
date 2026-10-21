"""Launcher used by PyInstaller to build SearchShip.exe (same as python -m tracker)."""
import sys

from tracker.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
