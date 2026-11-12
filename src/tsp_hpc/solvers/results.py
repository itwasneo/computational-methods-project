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


@dataclass(frozen=True, eq=False)
class SolverResult:
    """Owned, structurally validated result, not an objective certificate.

    Solvers validate against the actual instance before returning this record.
    Timings are phase observations, not stopping limits or an end-to-end envelope.
    History is currently disabled; no mutable search buffers are retained.
    """

    status: str
    best_tour: np.ndarray | None = field(repr=False)
    best_cost: float | None
    work: WorkCounters
    termination_reason: str
    history: None = None
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
        if self.history is not None:
            raise ValueError("history recording is not implemented")
        for name in ("search_time_s", "validation_time_s"):
            object.__setattr__(self, name, _nonnegative_real(getattr(self, name), name))
        if self.status == "no_solution":
            if self.best_tour is not None or self.best_cost is not None:
                raise ValueError("no_solution requires null tour and cost")
            if any(self.work.to_dict().values()):
                raise ValueError("no_solution cannot report completed search work")
            return
        if type(self.best_tour) is not np.ndarray:
            raise TypeError("ok requires a canonical NumPy tour")
        validate_tour(self.best_tour, self.best_tour.size)
        cost = _nonnegative_real(self.best_cost, "best_cost")
        tour = self.best_tour.copy(order="C")
        tour.flags.writeable = False
        object.__setattr__(self, "best_tour", tour)
        object.__setattr__(self, "best_cost", cost)
