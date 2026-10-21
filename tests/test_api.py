"""api.Api (what the window's JavaScript calls) and the browser dev server."""
import http.client
import inspect
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from helpers import row
from tracker import api as api_mod
from tracker import devserver
from tracker.api import Api
from tracker.paths import resource


class StubJobs:
    def __init__(self):
        self.scans = 0
        self.drafts = []

    def scan(self):
        self.scans += 1
        return True

    def draft(self, ids):
        self.drafts.append(ids)
        return True

    def status(self):
        return {"running": False, "kind": None, "log": [], "error": None, "result": None}

    def scan_if_due(self):
        return False


@pytest.fixture
def api(store):
    store.save_postings({"a": row("Acme"), "greenhouse:stripe:1": row("Stripe", source="Greenhouse")})
    return Api(store, StubJobs())


def public_methods(obj):
    return sorted(n for n in dir(obj) if not n.startswith("_") and callable(getattr(obj, n)))


# --- Api ---------------------------------------------------------------------------------------

def test_bootstrap(api, store):
    b = api.bootstrap()
    assert b["settings"] == store.load_settings()
    assert b["postingCount"] == 2 and b["hasResume"] is False
    assert b["dataDir"] == str(store.root)
    json.dumps(b)


def test_postings(api):
    p = api.postings()
    assert set(p["postings"]) == {"a", "greenhouse:stripe:1"} and "state" in p


@pytest.mark.parametrize("call", [
    lambda a: a.act("missing", "approve"),
    lambda a: a.act("a", "delete"),
    lambda a: a.act("a"),
    lambda a: a.import_postings("{not json"),
    lambda a: a.import_postings("[]"),
    lambda a: a.import_postings('{"x": {"foo": 1}}'),
    lambda a: a.import_postings(None),
    lambda a: a.provider_status("gemini"),
    lambda a: a.install_provider("gemini"),
    lambda a: a.login_provider(None),
    lambda a: a.choose_provider("gemini"),
    lambda a: a.open_url("javascript:alert(1)"),
    lambda a: a.bootstrap("unexpected", "args"),
])
def test_failures_come_back_as_error_objects(api, call):
    result = call(api)
    assert isinstance(result, dict) and set(result) == {"error"}
    assert isinstance(result["error"], str) and result["error"]


def test_no_public_method_raises_on_garbage_arguments(api, monkeypatch):
    monkeypatch.setattr(api_mod.webbrowser, "open", lambda *a, **k: True)
    monkeypatch.setattr(api_mod.subprocess, "Popen", lambda *a, **k: None)
    for name in public_methods(api):
        fn = getattr(api, name)
        n = len(inspect.signature(fn).parameters)
        for args in ([object()] * n, [None] * n, ["x"] * (n + 3)):
            result = fn(*args)
            assert isinstance(result, dict), name


def test_act_updates_the_row(api, store):
    r = api.act("greenhouse:stripe:1", "note", "Referral: Sam")
    assert r["row"]["notes"] == "Referral: Sam"
    assert store.load_postings()["greenhouse:stripe:1"]["notes"] == "Referral: Sam"
    assert api.act("a", "applied")["row"]["appliedOn"]


def test_choose_provider(api, store):
    assert api.choose_provider("claude")["settings"]["provider"] == "claude"
    assert api.choose_provider(None)["settings"]["provider"] is None
    assert "error" in api.choose_provider("gemini")
    assert store.load_settings()["provider"] is None


def test_providers_report_fake_status(api):
    p = api.providers()
    assert set(p) == {"claude", "codex"}
    assert p["claude"]["loggedIn"] is True
    assert p["codex"]["installed"] is False


def test_ai_ready(api):
    assert api.ai_ready() == {"ready": False, "label": None}
    api.choose_provider("claude")
    assert api.ai_ready() == {"ready": True, "label": "Claude"}


def test_save_settings_only_allows_user_settings(api, store):
    s = api.save_settings({"boards": "greenhouse:stripe = Stripe\n\nlever:plaid\n", "autoScan": False,
                           "onboarded": True, "lastWebSearch": "2030-01-01T00:00:00+00:00",
                           "sources": {"websearch": False}})["settings"]
    assert s["boards"] == ["greenhouse:stripe = Stripe", "lever:plaid"]
    assert s["autoScan"] is False and s["sources"]["websearch"] is False
    assert s["onboarded"] is False and s["lastWebSearch"] is None
    assert store.load_settings() == s


def test_resume_skip_list_and_import(api, store):
    assert api.save_resume("# Me\nPython") == {"ok": True}
    assert store.resume_text() == "# Me\nPython\n"
    assert api.bootstrap()["hasResume"] is True
    assert api.save_skip_companies("Acme\nBeta") == {"ok": True}
    assert api.settings_bundle()["skipCompanies"] == "Acme\nBeta\n"
    result = api.import_postings(json.dumps({"new-1": row("New Co"), "a": row("Changed")}))
    assert result == {"added": 1, "replaced": 0, "kept": 1, "total": 3}
    assert store.load_postings()["a"]["company"] == "Acme"


def test_finish_onboarding_and_work_calls(api, store):
    r = api.finish_onboarding()
    assert r["settings"]["onboarded"] is True and r["scanStarted"] is False
    assert api.scan() == {"started": True}
    assert api.draft("a") == {"started": True}
    assert api.draft(["a", "greenhouse:stripe:1"]) == {"started": True}
    assert api._jobs.drafts == [["a"], ["a", "greenhouse:stripe:1"]]
    assert api.job()["running"] is False


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/x", "",
                                 None, "  https://example.com", "data:text/html,hi", "about:blank",
                                 "C:\\Windows\\System32\\calc.exe", "ms-settings:"])
def test_open_url_rejects_non_web_links(api, monkeypatch, url):
    opened = []
    monkeypatch.setattr(api_mod.webbrowser, "open", lambda u, *a, **k: opened.append(u))
    assert "error" in api.open_url(url)
    assert opened == []


@pytest.mark.parametrize("url", ["https://boards.greenhouse.io/stripe/jobs/1", "http://example.com/a?b=c"])
def test_open_url_opens_web_links(api, monkeypatch, url):
    opened = []
    monkeypatch.setattr(api_mod.webbrowser, "open", lambda u, *a, **k: opened.append(u))
    assert api.open_url(url) == {"ok": True}
    assert opened == [url]


def test_open_data_folder(api, store, monkeypatch):
    launched = []
    monkeypatch.setattr(api_mod.subprocess, "Popen", lambda argv, *a, **k: launched.append(argv))
    monkeypatch.setattr(api_mod.os, "startfile", lambda p: launched.append(p), raising=False)
    assert api.open_data_folder() == {"ok": True}
    assert any(str(store.root) in str(x) for x in launched)


# --- devserver ------------------------------------------------------------------------------

@pytest.fixture
def server(api):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), devserver.make_handler(api))
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode("utf-8")
    finally:
        conn.close()


def call(srv, name, args=(), headers=None):
    h = {"X-SearchShip": "1", "Content-Type": "application/json"}
    h.update(headers or {})
    return request(srv, "POST", f"/api/{name}", json.dumps(list(args)).encode(), h)


def test_devserver_rejects_requests_without_the_header(server, store):
    status, _, body = request(server, "POST", "/api/act", json.dumps(["a", "skip"]).encode(),
                              {"Content-Type": "application/json"})
    assert status == 403 and json.loads(body) == {"error": "forbidden"}
    status, _, _ = request(server, "POST", "/api/act", json.dumps(["a", "skip"]).encode(), {"X-SearchShip": "0"})
    assert status == 403
    assert store.load_postings()["a"]["status"] == "new"


@pytest.mark.parametrize("name", ["nope", "_store", "_jobs", "__init__", "__class__", "_safe", "",
                                  "act/../bootstrap", "bootstrap?x=1"])
def test_devserver_rejects_unknown_and_private_methods(server, name):
    status, _, body = call(server, name)
    assert status == 403


def test_devserver_rejects_bad_bodies(server):
    h = {"X-SearchShip": "1"}
    assert request(server, "POST", "/api/bootstrap", b"{not json", h)[0] == 400
    assert request(server, "POST", "/api/bootstrap", b'{"a": 1}', h)[0] == 400


def test_devserver_calls_methods_with_json_args(server, store):
    status, headers, body = call(server, "act", ["greenhouse:stripe:1", "note", "Referral: Sam \u2713"])
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert headers.get("Cache-Control") == "no-store"
    assert json.loads(body)["row"]["notes"] == "Referral: Sam \u2713"
    assert store.load_postings()["greenhouse:stripe:1"]["notes"] == "Referral: Sam \u2713"

    status, _, body = call(server, "bootstrap")
    assert status == 200 and json.loads(body)["postingCount"] == 2

    status, _, body = request(server, "POST", "/api/postings", None, {"X-SearchShip": "1"})
    assert status == 200 and "a" in json.loads(body)["postings"]


def test_devserver_returns_api_errors_as_json(server):
    status, _, body = call(server, "act", ["missing", "approve"])
    assert status == 200 and "error" in json.loads(body)
    status, _, body = call(server, "act", [])
    assert status == 200 and "error" in json.loads(body)


def test_devserver_get_unknown_path_is_404(server):
    assert request(server, "GET", "/api/bootstrap")[0] == 404
    assert request(server, "GET", "/../../etc/passwd")[0] == 404


def test_devserver_serves_index_with_bridge(server):
    index = resource("tracker", "ui", "index.html")
    if not index.exists():
        pytest.skip("tracker/ui/index.html does not exist yet")
    raw = index.read_text(encoding="utf-8")
    assert devserver.MARKER in raw, "index.html needs the <!--SEARCHSHIP_BRIDGE--> marker for browser mode"
    for path in ("/", "/index.html", "/?x=1"):
        status, headers, body = request(server, "GET", path)
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert "<script>window.SEARCHSHIP_HTTP_API = true;</script>" in body
        assert devserver.MARKER not in body


def test_devserver_rejects_foreign_host_header(server, store):
    port = server.server_address[1]
    status, _, _ = call(server, "settings_bundle", headers={"Host": f"attacker.example:{port}"})
    assert status == 403


def test_saving_an_empty_resume_clears_it(api, store):
    assert api.save_resume("# Me\nPython") == {"ok": True}
    assert api.save_resume("   \n") == {"ok": True}
    assert store.resume_text() == ""
    assert api.bootstrap()["hasResume"] is False


def test_save_settings_reports_bad_board_lines(api):
    out = api.save_settings({"boards": "greenhouse:stripe = Stripe\nnot a board\nmyspace:x"})
    assert out["settings"]["boards"] == ["greenhouse:stripe = Stripe", "not a board", "myspace:x"]
    assert len(out["boardErrors"]) == 2
