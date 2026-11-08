from math import exp, isfinite

import numpy as np


def _tour_length(tour, distances):
    n = len(tour)
    return sum(float(distances[tour[i], tour[(i + 1) % n]]) for i in range(n))


def simulated_annealing(
    distances, seed, initial_temperature, cooling_rate, moves_per_temperature, max_moves
):
    """Return an open tour and its closed-tour cost for a symmetric distance matrix."""
    distances = np.asarray(distances, dtype=float)
    if distances.ndim != 2 or distances.shape[0] != distances.shape[1] or len(distances) < 2:
        raise ValueError("distances must be a square matrix for at least two cities")
    if (
        not np.isfinite(distances).all()
        or np.any(distances < 0)
        or not np.allclose(distances, distances.T, rtol=1e-12, atol=1e-12)
        or not np.allclose(np.diag(distances), 0.0, rtol=0.0, atol=1e-12)
    ):
        raise ValueError("distances must be finite, nonnegative, symmetric, and zero-diagonal")
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, (int, np.integer)) or not 0 <= seed < 2**64:
        raise ValueError("seed must be an integer in [0, 2**64)")
    try:
        initial_temperature = float(initial_temperature)
        cooling_rate = float(cooling_rate)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("temperature and cooling rate must be finite numbers") from error
    if not isfinite(initial_temperature) or initial_temperature <= 0:
        raise ValueError("initial_temperature must be finite and positive")
    if not isfinite(cooling_rate) or not 0 < cooling_rate < 1:
        raise ValueError("cooling_rate must be between zero and one")
    if (
        isinstance(moves_per_temperature, (bool, np.bool_))
        or not isinstance(moves_per_temperature, (int, np.integer))
        or moves_per_temperature < 1
    ):
        raise ValueError("moves_per_temperature must be a positive integer")
    if isinstance(max_moves, (bool, np.bool_)) or not isinstance(max_moves, (int, np.integer)) or max_moves < 0:
        raise ValueError("max_moves must be a nonnegative integer")

    rng = np.random.default_rng(seed)
    current = rng.permutation(len(distances))
    current_length = _tour_length(current, distances)
    if not isfinite(current_length):
        raise ValueError("tour cost must be finite")
    best = current.copy()
    best_length = current_length
    temperature = initial_temperature

    for move in range(max_moves):
        i = int(rng.integers(len(current)))
        j = int(rng.integers(len(current) - 1))
        if j >= i:
            j += 1
        candidate = current.copy()
        candidate[i], candidate[j] = candidate[j], candidate[i]
        candidate_length = _tour_length(candidate, distances)
        if not isfinite(candidate_length):
            raise ValueError("tour cost must be finite")
        delta = candidate_length - current_length

        # Uphill moves use the Metropolis acceptance probability.
        accept = delta <= 0
        if not accept and temperature > 0:
            accept = rng.random() < exp(-delta / temperature)
        if accept:
            current = candidate
            current_length = candidate_length
            if current_length < best_length:
                best = current.copy()
                best_length = current_length

        if (move + 1) % moves_per_temperature == 0:
            temperature *= cooling_rate

    return best, float(best_length)


def smart_sa(points, seed=0, initial_temperature=40.0, cooling_rate=0.95,
             moves_per_temperature=200, max_moves=10_000):
    """Accept (n, 2) coordinates and return a closed list of city indices."""
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
        raise ValueError("points must have shape (n, 2) with n >= 2")
    if not np.isfinite(points).all():
        raise ValueError("points must be finite")
    with np.errstate(over="ignore", invalid="ignore"):
        differences = points[:, None, :] - points[None, :, :]
        distances = np.hypot(differences[:, :, 0], differences[:, :, 1])
    tour, _ = simulated_annealing(
        distances, seed, initial_temperature, cooling_rate, moves_per_temperature, max_moves
    )
    route = tour.tolist()
    return route + [route[0]]
