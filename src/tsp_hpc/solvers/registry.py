"""Explicit, immutable implementation registration; not scientific schema validation.

Importing this module does not import numerical kernels. Only default_registry()
loads the explicitly registered production packages. Test registries are injected,
never installed through filename discovery or advertised as production solvers.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol

from tsp_hpc.solvers.configuration import VARIANTS

if TYPE_CHECKING:
    from tsp_hpc.problem.data import TSPInstance
    from tsp_hpc.problem.distances import PreparedDistances
    from tsp_hpc.solvers.results import SolverResult


class SolverAdapter(Protocol):
    @property
    def native_dispatchers(self) -> tuple[object, ...]: ...

    @property
    def metadata(self) -> dict: ...

    def solve(self, instance: "TSPInstance", *, seed: int, budget: dict,
              parameters: dict, record_history: bool = False,
              history_policy: dict | None = None) -> "SolverResult": ...


@dataclass(frozen=True)
class SolverSpec:
    algorithm: str
    implementation: str
    factory: Callable[["PreparedDistances"], SolverAdapter]
    logical_mode: Literal["batch", "independent"]
    supports_time: bool = False
    supports_history: bool = False
    native: bool = True

    def __post_init__(self):
        if type(self.algorithm) is not str or self.algorithm not in VARIANTS:
            raise ValueError("registration requires an agreed algorithm")
        if type(self.implementation) is not str or not self.implementation.strip():
            raise ValueError("registration requires a non-blank implementation")
        if not callable(self.factory):
            raise TypeError("adapter factory must be callable")
        expected = "batch" if self.algorithm == "nn" else "independent"
        if self.logical_mode != expected:
            raise ValueError("logical mode disagrees with the scientific variant")
        if any(type(value) is not bool for value in (self.supports_time, self.supports_history, self.native)):
            raise TypeError("capabilities must be boolean")

    @property
    def variant(self):
        return VARIANTS[self.algorithm]

    def description(self):
        return {"algorithm": self.algorithm, "variant": self.variant,
                "implementation": self.implementation, "backend": "serial",
                "distance_kinds": ["euclidean_2d"], "distance_modes": ["dense", "on_demand"],
                "logical_mode": self.logical_mode, "supports_time": self.supports_time,
                "supports_history": self.supports_history, "native": self.native}


class SolverRegistry:
    def __init__(self, specs: Iterable[SolverSpec], *, defaults: Mapping[str, str]):
        entries = {}
        for spec in specs:
            if not isinstance(spec, SolverSpec):
                raise TypeError("registry entries must be SolverSpec")
            key = spec.algorithm, spec.implementation
            if key in entries:
                raise ValueError("duplicate implementation registration")
            entries[key] = spec
        resolved = dict(defaults)
        if set(resolved) != {algorithm for algorithm, _ in entries}:
            raise ValueError("each registered algorithm requires one explicit default")
        if any((algorithm, name) not in entries for algorithm, name in resolved.items()):
            raise ValueError("default implementation is not registered")
        self._entries = MappingProxyType(entries)
        self._defaults = MappingProxyType(resolved)

    def resolve(self, algorithm: str, *, implementation: str | None = None,
                backend: str = "serial", distance_kind: str = "euclidean_2d",
                distance_mode: str = "on_demand", timed: bool = False,
                record_history: bool = False) -> SolverSpec:
        if backend != "serial":
            raise ValueError("runner supports only serial execution")
        if distance_kind != "euclidean_2d" or distance_mode not in ("dense", "on_demand"):
            raise ValueError("unsupported execution distance convention/representation")
        if type(algorithm) is not str or algorithm not in self._defaults:
            raise ValueError(f"unavailable algorithm: {algorithm!r}")
        if implementation is not None and type(implementation) is not str:
            raise TypeError("implementation must be a string")
        name = self._defaults[algorithm] if implementation is None else implementation
        try:
            spec = self._entries[algorithm, name]
        except KeyError:
            raise ValueError(f"unavailable {algorithm} implementation: {name!r}") from None
        if timed and not spec.supports_time:
            raise ValueError("implementation does not support time budgets")
        if record_history and not spec.supports_history:
            raise ValueError("implementation does not support progress histories")
        return spec

    def capabilities(self):
        return [{**spec.description(), "default": self._defaults[spec.algorithm] == spec.implementation}
                for spec in self._entries.values()]


def validate_adapter_metadata(solver, prepared, spec):
    """Validate the adapter boundary without importing numerical code on import."""
    from tsp_hpc.reproducibility.identity import canonical_json
    metadata = solver.metadata
    expected = {**prepared.metadata, "algorithm": spec.algorithm, "variant": spec.variant,
                "implementation": spec.implementation, "backend": "serial"}
    if type(metadata) is not dict or metadata.keys() != expected.keys() | {"solver_warmup_time_s"}:
        raise ValueError("adapter must report the shared preparation/warm-up metadata")
    if canonical_json({key: metadata[key] for key in expected}) != canonical_json(expected):
        raise ValueError("adapter metadata disagrees with its registration/preparation")
    duration = metadata["solver_warmup_time_s"]
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        raise ValueError("invalid adapter warm-up duration")
    return dict(metadata)


@lru_cache(maxsize=1)
def default_registry() -> SolverRegistry:
    from tsp_hpc.solvers.ga.solver import solver_spec as ga_solver_spec
    from tsp_hpc.solvers.nn.solver import DEFAULT_IMPLEMENTATION as NN_DEFAULT, solver_specs

    ga = ga_solver_spec()
    return SolverRegistry((*solver_specs(), ga), defaults={"nn": NN_DEFAULT, "ga": ga.implementation})
