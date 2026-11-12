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
    def open_session(root):
        secret = secrets.token_urlsafe(32)
        app = create_app(root, port=8000, bootstrap_secret=secret)
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
def session(tmp_path, paired_session):
    (tmp_path / "one.json").write_text("{}")
    with paired_session(tmp_path) as session:
        yield session
