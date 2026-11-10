"""HTTP security boundaries. Run with uv run --locked --extra interface pytest."""

import json
import secrets

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from tsp_hpc.interface.contracts import Artifact, ArtifactPage, Dataset, Diagnostic, SessionResponse
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.server import BODY_LIMIT, Pairing, create_app

ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def session(tmp_path):
    (tmp_path / "one.json").write_text("{}")
    secret = secrets.token_urlsafe(32)
    app = create_app(tmp_path, port=8000, bootstrap_secret=secret)
    with TestClient(app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        pairing = client.post("/api/session", json={"api_version": 1, "request_id": "pair", "secret": secret})
        assert pairing.status_code == 200
        assert pairing.json().keys() == SessionResponse.__required_keys__
        token = pairing.json()["session_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        workspace = client.get("/api/workspace").json()["workspace"]
        yield client, app, workspace, secret, token
    assert app.state.catalog._fd == -1


def query(workspace, **options):
    return {"api_version": 1, "request_id": "query", "workspace_id": workspace["workspace_id"],
            "limit": 100, "cursor": None, **options}


def test_authorized_workspace_listing_refresh_and_no_paths(session, tmp_path):
    client, app, workspace, _, _ = session
    assert workspace["access"] == "read_only"
    assert str(tmp_path) not in json.dumps(workspace)
    response = client.post("/api/artifacts/query", json=query(workspace))
    assert response.status_code == 200
    assert response.json().keys() == ArtifactPage.__required_keys__
    assert response.json()["items"][0].keys() == Artifact.__required_keys__
    assert response.json()["items"][0]["validation"] == "unvalidated"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    old = response.json()["revision"]
    (tmp_path / "two.jsonl").write_text("candidate")
    assert client.post("/api/artifacts/query", json=query(workspace)).status_code == 409
    refreshed = client.post("/api/artifacts/refresh", json={key: value for key, value in query(workspace).items() if key not in ("limit", "cursor")})
    assert refreshed.status_code == 200
    assert refreshed.json()["revision"] != old
    assert len(client.post("/api/artifacts/query", json=query(workspace)).json()["items"]) == 2


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example"}, {"Host": "localhost:8000"},
    {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": "http://127.0.0.1:8001"}, {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"}, {"Authorization": "Bearer invalid"},
])
def test_invalid_host_origin_and_auth_are_rejected(session, headers):
    client, _, _, _, _ = session
    response = client.get("/api/workspace", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "unauthorized"
    assert "access-control-allow-origin" not in response.headers


def test_same_origin_requests_and_native_clients_are_allowed(session):
    client, _, workspace, _, _ = session
    assert client.get("/api/workspace", headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.post("/api/artifacts/query", json=query(workspace), headers={"Origin": ORIGIN}).status_code == 200
    assert client.get("/api/workspace", headers={"X-Forwarded-Host": "evil.example"}).status_code == 200
    assert client.post("/api/artifacts/query", json=query(workspace, workspace_id="another-session")).status_code == 403


def test_pairing_requires_secret_is_once_only_and_never_echoes_it(tmp_path):
    secret = secrets.token_urlsafe(32)
    with TestClient(create_app(tmp_path, port=8000, bootstrap_secret=secret), base_url=ORIGIN) as client:
        assert client.get("/api/workspace").status_code == 403
        body = {"api_version": 1, "request_id": "pair", "secret": "x" * 43}
        rejected = client.post("/api/session", json=body)
        assert rejected.status_code == 403 and body["secret"] not in rejected.text
        body["secret"] = secret
        paired = client.post("/api/session", json=body)
        assert paired.status_code == 200 and secret not in paired.text
        assert paired.json()["session_token"] != secret
        assert client.post("/api/session", json=body).status_code == 403
        assert client.post("/api/session", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    pair = Pairing(secret)
    for _ in range(5):
        with pytest.raises(InterfaceError, match="pairing denied"):
            pair.exchange("wrong")
    with pytest.raises(InterfaceError, match="pairing denied"):
        pair.exchange(secret)


@pytest.mark.parametrize("replacement", [{"api_version": 2}, {"api_version": True}, {"limit": True},
    {"limit": 0}, {"limit": 501}, {"limit": "1"}, {"cursor": 2}, {"path": "/private/secret"}])
def test_request_validation_is_strict_and_sanitized(session, replacement):
    client, _, workspace, _, _ = session
    response = client.post("/api/artifacts/query", json=query(workspace, **replacement))
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "/private/secret" not in response.text


def test_body_limits_types_query_strings_and_duplicate_authority_headers(session):
    client, _, workspace, _, token = session
    for body in [b"x" * (BODY_LIMIT + 1), iter([b"x" * 10000, b"x" * 10000])]:
        assert client.post("/api/artifacts/query", content=body, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/artifacts/query", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/artifacts/query", content="{", headers={"Content-Type": "application/json"}).status_code == 400
    response = client.get(f"/api/workspace?token={token}")
    assert response.status_code == 400 and token not in response.text
    assert client.get("/api/workspace", headers=[("Host", "127.0.0.1:8000"), ("Host", "evil.example")]).status_code == 403
    assert client.options("/api/artifacts/query", headers={"Origin": "http://evil.example"}).status_code == 403


def test_no_file_execution_or_docs_endpoints(session):
    client, _, _, _, _ = session
    for path in ["/docs", "/openapi.json", "/api/files", "/api/run", "/api/workspace/switch"]:
        assert client.get(path).status_code == 404
    assert client.delete("/api/workspace").status_code == 405


def test_unexpected_errors_do_not_leak_workspace_secret_or_raw_traceback(session, tmp_path, monkeypatch, caplog):
    client, app, _, secret, token = session

    def fail():
        raise RuntimeError(f"{tmp_path} {secret} {token}")

    monkeypatch.setattr(app.state.catalog, "workspace", fail)
    response = client.get("/api/workspace")
    assert response.status_code == 500
    for private in (str(tmp_path), secret, token, "Traceback"):
        assert private not in response.text and private not in caplog.text


def test_websocket_channels_are_rejected(session):
    from starlette.websockets import WebSocketDisconnect
    client, _, _, _, _ = session
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/workspace"):
            pass
    assert error.value.code == 1008


def test_startup_failure_and_bad_port_are_explicit(tmp_path):
    secret = secrets.token_urlsafe(32)
    for port in (0, 65536, True):
        with pytest.raises(ValueError, match="Port"):
            create_app(tmp_path, port=port, bootstrap_secret=secret)
    with pytest.raises(InterfaceError, match="accessible directory"):
        with TestClient(create_app(tmp_path / "missing", port=8000, bootstrap_secret=secret), base_url=ORIGIN):
            pass


def dataset_request(session, tmp_path):
    client, _, workspace, _, _ = session
    (tmp_path / "empty.jsonl").touch()
    context = {"api_version": 1, "request_id": "ingest", "workspace_id": workspace["workspace_id"]}
    assert client.post("/api/artifacts/refresh", json=context).status_code == 200
    catalog = client.post("/api/artifacts/query", json=query(workspace)).json()
    handle = next(item["artifact_handle"] for item in catalog["items"] if item["kind"] == "result_shard")
    return {**context, "artifact_catalog_revision": catalog["revision"], "result_handles": [handle],
            "expected_manifest_handle": None}


def test_dataset_lifecycle_routes_match_contract_and_refresh_invalidates(session, tmp_path):
    client, app, workspace, _, _ = session
    body = dataset_request(session, tmp_path)
    accepted = client.post("/api/datasets", json=body)
    assert accepted.status_code == 202
    assert accepted.json().keys() == Dataset.__required_keys__
    assert accepted.json()["state"] == "loading" and accepted.json()["revision"] is None
    app.state.datasets._future.result(timeout=10)
    context = {"api_version": 1, "request_id": "poll", "workspace_id": workspace["workspace_id"]}
    poll = {**context, "dataset_id": accepted.json()["dataset_id"]}
    ready = client.post("/api/datasets/status", json=poll)
    assert ready.status_code == 200 and ready.json()["state"] == "ready"
    assert ready.json()["request_id"] == "poll"
    assert ready.json()["diagnostics"][0].keys() == Diagnostic.__required_keys__
    assert ready.json()["diagnostics_total"] == 1
    assert ready.json()["diagnostics_truncated"] is False
    assert ready.headers["cache-control"] == "no-store"
    assert str(tmp_path) not in ready.text
    assert client.post("/api/datasets/status", json={**poll, "dataset_id": "another-session"}).status_code == 404
    assert client.post("/api/artifacts/refresh", json=context).status_code == 200
    stale = client.post("/api/datasets/status", json=poll).json()
    assert stale["state"] == "stale" and stale["revision"] is None
    assert client.post("/api/datasets", json=body).status_code == 409


@pytest.mark.parametrize("replacement", [
    {"api_version": True}, {"result_handles": []}, {"result_handles": ["x"] * 65},
    {"result_handles": ["x", "x"]}, {"result_handles": [True]}, {"result_handles": ["x" * 129]},
    {"expected_manifest_handle": 1}, {"artifact_catalog_revision": False}, {"path": "/private/input"},
])
def test_dataset_request_validation_is_strict_and_sanitized(session, tmp_path, replacement):
    client, _, _, _, _ = session
    response = client.post("/api/datasets", json={**dataset_request(session, tmp_path), **replacement})
    assert response.status_code == 400 and response.json()["code"] == "invalid_request"
    assert "/private/input" not in response.text


def test_dataset_routes_require_session_workspace_and_explicit_nullable_fields(session, tmp_path):
    client, _, workspace, _, _ = session
    body = dataset_request(session, tmp_path)
    assert client.post("/api/datasets", json=body, headers={"Authorization": "Bearer invalid"}).status_code == 403
    assert client.post("/api/datasets", json={**body, "workspace_id": "another-session"}).status_code == 403
    assert client.post("/api/datasets", json={**body, "result_handles": ["/private/input"]}).status_code == 404
    del body["expected_manifest_handle"]
    assert client.post("/api/datasets", json=body).status_code == 400
    status = {"api_version": 1, "request_id": "poll", "workspace_id": workspace["workspace_id"], "dataset_id": "x"}
    assert client.post("/api/datasets/status", json=status, headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/datasets/status", json={**status, "workspace_id": "other"}).status_code == 403
    assert client.post("/api/datasets/status", json={**status, "revision": "extra"}).status_code == 400
