"""Exact source/snapshot binding and independent final-solution publication.

The shared reader validates envelopes/plans/histories; the shared independent
objective checks actual final permutations against captured original coordinates.
No persisted path is opened, no source is searched for a convenient/latest tour,
and no history observation or failed aggregate is promoted to final geometry.
"""

import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import threading

from tsp_hpc.interface.contracts import GEOMETRY_BYTES_MAX, TrialRow
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.ingestion import rule_identity
from tsp_hpc.interface.jobs import JobSource, TERMINAL
from tsp_hpc.interface.snapshots import wire_descriptor
from tsp_hpc.interface.storage import encode
from tsp_hpc.interface.wire import wire_value


class ResultPublisher:
    def __init__(self, catalog, datasets, jobs, snapshots, limits):
        self.catalog, self.datasets, self.jobs, self.snapshots, self.limits = catalog, datasets, jobs, snapshots, limits
        self._rules = rule_identity()
        self._cache = None  # At most ONE compact job index, never retained tours/records.
        self._lock = threading.RLock()

    def _assert_rules(self):
        try:
            changed = rule_identity() != self._rules
        except (OSError, ImportError):
            changed = True
        if changed:
            self._cache = None
            raise InterfaceError("stale_revision", "Publication implementation changed; restart the backend.", 409)

    @contextmanager
    def job_analysis(self, identifier, job_revision, result_revision):
        from tsp_hpc.analysis import read_sources
        from tsp_hpc.analysis.core import AnalysisLimitError
        from tsp_hpc.experiments.manifests import Manifest
        if self.jobs is None:
            raise InterfaceError("execution_disabled", "Local job authority is disabled.", 403)
        with self.jobs._lock:
            self.jobs._guard()
            job = deepcopy(self.jobs._find(identifier))
            if job["revision"] != job_revision:
                raise InterfaceError("job_stale", "Job revision changed; reload its status.", 409)
            if job["state"] not in TERMINAL or job["result_revision"] is None:
                raise InterfaceError("result_unavailable", "No exited, validated result source is available.", 409)
            if job["result_revision"] != result_revision:
                raise InterfaceError("stale_revision", "Result revision changed; reload its status.", 409)
        source = JobSource(self.jobs, job)
        with self._lock:
            self._assert_rules()
            try:
                before = source.stamp()
                key = (identifier, result_revision)
                if self._cache is not None and self._cache[0] == key:
                    _, stamp, analysis = self._cache
                    if stamp != before:
                        raise InterfaceError("stale_revision", "Result source changed; no cached record was published.", 409)
                else:
                    request = self.jobs.request(job)
                    analysis = read_sources([source], expected_manifest=Manifest(request["plan"]["manifest"]),
                                            max_record_bytes=self.limits.max_record_bytes, max_trials=job["planned_trials"],
                                            max_summary_bytes=self.limits.max_plan_bytes)
                    if analysis.sources[0]["sha256"] != result_revision:
                        raise InterfaceError("stale_revision", "Result content no longer matches its published revision.", 409)
                    if (any(value != job["campaign_id"] for value in analysis.trials["campaign_id"])
                            or analysis.trials["attempt_id"].tolist() != job["attempt_ids"]
                            or any(item["code"] == "unexpected_executions" for item in analysis.diagnostics)
                            or (any(item["code"] == "missing_executions" for item in analysis.diagnostics)
                                and not any(item["code"] == "incomplete_campaign" for item in analysis.diagnostics))):
                        raise InterfaceError("snapshot_mismatch", "Result does not belong to this admitted campaign.", 409)
                    if int(analysis.trials.memory_usage(index=True, deep=True).sum()) > self.limits.max_plan_bytes:
                        raise InterfaceError("resource_limit", "Result index exceeds its memory estimate bound.", 413)
                    self._cache = (key, before, analysis)
                yield analysis, job
                if source.stamp() != before:
                    raise InterfaceError("stale_revision", "Result changed during publication.", 409)
                self._assert_rules()
                with self.jobs._lock:
                    self.jobs._guard()
                    current = self.jobs._find(identifier)
                    if current["revision"] != job_revision or current["result_revision"] != result_revision:
                        raise InterfaceError("job_stale", "Job changed during publication.", 409)
            except AnalysisLimitError:
                raise InterfaceError("resource_limit", "Result exceeds shared analysis bounds.", 413) from None
            except (OSError, ValueError, TypeError, KeyError, RecursionError, ArithmeticError):
                self._cache = None
                raise InterfaceError("invalid_artifact", "Result failed shared validation or source checks.", 422) from None

    def _material(self, analysis, attempt, campaign, instance, snapshot_sha, binding, *, require_worker_path=False):
        import numpy as np
        from tsp_hpc.problem.objective import validate_solution
        from tsp_hpc.reproducibility.identity import canonical_json
        from tsp_hpc.solvers.validation import decode_result
        self._assert_rules()
        frame = analysis.trials.loc[analysis.trials["attempt_id"].eq(attempt)]
        if len(frame) != 1:
            raise InterfaceError("result_unavailable", "An exact unique recorded attempt is required.", 404)
        row = frame.iloc[0].to_dict()
        if row["campaign_id"] != campaign:
            raise InterfaceError("snapshot_mismatch", "Attempt belongs to a different campaign.", 409)
        try:
            record = analysis.trial(attempt)
        except (OSError, ValueError, KeyError):
            raise InterfaceError("stale_revision", "Result source changed; reload its authorized index.", 409) from None
        try:
            search = record["requested_plan"]["search"]
            if require_worker_path and record["input"]["path"] != "instance.json":
                raise InterfaceError("snapshot_mismatch", "Job record has an unexpected input binding.", 409)
            if (canonical_json(search["instance"]) != canonical_json(instance.descriptor)
                    or canonical_json(record["input"].get("descriptor")) != canonical_json(instance.descriptor)
                    or canonical_json(record["input"].get("provenance")) != canonical_json(instance.provenance)):
                raise InterfaceError("snapshot_mismatch", "Instance identity/order/objective/provenance does not match the recorded input.", 409)
            if instance.instance.n_cities > self.limits.max_cities:
                raise InterfaceError("geometry_too_large", "Instance exceeds the publication city bound.", 413)
            logical = None
            if record["schema_version"] == 3:
                logical = []
                for index, entry in enumerate(record["logical_results"]):
                    times = entry["timings"]
                    decoded = decode_result(entry["result"], search, search_time_s=times["search_time_s"],
                                            validation_time_s=times["solver_validation_time_s"])
                    cost = None if decoded.best_tour is None else validate_solution(instance.instance, decoded.best_tour, decoded.best_cost)
                    logical.append({"logical_index": index, "kind": entry["kind"], "logical_id": entry["logical_id"],
                                    "seed": str(entry["seed"]), "status": decoded.status, "best_cost": decoded.best_cost,
                                    "verified_cost": cost, "work": decoded.work.to_dict(), "termination_reason": decoded.termination_reason,
                                    "search_time_s": decoded.search_time_s,
                                    "history_points": None if decoded.history is None else len(decoded.history)})
            accepted = record["result"]
            tour = reference = None
            if accepted is not None and accepted["best_tour"] is not None:
                tour = np.asarray(accepted["best_tour"], dtype=np.int64)
                reference = validate_solution(instance.instance, tour, accepted["best_cost"])
            # A failed invocation/trial can retain accepted logical prefixes. It
            # still has NO aggregate final tour; never choose a prefix winner.
            result = {"status": "failed" if accepted is None else accepted["status"],
                      "best_cost": None if accepted is None else accepted["best_cost"], "verified_cost": reference,
                      "work": None if accepted is None else accepted["work"],
                      "termination_reason": None if accepted is None else accepted["termination_reason"],
                      "best_logical_index": None if accepted is None else accepted.get("best_logical_index")}
            geometry_revision = hashlib.sha256(encode({"binding": binding, "attempt_id": attempt, "campaign_id": campaign,
                                                        "snapshot_sha256": snapshot_sha, "rules": self._rules})).hexdigest()
            details = {"row": {key: row[key] for key in TrialRow.__annotations__},
                       "campaign_id": campaign, "attempt_id": attempt, "search_id": row["search_id"],
                       "requested_execution_id": row["requested_execution_id"], "execution_id": row["execution_id"],
                       "instance": wire_descriptor(instance.descriptor), "snapshot_sha256": snapshot_sha,
                       "geometry_revision": geometry_revision, "solver_seed": str(search["solver_seed"]),
                       "trial_seed": str(record["requested_plan"]["trial_seed"]),
                       "variant": search["variant"], "parameters": search["parameters"], "budget": search["budget"],
                       "budget_scope": "per-logical-invocation" if record["schema_version"] == 3 else "legacy-nn-batch",
                       "record_history": search["record_history"], "history_policy": search.get("history_policy"),
                       "logical_search_ids": search.get("logical_search_ids"), "logical_results": logical,
                       "result": result, "timings": record["timings"], "error_phase": row["error_phase"],
                       "execution_kind": row["execution_kind"], "native_verified": row["native_verified"]}
            return wire_value(details), instance.instance.coordinates, tour
        except (ValueError, TypeError, KeyError, RecursionError, ArithmeticError):
            raise InterfaceError("invalid_artifact", "Recorded solution failed independent permutation/objective validation.", 422) from None

    def _response(self, material, geometry_revision):
        details, coordinates, tour = material
        if geometry_revision is None:
            response = details
        else:
            if geometry_revision != details["geometry_revision"]:
                raise InterfaceError("snapshot_mismatch", "Geometry binding changed; request current verified details.", 409)
            size = coordinates.nbytes + (0 if tour is None else tour.size * 4)
            if size > GEOMETRY_BYTES_MAX or ((size + 2) // 3) * 4 + 4096 > self.limits.max_response_bytes:
                raise InterfaceError("geometry_too_large", "Canonical geometry exceeds its transport bound.", 413)
            response = {key: details[key] for key in ("campaign_id", "attempt_id", "instance", "snapshot_sha256", "geometry_revision")}
            response.update(coordinates={"encoding": "f64-le-base64", "data": base64.b64encode(coordinates.astype("<f8", copy=False).tobytes(order="C")).decode("ascii")},
                            tour=None if tour is None else {"encoding": "u32-le-base64", "data": base64.b64encode(tour.astype("<u4", copy=False).tobytes()).decode("ascii")})
        if len(encode(response)) + 1024 > self.limits.max_response_bytes:
            raise InterfaceError("resource_limit", "Publication response exceeds its byte bound.", 413)
        self._assert_rules()
        return response

    def job(self, request, *, geometry=False):
        with self.job_analysis(request["job_id"], request["expected_revision"], request["result_revision"]) as (analysis, job):
            if (request["campaign_id"] != job["campaign_id"] or request["instance_snapshot_id"] != job["instance_snapshot_id"]
                    or request["attempt_id"] not in job["attempt_ids"]):
                raise InterfaceError("snapshot_mismatch", "Campaign/attempt/snapshot does not match this exact job.", 409)
            instance, raw, metadata = self.snapshots.read(job["instance_snapshot_id"])
            stored = self.jobs.request(job)["plan"]
            if metadata["sha256"] != stored["public"]["snapshot_sha256"]:
                raise InterfaceError("snapshot_mismatch", "Original snapshot no longer matches the accepted plan.", 409)
            try:
                with self.jobs.directory(job) as directory:
                    if directory.read("instance.json", self.limits.max_instance_bytes) != raw:
                        raise InterfaceError("snapshot_mismatch", "Worker input differs from the original immutable snapshot.", 409)
                # A job uses its one fixed copied input, never a path from a record.
                binding = {key: request[key] for key in ("job_id", "expected_revision", "result_revision", "instance_snapshot_id")}
                material = self._material(analysis, request["attempt_id"], request["campaign_id"], instance, metadata["sha256"], binding,
                                          require_worker_path=True)
                response = self._response(material, request.get("geometry_revision") if geometry else None)
                # Also reject observed input mutation during objective/encoding work.
                if self.snapshots.read(job["instance_snapshot_id"])[1] != raw:
                    raise InterfaceError("snapshot_stale", "Snapshot changed during publication.", 409)
                with self.jobs.directory(job) as directory:
                    if directory.read("instance.json", self.limits.max_instance_bytes) != raw:
                        raise InterfaceError("snapshot_mismatch", "Worker input changed during publication.", 409)
            except OSError:
                raise InterfaceError("snapshot_stale", "Original worker input is missing or changed.", 409) from None
            return response

    def trial(self, request, *, geometry=False):
        from tsp_hpc.problem.instances import instance_from_json
        with self.datasets.analysis_snapshot(request["dataset_id"], request["revision"]) as analysis:
            self._assert_rules()
            source = self.catalog.select_instance_source(request["instance_handle"], request["artifact_catalog_revision"],
                                                         max_bytes=self.limits.max_instance_bytes)
            with source.open() as stream:
                raw = stream.read(self.limits.max_instance_bytes + 1)
            if len(raw) > self.limits.max_instance_bytes:
                raise InterfaceError("geometry_too_large", "Instance exceeds its byte bound.", 413)
            try:
                instance = instance_from_json(raw.decode("utf-8"), max_cities=self.limits.max_cities)
            except (ValueError, TypeError, UnicodeError, RecursionError, ArithmeticError):
                raise InterfaceError("invalid_artifact", "Authorized instance failed canonical validation or limits.", 422) from None
            checksum = hashlib.sha256(encode(instance.to_dict())).hexdigest()
            binding = {key: request[key] for key in ("dataset_id", "revision", "instance_handle", "artifact_catalog_revision")}
            material = self._material(analysis, request["attempt_id"], request["campaign_id"], instance, checksum, binding)
            response = self._response(material, request.get("geometry_revision") if geometry else None)
            self.catalog.assert_revision(request["artifact_catalog_revision"])
            return response
