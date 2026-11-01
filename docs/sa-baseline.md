# Swap-based simulated annealing baseline

`simulated_annealing.py` implements a manual, single-chain swap-neighborhood baseline with full-tour rescoring, Metropolis acceptance, and geometric cooling after a configurable number of proposals. It uses NumPy for arrays and seeded random numbers, not a TSP solver. This is an older-style baseline inspired by the conventional swap-based comparison in [Bayram and Şahin (2013)](https://doi.org/10.3390/mca18030313); it is not a verified reproduction of the paper.

## Interfaces

The internal `simulated_annealing(distances, seed, initial_temperature, cooling_rate, moves_per_temperature, max_moves)` accepts a symmetric distance matrix and returns `(tour, cost)`. The tour is an open NumPy permutation; its cost includes the closing edge.

For the Smart TSP Benchmark API, `smart_sa(points, seed=0, initial_temperature=40.0, cooling_rate=0.95, moves_per_temperature=200, max_moves=10000)` accepts finite two-dimensional coordinates and returns a Python list with the starting city repeated at the end. It uses unrounded Euclidean distances. Coordinate-to-matrix conversion is included in the function's execution time. Example:

```python
from simulated_annealing import smart_sa

route = smart_sa([(0, 0), (1, 0), (1, 1), (0, 1)], seed=1)
```

The Smart-compatible adapter is separate from the team's merged logical `solve(instance, ...)` interface; a common solver-result wrapper, work counters, and TSPLIB distance convention remain team integration work. PR #49's proposed reversal-based SA is a different, unmerged variant. A locally observed speedup of a separate optimized implementation is not part of this baseline.

Run tests from the project root with `python -m unittest discover -s tests -v` after installing NumPy.
