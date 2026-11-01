import unittest

import numpy as np

from benchmarks.smart_suite import checked_solver, run_suite
from simulated_annealing import smart_sa


class SmartSuiteTests(unittest.TestCase):
    def test_real_benchmark_runs_and_replays_instances(self):
        scenarios = (("random", 5, 123), ("cluster", 6, 124))
        np.random.seed(2026)
        before = np.random.get_state()
        first = run_suite(smart_sa, "SA", {"max_moves": 20}, scenarios, (1, 2))
        after = np.random.get_state()
        self.assertEqual(before[0], after[0])
        np.testing.assert_array_equal(before[1], after[1])
        self.assertEqual(before[2:], after[2:])
        second = run_suite(smart_sa, "SA", {"max_moves": 20}, scenarios, (1, 2))
        self.assertEqual(len(first["records"]), 4)
        for a, b in zip(first["records"], second["records"]):
            self.assertEqual(a["instance_sha256"], b["instance_sha256"])
            self.assertEqual(a["route"], b["route"])
            self.assertEqual(a["length"], b["length"])
        self.assertEqual(first["records"][0]["instance_sha256"], first["records"][1]["instance_sha256"])

    def test_invalid_routes_fail_before_scoring(self):
        points = np.zeros((3, 2))
        for route in ([0, 1, 2], [0, 1, 1, 0], [0, 1, 3, 0], [0, 1, 2, 1], [0., 1, 2, 0]):
            with self.subTest(route=route), self.assertRaises(ValueError):
                checked_solver(lambda points, **params: route)(points, seed=1)

    def test_solver_cannot_mutate_benchmark_points(self):
        points = np.array([[0., 0.], [1., 0.]])
        def mutating_solver(points, **params):
            points[:] = 0
            return [0, 1, 0]
        self.assertEqual(checked_solver(mutating_solver)(points, seed=1), [0, 1, 0])
        np.testing.assert_array_equal(points, [[0., 0.], [1., 0.]])

    def test_seed_is_reserved(self):
        with self.assertRaises(ValueError):
            run_suite(smart_sa, "SA", {"seed": 4}, (("random", 4, 123),), (1,))

    def test_two_solver_adapters_receive_identical_instances(self):
        def fixed_route(points, seed):
            return list(range(len(points))) + [0]
        scenarios = (("random", 5, 123), ("cluster", 6, 124))
        first = run_suite(smart_sa, "SA", {"max_moves": 20}, scenarios, (1,))
        second = run_suite(fixed_route, "fixed", {}, scenarios, (1,))
        self.assertEqual(
            [r["instance_sha256"] for r in first["records"]],
            [r["instance_sha256"] for r in second["records"]],
        )


if __name__ == "__main__":
    unittest.main()
