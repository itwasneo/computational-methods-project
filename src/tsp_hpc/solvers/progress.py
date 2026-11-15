"""Measured-search clock and bounded observation policy for checked adapters.

Create immediately before algorithm initialization. Call only at the variant's
safe checkpoints. Kernels never call Python per proposal; SA returns in bounded
chunks while retaining its RNG/cooling state. This object consumes no randomness.
"""

from time import perf_counter

from tsp_hpc.solvers.configuration import _real, normalize_history
from tsp_hpc.solvers.results import ProgressPoint


class HistoryLimitError(ValueError):
    """Requested recording exceeded its explicit bound; evidence is not truncated."""


class SearchControl:
    def __init__(self, *, time_limit_s=None, record_history=False, history_policy=None, clock=None):
        self.policy = normalize_history(record_history, history_policy)
        if time_limit_s is not None:
            time_limit_s = _real(time_limit_s, "time_limit_s")
            if time_limit_s <= 0:
                raise ValueError("time_limit_s must be positive")
        self.time_limit_s = time_limit_s
        self._clock = clock or perf_counter
        self._points = []
        self._start = self._clock()

    def elapsed(self):
        return self._clock() - self._start

    def stop_reason(self, *, work_limit_reached=False, algorithm_stop=False, initialized=True):
        # Zero GA/SA work must not skip initialization. NN passes initialized=True.
        if initialized and work_limit_reached:
            return "work_limit"
        if self.time_limit_s is not None and self.elapsed() >= self.time_limit_s:
            return "time_limit"
        if initialized and algorithm_stop:
            return "algorithm_stop"
        return None

    def observe(self, work, best_cost):
        if self.policy is None or best_cost is None:
            return
        elapsed = self.elapsed()
        if self._points and elapsed - self._points[-1].elapsed_s < self.policy["interval_s"]:
            return
        # Reserve a terminal slot, and fail rather than silently lose samples.
        if len(self._points) >= self.policy["max_points"] - 1:
            raise HistoryLimitError("history exceeds max_points; choose a larger bound or sampling interval")
        self._points.append(ProgressPoint(elapsed, work, best_cost))

    def finish(self, work, best_cost):
        if self.policy is not None and best_cost is not None:
            point = ProgressPoint(self.elapsed(), work, best_cost)
            if self._points and self._points[-1].work == work:
                self._points[-1] = point  # Same checkpoint: retain the actual terminal observation.
            else:
                self._points.append(point)
            if len(self._points) > self.policy["max_points"]:
                raise HistoryLimitError("terminal history exceeds max_points")
        return self.elapsed(), None if self.policy is None else tuple(self._points)
