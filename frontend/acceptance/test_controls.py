"""Controls operate on actual owned workers, not simulated browser progress."""
import json
import os
import signal
import time

from playwright.sync_api import sync_playwright, expect
from tsp_hpc.interface.processes import recover_pidfd, exited
from frontend.acceptance.test_workflow import servers


def test_queued_cancel_graceful_intent_and_confirmed_immediate_stop(tmp_path):
    with servers(tmp_path) as (secret, _, output), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(); page.set_default_timeout(30000)
        page.goto("http://127.0.0.1:5173")
        page.get_by_label("Pairing secret", exact=True).fill(secret)
        page.get_by_role("button", name="Connect", exact=True).click()
        page.get_by_label("Cities", exact=True).fill("4096")
        page.get_by_role("button", name="Generate instance", exact=True).click()
        expect(page.get_by_text("Instance ready: 4096 cities", exact=False)).to_be_visible()
        page.get_by_label("Ordered start cities").fill(",".join(str(i) for i in range(128)))
        page.get_by_label("Repetitions", exact=True).fill("1")
        page.get_by_label("Dense matrix", exact=True).uncheck()
        page.get_by_role("button", name="Review experiment", exact=True).click()
        page.get_by_role("button", name="Run reviewed experiment", exact=True).click()
        fd = None; deadline = time.monotonic() + 20
        try:
            while fd is None:
                queue = json.loads((output / ".tsp-local/queue.json").read_bytes())
                if queue["jobs"]:
                    job = queue["jobs"][0]
                    if job["worker"]:
                        fd = recover_pidfd(job["worker"], job["job_id"])
                assert time.monotonic() < deadline, "No owned worker observed"
                if fd is None: time.sleep(.02)
            signal.pidfd_send_signal(fd, signal.SIGSTOP)
            first = job["job_id"]
            # Queue another campaign behind the frozen owned worker.
            page.get_by_role("button", name="Review experiment", exact=True).click()
            page.get_by_role("button", name="Run reviewed experiment", exact=True).click()
            page.get_by_role("button", name="Cancel queued job", exact=True).click()
            expect(page.get_by_role("heading", name="Selected job: cancelled")).to_be_visible()
            page.get_by_role("button", name=first, exact=True).click()
            page.get_by_role("button", name="Stop after trial", exact=True).click()
            expect(page.get_by_role("heading", name="Selected job: stop_requested")).to_be_visible()
            assert not exited(fd)
            expect(page.get_by_role("button", name="Stop now", exact=True)).to_be_disabled()
            page.get_by_label("Confirm immediate interruption").check()
            page.get_by_role("button", name="Stop now", exact=True).click()
            expect(page.get_by_role("heading", name="Selected job: cancelled")).to_be_visible()
            assert exited(fd)
            queue = json.loads((output / ".tsp-local/queue.json").read_bytes())
            assert len(queue["jobs"]) == 2
            assert all(j["state"] == "cancelled" for j in queue["jobs"])
            pending = next(j for j in queue["jobs"] if j["job_id"] != first)
            assert pending["worker"] is None and pending["recorded_trials"] == 0
            assert not (output / ".tsp-local/jobs" / pending["job_id"] / "results.jsonl").exists()
        finally:
            if fd is not None:
                if not exited(fd): signal.pidfd_send_signal(fd, signal.SIGCONT)
                os.close(fd)
            browser.close()
