"""One bounded, session-only dataset; shared scientific validation runs off-request.

No database, workspace writes, solver execution or public raw-record endpoint.
A single worker has no request queue. New accepted selections replace the old
index; shutdown/cancellation and stale inputs never publish partial analysis.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import hashlib
from importlib import metadata, resources
import json
import secrets
import sys
import threading

from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.workspace import MAX_READ_BYTES

RULE_FILES = (
    "analysis/__init__.py", "analysis/core.py", "analysis/sources.py", "experiments/manifests.py", "problem/data.py",
    "reproducibility/identity.py", "reproducibility/random_streams.py", "solvers/results.py",
    "interface/contracts.py", "interface/ingestion.py", "interface/workspace.py", "interface/queries.py",
)


def rule_identity():
    """Conservative cache invalidation identity, NOT verified build provenance."""
    root = resources.files("tsp_hpc")
    digest = hashlib.sha256(b"tsp-interface-ingestion-rules-v1\0")
    for name in RULE_FILES:
        digest.update(name.encode())
        digest.update(root.joinpath(name).read_bytes())
    digest.update(json.dumps({"python": list(sys.version_info[:3]),
                              "numpy": metadata.version("numpy"), "pandas": metadata.version("pandas")},
                             sort_keys=True).encode())
    return digest.hexdigest()


@dataclass(frozen=True)
class IngestionLimits:
    max_trials: int = 10_000
    max_record_bytes: int = 4 * 1024 * 1024
    max_summary_bytes: int = 16 * 1024 * 1024
    max_table_bytes: int = 32 * 1024 * 1024
    max_manifest_plans: int = 10_000
    max_manifest_logical_items: int = 100_000
    max_manifest_plan_bytes: int = 16 * 1024 * 1024
    max_diagnostics: int = 100

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in asdict(self).values()):
            raise ValueError("ingestion bounds must be positive integers")


@dataclass
class DatasetEntry:
    dataset_id: str
    catalog_revision: str
    sources: list
    manifest_source: object
    state: str = "loading"
    revision: str | None = None
    analysis: object = None
    diagnostics: list = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)


class DatasetStore:
    def __init__(self, catalog, *, limits=None):
        self.catalog = catalog
        self.limits = limits or IngestionLimits()
        self._rules = rule_identity()
        self._lock = threading.RLock()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tsp-ingestion")
        self._future = None
        self._entry = None
        self._closed = False

    @staticmethod
    def _diagnostic(code, message, *, handle=None, attempt=None):
        return {"code": code, "artifact_handle": handle, "attempt_id": attempt, "message": message}

    def _payload(self, entry, request_id):
        diagnostics = entry.diagnostics[:self.limits.max_diagnostics]
        return {"api_version": 1, "request_id": request_id, "workspace_id": self.catalog.workspace_id,
                "dataset_id": entry.dataset_id, "revision": entry.revision, "state": entry.state,
                "diagnostics": [dict(item) for item in diagnostics],
                "diagnostics_total": len(entry.diagnostics),
                "diagnostics_truncated": len(entry.diagnostics) > len(diagnostics)}

    def _lookup(self, dataset_id):
        if self._closed:
            raise InterfaceError("workspace_unavailable", "Dataset session is closed.", 503)
        if self._entry is None or self._entry.dataset_id != dataset_id:
            raise InterfaceError("dataset_not_found", "Dataset is not available in this session.", 404)
        return self._entry

    def _assert_rules(self):
        try:
            changed = rule_identity() != self._rules
        except (OSError, metadata.PackageNotFoundError):
            changed = True
        if changed:
            raise InterfaceError("stale_revision", "Analysis implementation changed; restart the backend.", 409, retryable=True)

    def submit(self, handles, manifest_handle, catalog_revision, request_id):
        with self._lock:
            if self._closed:
                raise InterfaceError("workspace_unavailable", "Dataset session is closed.", 503)
            if self._future is not None and not self._future.done():
                raise InterfaceError("ingestion_busy", "An ingestion is already running; no additional work was queued.", 409, retryable=True)
            self._assert_rules()
            sources, manifest = self.catalog.select_sources(handles, manifest_handle, catalog_revision)
            entry = DatasetEntry(secrets.token_urlsafe(24), catalog_revision, sources, manifest)
            self._entry = entry  # Previous dataset becomes unavailable; retain at most one index.
            accepted = self._payload(entry, request_id)
            self._future = self._worker.submit(self._ingest, entry)
            return accepted

    def _mark_stale(self, entry, message="Sources or catalog changed; refresh and reload the dataset."):
        entry.cancel.set()
        entry.state, entry.revision, entry.analysis = "stale", None, None
        entry.diagnostics = [self._diagnostic("stale_revision", message)]

    def invalidate(self):
        with self._lock:
            if self._entry is not None:
                self._mark_stale(self._entry)

    def status(self, dataset_id, request_id):
        with self._lock:
            entry = self._lookup(dataset_id)
        if entry.state in ("loading", "ready"):
            try:
                self._assert_rules()
                self.catalog.assert_revision(entry.catalog_revision)
            except InterfaceError as error:
                if error.code == "catalog_busy":
                    raise  # Contention is not evidence of changed inputs.
                with self._lock:
                    self._mark_stale(entry, error.message)
        with self._lock:
            if self._lookup(dataset_id) is not entry:
                raise InterfaceError("dataset_not_found", "Dataset selection changed.", 404)
            return self._payload(entry, request_id)

    @contextmanager
    def analysis_snapshot(self, dataset_id, revision):
        """Internal query boundary for #76; never expose this mutable object to clients."""
        self.status(dataset_id, "internal")
        with self._lock:
            entry = self._lookup(dataset_id)
            if entry.state == "stale" or (entry.state == "ready" and revision != entry.revision):
                raise InterfaceError("stale_revision", "Dataset revision changed; reload before querying.", 409, retryable=True)
            if entry.state != "ready":
                raise InterfaceError("dataset_not_ready", "Dataset is not ready; reload before querying.", 409)
            analysis = entry.analysis
        yield analysis
        self.status(dataset_id, "internal")
        with self._lock:
            if self._lookup(dataset_id) is not entry or entry.state != "ready" or revision != entry.revision:
                raise InterfaceError("stale_revision", "Dataset changed while querying.", 409, retryable=True)

    def _manifest(self, entry):
        from tsp_hpc.analysis.core import AnalysisLimitError
        from tsp_hpc.experiments.manifests import Manifest
        from tsp_hpc.reproducibility.identity import canonical_json
        if entry.manifest_source is None:
            return None, None
        source = entry.manifest_source
        with source.open() as stream:
            raw = stream.read(MAX_READ_BYTES + 1)
        if len(raw) > MAX_READ_BYTES:
            raise AnalysisLimitError("manifest exceeds configured byte limit")
        manifest = Manifest.from_json(raw.decode("utf-8"))
        document = manifest.to_dict()
        instances = {item["instance_id"]: item for item in document["instances"]}
        plans = work = plan_bytes = 0
        for experiment in document["experiments"]:
            repetitions = len(experiment["repetitions"])
            count = repetitions * len(experiment["executions"])
            plans += count
            logical = experiment["budget"]["start_cities"] if experiment["algorithm"] == "nn" else experiment["logical_search_ids"]
            work += count * len(logical)
            # Conservative allocation estimate, not a second planner. Remove the
            # repetition list, which is not copied into each expanded search.
            search = {key: value for key, value in experiment.items() if key not in ("executions", "repetitions")}
            search["instance"] = instances[search.pop("instance_id")]
            repetition_bytes = max((len(str(value)) for value in experiment["repetitions"]), default=0)
            search_bytes = len(canonical_json(search).encode("ascii")) + repetition_bytes + 1024
            plan_bytes += repetitions * sum(search_bytes + len(canonical_json(execution).encode("ascii"))
                                            for execution in experiment["executions"])
        if (plans > self.limits.max_manifest_plans or work > self.limits.max_manifest_logical_items
                or plan_bytes > self.limits.max_manifest_plan_bytes):
            raise AnalysisLimitError("expected-manifest expansion exceeds configured limit")
        return manifest, {"handle": source.identity, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}

    def _public_diagnostics(self, analysis):
        # Keep authoritative diagnostics on Analysis; expose sanitized bounded summaries.
        result = []
        for item in analysis.diagnostics:
            code = item["code"]
            count = len(item.get("attempt_ids", item.get("requested_execution_ids", [])))
            messages = {
                "unterminated_tail": f"Unterminated final record at line {item.get('line')}; validated prefix retained.",
                "incomplete_campaign": f"Incomplete campaign; {item.get('observed_trials', 0)} observed trials excluded from aggregates.",
                "duplicate_execution": f"{count} attempts repeat an execution configuration; all ambiguous copies are excluded.",
                "failed_trials": f"{count} failed trials recorded.",
                "missing_executions": f"{count} expected executions are absent.",
                "unexpected_executions": f"{count} unexpected executions were observed.",
            }
            result.append(self._diagnostic(code, messages.get(code, "Analysis reported a diagnostic."), handle=item.get("path")))
        return result

    def _ingest(self, entry):
        # Heavy imports occur only in this worker, not during CLI/catalog startup.
        try:
            from tsp_hpc.analysis import read_sources
            from tsp_hpc.analysis.core import AnalysisLimitError, ShardValidationError
        except Exception:
            with self._lock:
                if self._entry is entry and entry.state == "loading":
                    entry.state = "failed"
                    entry.diagnostics = [self._diagnostic("internal_error", "Analysis dependencies could not be loaded.")]
            return

        def checkpoint():
            if entry.cancel.is_set():
                raise InterruptedError("ingestion interrupted")

        try:
            checkpoint()
            manifest, manifest_identity = self._manifest(entry)
            checkpoint()
            analysis = read_sources(entry.sources, expected_manifest=manifest,
                                    max_record_bytes=self.limits.max_record_bytes,
                                    max_trials=self.limits.max_trials,
                                    max_summary_bytes=self.limits.max_summary_bytes,
                                    checkpoint=checkpoint)
            if int(analysis.trials.memory_usage(index=True, deep=True).sum()) > self.limits.max_table_bytes:
                raise AnalysisLimitError("retained table exceeds configured limit")
            checkpoint()
            self.catalog.assert_revision(entry.catalog_revision, wait=True)
            self._assert_rules()
            context = {"schema": "tsp-dataset-index-v1", "workspace": self.catalog.workspace_id,
                       "catalog_revision": entry.catalog_revision, "rules": self._rules,
                       "limits": asdict(self.limits), "sources": analysis.sources,
                       "expected_manifest": manifest_identity, "references": []}
            revision = "dataset-v1-" + hashlib.sha256(json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            diagnostics = self._public_diagnostics(analysis)
            with self._lock:
                if not self._closed and self._entry is entry and not entry.cancel.is_set():
                    entry.analysis, entry.revision = analysis, revision
                    entry.diagnostics, entry.state = diagnostics, "ready"
        except Exception as error:
            with self._lock:
                if self._entry is not entry or entry.state != "loading":
                    return
                if isinstance(error, InterfaceError) and error.code in ("stale_revision", "workspace_unavailable"):
                    self._mark_stale(entry, error.message)
                else:
                    entry.state, entry.revision, entry.analysis = "failed", None, None
                    if isinstance(error, InterruptedError):
                        diagnostic = self._diagnostic("ingestion_interrupted", "Ingestion interrupted; reload the dataset.")
                    elif isinstance(error, AnalysisLimitError):
                        diagnostic = self._diagnostic("dataset_limit_exceeded", "Ingestion exceeds a configured resource limit; select a smaller dataset.")
                    elif isinstance(error, ShardValidationError):
                        diagnostic = self._diagnostic("invalid_artifact", f"Result validation failed at record {error.line}.", handle=error.source_id)
                    elif isinstance(error, (ValueError, TypeError, UnicodeError)):
                        diagnostic = self._diagnostic("invalid_artifact", "Selected sources or expected manifest failed validation.")
                    else:
                        diagnostic = self._diagnostic("internal_error", "Dataset ingestion failed.")
                    entry.diagnostics = [diagnostic]

    def close(self):
        with self._lock:
            self._closed = True
            if self._entry is not None:
                self._entry.cancel.set()
                self._entry.analysis = None
                self._entry.revision = None
        self._worker.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._entry = None
