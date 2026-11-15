"""Durable bounded serial job queue; the scientific runner lives in one child.

Immutable job inputs precede an atomic durable admission record. Request-key
retries only return the original job. Controller restart holds the queue and
reconciles owned workers; no retries, appends or campaign continuation occur.
"""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import fcntl
import hashlib
import math
import os
import re
import secrets
import subprocess
import sys
import threading
from time import monotonic

from tsp_hpc.interface.cursors import encode_cursor, decode_cursor
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.processes import WORKER_MODULE, exited, kill_verified, process_identity, recover_pidfd
from tsp_hpc.interface.runtime import runtime_binding
from tsp_hpc.interface.storage import decode, encode

STATE_BYTES = 4 * 1024 * 1024
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
ACTIVE = frozenset({"preparing", "running", "recovering", "stop_requested", "stopping"})
PUBLIC_FIELDS = ("job_id revision submission_key preflight_id instance_snapshot_id campaign_id state stop_mode "
                 "planned_trials recorded_trials failed_trials attempt_ids result_handle result_revision elapsed_time_s failure_code").split()
PRIVATE_FIELDS = set(PUBLIC_FIELDS) | {"request_sha256", "directory_identity", "worker", "started"}


def _hex(value, length=32):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None


class JobSource:
    """The shared analysis source protocol, with root/job authority on every read."""
    def __init__(self, owner, job):
        self.owner, self.job = owner, deepcopy(job)
        self.identity = "result-" + job["job_id"]

    def stamp(self):
        with self.open() as stream:
            info = os.fstat(stream.fileno())
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    @contextmanager
    def open(self):
        with self.owner.directory(self.job) as directory:
            with directory.open_regular("results.jsonl", self.owner.limits.max_result_bytes) as stream:
                yield stream


class JobQueue:
    def __init__(self, storage, snapshots, preflights):
        self.storage, self.snapshots, self.preflights = storage, snapshots, preflights
        self.limits = storage.authority.limits
        self._lock = threading.RLock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._closed, self._fatal = False, False
        self._pidfd, self._process = None, None
        self._cursor_key = secrets.token_bytes(32)
        try:
            state = decode(storage.read("queue.json", STATE_BYTES))
            self._validate_state(state)
            state["held"] = True  # Restart is not permission to run queued work.
        except FileNotFoundError:
            state = {"schema_version": 1, "revision": secrets.token_hex(16), "held": False,
                     "active_job_id": None, "jobs": []}
        except (ValueError, TypeError, KeyError, RecursionError):
            raise InterfaceError("execution_unavailable", "Queue state is invalid; preserved evidence needs owner inspection.", 503) from None
        self._commit(state)
        self._thread = threading.Thread(target=self._loop, name="tsp-local-queue", daemon=True)
        self._thread.start()

    def _validate_state(self, state):
        if (type(state) is not dict or set(state) != {"schema_version", "revision", "held", "active_job_id", "jobs"}
                or type(state["schema_version"]) is not int or state["schema_version"] != 1
                or type(state["held"]) is not bool or not _hex(state["revision"])
                or type(state["jobs"]) is not list or len(state["jobs"]) > self.limits.max_jobs):
            raise ValueError("invalid queue")
        ids, keys, active = set(), set(), []
        for job in state["jobs"]:
            if type(job) is not dict or set(job) != PRIVATE_FIELDS:
                raise ValueError("invalid job fields")
            if any(not _hex(job[key]) for key in ("job_id", "revision", "preflight_id", "instance_snapshot_id", "campaign_id")):
                raise ValueError("invalid job identity")
            if (not _hex(job["request_sha256"], 64) or type(job["submission_key"]) is not str or not 1 <= len(job["submission_key"]) <= 128
                    or job["job_id"] in ids or job["submission_key"] in keys or job["state"] not in TERMINAL | ACTIVE | {"queued"}
                    or type(job["planned_trials"]) is not int or not 1 <= job["planned_trials"] <= 10000
                    or type(job["directory_identity"]) is not list or len(job["directory_identity"]) != 2
                    or any(type(n) is not int or n < 0 for n in job["directory_identity"])):
                raise ValueError("invalid job metadata")
            self._progress({"attempt_ids": job["attempt_ids"], "counts": {
                "completed": job["recorded_trials"] - job["failed_trials"], "failed": job["failed_trials"]}}, job)
            if job["state"] in ACTIVE:
                active.append(job["job_id"])
            ids.add(job["job_id"]); keys.add(job["submission_key"])
        if active != ([] if state["active_job_id"] is None else [state["active_job_id"]]):
            raise ValueError("multiple or inconsistent active workers")

    def _commit(self, state):
        state["revision"] = secrets.token_hex(16)
        raw = encode(state)
        if len(raw) > STATE_BYTES:
            raise InterfaceError("resource_limit", "Durable queue metadata exceeds its bound.", 413)
        self.storage.check()
        self.storage.replace("queue.json", raw)
        self._state = state

    def _guard(self):
        if self._closed or self._fatal:
            raise InterfaceError("execution_unavailable", "Queue unavailable; restart and reconcile preserved state.", 503)
        self.storage.check()

    def _find(self, identifier, state=None):
        for job in (self._state if state is None else state)["jobs"]:
            if job["job_id"] == identifier:
                return job
        raise InterfaceError("job_not_found", "Job is unavailable in this execution root.", 404)

    def _change(self, identifier, **changes):
        state = deepcopy(self._state)
        job = self._find(identifier, state)
        job.update(changes, revision=secrets.token_hex(16))
        if job["state"] in TERMINAL and state["active_job_id"] == identifier:
            state["active_job_id"] = None
        self._commit(state)

    @contextmanager
    def directory(self, job):
        self.storage.check()
        with self.storage.directory("jobs") as jobs:
            with jobs.directory(job["job_id"]) as directory:
                info = os.fstat(directory.fd)
                if [info.st_dev, info.st_ino] != job["directory_identity"]:
                    raise InterfaceError("snapshot_stale", "Job directory changed.", 409)
                yield directory
                info = os.stat(job["job_id"], dir_fd=jobs.fd, follow_symlinks=False)
                if [info.st_dev, info.st_ino] != job["directory_identity"]:
                    raise InterfaceError("snapshot_stale", "Job directory changed during access.", 409)
        self.storage.check()

    def _public(self, job):
        value = {key: deepcopy(job[key]) for key in PUBLIC_FIELDS}
        queued = [entry["job_id"] for entry in self._state["jobs"] if entry["state"] == "queued"]
        value["queue_position"] = queued.index(job["job_id"]) if job["job_id"] in queued else None
        if job["state"] == "queued" and self._state["held"]:
            value["state"] = "held"
        return value

    def queue(self):
        with self._lock:
            self._guard()
            return {"queue_revision": self._state["revision"], "dispatch": "held" if self._state["held"] else "enabled",
                    "active_job_id": self._state["active_job_id"],
                    "pending_job_ids": [job["job_id"] for job in self._state["jobs"] if job["state"] == "queued"]}

    def status(self, identifier):
        with self._lock:
            self._guard()
            return self._public(self._find(identifier))

    def page(self, *, limit, cursor):
        with self._lock:
            self._guard()
            offset = 0
            if cursor is not None:
                try:
                    revision, size, offset = decode_cursor(self._cursor_key, cursor)
                    if revision != self._state["revision"] or size != limit or type(offset) is not int or offset < 0:
                        raise ValueError
                except (ValueError, TypeError, UnicodeError):
                    raise InterfaceError("cursor_invalid", "Job cursor changed; reload the queue.", 409) from None
            jobs = self._state["jobs"]
            next_cursor = (encode_cursor(self._cursor_key, [self._state["revision"], limit, offset + limit])
                           if offset + limit < len(jobs) else None)
            result = {"queue_revision": self._state["revision"], "items": [self._public(job) for job in jobs[offset:offset + limit]],
                      "next_cursor": next_cursor}
            if len(encode(result)) > self.limits.max_response_bytes:
                raise InterfaceError("resource_limit", "Job page is too large; request a smaller page.", 413)
            return result

    def submit(self, preflight_id, submission_key, session_id):
        with self._lock:
            self._guard()
            # Durable idempotency precedes ephemeral plan lookup: retries still
            # succeed after expiry, re-pairing and backend restart, never rerun.
            existing = next((job for job in self._state["jobs"] if job["submission_key"] == submission_key), None)
            if existing is not None:
                if existing["preflight_id"] != preflight_id:
                    raise InterfaceError("submission_conflict", "Submission key already belongs to a different reviewed plan.", 409)
                return self._public(existing), True
            if sum(job["state"] == "queued" for job in self._state["jobs"]) >= self.limits.max_pending:
                raise InterfaceError("queue_full", "Pending queue is full; no work was accepted.", 409, retryable=True)
            plan = self.preflights.validate(preflight_id, session_id)
            plan.pop("session_id")  # No browser/session authority is persisted for the worker.
            _, raw, _ = self.snapshots.read(plan["public"]["instance_snapshot_id"])
            identifier, campaign = secrets.token_hex(16), secrets.token_hex(16)
            request = encode({"schema_version": 1, "job_id": identifier, "campaign_id": campaign, "plan": plan})
            if len(request) > min(self.limits.max_plan_bytes, STATE_BYTES):
                raise InterfaceError("resource_limit", "Submission metadata exceeds its bound.", 413)
            with self.storage.directory("jobs") as jobs:
                if len(jobs.names(self.limits.max_jobs)) >= self.limits.max_jobs:
                    raise InterfaceError("resource_limit", "Retained job capacity reached; use a new output root.", 413)
                with jobs.directory(identifier, create=True) as directory:
                    info = os.fstat(directory.fd)
                    directory.write_new("instance.json", raw)
                    directory.write_new("request.json", request)
            job = dict(job_id=identifier, revision=secrets.token_hex(16), submission_key=submission_key, preflight_id=preflight_id,
                       instance_snapshot_id=plan["public"]["instance_snapshot_id"], campaign_id=campaign,
                       state="queued", stop_mode=None, planned_trials=plan["public"]["trial_count"], recorded_trials=0, failed_trials=0,
                       attempt_ids=[], result_handle=None, result_revision=None, elapsed_time_s=None, failure_code=None,
                       request_sha256=hashlib.sha256(request).hexdigest(), directory_identity=[info.st_dev, info.st_ino],
                       worker=None, started=None)
            state = deepcopy(self._state)
            state["jobs"].append(job)
            self._commit(state)  # Durable acceptance before acknowledge or dispatch.
            self._wake.set()
            return self._public(job), False

    def _lease(self):
        fd = os.open("worker.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     0o600, dir_fd=self.storage.fd)
        try:
            import stat
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise InterfaceError("execution_unavailable", "Worker lease is invalid.", 503)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _launch(self, job):
        lease = self._lease()
        if lease is None:
            state = deepcopy(self._state)
            state["held"] = True
            self._commit(state)
            return
        phase = "preflight"
        try:
            with self.directory(job) as directory:
                raw = directory.read("request.json", STATE_BYTES)
                if hashlib.sha256(raw).hexdigest() != job["request_sha256"]:
                    raise ValueError("submission changed")
                request = decode(raw)
                plan = request["plan"]
                if plan["limits"] != asdict(self.limits) or runtime_binding()["runtime_key"] != plan["runtime"]["runtime_key"]:
                    raise ValueError("runtime/resource policy changed")
                if hashlib.sha256(directory.read("instance.json", self.limits.max_instance_bytes)).hexdigest() != plan["public"]["snapshot_sha256"]:
                    raise ValueError("input changed")
                with self.storage.directory("cache") as caches:
                    name = plan["runtime"]["runtime_key"]
                    try:
                        os.mkdir(name, 0o700, dir_fd=caches.fd)
                    except FileExistsError:
                        pass
                    with caches.directory(name) as cache:
                        state = deepcopy(self._state)
                        active = self._find(job["job_id"], state)
                        active.update(state="preparing", started=monotonic(), revision=secrets.token_hex(16))
                        state["active_job_id"] = job["job_id"]
                        self._commit(state)
                        environment = dict(os.environ)
                        for key, value in plan["runtime"]["worker_environment"].items():
                            if value is None:
                                environment.pop(key, None)
                            else:
                                environment[key] = value
                        environment["NUMBA_CACHE_DIR"] = f"/proc/self/fd/{cache.fd}"
                        args = [sys.executable, "-I", "-B", "-W", "error", "-m", WORKER_MODULE,
                                job["job_id"], str(directory.fd), str(lease), str(cache.fd), job["request_sha256"]]
                        phase = "launch"
                        self._process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                         stderr=subprocess.DEVNULL, close_fds=True,
                                                         pass_fds=(directory.fd, lease, cache.fd), env=environment,
                                                         start_new_session=True)
                        # An unreaped direct child cannot be a reused PID. Recovery
                        # additionally verifies boot/start ticks and worker marker.
                        self._pidfd = os.pidfd_open(self._process.pid, 0)
                        identity = process_identity(self._process.pid, job["job_id"])
                        self._change(job["job_id"], worker=identity)
        except (OSError, ValueError, TypeError, InterfaceError):
            if self._process is not None:
                if self._pidfd is not None:
                    kill_verified(self._pidfd)
                self._change(job["job_id"], state="stopping", failure_code="launch_failed")
            else:
                self._change(job["job_id"], state="failed", failure_code="launch_failed" if phase == "launch" else "preflight_stale")
        finally:
            os.close(lease)  # Only the actual child retains the worker lease now.

    @staticmethod
    def _progress(value, job):
        if type(value) is not dict or set(value) != {"attempt_ids", "counts"}:
            raise ValueError("invalid progress fields")
        attempts, counts = value["attempt_ids"], value["counts"]
        if (type(attempts) is not list or len(attempts) > job["planned_trials"] or any(not _hex(item) for item in attempts)
                or len(set(attempts)) != len(attempts) or type(counts) is not dict or set(counts) != {"completed", "failed"}
                or any(type(n) is not int or n < 0 for n in counts.values()) or sum(counts.values()) != len(attempts)):
            raise ValueError("invalid progress accounting")
        return {"attempt_ids": attempts, "recorded_trials": len(attempts), "failed_trials": counts["failed"]}

    def _finish(self, job):
        """Only after verified exit/free lease; shared validation owns the prefix."""
        from tsp_hpc.analysis import read_sources
        from tsp_hpc.experiments.manifests import Manifest
        changes = {"state": "failed", "failure_code": job["failure_code"] or "worker_failed"}
        try:
            with self.directory(job) as directory:
                try:
                    receipt = decode(directory.read("receipt.json", 65536))
                    progress = self._progress({key: receipt[key] for key in ("attempt_ids", "counts")}, job)
                    if receipt["schema_version"] != 1 or receipt["job_id"] != job["job_id"] or receipt["state"] not in {"completed", "cancelled", "failed"}:
                        raise ValueError("invalid receipt")
                except FileNotFoundError:
                    receipt, progress = None, None
                raw = directory.read("request.json", STATE_BYTES)
                if hashlib.sha256(raw).hexdigest() != job["request_sha256"]:
                    raise ValueError("submission changed")
                manifest = Manifest(decode(raw)["plan"]["manifest"])
                try:
                    result = directory.read("results.jsonl", self.limits.max_result_bytes)
                except FileNotFoundError:
                    result = None
            if result is not None:
                source = JobSource(self, job)
                analysis = read_sources([source], expected_manifest=manifest, max_record_bytes=self.limits.max_record_bytes,
                                        max_trials=job["planned_trials"], max_summary_bytes=self.limits.max_plan_bytes)
                if analysis.sources[0]["sha256"] != hashlib.sha256(result).hexdigest():
                    raise ValueError("result changed between validation and publication")
                frame = analysis.trials
                attempts = frame["attempt_id"].tolist()
                failed = int(frame["outcome"].eq("failed").sum())
                complete = not any(item["code"] in ("incomplete_campaign", "missing_executions", "unexpected_executions") for item in analysis.diagnostics)
                if any(item != job["campaign_id"] for item in frame["campaign_id"].tolist()):
                    raise ValueError("campaign mismatch")
                changes.update(attempt_ids=attempts, recorded_trials=len(attempts), failed_trials=failed,
                               result_handle=source.identity, result_revision=hashlib.sha256(result).hexdigest())
                if receipt is not None and (progress["attempt_ids"] != attempts or progress["failed_trials"] != failed):
                    raise ValueError("receipt/prefix mismatch")
            else:
                complete = False
            if receipt is not None and receipt["state"] == "completed" and complete:
                changes.update(state="completed", failure_code=None)
            elif receipt is not None and receipt["state"] == "cancelled" and not complete:
                changes.update(state="cancelled", failure_code=None)
            elif receipt is not None and receipt["state"] == "failed":
                changes.update(failure_code=receipt.get("failure_code", "worker_failed"))
            elif job["stop_mode"] == "now":
                changes.update(state="cancelled", failure_code=None)
            elif job["failure_code"] is None:
                changes.update(state="interrupted", failure_code="worker_interrupted")
        except (OSError, ValueError, TypeError, KeyError, InterfaceError):
            changes.update(state="failed", failure_code="invalid_artifact")
        if self._process is not None and job["started"] is not None:
            changes["elapsed_time_s"] = max(0., monotonic() - job["started"])
        self._change(job["job_id"], **changes)
        if self._process is not None:
            self._process.wait(timeout=5)
            self._process = None
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None

    def _tick(self):
        with self._lock:
            self.storage.check()
            identifier = self._state["active_job_id"]
            if identifier is not None:
                job = self._find(identifier)
                if self._pidfd is None:
                    identity = job["worker"]
                    if identity is None:
                        try:
                            with self.directory(job) as directory:
                                identity = decode(directory.read("worker.json", 4096))
                        except FileNotFoundError:
                            pass
                    self._pidfd = recover_pidfd(identity, identifier)
                    if self._pidfd is not None:
                        self._change(identifier, worker=identity)
                        job = self._find(identifier)
                    else:
                        lease = self._lease()
                        if lease is not None:
                            os.close(lease)
                            self._finish(job)
                            return
                        if job["state"] != "recovering":
                            self._change(identifier, state="recovering")
                        return  # Unknown live lease: hold, never signal or dispatch.
                if exited(self._pidfd):
                    self._finish(job)
                    return
                if job["started"] is not None and monotonic() - job["started"] >= self.limits.max_job_seconds:
                    kill_verified(self._pidfd)
                    self._change(identifier, state="stopping", failure_code="resource_limit")
                    return
                try:
                    with self.directory(job) as directory:
                        value = decode(directory.read("progress.json", 65536))
                    progress = self._progress(value, job)
                    if progress["recorded_trials"] < job["recorded_trials"] or progress["attempt_ids"][:job["recorded_trials"]] != job["attempt_ids"]:
                        raise ValueError("regressing progress")
                    if progress["attempt_ids"] != job["attempt_ids"]:
                        self._change(identifier, **progress)
                except FileNotFoundError:
                    pass
                if job["state"] in ("preparing", "recovering"):
                    self._change(identifier, state="running")
            elif not self._state["held"]:
                job = next((job for job in self._state["jobs"] if job["state"] == "queued"), None)
                if job is not None:
                    self._launch(job)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                # Fail closed on corrupt storage/control observations. Preserve
                # durable state; never hide an exception by launching more work.
                with self._lock:
                    self._fatal = True
                    if self._pidfd is not None:
                        kill_verified(self._pidfd)
                return
            self._wake.wait(.05)
            self._wake.clear()

    def close(self):
        self._stop.set(); self._wake.set()
        self._thread.join()
        with self._lock:
            # Termination authority is the owned pidfd, not a writable state path.
            # Even a replaced/unavailable output root must not skip child cleanup.
            if self._pidfd is not None:
                kill_verified(self._pidfd)
            try:
                state = deepcopy(self._state)
                state["held"] = True
                self._commit(state)
                if self._pidfd is not None:
                    kill_verified(self._pidfd)
                    deadline = monotonic() + 5
                    while not exited(self._pidfd) and monotonic() < deadline:
                        threading.Event().wait(.01)
                    if exited(self._pidfd) and self._state["active_job_id"] is not None:
                        self._finish(self._find(self._state["active_job_id"]))
            finally:
                self._closed = True
                if self._pidfd is not None:
                    os.close(self._pidfd)
                    self._pidfd = None
                if self._process is not None:
                    # An unconfirmed exit is left active for recovery, not
                    # labelled cancelled. Retain the Popen until eventual reap.
                    threading.Thread(target=self._process.wait, name="tsp-worker-reaper", daemon=True).start()
                    self._process = None
