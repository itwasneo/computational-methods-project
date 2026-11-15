"""Shared paired HTTP session; scientific fixtures stay with their domain tests."""

from contextlib import contextmanager
import secrets

import pytest


@pytest.fixture
def paired_session():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tsp_hpc.interface.contracts import SessionResponse
    from tsp_hpc.interface.server import create_app

    @contextmanager
    def open_session(root, **options):
        secret = secrets.token_urlsafe(32)
        app = create_app(root, port=8000, bootstrap_secret=secret, **options)
        with TestClient(app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as client:
            pairing = client.post("/api/session", json={"api_version": 1, "request_id": "pair", "secret": secret})
            assert pairing.status_code == 200
            assert pairing.json().keys() == SessionResponse.__required_keys__
            token = pairing.json()["session_token"]
            client.headers["Authorization"] = f"Bearer {token}"
            workspace = client.get("/api/workspace").json()["workspace"]
            yield client, app, workspace, secret, token
        assert app.state.catalog._fd == -1

    return open_session


@pytest.fixture
def execution_session(tmp_path, paired_session):
    workspace, output = tmp_path / "read", tmp_path / "output"
    workspace.mkdir(); output.mkdir()
    with paired_session(workspace, enable_execution=True, execution_root=output) as (client, app, ws, secret, token):
        context = {"api_version": 1, "request_id": "run", "workspace_id": ws["workspace_id"],
                   "session_id": app.state.pairing._session_id}
        yield client, app, context, workspace, output


class JobClient:
    """Native/API-style test client; never a second scientific runner."""
    def __init__(self, client, context):
        self.client, self.context = client, context

    def post(self, path, **fields):
        response = self.client.post(path, json={**self.context, **fields})
        assert 200 <= response.status_code < 300, response.text
        return response.json()

    def snapshot(self, **options):
        generation = {"generator": "uniform-v1", "n_cities": 4, "seed": "7", "side_length": 1., **options}
        return self.post("/api/instances/generate", generation=generation)["instance_snapshot_id"]

    def review(self, snapshot=None, **changes):
        identifier = snapshot or self.snapshot()
        configuration = {"algorithm": "nn", "parameters": {}, "budget": {"start_cities": [2, 0]},
                         "logical_search_ids": None, "implementations": [None], "distance_modes": ["on_demand"],
                         "repetitions": [0], "solver_seed": str(2**64 - 1), "max_matrix_bytes": 0,
                         "record_history": True, "history_policy": None, **changes}
        caps = self.post("/api/runs/capabilities")
        return self.post("/api/runs/preflight", instance_snapshot_id=identifier,
                         capabilities_revision=caps["capabilities_revision"], configuration=configuration)

    def submit(self, plan, key="submission"):
        return self.post("/api/jobs/submit", preflight_id=plan["preflight_id"], submission_key=key)

    def status(self, identifier):
        return self.post("/api/jobs/status", job_id=identifier)["job"]

    def wait(self, identifier, predicate=None, timeout=60):
        from time import monotonic, sleep
        predicate = predicate or (lambda job: job["state"] in {"completed", "cancelled", "failed", "interrupted"})
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            value = self.status(identifier)
            if predicate(value):
                return value
            sleep(.025)
        raise AssertionError(f"job did not reach expected state: {value}")


@pytest.fixture
def native_client_factory():
    def make(client, app, workspace):
        return JobClient(client, {"api_version": 1, "request_id": "run", "workspace_id": workspace["workspace_id"],
                                  "session_id": app.state.pairing._session_id})
    return make


@pytest.fixture
def job_client(execution_session):
    client, _, context, _, _ = execution_session
    return JobClient(client, context)


@pytest.fixture
def session(tmp_path, paired_session):
    (tmp_path / "one.json").write_text("{}")
    with paired_session(tmp_path) as session:
        yield session
