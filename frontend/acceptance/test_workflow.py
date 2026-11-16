"""Real browser → guarded proxy → backend → native worker acceptance.

Run explicitly: uv run --locked --extra interface pytest frontend/acceptance -q -W error
Requires pnpm install and python -m playwright install chromium.
"""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def servers(tmp_path, *, enabled=True, control=None):
    for port in (8000, 5173):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                pytest.fail(f"Acceptance requires free local port {port}; no existing process was stopped")
    workspace, output = tmp_path / "inputs", tmp_path / "outputs"
    workspace.mkdir(); output.mkdir()
    shutil.copy(ROOT / "examples/instances/square.json", workspace / "square.json")
    secret = secrets.token_urlsafe(32)
    command = [sys.executable, "-m", "tsp_hpc", "serve", "--workspace", str(workspace), "--session-secret-stdin"]
    if enabled:
        command += ["--enable-execution", "--execution-root", str(output)]
    env = {**os.environ, "NUMBA_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    with (tmp_path / "server.log").open("w") as log:
        backend = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
        frontend = subprocess.Popen([shutil.which("pnpm"), "--dir", "frontend", "run", "dev"], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        try:
            backend.stdin.write(secret + "\n"); backend.stdin.close()
            for port in (8000, 5173):
                deadline = time.monotonic() + 30
                while True:
                    try:
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                        connection.request("GET", "/")
                        connection.getresponse().read(); connection.close(); break
                    except OSError:
                        if time.monotonic() > deadline or backend.poll() is not None or frontend.poll() is not None:
                            pytest.fail("Local app failed to start: " + (tmp_path / "server.log").read_text())
                        time.sleep(.1)
            def restart():
                nonlocal backend, secret
                backend.terminate(); backend.wait(timeout=15)
                secret = secrets.token_urlsafe(32)
                backend = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
                backend.stdin.write(secret + "\n"); backend.stdin.close()
                deadline = time.monotonic() + 20
                while True:
                    try:
                        connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=1)
                        connection.request("GET", "/"); connection.getresponse().read(); connection.close(); break
                    except OSError:
                        assert backend.poll() is None and time.monotonic() < deadline
                        time.sleep(.1)
                return secret
            if control is not None:
                control["restart"] = restart
            yield secret, workspace, output
        finally:
            for process in (backend, frontend):
                if process is frontend:
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: process.kill(); process.wait()


def test_generate_compare_inspect_history_and_fresh_rerun(tmp_path):
    control = {}
    with servers(tmp_path, control=control) as (secret, workspace, output), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_default_timeout(30000)
        page.goto("http://127.0.0.1:5173")
        page.get_by_label("Pairing secret", exact=True).fill(secret)
        page.get_by_role("button", name="Connect", exact=True).click()
        expect(page.get_by_role("heading", name="1. Create or load an instance")).to_be_visible()
        page.get_by_label("Cities", exact=True).fill("32")
        page.get_by_label("Instance seed (uint64)").fill("18446744073709551615")
        page.get_by_role("button", name="Generate instance", exact=True).click()
        expect(page.get_by_text("Instance ready: 32 cities", exact=False)).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("button", name="Download canonical instance").click()
        artifact = json.loads(Path(download.value.path()).read_bytes())
        assert artifact["instance"]["instance_seed"] == 2**64 - 1
        page.get_by_label("Repetitions", exact=True).fill("2")
        page.get_by_label("Record best-so-far history").check()
        page.get_by_label("Sampling interval (seconds)").fill("0")
        page.get_by_role("button", name="Review experiment", exact=True).click()
        expect(page.get_by_text("4 trials ·", exact=False)).to_be_visible()
        submissions = []
        def lose_acknowledgement(route):
            submissions.append(route.request.post_data_json)
            route.fetch()  # Admission completes, but the browser loses the acknowledgement.
            route.abort()
        page.route("**/api/jobs/submit", lose_acknowledgement, times=1)
        page.get_by_role("button", name="Run reviewed experiment", exact=True).click()
        expect(page.get_by_role("heading", name="Submission acknowledgement uncertain")).to_be_visible()
        page.get_by_role("button", name="Retry original submission").click()
        expect(page.get_by_role("heading", name="Selected job: completed")).to_be_visible(timeout=90000)
        expect(page.get_by_role("heading", name="4. Analyze results")).to_be_visible()
        assert len(submissions) == 1
        idle_requests = []
        page.on('request', lambda request: idle_requests.append(request.url) if '/api/' in request.url else None)
        expect(page.get_by_text('trials: 4 rows', exact=False)).to_be_visible()
        count = len(idle_requests); page.wait_for_timeout(2000)
        assert len(idle_requests) == count, 'completed experiments must not generate background API requests'
        expect(page.get_by_text("trials: 4 rows", exact=False)).to_be_visible()
        rows = list((output / ".tsp-local/jobs").glob("*/results.jsonl"))
        # Check actual native records independently of what the page displays.
        assert len(rows) == 1
        records = [json.loads(line) for line in rows[0].read_text().splitlines()]
        trials = [r for r in records if r["record_type"] == "trial"]
        assert len(trials) == 4 and all(r["native_execution"]["verified"] for r in trials)
        assert {r["effective_execution"]["distance_mode"] for r in trials} == {"dense", "on_demand"}
        assert len({r["result"]["best_cost"] for r in trials}) == 1
        attempt = trials[0]["attempt_id"]
        attempt_button = page.get_by_role('button', name=f'Inspect attempt {attempt[:8]}…{attempt[-6:]} ({attempt})', exact=True)
        expect(attempt_button).to_have_text(f'{attempt[:8]}…{attempt[-6:]}')
        time_cell = attempt_button.locator('xpath=ancestor::tr').locator('td[title^="search_time_s:"]')
        raw_seconds = time_cell.get_attribute('title').split(': ', 1)[1].removesuffix(' s')
        assert time_cell.inner_text().endswith((' ms', ' s', ' min', ' h'))
        page.get_by_label('Full-precision values').check()
        page.get_by_label('All columns', exact=True).check()
        expect(attempt_button).to_have_text(attempt)
        expect(time_cell).to_have_text(raw_seconds)
        page.get_by_label('Full-precision values').uncheck()
        page.get_by_label('All columns', exact=True).uncheck()
        # Exercise both scroll axes; headers must stay pinned and column-aligned.
        region = page.locator('[aria-label="Experiment analysis"] .table-region')
        region.evaluate("e => { e.style.height = '120px'; e.style.width = '480px'; e.querySelector('table').style.minWidth = '960px'; }")
        header = region.locator('thead')
        top = header.bounding_box()['y']
        region.evaluate('e => { e.scrollTop = e.scrollHeight; e.scrollLeft = 200; }')
        page.wait_for_timeout(100)
        assert region.evaluate('e => e.scrollTop > 0 && e.scrollLeft > 0')
        assert abs(header.bounding_box()['y'] - top) <= 1
        assert abs(region.locator('th').first.bounding_box()['x'] - region.locator('tbody td').first.bounding_box()['x']) <= 1
        region.evaluate("e => { e.style.height = ''; e.style.width = ''; e.querySelector('table').style.minWidth = ''; e.scrollTop = 0; e.scrollLeft = 0; }")
        attempt_button.click()
        expect(page.get_by_role("heading", name="Verified final result")).to_be_visible()
        expect(page.get_by_label("Show tour")).to_be_checked()
        expect(page.get_by_role("region", name="Instance inspection")).to_be_visible()
        expect(page.get_by_role('heading', name='Verified final result')).to_be_in_viewport()
        # Deliberately hold the next result request to observe the loading layout.
        held = []
        page.route('**/api/jobs/result', lambda route: held.append(route), times=1)
        next_attempt = trials[1]['attempt_id']
        next_button = page.get_by_role('button', name=f'Inspect attempt {next_attempt[:8]}…{next_attempt[-6:]} ({next_attempt})', exact=True)
        next_button.scroll_into_view_if_needed()
        before = page.evaluate('({y: scrollY, height: document.documentElement.scrollHeight})')
        next_button.click()
        expect(page.locator('.result-slot')).to_have_attribute('aria-busy', 'true')
        page.wait_for_timeout(100)
        during = page.evaluate('({y: scrollY, height: document.documentElement.scrollHeight})')
        assert during['height'] >= before['height'], 'loading must not collapse the old viewer space'
        assert abs(during['y'] - before['y']) <= 2, 'loading must not jump the document upward'
        assert len(held) == 1
        held[0].continue_()
        expect(page.get_by_text(f'Attempt: {next_attempt}', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='Verified final result')).to_be_in_viewport()
        expect(page.get_by_role('region', name='Instance inspection')).to_be_in_viewport(ratio=0.5)
        page.get_by_label("Analysis table").select_option("comparisons")
        with page.expect_response("**/api/jobs/analysis") as comparison:
            page.get_by_role("button", name="Show analysis", exact=True).click()
        expect(page.get_by_text("comparisons: 2 rows", exact=False)).to_be_visible()
        pairs = comparison.value.json()["items"]
        if all(r["runtime"]["source"]["dirty"] is False for r in trials):
            assert all(p["speedup"] > 0 and p["reason"] is None for p in pairs)
            expect(page.get_by_role("img", name="Server-calculated paired time ratios on this page")).to_be_visible()
        else:
            assert all(p["speedup"] is None and p["reason"] == "unknown_or_dirty_source" for p in pairs)
        # Dirty development source legitimately blocks ratios; never invent a ratio.
        page.get_by_label("Analysis table").select_option("progress")
        page.get_by_role("button", name="Show analysis", exact=True).click()
        expect(page.get_by_role("img", name="Recorded cost observations on this page, without interpolation")).to_be_visible()
        page.get_by_role("button", name="Review fresh rerun").click()
        page.get_by_role("button", name="Run reviewed experiment", exact=True).click()
        expect(page.get_by_role("heading", name="Selected job: completed")).to_be_visible(timeout=90000)
        # Wait for the second result publication, not the previous completed heading.
        expect(page.get_by_text("trials: 4 rows", exact=False)).to_be_visible()
        assert len(list((output / ".tsp-local/jobs").iterdir())) == 2
        assert [json.loads(l) for l in rows[0].read_text().splitlines()] == records
        page.get_by_role("link", name="Saved results", exact=True).click()
        expect(page.get_by_text("Workspace: inputs", exact=True)).to_be_visible()
        assert not page.get_by_role("button", name="Pair with local backend").is_visible()
        # Restart/reload loses credentials, not jobs. Dispatch must stay held until explicit continuation.
        secret = control["restart"]()
        page.reload()
        expect(page.get_by_role('heading', name='Local experiment results')).to_be_visible()
        page.get_by_role('link', name='Experiments', exact=True).click()
        page.get_by_label("Pairing secret", exact=True).fill(secret)
        page.get_by_role("button", name="Connect", exact=True).click()
        expect(page.get_by_role("button", name="Continue held queue")).to_be_visible()
        assert json.loads((output / ".tsp-local/queue.json").read_bytes())["held"] is True
        page.get_by_role("button", name=f'Select job {rows[0].parent.name[:8]}…{rows[0].parent.name[-6:]} ({rows[0].parent.name})', exact=True).click()
        expect(page.get_by_text("trials: 4 rows", exact=False)).to_be_visible()
        page.get_by_role("button", name="Review fresh rerun").click()
        page.get_by_role("button", name="Run reviewed experiment", exact=True).click()
        expect(page.get_by_role("heading", name="Selected job: held")).to_be_visible()
        page.get_by_role("button", name="Continue held queue").click()
        expect(page.get_by_role("heading", name="Selected job: completed")).to_be_visible(timeout=90000)
        assert len(list((output / ".tsp-local/jobs").iterdir())) == 3
        assert [json.loads(l) for l in rows[0].read_text().splitlines()] == records
        assert not errors
        assert sorted(p.name for p in workspace.iterdir()) == ["square.json"]
        browser.close()


def test_readonly_cannot_expose_execution_controls(tmp_path):
    with servers(tmp_path, enabled=False) as (secret, _, output), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(); page.goto("http://127.0.0.1:5173")
        page.get_by_label("Pairing secret", exact=True).fill(secret)
        page.get_by_role("button", name="Connect", exact=True).click()
        expect(page.get_by_text("Execution is disabled.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Generate instance")).to_have_count(0)
        assert not list(output.iterdir())
        # Tool layout fills the viewport rather than imposing a centered desktop cap.
        for width in (390, 1280, 2560, 3840):
            page.set_viewport_size({"width": width, "height": 900})
            for tab in ('Experiments', 'Saved results'):
                page.get_by_role('link', name=tab, exact=True).click()
                available = page.evaluate('document.body.clientWidth') # Excludes the reserved scrollbar gutter.
                for selector in ('main:visible', '.app-nav'):
                    bounds = page.locator(selector).bounding_box()
                    assert bounds['x'] == 0
                    assert abs(bounds['width'] - available) <= 1, (width, tab, bounds)
                assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth'), (width, tab)
        browser.close()
