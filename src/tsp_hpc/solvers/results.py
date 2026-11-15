"""Shared in-memory solver results; persisted envelopes belong to the runner."""

from dataclasses import asdict, dataclass, field
import math
from numbers import Real

import numpy as np

from tsp_hpc.problem.data import validate_tour


@dataclass(frozen=True)
class WorkCounters:
    """Actual completed work, excluding warm-up and final validation."""

    nn_starts_completed: int = 0
    ga_generations_completed: int = 0
    sa_proposals_evaluated: int = 0
    full_tour_evaluations: int = 0

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            if type(value) is not int:
                raise TypeError(f"{name} must be a Python integer, not a boolean")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _nonnegative_real(value: object, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number, not a boolean")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} is outside the float64 range") from error
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return result


@dataclass(frozen=True)
class ProgressPoint:
    """One observed complete incumbent at a safe search checkpoint."""

    elapsed_s: float
    work: WorkCounters
    best_cost: float

    def __post_init__(self):
        if not isinstance(self.work, WorkCounters):
            raise TypeError("progress work must be WorkCounters")
        for name in ("elapsed_s", "best_cost"):
            object.__setattr__(self, name, _nonnegative_real(getattr(self, name), name))

    def to_dict(self):
        return {"elapsed_s": self.elapsed_s, "work": self.work.to_dict(), "best_cost": self.best_cost}


def validate_history(history, work, cost, elapsed):
    if history is None:
        return
    if type(history) is not tuple or any(not isinstance(point, ProgressPoint) for point in history):
        raise ValueError("history must be an immutable tuple of ProgressPoint")
    if cost is None:
        if history:
            raise ValueError("no solution cannot have incumbent history")
        return
    if not history:
        raise ValueError("enabled solution history requires a terminal observation")
    previous = None
    for point in history:
        if point.elapsed_s > elapsed + 1e-9:
            raise ValueError("history exceeds search timing")
        if any(value > work.to_dict()[key] for key, value in point.work.to_dict().items()):
            raise ValueError("history exceeds completed work")
        if previous is not None:
            if point.elapsed_s < previous.elapsed_s or any(
                value < previous.work.to_dict()[key] for key, value in point.work.to_dict().items()
            ) or point.best_cost > previous.best_cost + 1e-12 + 1e-10 * abs(previous.best_cost):
                raise ValueError("history is not monotonic")
        previous = point
    if history[-1].work != work or abs(history[-1].best_cost - cost) > 1e-12 + 1e-10 * abs(cost):
        raise ValueError("terminal history disagrees with result")


@dataclass(frozen=True, eq=False)
class SolverResult:
    """Owned, structurally validated result, not an objective certificate.

    Solvers validate against the actual instance before returning this record.
    Timings are phase observations, not stopping limits or an end-to-end envelope.
    History contains immutable safe-checkpoint observations, not mutable buffers.
    """

    status: str
    best_tour: np.ndarray | None = field(repr=False)
    best_cost: float | None
    work: WorkCounters
    termination_reason: str
    history: tuple[ProgressPoint, ...] | None = None
    search_time_s: float = 0.0
    validation_time_s: float = 0.0

    def __post_init__(self) -> None:
        if type(self.status) is not str or type(self.termination_reason) is not str:
            raise TypeError("status and termination_reason must be strings")
        if self.status not in ("ok", "no_solution"):
            raise ValueError("unsupported result status")
        if self.termination_reason not in ("work_limit", "time_limit", "algorithm_stop"):
            raise ValueError("unsupported termination reason")
        if not isinstance(self.work, WorkCounters):
            raise TypeError("work must be WorkCounters")
        for name in ("search_time_s", "validation_time_s"):
            object.__setattr__(self, name, _nonnegative_real(getattr(self, name), name))
        if self.status == "no_solution":
            if self.best_tour is not None or self.best_cost is not None:
                raise ValueError("no_solution requires null tour and cost")
            if any(self.work.to_dict().values()):
                raise ValueError("no_solution cannot report completed search work")
            validate_history(self.history, self.work, None, self.search_time_s)
            return
        if type(self.best_tour) is not np.ndarray:
            raise TypeError("ok requires a canonical NumPy tour")
        validate_tour(self.best_tour, self.best_tour.size)
        cost = _nonnegative_real(self.best_cost, "best_cost")
        validate_history(self.history, self.work, cost, self.search_time_s)
        tour = self.best_tour.copy(order="C")
        tour.flags.writeable = False
        object.__setattr__(self, "best_tour", tour)
        object.__setattr__(self, "best_cost", cost)
