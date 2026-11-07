"""Strict fixed-work CPU manifests and deterministic, non-executing trial plans."""

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re

from tsp_hpc.identity import canonical_json, stable_id
from tsp_hpc.random_streams import BIT_GENERATOR, SEED_SCHEME, RandomStream, derive_stream, validate_seed

_VARIANTS = {
    "nn": "nn-fixed-starts-v1",
    "ga": "ga-tournament-ox-swap-elitist-v1",
    "sa": "sa-reversal-geometric-v1",
}
_GA_DEFAULTS = {
    "population_size": 128, "tournament_size": 3, "elite_count": 1,
    "crossover_probability": 0.9, "mutation_probability": 0.2,
}
_SA_DEFAULTS = {
    "initial_temperature": 1.0, "cooling_factor": 0.999,
    "minimum_temperature": 1e-12, "cost_check_interval": 1024,
}


def _object(value: object, required: set[str], optional: set[str], name: str) -> dict:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise TypeError(f"{name} must be a JSON object with string keys")
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing or unknown:
        raise ValueError(f"{name}: missing fields {sorted(missing)}, unknown fields {sorted(unknown)}")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _integer(value: object, minimum: int, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be a Python integer, not a boolean")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _real(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a JSON number, not a boolean")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} is outside the float64 range") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return 0.0 if result == 0 else result


def _list(value: object, name: str, *, empty: bool = False) -> list:
    if type(value) is not list:
        raise TypeError(f"{name} must be a JSON array")
    if not empty and not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _unique(values: list, name: str) -> list:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate logical IDs")
    return values


def _instance(value: object) -> dict:
    item = _object(value, {"instance_id", "coordinate_checksum", "n_cities", "distance_kind"}, {"instance_seed"}, "instance")
    identifier = _text(item["instance_id"], "instance_id")
    checksum = _text(item["coordinate_checksum"], "coordinate_checksum")
    if re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
        raise ValueError("coordinate_checksum must be a lowercase SHA-256 hex digest")
    n_cities = _integer(item["n_cities"], 2, "n_cities")
    kind = _text(item["distance_kind"], "distance_kind")
    if kind not in ("euclidean_2d", "tsplib_euc_2d"):
        raise ValueError("unsupported distance_kind")
    seed = item.get("instance_seed")
    if seed is not None:
        validate_seed(seed)
    return dict(instance_id=identifier, coordinate_checksum=checksum, n_cities=n_cities,
                distance_kind=kind, instance_seed=seed)


def _parameters(algorithm: str, value: object) -> dict:
    defaults = {"nn": {}, "ga": _GA_DEFAULTS, "sa": _SA_DEFAULTS}[algorithm]
    parameters = _object(value, set(), set(defaults), "parameters")
    result = {**defaults, **parameters}
    if algorithm == "ga":
        for name, minimum in (("population_size", 2), ("tournament_size", 1), ("elite_count", 0)):
            result[name] = _integer(result[name], minimum, name)
        if result["elite_count"] >= result["population_size"]:
            raise ValueError("elite_count must be smaller than population_size")
        for name in ("crossover_probability", "mutation_probability"):
            result[name] = _real(result[name], name)
            if not 0 <= result[name] <= 1:
                raise ValueError(f"{name} must lie in [0, 1]")
    elif algorithm == "sa":
        for name in ("initial_temperature", "cooling_factor", "minimum_temperature"):
            result[name] = _real(result[name], name)
            if result[name] <= 0:
                raise ValueError(f"{name} must be positive")
        if result["cooling_factor"] > 1:
            raise ValueError("cooling_factor must be <= 1")
        if result["minimum_temperature"] > result["initial_temperature"]:
            raise ValueError("minimum_temperature must not exceed initial_temperature")
        result["cost_check_interval"] = _integer(result["cost_check_interval"], 1, "cost_check_interval")
    return result


def _budget(algorithm: str, value: object, n_cities: int) -> dict:
    name = {"nn": "start_cities", "ga": "max_generations", "sa": "max_proposals"}[algorithm]
    item = _object(value, {name}, set(), "fixed-work budget")
    if algorithm == "nn":
        starts = [_integer(city, 0, "start city") for city in _list(item[name], name, empty=True)]
        if any(city >= n_cities for city in starts):
            raise ValueError("start city is out of range")
        return {name: _unique(starts, name)}
    return {name: _integer(item[name], 0, name)}


def _execution(value: object) -> dict:
    item = _object(value, {"backend", "resources"}, {"distance_mode"}, "execution")
    backend = _text(item["backend"], "backend")
    if backend not in ("serial", "multicore"):
        raise ValueError("v1 plans only serial/multicore CPU execution")
    mode = _text(item.get("distance_mode", "on_demand"), "distance_mode")
    if mode not in ("dense", "on_demand"):
        raise ValueError("unsupported distance_mode")
    resources = _object(item["resources"], {"profile"}, {"workers", "threads_per_worker"}, "resources")
    profile = _text(resources["profile"], "resource profile")
    workers = _integer(resources.get("workers", 1), 1, "workers")
    threads = _integer(resources.get("threads_per_worker", 1), 1, "threads_per_worker")
    if backend == "serial" and (workers != 1 or threads != 1):
        raise ValueError("serial execution requires one worker and one thread")
    return dict(backend=backend, distance_mode=mode,
                resources=dict(profile=profile, workers=workers, threads_per_worker=threads))


def _experiment(value: object, instances: dict[str, dict]) -> dict:
    item = _object(value, {"instance_id", "algorithm", "budget", "solver_seed", "repetitions", "executions"},
                   {"variant", "parameters", "logical_search_ids", "record_history"}, "experiment")
    identifier = _text(item["instance_id"], "instance_id")
    if identifier not in instances:
        raise ValueError(f"unknown instance_id: {identifier!r}")
    algorithm = _text(item["algorithm"], "algorithm")
    if algorithm not in _VARIANTS:
        raise ValueError("algorithm must be nn, ga, or sa")
    variant = _text(item.get("variant", _VARIANTS[algorithm]), "variant")
    if variant != _VARIANTS[algorithm]:
        raise ValueError("only the agreed v1 baseline variant is supported")
    validate_seed(item["solver_seed"])
    repetitions = [_integer(rep, 0, "repetition") for rep in _list(item["repetitions"], "repetitions")]
    _unique(repetitions, "repetitions")
    history = item.get("record_history", False)
    if type(history) is not bool:
        raise TypeError("record_history must be boolean")
    if history:
        raise ValueError("history is not supported until the time/history extension")
    result = dict(
        instance_id=identifier, algorithm=algorithm, variant=variant,
        parameters=_parameters(algorithm, item.get("parameters", {})),
        budget=_budget(algorithm, item["budget"], instances[identifier]["n_cities"]),
        solver_seed=item["solver_seed"], repetitions=repetitions, record_history=False,
        executions=[_execution(entry) for entry in _list(item["executions"], "executions")],
    )
    if algorithm == "nn":
        if "logical_search_ids" in item:
            raise ValueError("NN logical searches are defined by budget.start_cities")
    else:
        ids = [_text(identifier, "logical search ID") for identifier in
               _list(item.get("logical_search_ids", ["0"]), "logical_search_ids")]
        result["logical_search_ids"] = _unique(ids, "logical_search_ids")
    return result


def _normalize(document: object) -> dict:
    root = _object(document, {"schema_version", "instances", "experiments"}, set(), "manifest")
    if _integer(root["schema_version"], 1, "schema_version") != 1:
        raise ValueError("unsupported manifest schema_version")
    instances = [_instance(item) for item in _list(root["instances"], "instances")]
    _unique([item["instance_id"] for item in instances], "instance IDs")
    by_id = {item["instance_id"]: item for item in instances}
    experiments = [_experiment(item, by_id) for item in _list(root["experiments"], "experiments")]
    return dict(schema_version=1, instances=instances, experiments=experiments)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


@dataclass(frozen=True)
class LogicalSearch:
    """One start, chain or independent population in fixed logical order."""

    kind: str
    logical_id: str | int
    stream: RandomStream


@dataclass(frozen=True)
class TrialPlan:
    """Read-only plan produced by Manifest.expand(), not a solver execution."""

    search_id: str
    execution_id: str
    trial_seed: int
    logical_searches: tuple[LogicalSearch, ...]
    _search_json: str = field(repr=False)
    _execution_json: str = field(repr=False)

    @property
    def search(self) -> dict:
        """Return a detached snapshot of the resolved scientific configuration."""
        return json.loads(self._search_json)

    @property
    def execution(self) -> dict:
        """Return a detached snapshot of the requested execution configuration."""
        return json.loads(self._execution_json)

    def stream(self, role: str, *logical_ids: str | int) -> RandomStream:
        """Plan another role-specific stream, e.g. warm-up or a future island."""
        return derive_stream(self.trial_seed, role, *logical_ids)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1, "search_id": self.search_id, "execution_id": self.execution_id,
            "trial_seed": self.trial_seed, "search": self.search, "execution": self.execution,
            "logical_searches": [
                {"kind": item.kind, "logical_id": item.logical_id,
                 "stream_id": item.stream.stream_id, "seed": item.stream.seed}
                for item in self.logical_searches
            ],
        }


@dataclass(frozen=True, init=False)
class Manifest:
    """Validated JSON snapshot; no caller-owned dictionaries are retained."""

    _json: str = field(repr=False)

    def __init__(self, document: dict):
        object.__setattr__(self, "_json", canonical_json(_normalize(document)))

    @classmethod
    def from_json(cls, text: str) -> "Manifest":
        return cls(json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant))

    @classmethod
    def from_file(cls, path: str | Path) -> "Manifest":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def to_dict(self) -> dict:
        return json.loads(self._json)

    def expand(self) -> tuple[TrialPlan, ...]:
        """Expand in declared experiment/repetition/execution order; reject duplicates.

        Order controls presentation, never IDs or random streams. Logical search
        order (including NN starts) is scientific tie-breaking input and is retained.
        V1 materializes the plans; it does not schedule or dispatch any of them.
        """
        document = self.to_dict()
        instances = {item["instance_id"]: item for item in document["instances"]}
        plans = []
        seen = set()
        for experiment in document["experiments"]:
            instance = instances[experiment["instance_id"]]
            # Generation provenance is not a source of solver randomness.
            seed_instance = {key: value for key, value in instance.items() if key != "instance_seed"}
            for repetition in experiment["repetitions"]:
                search = {key: value for key, value in experiment.items()
                          if key not in ("executions", "repetitions", "instance_id")}
                search.update(instance=instance, repetition=repetition,
                              seed_scheme=SEED_SCHEME, bit_generator=BIT_GENERATOR)
                search_id = stable_id("search", search)
                trial_seed = derive_stream(experiment["solver_seed"], "solver",
                                            stable_id("instance-reference", seed_instance),
                                            experiment["variant"], repetition).seed
                algorithm = experiment["algorithm"]
                kind = {"nn": "start", "ga": "population", "sa": "chain"}[algorithm]
                ids = experiment["budget"]["start_cities"] if algorithm == "nn" else experiment["logical_search_ids"]
                logical = tuple(LogicalSearch(kind, identifier, derive_stream(trial_seed, kind, identifier))
                                for identifier in ids)
                if len({item.stream.seed for item in logical}) != len(logical):
                    raise ValueError("derived uint64 seed collision within a logical search collection")
                for execution in experiment["executions"]:
                    execution_id = stable_id("execution", {"search_id": search_id, "execution": execution})
                    if execution_id in seen:
                        raise ValueError("duplicate expanded execution; use distinct repetition IDs")
                    seen.add(execution_id)
                    plans.append(TrialPlan(search_id, execution_id, trial_seed, logical,
                                           canonical_json(search), canonical_json(execution)))
        return tuple(plans)
