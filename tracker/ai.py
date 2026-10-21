"""Bridge to the user's own AI subscription.

SearchShip never handles a password or token. It drives the official
command-line apps, which keep their own sign-in:

  * Claude (Pro / Max) through Claude Code:   claude auth login
  * ChatGPT (Plus / Pro) through Codex CLI:   codex login

Every request runs headless (`claude -p` / `codex exec`) in an empty temp
folder, with only web search and web fetch available, and must answer with
JSON matching a schema we pass in.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


class AIError(Exception):
    pass


def _windows_registry_path() -> str:
    """The PATH a fresh terminal would get. Lets us find a CLI the user just
    installed without restarting SearchShip."""
    try:
        import winreg
    except ImportError:
        return ""
    parts = []
    for root, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    return os.pathsep.join(parts)


def child_env() -> dict:
    env = dict(os.environ)
    if IS_WINDOWS:
        extra = _windows_registry_path()
        if extra:
            env["PATH"] = env.get("PATH", "") + os.pathsep + extra
    return env


def _argv(exe: str, args: list) -> list:
    # .cmd/.bat shims (npm installs) must run through cmd.exe
    if IS_WINDOWS and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/d", "/c", exe, *args]
    return [exe, *args]


def _run(argv, *, input_text=None, timeout=60, cwd=None):
    return subprocess.run(
        argv, input=input_text, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, cwd=cwd, env=child_env(),
        creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
    )


def open_console(argv: list, title: str = "SearchShip") -> subprocess.Popen:
    """Run a command where the user can see and interact with it."""
    if IS_WINDOWS:
        return subprocess.Popen(argv, env=child_env(), creationflags=CREATE_NEW_CONSOLE)
    return subprocess.Popen(argv, env=child_env())


def _extract_json(text: str):
    """First JSON object or array in `text` (tolerates code fences and chatter)."""
    text = text or ""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return decoder.raw_decode(text, i)[0]
            except ValueError:
                continue
    raise AIError("The AI did not return readable JSON.")


class Provider:
    name = ""
    label = ""
    plan = ""
    exe_names = ()
    install_hint = ""

    def candidates(self):
        return []

    def find(self):
        env_path = child_env().get("PATH")
        for n in self.exe_names:
            hit = shutil.which(n, path=env_path)
            if hit:
                return hit
        for c in self.candidates():
            if c and Path(c).is_file():
                return str(c)
        return None

    def require(self) -> str:
        exe = self.find()
        if not exe:
            raise AIError(f"{self.label} is not installed. Open Settings and connect it.")
        return exe

    def status(self) -> dict:
        exe = self.find()
        base = {"name": self.name, "label": self.label, "plan": self.plan,
                "installCommand": self.install_hint, "installed": bool(exe),
                "loggedIn": False, "detail": "", "path": exe or ""}
        if not exe:
            base["detail"] = "Not installed"
            return base
        try:
            base.update(self._login_status(exe))
        except subprocess.TimeoutExpired:
            base["detail"] = "Timed out checking sign-in"
        except OSError as e:
            base["detail"] = str(e)
        return base

    def login(self) -> subprocess.Popen:
        return open_console(_argv(self.require(), self.login_args), f"Sign in to {self.label}")

    def install(self) -> subprocess.Popen:
        if IS_WINDOWS:
            return open_console(["powershell.exe", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                 "-Command", self.install_hint], f"Install {self.label}")
        raise AIError(f"Run this in a terminal: {self.install_hint}")

    def run_json(self, prompt: str, schema: dict, web: bool = True, timeout: int = 900) -> dict:
        raise NotImplementedError


class Claude(Provider):
    name = "claude"
    label = "Claude"
    plan = "Claude Pro or Max, through Claude Code"
    exe_names = ("claude",)
    install_hint = ("irm https://claude.ai/install.ps1 | iex" if IS_WINDOWS
                    else "curl -fsSL https://claude.ai/install.sh | bash")
    login_args = ["auth", "login", "--claudeai"]

    def candidates(self):
        home = Path.home()
        appdata = os.environ.get("APPDATA", "")
        return [home / ".local" / "bin" / ("claude.exe" if IS_WINDOWS else "claude"),
                home / ".claude" / "local" / ("claude.exe" if IS_WINDOWS else "claude"),
                Path(appdata) / "npm" / "claude.cmd" if appdata else None]

    def _login_status(self, exe):
        r = _run(_argv(exe, ["auth", "status", "--json"]), timeout=30)
        try:
            data = _extract_json(r.stdout)
        except AIError:
            return {"loggedIn": False, "detail": (r.stderr or r.stdout).strip()[:200] or "Not signed in"}
        logged = bool(data.get("loggedIn"))
        detail = "Signed in" if logged else "Not signed in"
        if logged and data.get("email"):
            detail = f"Signed in as {data['email']}"
        if logged and data.get("authMethod") not in (None, "oauth_token", "claude.ai", "claudeai", "oauth"):
            detail += f" ({data.get('authMethod')})"
        return {"loggedIn": logged, "detail": detail}

    def run_json(self, prompt, schema, web=True, timeout=900):
        exe = self.require()
        tools = "WebSearch,WebFetch" if web else ""
        args = ["-p", "--output-format", "json", "--json-schema", json.dumps(schema, separators=(",", ":")),
                "--max-turns", "40", "--no-session-persistence", "--tools", tools]
        if web:
            args += ["--allowedTools", tools]
        with tempfile.TemporaryDirectory(prefix="searchship-") as work:
            try:
                r = _run(_argv(exe, args), input_text=prompt, timeout=timeout, cwd=work)
            except subprocess.TimeoutExpired:
                raise AIError("Claude took too long and was stopped.")
        out = r.stdout.strip()
        try:
            data = _extract_json(out)
        except AIError:
            raise AIError(f"Claude Code failed: {(r.stderr or out).strip()[:400]}")
        if data.get("is_error") or data.get("subtype") not in (None, "success"):
            raise AIError(f"Claude Code: {str(data.get('result') or data.get('subtype'))[:400]}")
        if isinstance(data.get("structured_output"), dict):
            return data["structured_output"]
        return _extract_json(str(data.get("result") or ""))


class Codex(Provider):
    name = "codex"
    label = "ChatGPT"
    plan = "ChatGPT Plus or Pro, through the Codex CLI"
    exe_names = ("codex",)
    install_hint = "npm install -g @openai/codex"
    login_args = ["login"]

    def candidates(self):
        appdata = os.environ.get("APPDATA", "")
        return [Path(appdata) / "npm" / "codex.cmd" if appdata else None,
                Path.home() / ".local" / "bin" / ("codex.exe" if IS_WINDOWS else "codex")]

    def _login_status(self, exe):
        r = _run(_argv(exe, ["login", "status"]), timeout=30)
        msg = (r.stderr.strip() or r.stdout.strip()).splitlines()
        detail = msg[-1] if msg else ""
        logged = r.returncode == 0 and "not logged in" not in detail.lower()
        return {"loggedIn": logged, "detail": detail or ("Signed in" if logged else "Not signed in")}

    def run_json(self, prompt, schema, web=True, timeout=900):
        exe = self.require()
        with tempfile.TemporaryDirectory(prefix="searchship-") as work:
            schema_path = Path(work) / "schema.json"
            out_path = Path(work) / "answer.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            args = ["exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                    "--color", "never", "--output-schema", str(schema_path),
                    "--output-last-message", str(out_path),
                    "-c", "web_search=" + ('"live"' if web else '"disabled"'), "-"]
            try:
                r = _run(_argv(exe, args), input_text=prompt, timeout=timeout, cwd=work)
            except subprocess.TimeoutExpired:
                raise AIError("Codex took too long and was stopped.")
            answer = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if r.returncode != 0 and not answer.strip():
            raise AIError(f"Codex failed: {(r.stderr or r.stdout).strip()[-400:]}")
        return _extract_json(answer)


class Fake(Provider):
    """Stand-in used when SEARCHSHIP_FAKE_AI=1 (UI development and tests).
    Claude reports signed in, ChatGPT reports not installed; answers are canned."""

    def __init__(self, name, label, plan, signed_in):
        self.name, self.label, self.plan, self.signed_in = name, label, plan, signed_in
        self.install_hint = Claude.install_hint if name == "claude" else Codex.install_hint

    def find(self):
        return f"/fake/{self.name}" if self.signed_in else None

    def _login_status(self, exe):
        return {"loggedIn": True, "detail": "Signed in (fake)"}

    def login(self):
        return None

    def install(self):
        return None

    def run_json(self, prompt, schema, web=True, timeout=900):
        import re
        import time
        time.sleep(1.5)
        if "drafts" in schema.get("properties", {}):
            ids = re.findall(r'"id": "([^"]+)"', prompt)
            return {"drafts": [{"id": i, "status": "ready", "notes": "",
                                "fit": "Fake fit sentence for UI testing.",
                                "project": "Fake project line with numbers 1, 2, 3.",
                                "why": "Fake why answer. It has a few sentences. It is only for testing."}
                               for i in ids]}
        return {"postings": []}


if os.environ.get("SEARCHSHIP_FAKE_AI") == "1":
    PROVIDERS = {"claude": Fake("claude", Claude.label, Claude.plan, True),
                 "codex": Fake("codex", Codex.label, Codex.plan, False)}
else:
    PROVIDERS = {"claude": Claude(), "codex": Codex()}


def get(name):
    if name not in PROVIDERS:
        raise AIError("No AI account is connected. Open Settings to sign in.")
    return PROVIDERS[name]
