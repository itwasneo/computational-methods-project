"""Additive timed/history planning preserves fixed-work identities and streams."""

import pytest

from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.problem.instances import fixture


@pytest.mark.parametrize("algorithm,work", [("nn", {"start_cities": [2, 0]}),
                                           ("ga", {"max_generations": 0}),
                                           ("sa", {"max_proposals": 0})])
def test_history_and_time_normalization_and_stream_invariance(algorithm, work):
    instance = fixture("square")
    experiment = {"instance_id": instance.descriptor["instance_id"], "algorithm": algorithm,
                  "budget": work, "solver_seed": 2**63 + 7, "repetitions": [0],
                  "executions": [{"backend": "serial", "resources": {"profile": "test"}}]}
    document = {"schema_version": 1, "instances": [instance.descriptor], "experiments": [experiment]}
    original = Manifest(document).expand()[0]
    assert "history_policy" not in original.search
    experiment.update(record_history=True, budget={**work, "time_limit_s": 1})
    timed = Manifest(document).expand()[0]
    assert timed.search_id != original.search_id
    assert timed.trial_seed == original.trial_seed
    assert timed.logical_searches == original.logical_searches
    assert timed.search["history_policy"] == {"interval_s": .1, "max_points": 4096}
    assert timed.search["budget"]["time_limit_s"] == 1.
    experiment["history_policy"] = {"interval_s": .1, "max_points": 4096}
    assert Manifest(document).expand()[0].search_id == timed.search_id
    if algorithm != "nn":
        experiment["budget"] = {"time_limit_s": 1}
        assert Manifest(document).expand()[0].search["budget"]["time_limit_s"] == 1.
