"""Exercise the locked NumPy/Numba combination, not a TSP implementation."""

import numpy as np
from numba import njit


@njit
def _sum_of_squares(values):
    total = 0.0
    for value in values:
        total += value * value
    return total


def test_numba_compiles_and_executes_float64_array_kernel() -> None:
    values = np.arange(5, dtype=np.float64)
    original = values.copy()

    assert _sum_of_squares(values) == 30.0
    assert _sum_of_squares.nopython_signatures
    np.testing.assert_array_equal(values, original)

    # Execute the compiled specialization again, including the empty-input case.
    assert _sum_of_squares(values) == 30.0
    assert _sum_of_squares(np.empty(0, dtype=np.float64)) == 0.0
