"""Bounded, non-executing preflight over the shared registry and manifest planner."""

from dataclasses import asdict, dataclass
import hashlib
import secrets
import threading
from time import monotonic

from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.runtime import runtime_binding, code_identity
from tsp_hpc.interface.storage import decode, encode


def public_review(private):
    """Project old serial review metadata without rewriting or reauthorizing it."""
    public = decode(encode(private["public"]))
    config = public["configuration"]
    if ("backend" not in config or "threads_per_worker" not in config
            or "estimated_solver_scratch_bytes" not in public):
        executions = [execution for experiment in private["manifest"]["experiments"]
                      for execution in experiment["executions"]]
        if (config.get("backend", "serial") != "serial" or config.get("threads_per_worker", 1) != 1
                or not executions or any(e["backend"] != "serial" or e["resources"]["workers"] != 1
                                 or e["resources"]["threads_per_worker"] != 1 for e in executions)):
            raise InterfaceError("invalid_artifact", "Legacy resource metadata is inconsistent.", 422)
        config.setdefault("backend", "serial")
        config.setdefault("threads_per_worker", 1)
    if "estimated_solver_scratch_bytes" not in public:
        from tsp_hpc.solvers.resources import nn_scratch_bytes
        n = private["manifest"]["instances"][0]["n_cities"]
        public["estimated_solver_scratch_bytes"] = (nn_scratch_bytes(n, len(config["budget"]["start_cities"]), 1)
            if config["algorithm"] == "nn" else config["parameters"].get("population_size", 1) * n * 8 * 6)
    return public


@dataclass(frozen=True)
class ReviewedPlan:
    document: bytes
    expires: float

    def read(self):
        return decode(self.document)


class PreflightStore:
    def __init__(self, authority, snapshots, *, registry=None, clock=monotonic):
        self.authority, self.snapshots, self._registry = authority, snapshots, registry
        self.limits, self._clock = authority.limits, clock
        self._plans, self._lock = {}, threading.RLock()
        self._epoch = secrets.token_hex(16)
        self._code = code_identity()

    @property
    def registry(self):
        if self._registry is None:
            from tsp_hpc.solvers.registry import default_registry
            return default_registry()
        return self._registry

    def _capabilities(self):
        runtime = runtime_binding()
        if runtime["code"] != self._code:
            raise InterfaceError("preflight_stale", "Backend code/capabilities changed; restart before review.", 409)
        authority = self.authority.describe()
        from tsp_hpc.solvers.resources import available_threads
        authority["limits"]["max_solver_threads"] = min(self.limits.max_solver_threads, available_threads())
        descriptions = self.registry.capabilities()
        items = []
        for spec in descriptions:
            reason = ("execution_disabled" if not self.authority.enabled else
                      "platform_unsupported" if not runtime["platform_available"] else
                      "native_disabled" if runtime["compiler"]["config"]["disable_jit"] else None)
            # Distance preparation is native even for explicit non-native test adapters.
            items.append({**spec, "available": reason is None, "unavailable_reason": reason})
        context = {"epoch": self._epoch, "runtime_key": runtime["runtime_key"],
                   "implementations": items, **authority}
        revision = hashlib.sha256(encode(context)).hexdigest()
        return {"capabilities_revision": revision, **authority, "implementations": items,
                "budget_scope": "per-logical-invocation", "worker_processes": 1}, runtime

    def capabilities(self):
        return self._capabilities()[0]

    def _prune(self):
        now = self._clock()
        self._plans = {key: plan for key, plan in self._plans.items() if plan.expires > now}

    def preflight(self, snapshot_id, configuration, capabilities_revision, session_id):
        from tsp_hpc.experiments.manifests import Manifest
        self.authority.require()
        with self._lock:
            self._prune()
            if len(self._plans) >= self.limits.max_preflights:
                raise InterfaceError("resource_limit", "Too many unexpired reviewed plans.", 413)
            caps, runtime = self._capabilities()
            if capabilities_revision != caps["capabilities_revision"]:
                raise InterfaceError("preflight_stale", "Capabilities changed; review a fresh plan.", 409)
            if not runtime["platform_available"] or runtime["compiler"]["config"]["disable_jit"]:
                raise InterfaceError("unsupported_execution", "Native local worker prerequisites are unavailable.", 422)
            record, _, snapshot = self.snapshots.read(snapshot_id)
            config = configuration.model_dump()
            algorithm, budget = config["algorithm"], config["budget"]
            threads = config["threads_per_worker"]
            if threads > caps["limits"]["max_solver_threads"]:
                raise InterfaceError("resource_limit", "Requested threads exceed the CPU/resource limit.", 413)
            try:
                implementations = [self.registry.resolve(algorithm, implementation=name,
                    backend=config["backend"], distance_kind=record.instance.distance_kind, timed="time_limit_s" in budget,
                    record_history=config["record_history"]).implementation for name in config["implementations"]]
            except (ValueError, TypeError):
                raise InterfaceError("unsupported_execution", "Algorithm, implementation, objective or recording is unavailable.", 422) from None
            if len(set(implementations)) != len(implementations):
                raise InterfaceError("invalid_plan", "Default and explicit implementation choices duplicate an execution.", 422)
            logical = budget["start_cities"] if algorithm == "nn" else config["logical_search_ids"]
            invocations = 1 if algorithm == "nn" else len(logical)
            count = len(config["repetitions"]) * len(config["implementations"]) * len(config["distance_modes"])
            # Bounds precede Manifest.expand and any quadratic/native preparation.
            plan_bytes = count * (len(encode(config)) + 1024 + len(logical) * 512)
            matrix = 8 * record.instance.n_cities**2 if "dense" in config["distance_modes"] else 0
            if (len(logical) > self.limits.max_logical_searches or len(config["repetitions"]) > self.limits.max_repetitions
                    or count > self.limits.max_trials or plan_bytes > min(self.limits.max_plan_bytes, self.limits.max_response_bytes)
                    or config["max_matrix_bytes"] > self.limits.max_matrix_bytes or matrix > config["max_matrix_bytes"]):
                raise InterfaceError("resource_limit", "Plan exceeds logical-work, expansion or matrix limits.", 413)
            # Typed wire counts, not approximate JavaScript integers.
            integers = [v for item in (budget, config["parameters"], config["history_policy"] or {}) for v in item.values() if type(v) is int]
            if any(value > 2**53 - 1 for value in integers):
                raise InterfaceError("invalid_plan", "Work/parameter counts must be exactly safe wire integers.", 422)
            population_bytes = (config["parameters"].get("population_size", 1) * record.instance.n_cities * 8 * 6)
            from tsp_hpc.solvers.resources import nn_scratch_bytes
            scratch = (nn_scratch_bytes(record.instance.n_cities, len(logical), threads)
                       if algorithm == "nn" else population_bytes)
            if matrix + scratch > self.limits.max_worker_memory_bytes // 2:
                raise InterfaceError("resource_limit", "Conservative solver-array estimate exceeds worker memory policy.", 413)
            allowance = budget.get("time_limit_s")
            total_allowance = None if allowance is None else allowance * count * invocations
            if total_allowance is not None and total_allowance > self.limits.max_job_seconds:
                raise InterfaceError("resource_limit", "Summed search allowances exceed the separate job time ceiling.", 413)
            points = 0
            if config["record_history"]:
                per_call = config["history_policy"]["max_points"]
                if algorithm == "nn":
                    per_call = min(per_call, len(logical))
                elif "max_generations" in budget:
                    per_call = min(per_call, config["parameters"]["population_size"] + budget["max_generations"])
                elif "max_proposals" in budget:
                    per_call = min(per_call, budget["max_proposals"] + 1)
                points = count * invocations * per_call
            output_estimate = count * (32768 + len(encode(record.provenance)) + invocations * record.instance.n_cities * 24) + points * 512
            if (points > self.limits.max_history_points or output_estimate > self.limits.max_result_bytes
                    or output_estimate // count > self.limits.max_record_bytes):
                raise InterfaceError("resource_limit", "Plan exceeds history/output limits; reduce work or recording bounds.", 413)
            config["implementations"] = implementations
            experiment = {key: config[key] for key in ("algorithm", "parameters", "budget", "repetitions", "record_history")}
            experiment.update(instance_id=record.instance.instance_id, solver_seed=int(config["solver_seed"]))
            if config["record_history"]:
                experiment["history_policy"] = config["history_policy"]
            if algorithm != "nn":
                experiment["logical_search_ids"] = logical
            experiment["executions"] = [{"backend": config["backend"], "implementation": name, "distance_mode": mode,
                                          "resources": {"profile": "local-" + config["backend"], "workers": 1, "threads_per_worker": threads}}
                                         for name in implementations for mode in config["distance_modes"]]
            try:
                manifest = Manifest({"schema_version": 1, "instances": [record.descriptor], "experiments": [experiment]})
                plans = manifest.expand()
            except (ValueError, TypeError, OverflowError):
                raise InterfaceError("invalid_plan", "Plan failed shared scientific validation.", 422) from None
            identifier = secrets.token_hex(16)
            trials = [{"search_id": plan.search_id, "requested_execution_id": plan.execution_id,
                       "implementation": plan.execution["implementation"], "distance_mode": plan.execution["distance_mode"],
                       "repetition": plan.search["repetition"], "trial_seed": str(plan.trial_seed),
                       "invocations": [{**item, "seed": str(item["seed"])} for item in plan.invocations()]} for plan in plans]
            payload = {"preflight_id": identifier, "instance_snapshot_id": snapshot_id,
                       "snapshot_sha256": snapshot["sha256"], "capabilities_revision": capabilities_revision,
                       "configuration": config, "budget_scope": "per-logical-invocation",
                       "trial_count": count, "trials": trials, "estimated_matrix_bytes": matrix,
                       "estimated_solver_scratch_bytes": scratch,
                       "maximum_history_points": points, "estimated_output_bytes": output_estimate,
                       "summed_search_allowance_s": total_allowance, "job_time_ceiling_s": self.limits.max_job_seconds,
                       "valid_for_s": self.limits.preflight_seconds, "output_root": caps["output_root"]}
            if len(encode(payload)) > self.limits.max_response_bytes:
                raise InterfaceError("resource_limit", "Reviewed plan response exceeds its bound.", 413)
            internal = {"public": payload, "manifest": manifest.to_dict(), "runtime": runtime,
                        "session_id": session_id, "limits": asdict(self.limits)}
            encoded = encode(internal)
            if len(encoded) + 256 > min(self.limits.max_plan_bytes, 4 * 1024 * 1024):
                raise InterfaceError("resource_limit", "Private submission exceeds its byte bound.", 413)
            self._plans[identifier] = ReviewedPlan(encoded, self._clock() + self.limits.preflight_seconds)
            return payload

    def validate(self, identifier, session_id):
        self.authority.require()
        with self._lock:
            self._prune()
            plan = self._plans.get(identifier)
            if plan is None:
                raise InterfaceError("preflight_stale", "Reviewed plan expired or belongs to an earlier backend.", 409)
            value = plan.read()
            if session_id != value["session_id"] or self.capabilities()["capabilities_revision"] != value["public"]["capabilities_revision"]:
                raise InterfaceError("preflight_stale", "Session or capabilities changed; review again.", 409)
            _, _, snapshot = self.snapshots.read(value["public"]["instance_snapshot_id"])
            if snapshot["sha256"] != value["public"]["snapshot_sha256"]:
                raise InterfaceError("preflight_stale", "Reviewed instance changed.", 409)
            return value  # Fresh detached copy; queue persists its own immutable submission.
