"""Fast mounted-Svelte tests. API captures are UI fixtures, not execution evidence."""
from copy import deepcopy
import json
import os
import re
from pathlib import Path
import signal
import socket
import subprocess
import time
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
BACKEND = json.loads((ROOT / "examples/interface-contracts/backend-v1.json").read_text())["examples"]
ANALYSIS = json.loads((ROOT / "examples/interface-contracts/analysis-v1.json").read_text())["examples"]
SAVED = json.loads((ROOT / "examples/interface-contracts/read-only-v1.json").read_text())
SECRET = "ui-test-only-" + "x" * 40
BROWSER = os.environ.get("TSP_UI_BROWSER", "chromium")


def captured(name):
    return deepcopy(BACKEND[name]["value"])


@pytest.fixture(scope="session")
def ui_server(tmp_path_factory):
    # Never take over an owner service or require the fixed native-test ports.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log_path = tmp_path_factory.mktemp("ui-vite") / "vite.log"
    with log_path.open("w+") as log:
        process = subprocess.Popen(["pnpm", "exec", "vite", "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
                                   cwd=FRONTEND, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, log_path.read_text()
                try:
                    with urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                assert time.monotonic() < deadline, log_path.read_text()
                time.sleep(.05)
            yield url
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        options = {"headless": True}
        if BROWSER == "chromium":
            options["args"] = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        instance = getattr(playwright, BROWSER).launch(**options)
        yield instance
        instance.close()


class MockApi:
    def __init__(self, page):
        self.page = page
        self.calls = []
        self.fail_next = {}
        self.holds = {}
        self.catalog_revision = "catalog-0"
        self.grant = 0
        self.jobs = [captured("completed")["job"]]
        self.preflight = captured("preflight")
        self.page_size = 100
        page.route("**/api/**", self.handle)

    @property
    def job(self):
        return self.jobs[0]

    @property
    def attempt(self):
        return self.job["attempt_ids"][0]

    def candidates(self):
        return [{"artifact_handle": f"{kind}-{self.catalog_revision}", "display_name": name, "kind": kind,
                 "revision": self.catalog_revision, "validation": "unvalidated"}
                for kind, name in [("json", "square.json"), ("result_shard", "runs.jsonl")]]

    def hold(self, path):
        self.holds[path] = []

    def release(self, path):
        routes = self.holds.pop(path)
        for route in routes:
            self.reply(route)

    def handle(self, route):
        path = route.request.url.split("/api", 1)[1]
        body = route.request.post_data_json or {}
        self.calls.append((path, body))
        if path in self.holds:
            self.holds[path].append(route)
        else:
            self.reply(route)

    def reply(self, route):
        path = route.request.url.split("/api", 1)[1]
        body = route.request.post_data_json or {}
        request_id = route.request.headers["x-request-id"]
        code = self.fail_next.pop(path, None)
        if code:
            route.fulfill(status=403 if code == "unauthorized" else 409, json={"api_version": 1, "request_id": request_id,
                "code": code, "message": f"Injected {code}", "retryable": False})
            return
        if path == "/session":
            self.grant += 1
            value = {"session_token": "fixture-token", "session_id": f"fixture-session-{self.grant}", "token_type": "Bearer"}
        elif path == "/workspace":
            value = {"workspace": {"workspace_id": captured("execution")["workspace_id"], "display_name": "UI fixtures", "access": "read_only"}}
        elif path == "/artifacts/query":
            value = {"revision": self.catalog_revision, "items": self.candidates(), "next_cursor": None}
        elif path == "/artifacts/refresh":
            self.catalog_revision = f"catalog-{len(self.calls)}"
            value = {"revision": self.catalog_revision}
        elif path == "/runs/capabilities":
            value = captured("capabilities")
        elif path in ("/instances/generate", "/instances/load", "/instances/snapshot"):
            if path == "/instances/load":
                assert body["artifact_revision"] == self.catalog_revision
                assert body["artifact_handle"] in [item["artifact_handle"] for item in self.candidates()]
            value = captured("snapshot")
        elif path == "/runs/preflight":
            self.preflight = captured("preflight")
            self.preflight["configuration"] = body["configuration"]
            self.preflight["trial_count"] = len(body["configuration"]["repetitions"]) * len(body["configuration"]["distance_modes"])
            value = deepcopy(self.preflight)
        elif path == "/jobs/query":
            value = captured("jobs")
            value["items"] = deepcopy(self.jobs)
        elif path == "/queue/status":
            value = captured("queue")
        elif path == "/jobs/review":
            value = captured("accepted_review")
            value["job"] = deepcopy(next(job for job in self.jobs if job["job_id"] == body["job_id"]))
        elif path == "/jobs/submit":
            value = captured("submitted")
            value["job"]["submission_key"] = body["submission_key"]
            value["job"]["preflight_id"] = body["preflight_id"]
        elif path == "/jobs/control":
            value = {"job": deepcopy(self.job)}
            value["job"]["state"] = "stop_requested"
        elif path == "/jobs/analysis":
            query = body["query"]
            source = deepcopy(ANALYSIS[query["table"]]["value"])
            value = captured("result")
            del value["details"]
            value.update({key: item for key, item in source.items() if key not in ("api_version", "request_id", "workspace_id", "dataset_id", "revision")})
            for index, row in enumerate(value["items"]):
                if "attempt_id" in row:
                    row["attempt_id"] = self.attempt if index == 0 else f"ui-attempt-{index}"
            offset = int(query["cursor"] or "0")
            rows = value["items"]
            value["items"] = rows[offset:offset + self.page_size]
            value["next_cursor"] = str(offset + self.page_size) if offset + self.page_size < len(rows) else None
            value["total_rows"] = len(rows)
        elif path in ("/jobs/result", "/jobs/geometry"):
            value = captured("result" if path.endswith("result") else "geometry")
            payload = value["details"] if "details" in value else value["geometry"]
            payload["attempt_id"] = body["attempt_id"]
            if "row" in payload:
                payload["row"]["attempt_id"] = body["attempt_id"]
        elif path == "/datasets":
            value = deepcopy(SAVED["dataset"])
        elif path == "/trials/query":
            value = deepcopy(SAVED["page"])
        else:
            raise AssertionError(f"Unmocked UI request: {path}")
        value.update({key: body[key] for key in ("workspace_id", "session_id", "dataset_id", "revision") if key in body})
        value.update(api_version=1, request_id=request_id)
        route.fulfill(json=value)

    def connect(self):
        self.page.get_by_label("Pairing secret", exact=True).fill(SECRET)
        self.page.get_by_role("button", name="Connect", exact=True).click()
        expect(self.page.get_by_role("heading", name="1. Create or load an instance")).to_be_visible()

    def select_job(self):
        self.page.get_by_role("button", name=re.compile(rf"^Select job .*{self.job['job_id']}")).click()
        expect(self.page.get_by_role("region", name="Experiment analysis", exact=True)).to_be_visible()
        expect(self.page.get_by_role("button", name=re.compile(rf"^Inspect attempt .*{self.attempt}"))).to_be_visible()

    def prepare_review(self):
        self.page.get_by_role("button", name="Generate instance", exact=True).click()
        expect(self.page.get_by_text("Instance ready: 4 cities", exact=False)).to_be_visible()
        self.page.get_by_label("Ordered start cities").fill("0,1")
        self.page.get_by_label("Repetitions", exact=True).fill("2")
        self.page.get_by_role("button", name="Review experiment", exact=True).click()
        expect(self.page.get_by_role("button", name="Run reviewed experiment", exact=True)).to_be_enabled()


@pytest.fixture
def ui(browser, ui_server):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    api = MockApi(page)
    page.goto(ui_server)
    yield page, api
    context.close()
    assert not errors, errors
