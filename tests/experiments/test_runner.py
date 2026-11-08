"""Manifest-to-shard execution, identity resolution and failure boundaries."""

import json
import random
import subprocess
import sys

import numpy as np
import pytest

from tsp_hpc.problem.instances import fixture, save_instance, uniform_instance
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.reproducibility.identity import stable_id
from tsp_hpc.problem.objective import validate_solution
from tsp_hpc.solvers.results import SolverResult
import tsp_hpc.experiments.runner as runner


def campaign(tmp_path, *, record=None, executions=None, repetitions=None, starts=None):
    record = record or fixture("square")
    source = tmp_path / "input.json"
    save_instance(record, source)
    manifest = Manifest({
        "schema_version": 1, "instances": [record.descriptor], "experiments": [{
            "instance_id": record.instance.instance_id, "algorithm": "nn", "solver_seed": 42,
            "budget": {"start_cities": [2, 0] if starts is None else starts},
            "repetitions": [0] if repetitions is None else repetitions,
            "executions": executions or [{"backend": "serial", "resources": {"profile": "local-cpu"}}],
        }],
    })
    return record, manifest, {record.instance.instance_id: source}, tmp_path / "results.jsonl"


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_source_provenance_tracks_package_checkout_not_callers_directory(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    source = checkout / "src/tsp_hpc/experiments/runner.py"
    source.parent.mkdir(parents=True)
    source.write_text("# provenance fixture\n")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(checkout), *args], text=True, stderr=subprocess.DEVNULL,
        ).strip()

    git("init", "--quiet")
    git("add", "src")
    git("-c", "user.name=Provenance Test", "-c", "user.email=test@example.invalid",
        "commit", "--quiet", "--no-gpg-sign", "-m", "fixture")
    revision = git("rev-parse", "HEAD")
    monkeypatch.setattr(runner, "__file__", str(source))
    assert runner._source_state() == {"revision": revision, "dirty": False}
    source.write_text("# changed provenance fixture\n")
    assert runner._source_state() == {"revision": revision, "dirty": True}

    monkeypatch.chdir(checkout)
    installed = tmp_path / "installed/lib/python3.12/site-packages/tsp_hpc/experiments/runner.py"
    monkeypatch.setattr(runner, "__file__", str(installed))
    assert runner._source_state() == {"revision": None, "dirty": None}


@pytest.mark.parametrize("implementation", runner.nn.implementation_names())
@pytest.mark.parametrize("mode", ["dense", "on_demand"])
def test_generate_run_validate_save(tmp_path, implementation, mode):
    record, manifest, bindings, output = campaign(
        tmp_path, record=uniform_instance(12, seed=7),
        executions=[{"backend": "serial", "distance_mode": mode, "implementation": implementation,
                     "resources": {"profile": "local-cpu"}}],
    )
    before = manifest.to_dict()
    summary = runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    trial, saved_summary = records(output)
    assert summary == saved_summary and summary["counts"] == {"completed": 1, "failed": 0}
    assert trial["requested_plan"] == manifest.expand()[0].to_dict()
    assert trial["execution_id"] == manifest.expand()[0].execution_id
    assert trial["adapter"]["implementation"] == implementation
    assert trial["input"]["descriptor"] == record.descriptor
    assert trial["input"]["provenance"] == record.provenance
    assert trial["runtime"]["versions"]["numpy"] == np.__version__
    result = trial["result"]
    validate_solution(record.instance, np.array(result["best_tour"], dtype=np.int64), result["best_cost"])
    assert result["work"]["nn_starts_completed"] == result["work"]["full_tour_evaluations"] == 2
    assert trial["error"] is None and trial["outcome"] == "completed"
    assert all(value >= 0 for value in trial["timings"].values())
    assert summary["timings"]["end_to_end_time_s"] >= trial["timings"]["pre_persistence_time_s"]
    assert summary["timings"]["trial_persistence_time_s"] > 0
    receipt = summary["trial_timings"][0]
    assert receipt["attempt_id"] == trial["attempt_id"]
    assert receipt["end_to_end_time_s"] >= trial["timings"]["pre_persistence_time_s"] + receipt["persistence_time_s"]
    assert manifest.to_dict() == before


def test_multiple_inputs_follow_plan_order_not_binding_order(tmp_path):
    square, manifest, bindings, output = campaign(tmp_path)
    triangle = fixture("triangle")
    source = tmp_path / "triangle.json"
    save_instance(triangle, source)
    bindings[triangle.instance.instance_id] = source
    document = manifest.to_dict()
    document["instances"].append(triangle.descriptor)
    document["experiments"].insert(0, {
        **document["experiments"][0], "instance_id": triangle.instance.instance_id,
    })
    runner.run_manifest(Manifest(document), instance_paths=bindings, output_path=output)
    trials = records(output)[:-1]
    assert [trial["input"]["descriptor"] for trial in trials] == [triangle.descriptor, square.descriptor]
    assert [trial["result"]["best_cost"] for trial in trials] == [12., 4.]


def test_resolved_default_identity_and_fresh_attempts(tmp_path):
    _, manifest, bindings, output = campaign(tmp_path)
    requested = manifest.expand()[0]
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    first = records(output)[0]
    assert first["effective_execution"]["implementation"] == runner.nn.DEFAULT_IMPLEMENTATION
    assert first["execution_id"] != requested.execution_id
    assert first["execution_id"] == stable_id("execution", {
        "search_id": requested.search_id, "execution": first["effective_execution"],
    })
    other = tmp_path / "again.jsonl"
    runner.run_manifest(manifest, instance_paths=bindings, output_path=other)
    second = records(other)[0]
    assert first["result"] == second["result"]
    assert first["execution_id"] == second["execution_id"]
    assert first["attempt_id"] != second["attempt_id"]
    assert first["campaign_id"] != second["campaign_id"]


def test_effective_collision_rejected_before_output_or_setup(tmp_path, monkeypatch):
    base = {"backend": "serial", "resources": {"profile": "local-cpu"}}
    _, manifest, bindings, output = campaign(tmp_path, executions=[
        base, {**base, "implementation": runner.nn.DEFAULT_IMPLEMENTATION},
    ])
    monkeypatch.setattr(runner, "prepare_distances", lambda *a, **k: pytest.fail("unexpected preparation"))
    with pytest.raises(ValueError, match="duplicate effective"):
        runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    assert not output.exists()


def test_trial_setup_is_fresh_and_seeded_only_after_warmup(tmp_path, monkeypatch):
    _, manifest, bindings, output = campaign(tmp_path, repetitions=[8, 2])
    events = []
    prepare = runner.prepare_distances
    warm = runner.nn.warmup_nearest_neighbour
    solve = runner.nn.NearestNeighbourSolver.solve
    def observe_prepare(*args, **kwargs):
        events.append("prepare")
        return prepare(*args, **kwargs)
    def observe_warm(**kwargs):
        events.append("warm")
        return warm(**kwargs)
    def observe_solve(self, instance, **kwargs):
        events.append(kwargs["seed"])
        return solve(self, instance, **kwargs)
    monkeypatch.setattr(runner, "prepare_distances", observe_prepare)
    monkeypatch.setattr(runner.nn, "warmup_nearest_neighbour", observe_warm)
    monkeypatch.setattr(runner.nn.NearestNeighbourSolver, "solve", observe_solve)
    python_state, numpy_state = random.getstate(), np.random.get_state()
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    expected = []
    for plan in manifest.expand():
        expected.extend(["prepare", "warm", plan.trial_seed])
    assert events == expected
    assert random.getstate() == python_state
    for before, after in zip(numpy_state, np.random.get_state()):
        np.testing.assert_array_equal(before, after)


@pytest.mark.parametrize("unsupported", ["ga", "sa", "multicore", "implementation", "rounded"])
def test_unavailable_capabilities_are_failure_records_without_setup(tmp_path, monkeypatch, unsupported):
    record = fixture("square", distance_kind="tsplib_euc_2d") if unsupported == "rounded" else None
    _, manifest, bindings, output = campaign(tmp_path, record=record)
    document = manifest.to_dict()
    experiment = document["experiments"][0]
    if unsupported in ("ga", "sa"):
        experiment["algorithm"] = unsupported
        experiment.pop("variant")
        experiment["budget"] = {"max_generations" if unsupported == "ga" else "max_proposals": 0}
    elif unsupported == "multicore":
        experiment["executions"][0]["backend"] = "multicore"
    elif unsupported == "implementation":
        experiment["executions"][0]["implementation"] = "__unavailable__"
    monkeypatch.setattr(runner, "load_instance", lambda *a: pytest.fail("unexpected load"))
    runner.run_manifest(Manifest(document), instance_paths=bindings, output_path=output)
    trial, summary = records(output)
    assert trial["error"]["phase"] == "capability"
    assert trial["effective_execution"] is trial["execution_id"] is trial["result"] is None
    assert summary["counts"] == {"completed": 0, "failed": 1}


@pytest.mark.parametrize("failure", ["missing_binding", "missing_file", "corrupt", "descriptor", "seed"])
def test_input_failures_are_recorded_before_preparation(tmp_path, monkeypatch, failure):
    _, manifest, bindings, output = campaign(tmp_path, record=uniform_instance(4, seed=7))
    source = next(iter(bindings.values()))
    if failure == "missing_binding":
        bindings = {}
    elif failure == "missing_file":
        source.unlink()
    elif failure == "corrupt":
        source.write_text('{"schema_version":NaN}')
    else:
        doc = manifest.to_dict()
        doc["instances"][0]["instance_seed" if failure == "seed" else "coordinate_checksum"] = (
            8 if failure == "seed" else "0" * 64)
        manifest = Manifest(doc)
    monkeypatch.setattr(runner, "prepare_distances", lambda *a, **k: pytest.fail("unexpected preparation"))
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    trial = records(output)[0]
    assert trial["error"]["phase"] == "input" and trial["result"] is None


def test_memory_ceiling_failure_does_not_fall_back(tmp_path):
    _, manifest, bindings, output = campaign(tmp_path, executions=[{
        "backend": "serial", "distance_mode": "dense", "resources": {"profile": "local-cpu"}}])
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output, max_matrix_bytes=0)
    trial = records(output)[0]
    assert trial["error"]["type"] == "MemoryError"
    assert trial["error"]["phase"] == "distance_preparation"
    assert trial["effective_execution"]["distance_mode"] == "dense"


def test_runner_independently_validates_and_continues_after_failure(tmp_path, monkeypatch):
    _, manifest, bindings, output = campaign(tmp_path, repetitions=[0, 1])
    solve = runner.nn.NearestNeighbourSolver.solve
    calls = []
    def corrupt_first(self, *args, **kwargs):
        result = solve(self, *args, **kwargs)
        calls.append(1)
        return result if len(calls) > 1 else SolverResult(
            "ok", result.best_tour, result.best_cost + 1, result.work, "work_limit")
    monkeypatch.setattr(runner.nn.NearestNeighbourSolver, "solve", corrupt_first)
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    failed, passed, summary = records(output)
    assert failed["error"]["phase"] == "validation" and failed["result"] is None
    assert failed["error"]["reported_work"]["nn_starts_completed"] == 2
    assert passed["result"]["best_cost"] == 4
    assert summary["counts"] == {"completed": 1, "failed": 1}


def test_empty_work_is_normal_no_solution(tmp_path, monkeypatch):
    _, manifest, bindings, output = campaign(tmp_path, starts=[])
    monkeypatch.setattr(runner, "validate_solution", lambda *a: pytest.fail("no tour to validate"))
    runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    trial = records(output)[0]
    assert trial["outcome"] == "completed" and trial["error"] is None
    assert trial["result"]["status"] == "no_solution"
    assert trial["result"]["best_tour"] is trial["result"]["best_cost"] is None
    assert not any(trial["result"]["work"].values())
    assert trial["timings"]["search_time_s"] == 0


def test_existing_shard_and_input_paths_are_never_overwritten(tmp_path, monkeypatch):
    _, manifest, bindings, output = campaign(tmp_path)
    output.write_text("existing evidence\n")
    monkeypatch.setattr(runner, "_trial", lambda *a: pytest.fail("unexpected execution"))
    with pytest.raises(FileExistsError):
        runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    assert output.read_text() == "existing evidence\n"
    with pytest.raises(ValueError, match="input artifact"):
        runner.run_manifest(manifest, instance_paths=bindings, output_path=next(iter(bindings.values())))


@pytest.mark.parametrize("exception", [OSError("disk full"), KeyboardInterrupt()])
def test_write_failure_or_interrupt_aborts_without_success_summary(tmp_path, monkeypatch, exception):
    _, manifest, bindings, output = campaign(tmp_path, repetitions=[0, 1])
    target, name = ((runner.nn.NearestNeighbourSolver, "solve")
                    if isinstance(exception, KeyboardInterrupt) else (runner, "_write_record"))
    original = getattr(target, name)
    count = 0
    def fail_after_first(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise exception
        return original(*args, **kwargs)
    monkeypatch.setattr(target, name, fail_after_first)
    with pytest.raises(type(exception)):
        runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    lines = records(output)
    assert len(lines) == 1 and lines[0]["record_type"] == "trial"


def test_campaign_end_to_end_includes_trial_persistence_but_not_its_own_summary(tmp_path, monkeypatch):
    _, manifest, bindings, output = campaign(tmp_path, repetitions=[0, 1])
    clock = [0.0]
    write = runner._write_record
    def observed_write(*args):
        write(*args)
        clock[0] += 11.0
    monkeypatch.setattr(runner, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(runner, "_write_record", observed_write)
    summary = runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    assert summary["timings"]["trial_persistence_time_s"] == 22.0
    assert summary["timings"]["end_to_end_time_s"] == 22.0
    assert clock[0] == 33.0
    trials = records(output)[:-1]
    assert [item["attempt_id"] for item in summary["trial_timings"]] == [line["attempt_id"] for line in trials]
    assert all(item["end_to_end_time_s"] == item["persistence_time_s"] == 11.0
               for item in summary["trial_timings"])
    assert all(line["timings"]["pre_persistence_time_s"] == 0 for line in trials)


@pytest.mark.parametrize("phase", ["solver_warmup", "solve"])
def test_solver_errors_are_distinguished_from_success(tmp_path, monkeypatch, phase):
    _, manifest, bindings, output = campaign(tmp_path)
    def fail(*args, **kwargs):
        raise ArithmeticError("injected numerical failure")
    if phase == "solver_warmup":
        monkeypatch.setattr(runner.nn, "warmup_nearest_neighbour", fail)
    else:
        monkeypatch.setattr(runner.nn.NearestNeighbourSolver, "solve", fail)
    summary = runner.run_manifest(manifest, instance_paths=bindings, output_path=output)
    trial = records(output)[0]
    assert trial["error"]["phase"] == phase and trial["error"]["type"] == "ArithmeticError"
    assert trial["result"] is None and summary["counts"]["failed"] == 1
    assert trial["error"]["reported_work"] is None


def test_cli_campaign_outside_checkout_and_exit_codes(tmp_path):
    _, manifest, bindings, output = campaign(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict()))
    identifier, source = next(iter(bindings.items()))
    base = [sys.executable, "-m", "tsp_hpc", "run", str(manifest_path)]
    for extra, destination, expected in [
        (["--instance", f"{identifier}={source}"], output, 0),
        ([], tmp_path / "missing.jsonl", 1),
        ([], output, 2),
        (["--instance", "bad-binding"], tmp_path / "bad.jsonl", 2),
    ]:
        process = subprocess.run(base + extra + ["--output", str(destination)], cwd=tmp_path,
                                 capture_output=True, text=True, timeout=30)
        assert process.returncode == expected, process.stderr
    assert records(output)[-1]["counts"] == {"completed": 1, "failed": 0}
    assert not (tmp_path / "bad.jsonl").exists()
