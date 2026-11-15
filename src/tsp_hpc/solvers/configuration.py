"""Scientific configuration shared by manifests and solver adapters.

These rules describe supported variants, independently of which implementations
are installed. Historical records must not depend on today's execution registry.
"""

import math

VARIANTS = {
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


def _object(value, required, optional, name):
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise TypeError(f"{name} must be a JSON object with string keys")
    missing, unknown = required - value.keys(), value.keys() - required - optional
    if missing or unknown:
        raise ValueError(f"{name}: missing fields {sorted(missing)}, unknown fields {sorted(unknown)}")
    return value


def _integer(value, minimum, name):
    if type(value) is not int:
        raise TypeError(f"{name} must be a Python integer, not a boolean")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _real(value, name):
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a JSON number, not a boolean")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} is outside the float64 range") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return 0.0 if result == 0 else result


def normalize_parameters(algorithm, value):
    defaults = {"nn": {}, "ga": _GA_DEFAULTS, "sa": _SA_DEFAULTS}[algorithm]
    result = {**defaults, **_object(value, set(), set(defaults), "parameters")}
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


def normalize_budget(algorithm, value, n_cities):
    name = {"nn": "start_cities", "ga": "max_generations", "sa": "max_proposals"}[algorithm]
    optional = {"time_limit_s"}
    if algorithm == "sa":
        optional.add("time_check_interval_proposals")
    required = {name} if algorithm == "nn" else set()
    item = _object(value, required, optional | ({name} - required), "budget")
    if name not in item and "time_limit_s" not in item:
        raise ValueError("at least one stopping limit is required")
    result = {}
    if name in item:
        if algorithm != "nn":
            result[name] = _integer(item[name], 0, name)
        else:
            if type(item[name]) is not list:
                raise TypeError("start_cities must be a JSON array")
            starts = [_integer(city, 0, "start city") for city in item[name]]
            if any(city >= n_cities for city in starts):
                raise ValueError("start city is out of range")
            if len(set(starts)) != len(starts):
                raise ValueError("start_cities contains duplicate logical IDs")
            result[name] = starts
    if "time_limit_s" in item:
        seconds = _real(item["time_limit_s"], "time_limit_s")
        if seconds <= 0:
            raise ValueError("time_limit_s must be positive")
        result["time_limit_s"] = seconds
        if algorithm == "sa":
            result["time_check_interval_proposals"] = _integer(
                item.get("time_check_interval_proposals", 256), 1, "time_check_interval_proposals")
    elif "time_check_interval_proposals" in item:
        raise ValueError("time checkpoints require a time limit")
    return result


def normalize_history(record_history, policy=None):
    """Resolved safe-checkpoint sampling; finite bounds, never silent truncation."""
    if type(record_history) is not bool:
        raise TypeError("record_history must be boolean")
    if not record_history:
        if policy is not None:
            raise ValueError("history_policy requires record_history")
        return None
    item = {} if policy is None else _object(
        policy, set(), {"interval_s", "max_points"}, "history_policy")
    interval = _real(item.get("interval_s", 0.1), "history interval_s")
    if interval < 0:
        raise ValueError("history interval_s must be non-negative")
    return {"interval_s": interval, "max_points": _integer(item.get("max_points", 4096), 2, "max_points")}
