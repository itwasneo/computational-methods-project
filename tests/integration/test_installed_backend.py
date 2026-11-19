"""Installed server PROCESS + native HTTP client, not TestClient or a benchmark.

Workers and server import the real wheel outside either checkout. The test owner
observes private process metadata for deterministic crash/exit checks; HTTP never
accepts a PID, command, module or arbitrary filesystem path.
"""

from contextlib import contextmanager
import base64
import ctypes
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import struct
import subprocess
from time import monotonic, sleep

import pytest

from tsp_hpc.interface.processes import exited, kill_verified

ROOT = Path(__file__).resolve().parents[2]
TERMINAL = {"completed", "cancelled", "failed", "interrupted"}


@contextmanager
def orphan_reaper():
    """Keep the genuinely surviving grandchild reapable even under a minimal PID 1."""
    libc = ctypes.CDLL(None, use_errno=True)
    old = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(old), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    try:
        yield
    finally:
        assert libc.prctl(36, old.value, 0, 0, 0) == 0


class NativeServer:
    def __init__(self, python, workspace, output, cwd, *, enabled=True, env=None):
        self.python, self.workspace, self.output, self.cwd = python, workspace, output, cwd
        self.enabled, self.env = enabled, env
        self.process, self.token = None, None
        self.transcripts, self.credentials = [], []
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            self.port = reserved.getsockname()[1]

    def http(self, path, body=None, *, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)  # Never environment proxies.
        data = raw if raw is not None else None if body is None else json.dumps(body, allow_nan=False).encode()
        values = {"Authorization": "Bearer " + self.token} if self.token else {}
        if data is not None:
            values["Content-Type"] = "application/json"
        values.update(headers or {})
        try:
            connection.request("POST" if data is not None else "GET", path, body=data, headers=values)
            response = connection.getresponse()
            content = response.read()
            assert response.getheader("cache-control") == "no-store"
            return response.status, content
        finally:
            connection.close()

    def post(self, path, **extra):
        status, content = self.http(path, {**self.context, **extra})
        assert 200 <= status < 300, (status, content)
        assert str(self.output).encode() not in content and str(self.workspace).encode() not in content
        return json.loads(content)

    def start(self):
        secret = secrets.token_urlsafe(32)
        self.credentials.append(secret)
        args = [str(self.python), "-I", "-B", "-W", "error", "-m", "tsp_hpc", "serve", "--workspace", str(self.workspace),
                "--port", str(self.port), "--session-secret-stdin"]
        if self.enabled:
            args.extend(["--enable-execution", "--execution-root", str(self.output)])
        self.token = None
        self.process = subprocess.Popen(args, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.process.stdin.write(secret + "\n")
        self.process.stdin.close(); self.process.stdin = None
        deadline = monotonic() + 30
        while monotonic() < deadline:
            try:
                status, _ = self.http("/api/workspace")
                assert status == 403
                break
            except (OSError, http.client.HTTPException):
                assert self.process.poll() is None, self.process.communicate(timeout=5)
                sleep(.025)
        else:
            raise AssertionError("installed server startup timeout")
        status, raw = self.http("/api/session", {"api_version": 1, "request_id": "native-pair", "secret": secret})
        assert status == 200, raw
        session = json.loads(raw)
        self.token = session["session_token"]
        self.credentials.append(self.token)
        _, raw = self.http("/api/workspace")
        ws = json.loads(raw)["workspace"]
        self.context = {"api_version": 1, "request_id": "native", "workspace_id": ws["workspace_id"], "session_id": session["session_id"]}
        return self

    def stop(self, *, crash=False):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGKILL if crash else signal.SIGINT)
            stdout, stderr = self.process.communicate(timeout=15)
            assert self.process.returncode == (-signal.SIGKILL if crash else 0), stderr
            self.transcripts.append(stdout + stderr)
            self.process = None

    def wait(self, identifier, predicate=None):
        deadline = monotonic() + 60
        while monotonic() < deadline:
            job = self.post("/api/jobs/status", job_id=identifier)["job"]
            if (predicate or (lambda value: value["state"] in TERMINAL))(job):
                return job
            sleep(.025)
        raise AssertionError(job)

    def generate(self, n=4):
        return self.post("/api/instances/generate", generation={"generator": "uniform-v1", "n_cities": n,
                         "seed": str(2**64 - 1), "side_length": 1.})["instance_snapshot_id"]

    def review(self, snapshot, **changes):
        config = {"algorithm": "nn", "parameters": {}, "budget": {"start_cities": [2, 0]}, "logical_search_ids": None,
                  "implementations": [None], "distance_modes": ["on_demand"], "repetitions": [0], "solver_seed": str(2**64 - 1),
                  "max_matrix_bytes": 0, "record_history": True, "history_policy": None, **changes}
        caps = self.post("/api/runs/capabilities")
        assert {entry["algorithm"] for entry in caps["implementations"]} == {"nn", "ga"}
        return self.post("/api/runs/preflight", instance_snapshot_id=snapshot, configuration=config,
                         capabilities_revision=caps["capabilities_revision"])

    def submit(self, plan, key):
        return self.post("/api/jobs/submit", preflight_id=plan["preflight_id"], submission_key=key)

    def control(self, identifier, action):
        for _ in range(10):
            job = self.post("/api/jobs/status", job_id=identifier)["job"]
            status, raw = self.http("/api/jobs/control", {**self.context, "job_id": identifier, "expected_revision": job["revision"],
                                    "action": action, "confirm_stop_now": action == "stop_now"})
            if status == 200:
                return json.loads(raw)["job"]
            assert status == 409 and json.loads(raw)["code"] == "job_stale", raw
        raise AssertionError("unstable control revision")

    def worker(self, identifier):
        deadline = monotonic() + 15
        while monotonic() < deadline:
            state = json.loads((self.output / ".tsp-local/queue.json").read_bytes())
            job = next(item for item in state["jobs"] if item["job_id"] == identifier)
            if job["worker"] is not None:
                fd = os.pidfd_open(job["worker"]["pid"], 0)
                # The venv may contain a COPY of Python, not this pytest executable.
                # Use the installed identity checker, never weaken its exe binding.
                probe = subprocess.run([str(self.python), "-I", "-c",
                    "import json, sys; from tsp_hpc.interface.processes import process_identity; "
                    "print(json.dumps(process_identity(int(sys.argv[1]), sys.argv[2])))",
                    str(job["worker"]["pid"]), identifier], cwd=self.cwd, capture_output=True, text=True, timeout=10)
                if probe.returncode == 0 and json.loads(probe.stdout) == job["worker"] and not exited(fd):
                    return fd, job["worker"]
                os.close(fd)
            sleep(.01)
        raise AssertionError("no verified owned installed worker")

    def publication(self, job):
        return {"job_id": job["job_id"], "expected_revision": job["revision"], "result_revision": job["result_revision"],
                "campaign_id": job["campaign_id"], "attempt_id": job["attempt_ids"][0], "instance_snapshot_id": job["instance_snapshot_id"]}

    def assert_private(self):
        transcript = "".join(self.transcripts)
        for forbidden in (*self.credentials, str(self.workspace), str(self.output), "Traceback"):
            assert forbidden not in transcript


def test_installed_native_workflow_disconnect_crash_recovery_and_fresh_rerun(installed, built, tmp_path):
    python, site = installed
    workspace, output, cwd = [tmp_path / name for name in ("read", "output", "outside")]
    for directory in (workspace, output, cwd):
        directory.mkdir()
    shutil.copy2(ROOT / "examples/instances/square.json", workspace / "square.json")
    before = (workspace / "square.json").read_bytes()
    (cwd / "tsp_hpc.py").write_text("raise RuntimeError('cwd module must never load')\n")
    server = NativeServer(python, workspace, output, cwd)
    fd = identity = None
    with orphan_reaper():
        try:
            server.start()
            read_context = {key: value for key, value in server.context.items() if key != "session_id"}
            status, raw = server.http("/api/artifacts/query", {**read_context, "limit": 10, "cursor": None})
            assert status == 200
            item, = json.loads(raw)["items"]
            loaded = server.post("/api/instances/load", artifact_handle=item["artifact_handle"], artifact_revision=item["revision"])
            plan = server.review(loaded["instance_snapshot_id"])
            # Lose the response BODY/job identity after durable acceptance. Closing
            # this real client connection neither cancels nor resubmits the worker.
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
            try:
                connection.request("POST", "/api/jobs/submit", body=json.dumps({**server.context, "preflight_id": plan["preflight_id"],
                    "submission_key": "lost-ack"}), headers={"Content-Type": "application/json", "Authorization": "Bearer " + server.token})
                response = connection.getresponse()
                assert response.status == 202
                response.close()
            finally:
                connection.close()
            retried = server.submit(plan, "lost-ack")
            assert retried["deduplicated"]
            first = server.wait(retried["job"]["job_id"])
            assert first["state"] == "completed" and first["failed_trials"] == 0
            details = server.post("/api/jobs/result", **server.publication(first))["details"]
            geometry = server.post("/api/jobs/geometry", **server.publication(first), geometry_revision=details["geometry_revision"])["geometry"]
            coordinates = struct.unpack("<8d", base64.b64decode(geometry["coordinates"]["data"]))
            tour = struct.unpack("<4I", base64.b64decode(geometry["tour"]["data"]))
            assert len(coordinates) == 8 and sorted(tour) == list(range(4)) and details["result"]["verified_cost"] == 4
            assert details["native_verified"] and details["solver_seed"] == str(2**64 - 1)
            scope = {key: value for key, value in server.publication(first).items() if key in {"job_id", "expected_revision", "result_revision"}}
            history = server.post("/api/jobs/analysis", **scope, query={"table": "progress", "attempt_ids": None, "limit": 1, "cursor": None, "max_points": 100})
            assert history["items"] and history["clock"] == "logical_and_concatenated_search"
            source = output / ".tsp-local/jobs" / first["job_id"] / "results.jsonl"
            original = source.read_bytes()
            assert not list((site / "tsp_hpc").rglob("*.nbc"))
            cached = list((output / ".tsp-local/cache").rglob("*.nbc"))
            assert cached and all(path.stat().st_mode & 0o077 == 0 for path in cached)
            record = json.loads(original.splitlines()[0])
            assert record["runtime"]["build"]["status"] == "verified"
            assert record["runtime"]["build"]["identity"]["build_id"] == built[2]["build"]["build_id"]
            # Owner inspection invokes the SAME installed CLI runner with the
            # accepted manifest; input path/timing/context differences are not science.
            private = json.loads((source.parent / "request.json").read_bytes())
            manifest = cwd / "accepted.json"
            manifest.write_text(json.dumps(private["plan"]["manifest"]))
            artifact = cwd / "instance.json"
            status, export = server.http("/api/instances/export", {**server.context, "instance_snapshot_id": loaded["instance_snapshot_id"]})
            assert status == 200
            artifact.write_bytes(export)
            cli_output = cwd / "cli.jsonl"
            result = subprocess.run([str(python), "-I", "-B", "-W", "error", "-m", "tsp_hpc", "run", str(manifest),
                "--instance", f"{geometry['instance']['instance_id']}={artifact}", "--output", str(cli_output), "--max-matrix-bytes", "0"],
                cwd=cwd, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stderr
            cli = json.loads(cli_output.read_bytes().splitlines()[0])
            assert cli["requested_plan"] == record["requested_plan"]
            assert cli["result"]["best_tour"] == record["result"]["best_tour"] and cli["result"]["work"] == record["result"]["work"]
            assert cli["native_execution"]["verified"]

            generated = server.generate(4096)
            heavy = server.review(generated, budget={"start_cities": list(range(128))}, record_history=False)
            active = server.submit(heavy, "crash-active")["job"]
            fd, identity = server.worker(active["job_id"])
            signal.pidfd_send_signal(fd, signal.SIGSTOP)
            assert not exited(fd)
            pending = server.submit(plan, "pending")["job"]
            cancelled = server.submit(plan, "queued-cancel")["job"]
            assert server.control(cancelled["job_id"], "cancel_queued")["state"] == "cancelled"
            assert not (output / ".tsp-local/jobs" / pending["job_id"] / "worker.json").exists()
            old_token = server.token
            server.stop(crash=True)  # Real SIGKILL of the installed controller process.
            assert not exited(fd)
            server.start()
            assert server.http("/api/workspace", headers={"Authorization": "Bearer " + old_token})[0] == 403
            recovered = server.wait(active["job_id"], lambda job: job["state"] in {"preparing", "running"})
            state = server.post("/api/queue/status")
            assert state["dispatch"] == "held" and state["active_job_id"] == active["job_id"]
            assert server.post("/api/jobs/status", job_id=pending["job_id"])["job"]["state"] == "held"
            new_fd, same = server.worker(active["job_id"])
            os.close(new_fd)
            assert same == identity and recovered["campaign_id"] == active["campaign_id"]
            assert not (output / ".tsp-local/jobs" / pending["job_id"] / "worker.json").exists()
            assert server.control(active["job_id"], "stop_now")["state"] == "stopping"
            assert server.wait(active["job_id"])["state"] == "cancelled" and exited(fd)
            os.waitpid(identity["pid"], 0)  # This test owns the orphan via subreaper, not guessed PID signalling.
            os.close(fd); fd = None
            assert server.submit(plan, "lost-ack")["job"]["job_id"] == first["job_id"]
            assert source.read_bytes() == original
            # Owner fault injection into a held COPY, never an HTTP path override.
            # The installed controller must reject it rather than fall back to the
            # still-valid original snapshot or launch an unreviewed worker.
            damaged = server.submit(server.review(loaded["instance_snapshot_id"]), "damaged-copy")["job"]
            damaged_dir = output / ".tsp-local/jobs" / damaged["job_id"]
            (damaged_dir / "instance.json").write_bytes(b"{}")
            state = server.post("/api/queue/status")
            server.post("/api/queue/continue", expected_queue_revision=state["queue_revision"])
            finished = server.wait(pending["job_id"])
            assert finished["state"] == "completed"
            failed = server.wait(damaged["job_id"])
            assert failed["state"] == "failed" and failed["failure_code"] == "preflight_stale"
            assert failed["recorded_trials"] == 0 and not (damaged_dir / "worker.json").exists()
            reviewed = server.post("/api/jobs/review", job_id=first["job_id"])["plan"]
            renewed = server.review(reviewed["instance_snapshot_id"], **reviewed["configuration"])
            fresh = server.wait(server.submit(renewed, "fresh-whole-campaign")["job"]["job_id"])
            assert fresh["state"] == "completed" and fresh["campaign_id"] not in {first["campaign_id"], finished["campaign_id"]}
            assert not set(fresh["attempt_ids"]) & set(first["attempt_ids"] + finished["attempt_ids"])
            assert renewed["trials"] == plan["trials"] and source.read_bytes() == original
            assert sorted(path.name for path in workspace.iterdir()) == ["square.json"]
            assert (workspace / "square.json").read_bytes() == before
        finally:
            server.stop()
            if fd is not None:
                kill_verified(fd)
                try:
                    os.waitpid(identity["pid"], 0)
                except ChildProcessError:
                    pass
                os.close(fd)
            server.assert_private()


def test_installed_graceful_native_prefix_and_observed_history_are_not_completion(installed, tmp_path):
    python, _ = installed
    workspace, output, cwd = [tmp_path / name for name in ("read", "output", "outside")]
    for directory in (workspace, output, cwd):
        directory.mkdir()
    server = NativeServer(python, workspace, output, cwd)
    try:
        server.start()
        plan = server.review(server.generate(1024), budget={"start_cities": list(range(128))}, repetitions=list(range(32)),
                             history_policy={"interval_s": 1., "max_points": 128})
        job = server.submit(plan, "graceful-prefix")["job"]
        server.wait(job["job_id"], lambda value: value["recorded_trials"] > 0)
        assert server.control(job["job_id"], "stop_after_trial")["state"] == "stop_requested"
        stopped = server.wait(job["job_id"])
        assert stopped["state"] == "cancelled" and stopped["failed_trials"] == 0 and 1 <= stopped["recorded_trials"] < 32
        details = server.post("/api/jobs/result", **server.publication(stopped))["details"]
        assert not details["row"]["campaign_complete"] and not details["row"]["usable"] and details["result"]["verified_cost"] > 0
        scope = {key: value for key, value in server.publication(stopped).items() if key in {"job_id", "expected_revision", "result_revision"}}
        progress = server.post("/api/jobs/analysis", **scope, query={"table": "progress", "attempt_ids": None, "limit": 100, "cursor": None, "max_points": 4096})
        assert progress["items"] and not any(row["usable"] for row in progress["items"])
        source = output / ".tsp-local/jobs" / stopped["job_id"] / "results.jsonl"
        raw = source.read_bytes()
        assert b'"campaign_summary"' not in raw and len(raw.splitlines()) == stopped["recorded_trials"]
        assert server.submit(plan, "graceful-prefix")["job"]["state"] == "cancelled" and source.read_bytes() == raw
    finally:
        server.stop()
        server.assert_private()


def test_installed_read_only_and_transport_security_remain_optional(installed, tmp_path):
    python, _ = installed
    workspace, output, cwd = [tmp_path / name for name in ("read", "output", "outside")]
    for directory in (workspace, output, cwd):
        directory.mkdir()
    server = NativeServer(python, workspace, output, cwd, enabled=False)
    try:
        server.start()
        assert not server.post("/api/execution")["local_runs_enabled"]
        for headers in ({"Host": "localhost"}, {"Origin": "null"}, {"Origin": "https://foreign.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            assert server.http("/api/workspace", headers=headers)[0] == 403
        assert server.http("/api/execution", {**server.context, "enable_execution": True})[0] == 400
        assert server.http("/api/jobs/status", {**server.context, "job_id": "../outside"})[0] == 403
        for raw in (b'{"api_version":1,"api_version":1}', b'{"value":NaN}', b'{"value":1e999}', b'{"value":"\\ud800"}', b'\xff'):
            status, content = server.http("/api/execution", raw=raw)
            assert status == 400 and json.loads(content)["code"] == "invalid_request"
        assert not list(output.iterdir()) and not list(workspace.iterdir())
    finally:
        server.stop()
        server.assert_private()
    script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'numba', 'fastapi', 'pydantic', 'uvicorn'}:
            raise AssertionError('help imported an optional/numerical stack')
sys.meta_path.insert(0, Block())
from tsp_hpc.cli import main
raise SystemExit(main(sys.argv[1:]))
'''
    for arguments in (["--help"], ["--version"], ["serve", "--help"]):
        result = subprocess.run([str(python), "-I", "-c", script, *arguments], cwd=cwd, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
