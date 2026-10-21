"""Methods the window's JavaScript can call (window.pywebview.api.* in the
desktop app, POST /api/<name> in the browser dev server).

Every method returns plain JSON data. Failures come back as {"error": "..."}
instead of raising, so the UI can show them inline.
"""
import functools
import json
import os
import subprocess
import sys
import webbrowser

from . import __version__, actions, ai
from .jobs import connected_provider


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # report to the UI rather than crash the bridge
            return {"error": str(e) or e.__class__.__name__}
    return wrapper


class Api:
    def __init__(self, store, jobs):
        self._store = store
        self._jobs = jobs

    # startup ------------------------------------------------------------------
    @_safe
    def bootstrap(self):
        settings = self._store.load_settings()
        return {
            "version": __version__,
            "settings": settings,
            "state": self._store.load_state(),
            "hasResume": bool(self._store.resume_text().strip()),
            "postingCount": len(self._store.load_postings()),
            "dataDir": str(self._store.root),
            "platform": sys.platform,
        }

    @_safe
    def postings(self):
        return {"postings": self._store.load_postings(), "state": self._store.load_state()}

    # AI sign-in -----------------------------------------------------------------
    @_safe
    def providers(self):
        return {name: p.status() for name, p in ai.PROVIDERS.items()}

    @_safe
    def provider_status(self, name):
        return ai.get(name).status()

    @_safe
    def install_provider(self, name):
        ai.get(name).install()
        return {"ok": True}

    @_safe
    def login_provider(self, name):
        ai.get(name).login()
        return {"ok": True}

    @_safe
    def choose_provider(self, name):
        if name is not None:
            ai.get(name)
        return {"settings": self._store.save_settings({"provider": name})}

    # setup / settings -------------------------------------------------------------
    @_safe
    def settings_bundle(self):
        return {"settings": self._store.load_settings(), "resume": self._store.resume_text(),
                "skipCompanies": self._store.skip_text(), "dataDir": str(self._store.root)}

    @_safe
    def save_settings(self, updates):
        updates = dict(updates or {})
        if isinstance(updates.get("boards"), str):
            updates["boards"] = updates["boards"].splitlines()
        allowed = {k: updates[k] for k in ("sources", "boards", "autoScan", "provider") if k in updates}
        return {"settings": self._store.save_settings(allowed)}

    @_safe
    def save_resume(self, text):
        if not str(text or "").strip():
            raise ValueError("The resume is empty.")
        self._store.save_resume(str(text))
        return {"ok": True}

    @_safe
    def save_skip_companies(self, text):
        self._store.save_skip(str(text or ""))
        return {"ok": True}

    @_safe
    def import_postings(self, text):
        try:
            data = json.loads(text)
        except ValueError:
            raise ValueError("That file is not valid JSON.")
        return self._store.import_postings(data)

    @_safe
    def finish_onboarding(self):
        settings = self._store.save_settings({"onboarded": True})
        started = self._jobs.scan_if_due()
        return {"settings": settings, "scanStarted": started}

    # work ---------------------------------------------------------------------------
    @_safe
    def scan(self):
        return {"started": self._jobs.scan()}

    @_safe
    def draft(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        return {"started": self._jobs.draft(list(ids or []))}

    @_safe
    def job(self):
        return self._jobs.status()

    @_safe
    def ai_ready(self):
        p = connected_provider(self._store.load_settings())
        return {"ready": bool(p), "label": p.label if p else None}

    @_safe
    def act(self, pid, cmd, text=None):
        return {"row": actions.apply_to_store(self._store, cmd, pid, text)}

    # desktop helpers ------------------------------------------------------------------
    @_safe
    def open_url(self, url):
        url = str(url or "")
        if not url.startswith(("https://", "http://")):
            raise ValueError("Only web links can be opened.")
        webbrowser.open(url)
        return {"ok": True}

    @_safe
    def open_data_folder(self):
        path = str(self._store.root)
        if os.name == "nt":
            os.startfile(path)  # noqa: only exists on Windows
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return {"ok": True}
