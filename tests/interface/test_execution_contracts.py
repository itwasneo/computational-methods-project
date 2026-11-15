"""Owner authority and solver-neutral wire requests, without scientific work."""

from copy import deepcopy
import json
import secrets

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tsp_hpc.interface.execution import ExecutionAuthority, ExecutionLimits
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.requests import ScientificConfigurationRequest, ColumnFilterRequest, ExecutionControlRequest
from tsp_hpc.interface.server import create_app


def configuration(algorithm="nn"):
    return {"algorithm": algorithm, "parameters": {},
            "budget": {"nn": {"start_cities": [2, 0]}, "ga": {"max_generations": 0}, "sa": {"time_limit_s": 1.}}[algorithm],
            "logical_search_ids": None if algorithm == "nn" else ["a", "b"],
            "implementations": [None], "distance_modes": ["on_demand"], "repetitions": [0],
            "solver_seed": str(2**64 - 1), "max_matrix_bytes": 0,
            "record_history": True, "history_policy": None}


@pytest.mark.parametrize("algorithm", ["nn", "ga", "sa"])
def test_shared_rules_and_no_execution_at_the_wire_boundary(algorithm):
    raw = configuration(algorithm)
    saved = deepcopy(raw)
    model = ScientificConfigurationRequest.model_validate(raw)
    assert raw == saved
    assert model.history_policy == {"interval_s": .1, "max_points": 4096}
    assert model.solver_seed == str(2**64 - 1)
    if algorithm == "sa":
        assert model.budget["time_check_interval_proposals"] == 256
    if algorithm == "ga":
        assert model.parameters["population_size"] == 128
    for field, value in [("solver_seed", 1), ("record_history", 1), ("budget", {"unknown": 0}),
                         ("parameters", {"script": "never execute"}), ("repetitions", [True]),
                         ("implementations", ["../file"]), ("logical_search_ids", [])]:
        with pytest.raises(ValidationError):
            ScientificConfigurationRequest.model_validate({**raw, field: value})


@pytest.mark.parametrize("value", [1e20, 100000000000000000000, -1e20, 0, 0.5])
def test_float64_observations_do_not_depend_on_json_number_spelling(value):
    for field in ("best_cost", "search_time_s"):
        assert ColumnFilterRequest(field=field, values=[value]).values == [value]
    if type(value) is not int or value < 2 or value > 2**53 - 1:
        with pytest.raises(ValidationError):
            ColumnFilterRequest(field="n_cities", values=[value])


@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), 10**400])
def test_nonfinite_or_boolean_filter_numbers_are_invalid(value):
    with pytest.raises(ValidationError):
        ColumnFilterRequest(field="best_cost", values=[value])


def test_stop_now_requires_explicit_confirmation():
    body = dict(api_version=1, request_id="stop", workspace_id="w", session_id="s", job_id="j",
                expected_revision="r", action="stop_now", confirm_stop_now=True)
    ExecutionControlRequest.model_validate(body)
    with pytest.raises(ValidationError):
        ExecutionControlRequest.model_validate({**body, "confirm_stop_now": False})


def test_owner_options_and_roots_cannot_be_confused(tmp_path):
    workspace, output = tmp_path / "read", tmp_path / "write"
    workspace.mkdir(); output.mkdir()
    for enabled, root in [(True, None), (False, output), (1, output)]:
        with pytest.raises(ValueError):
            ExecutionAuthority(workspace, enabled=enabled, output_root=root)
    for root in (workspace, tmp_path):
        with pytest.raises(ValueError, match="disjoint"):
            ExecutionAuthority(workspace, enabled=True, output_root=root)
    readonly = ExecutionAuthority(workspace)
    with pytest.raises(InterfaceError) as caught:
        readonly.require()
    assert caught.value.code == "execution_disabled"
    with pytest.raises(ValueError):
        ExecutionLimits(max_cities=True)
    authority = ExecutionAuthority(workspace, enabled=True, output_root=output)
    assert authority.describe()["output_root"]["display_name"] == "write"
    output.rename(tmp_path / "old")
    output.mkdir()
    with pytest.raises(InterfaceError):
        authority.require()
    authority.close()


@pytest.mark.parametrize("enabled", [False, True])
def test_http_authority_info_is_bound_to_current_session_and_not_browser_elevated(tmp_path, enabled):
    workspace, output = tmp_path / "read", tmp_path / "write"
    workspace.mkdir(); output.mkdir()
    secret = secrets.token_urlsafe(32)
    app = create_app(workspace, port=8000, bootstrap_secret=secret,
                     enable_execution=enabled, execution_root=output if enabled else None)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        paired = client.post("/api/session", json={"api_version": 1, "request_id": "pair", "secret": secret}).json()
        client.headers["Authorization"] = f"Bearer {paired['session_token']}"
        ws = client.get("/api/workspace").json()["workspace"]
        body = dict(api_version=1, request_id="authority", workspace_id=ws["workspace_id"], session_id=paired["session_id"])
        result = client.post("/api/execution", json=body)
        assert result.status_code == 200
        assert result.json()["local_runs_enabled"] is enabled
        assert ws["access"] == "read_only"
        assert str(tmp_path) not in result.text
        assert client.post("/api/execution", json={**body, "enabled": True}).status_code == 400
        assert client.post("/api/execution", json={**body, "session_id": "old"}).status_code == 403
        assert client.post("/api/execution", json=body, headers={"Origin": "null"}).status_code == 403
        app.state.pairing.rearm(secrets.token_urlsafe(32))
        assert client.post("/api/execution", json=body).status_code == 403
    assert app.state.execution.fd == -1
    assert not list(output.iterdir())  # Authority inspection has no execution side effects.
