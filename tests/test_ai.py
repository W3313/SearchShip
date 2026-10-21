"""ai: JSON extraction and the Claude Code / Codex CLI plumbing.

The CLI tests put a fake `claude` or `codex` (a small Python script) alone
on PATH and point HOME at a temp folder, so the real CLIs installed on the
machine are never found or run.
"""
import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from tracker import ai, drafting
from tracker.ai import AIError, _extract_json

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake executables are POSIX scripts")


# --- _extract_json ------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"drafts": [{"id": "x"}]}\n```', {"drafts": [{"id": "x"}]}),
    ('```\n[1, 2]\n```', [1, 2]),
    ('Here is the JSON you asked for:\n\n{"ok": true, "n": {"m": [1]}}\n\nLet me know!',
     {"ok": True, "n": {"m": [1]}}),
    ('Thinking {not json} ... final: {"a": "b"}', {"a": "b"}),
    ('{"text": "braces } and { inside strings"} trailing', {"text": "braces } and { inside strings"}),
])
def test_extract_json(text, expected):
    assert _extract_json(text) == expected


@pytest.mark.parametrize("text", ["", None, "no json here", "{broken", "```json\n```"])
def test_extract_json_raises_ai_error(text):
    with pytest.raises(AIError):
        _extract_json(text)


# --- fake CLI helpers ------------------------------------------------------------------------------

FAKE_CLAUDE = r'''
import json, os, sys
log = os.environ["FAKE_LOG"]
stdin = sys.stdin.read() if "-p" in sys.argv[1:] else ""
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd()}) + "\n")
if sys.argv[1:3] == ["auth", "status"]:
    default = '{"loggedIn": true, "email": "me@example.com", "authMethod": "claude.ai"}'
    sys.stdout.write(os.environ.get("FAKE_STATUS", default))
    sys.exit(int(os.environ.get("FAKE_STATUS_CODE", "0")))
sys.stderr.write(os.environ.get("FAKE_STDERR", ""))
sys.stdout.write(os.environ["FAKE_STDOUT"])
sys.exit(int(os.environ.get("FAKE_CODE", "0")))
'''

FAKE_CODEX = r'''
import json, os, sys
log = os.environ["FAKE_LOG"]
args = sys.argv[1:]
stdin = sys.stdin.read() if args and args[-1] == "-" else ""
entry = {"argv": args, "stdin": stdin, "cwd": os.getcwd()}
if "--output-schema" in args:
    entry["schema"] = json.load(open(args[args.index("--output-schema") + 1], encoding="utf-8"))
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry) + "\n")
if args[:2] == ["login", "status"]:
    sys.stderr.write(os.environ.get("FAKE_STATUS", "Logged in using ChatGPT") + "\n")
    sys.exit(int(os.environ.get("FAKE_STATUS_CODE", "0")))
if "--output-last-message" in args and "FAKE_ANSWER" in os.environ:
    with open(args[args.index("--output-last-message") + 1], "w", encoding="utf-8") as f:
        f.write(os.environ["FAKE_ANSWER"])
sys.stdout.write("codex progress output\n")
sys.stderr.write(os.environ.get("FAKE_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_CODE", "0")))
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Returns install(name, body) -> (exe_path, read_log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", str(bindir))       # nothing else, so the real CLIs cannot be found
    monkeypatch.setenv("HOME", str(home))         # Path.home() candidates point at an empty folder
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("FAKE_LOG", str(log))

    def install(name, body):
        exe = bindir / name
        exe.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return exe

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

    return install, calls


SCHEMA = drafting.DRAFT_SCHEMA
ANSWER = {"drafts": [{"id": "a", "status": "skipped", "notes": "MS only", "fit": "", "project": "", "why": ""}]}


def claude_result(**over):
    r = {"type": "result", "subtype": "success", "is_error": False, "result": "done",
         "structured_output": ANSWER, "session_id": "s"}
    r.update(over)
    return json.dumps(r)


# --- Claude ------------------------------------------------------------------------------------------

@posix_only
def test_claude_run_json_command_and_parsing(fake_cli, monkeypatch, tmp_path):
    install, calls = fake_cli
    exe = install("claude", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_STDOUT", claude_result())
    p = ai.Claude()
    assert p.find() == str(exe)
    prompt = "Draft prep for these postings.\n<postings>[]</postings>"
    assert p.run_json(prompt, SCHEMA, web=True) == ANSWER

    (call,) = calls()
    argv = call["argv"]
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    tools = argv[argv.index("--tools") + 1]
    assert set(tools.split(",")) == {"WebSearch", "WebFetch"}
    for flag_value in argv:
        for dangerous in ("Bash", "Edit", "Write", "NotebookEdit"):
            assert dangerous not in flag_value, f"Claude must not get the {dangerous} tool"
    assert "--dangerously-skip-permissions" not in argv
    assert call["stdin"] == prompt, "the prompt goes through stdin, not the command line"
    assert prompt not in argv
    cwd = Path(call["cwd"])
    assert cwd != Path.cwd() and not cwd.exists(), "Claude runs in a throwaway temp folder"


@posix_only
def test_claude_run_json_without_web_disables_all_tools(fake_cli, monkeypatch):
    install, calls = fake_cli
    install("claude", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_STDOUT", claude_result())
    ai.Claude().run_json("hi", SCHEMA, web=False)
    argv = calls()[0]["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv


@posix_only
def test_claude_falls_back_to_result_text(fake_cli, monkeypatch):
    install, _ = fake_cli
    install("claude", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_STDOUT", claude_result(structured_output=None,
                                                    result="Sure:\n```json\n" + json.dumps(ANSWER) + "\n```"))
    assert ai.Claude().run_json("hi", SCHEMA) == ANSWER


@posix_only
@pytest.mark.parametrize("stdout, code, stderr, match", [
    (claude_result(is_error=True, result="Credit balance is too low"), 1, "", "Credit balance"),
    (claude_result(subtype="error_max_turns", result=None), 0, "", "error_max_turns"),
    ("not json at all", 1, "Error: not logged in", "not logged in"),
    ("", 1, "boom", "boom"),
])
def test_claude_errors_become_ai_errors(fake_cli, monkeypatch, stdout, code, stderr, match):
    install, _ = fake_cli
    install("claude", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_STDOUT", stdout)
    monkeypatch.setenv("FAKE_CODE", str(code))
    monkeypatch.setenv("FAKE_STDERR", stderr)
    with pytest.raises(AIError, match=match):
        ai.Claude().run_json("hi", SCHEMA)


@posix_only
def test_claude_status_signed_in(fake_cli):
    install, calls = fake_cli
    exe = install("claude", FAKE_CLAUDE)
    s = ai.Claude().status()
    assert s["installed"] is True and s["loggedIn"] is True
    assert s["path"] == str(exe)
    assert "me@example.com" in s["detail"]
    assert calls()[0]["argv"][:2] == ["auth", "status"]


@posix_only
def test_claude_status_signed_out(fake_cli, monkeypatch):
    install, _ = fake_cli
    install("claude", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_STATUS", '{"loggedIn": false}')
    monkeypatch.setenv("FAKE_STATUS_CODE", "1")
    s = ai.Claude().status()
    assert s["installed"] is True and s["loggedIn"] is False


@posix_only
def test_claude_status_unreadable_output_means_signed_out(fake_cli, monkeypatch):
    install, _ = fake_cli
    install("claude", FAKE_CLAUDE)
    monkeypatch.setenv("FAKE_STATUS", "error: unknown command 'auth'")
    s = ai.Claude().status()
    assert s["loggedIn"] is False and s["detail"]


@posix_only
def test_not_installed(fake_cli):
    for p in (ai.Claude(), ai.Codex()):
        s = p.status()
        assert s["installed"] is False and s["loggedIn"] is False and s["detail"] == "Not installed"
        assert s["installCommand"]
        with pytest.raises(AIError):
            p.run_json("hi", SCHEMA)


# --- Codex ---------------------------------------------------------------------------------------------

@posix_only
def test_codex_run_json_command_and_parsing(fake_cli, monkeypatch):
    install, calls = fake_cli
    exe = install("codex", FAKE_CODEX)
    monkeypatch.setenv("FAKE_ANSWER", json.dumps(ANSWER))
    p = ai.Codex()
    assert p.find() == str(exe)
    prompt = "Draft prep.\n<postings>[]</postings>"
    assert p.run_json(prompt, SCHEMA, web=True) == ANSWER

    (call,) = calls()
    argv = call["argv"]
    assert argv[0] == "exec"
    assert "--output-schema" in argv and call["schema"] == SCHEMA
    assert "--output-last-message" in argv
    assert argv[-1] == "-" and call["stdin"] == prompt
    assert prompt not in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv and "--full-auto" not in argv
    assert any(a.startswith("web_search=") and "live" in a for a in argv)
    assert "features.shell_tool=false" in argv and "features.unified_exec=false" in argv
    assert not Path(call["cwd"]).exists(), "Codex runs in a throwaway temp folder"


@posix_only
def test_codex_without_web_disables_search(fake_cli, monkeypatch):
    install, calls = fake_cli
    install("codex", FAKE_CODEX)
    monkeypatch.setenv("FAKE_ANSWER", json.dumps(ANSWER))
    ai.Codex().run_json("hi", SCHEMA, web=False)
    argv = calls()[0]["argv"]
    assert any(a.startswith("web_search=") and "disabled" in a for a in argv)


@posix_only
def test_codex_failure_without_answer_raises(fake_cli, monkeypatch):
    install, _ = fake_cli
    install("codex", FAKE_CODEX)
    monkeypatch.setenv("FAKE_CODE", "1")
    monkeypatch.setenv("FAKE_STDERR", "You are not logged in")
    with pytest.raises(AIError, match="not logged in"):
        ai.Codex().run_json("hi", SCHEMA)


@posix_only
def test_codex_answer_with_chatter_is_parsed(fake_cli, monkeypatch):
    install, _ = fake_cli
    install("codex", FAKE_CODEX)
    monkeypatch.setenv("FAKE_ANSWER", "Here you go:\n" + json.dumps(ANSWER))
    assert ai.Codex().run_json("hi", SCHEMA) == ANSWER


@posix_only
def test_codex_status(fake_cli, monkeypatch):
    install, calls = fake_cli
    install("codex", FAKE_CODEX)
    s = ai.Codex().status()
    assert s["installed"] is True and s["loggedIn"] is True
    assert calls()[0]["argv"][:2] == ["login", "status"]
    monkeypatch.setenv("FAKE_STATUS", "Not logged in")
    monkeypatch.setenv("FAKE_STATUS_CODE", "1")
    assert ai.Codex().status()["loggedIn"] is False


# --- registry / fakes ------------------------------------------------------------------------------------

def test_tests_only_ever_see_fake_providers():
    assert os.environ.get("SEARCHSHIP_FAKE_AI") == "1"
    assert all(isinstance(p, ai.Fake) for p in ai.PROVIDERS.values())
    assert set(ai.PROVIDERS) == {"claude", "codex"}


def test_get_unknown_provider_raises():
    with pytest.raises(AIError):
        ai.get("gemini")
    with pytest.raises(AIError):
        ai.get(None)


def test_login_commands_use_the_official_clis():
    assert ai.Claude.login_args[:2] == ["auth", "login"]
    assert ai.Codex.login_args == ["login"]


def test_npm_shim_command_survives_a_space_in_the_path(monkeypatch):
    monkeypatch.setattr(ai, "IS_WINDOWS", True)
    monkeypatch.setattr(ai, "CMD_EXE", r"C:\Windows\System32\cmd.exe")
    line = ai._argv(r"C:\Users\Jane Doe\AppData\Roaming\npm\codex.cmd", ["exec", "-c", 'web_search="live"', "-"])
    assert line == (r'C:\Windows\System32\cmd.exe /d /s /c "'
                    r'"C:\Users\Jane Doe\AppData\Roaming\npm\codex.cmd" exec -c web_search=\"live\" -"')
    assert ai._argv(r"C:\Tools\claude.exe", ["-p"]) == [r"C:\Tools\claude.exe", "-p"]
