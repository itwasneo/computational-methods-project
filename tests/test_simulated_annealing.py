import itertools
import unittest

import numpy as np

from simulated_annealing import simulated_annealing, smart_sa


def tour_length(tour, distances):
    return sum(distances[tour[i], tour[(i + 1) % len(tour)]] for i in range(len(tour)))


class SimulatedAnnealingTests(unittest.TestCase):
    def setUp(self):
        self.points = np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]])
        self.distances = np.linalg.norm(self.points[:, None, :] - self.points[None, :, :], axis=2)

    def test_zero_budget_returns_initial_tour(self):
        initial = np.random.default_rng(7).permutation(4)
        tour, length = simulated_annealing(self.distances, 7, 1., .95, 10, 0)
        np.testing.assert_array_equal(tour, initial)
        self.assertEqual(length, tour_length(initial, self.distances))

    def test_seed_replays_valid_best_tour(self):
        args = (self.distances, 11, 1., .95, 10, 1000)
        tour, length = simulated_annealing(*args)
        again, again_length = simulated_annealing(*args)
        np.testing.assert_array_equal(tour, again)
        self.assertEqual(length, again_length)
        self.assertCountEqual(tour.tolist(), range(4))
        self.assertEqual(length, tour_length(tour, self.distances))
        initial = np.random.default_rng(11).permutation(4)
        self.assertLessEqual(length, tour_length(initial, self.distances))
        optimum = min(tour_length((0,) + p, self.distances) for p in itertools.permutations(range(1, 4)))
        self.assertGreaterEqual(length + 1e-12, optimum)

    def test_two_city_and_zero_cost(self):
        for distance in (0., 3.):
            matrix = np.array([[0., distance], [distance, 0.]])
            for moves in (0, 10):
                tour, length = simulated_annealing(matrix, 2**64 - 1, 1., .95, 3, moves)
                self.assertCountEqual(tour.tolist(), [0, 1])
                self.assertEqual(length, 2 * distance)

    def test_smart_interface_returns_closed_list_without_mutation(self):
        original = self.points.copy()
        route = smart_sa(self.points, seed=4, max_moves=100)
        self.assertIsInstance(route, list)
        self.assertEqual(len(route), 5)
        self.assertEqual(route[0], route[-1])
        self.assertCountEqual(route[:-1], range(4))
        np.testing.assert_array_equal(self.points, original)
        self.assertEqual(route, smart_sa(self.points, seed=4, max_moves=100))

    def test_invalid_inputs(self):
        for points in (np.zeros((1, 2)), np.zeros((2, 3)), [[0, 0], [float('nan'), 1]]):
            with self.assertRaises(ValueError):
                smart_sa(points)
        for args in ((-1, 1., .95, 10, 100), (2**64, 1., .95, 10, 100),
                     (1, 0., .95, 10, 100), (1, 1., 1., 10, 100),
                     (1, 1., .95, 0, 100), (1, 1., .95, 10, -1)):
            with self.assertRaises(ValueError):
                simulated_annealing(self.distances, *args)


if __name__ == "__main__":
    unittest.main()
