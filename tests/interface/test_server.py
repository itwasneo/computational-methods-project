"""HTTP security boundaries. Run with uv run --locked --extra interface pytest."""

import json
import secrets

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from tsp_hpc.interface.contracts import Artifact, ArtifactPage, Dataset, Diagnostic
from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.server import BODY_LIMIT, Pairing, create_app

ORIGIN = "http://127.0.0.1:8000"


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


def test_owner_rearming_replaces_grant_and_recovers_exhausted_pairing(monkeypatch):
    first, second, third = (secrets.token_urlsafe(32) for _ in range(3))
    pair = Pairing(first)
    token, identity = pair.exchange(first)
    header = f"Bearer {token}".encode()
    for invalid in ("", "short", "x" * 129, first):
        with pytest.raises(ValueError):
            pair.rearm(invalid)
        assert pair.authorized(header)
    pair.rearm(second)
    assert not pair.authorized(header)
    for _ in range(5):
        with pytest.raises(InterfaceError):
            pair.exchange(first)
    with pytest.raises(InterfaceError):
        pair.exchange(second)
    pair.rearm(third)
    replacement, new_identity = pair.exchange(third)
    assert replacement != token and new_identity != identity
    assert pair.authorized(f"Bearer {replacement}".encode())
    assert not pair.authorized(f"Bearer {new_identity}".encode())  # Identity is not authority.
    with pytest.raises(InterfaceError):
        pair.exchange(third)

    def broken_random(_size):
        raise RuntimeError("random source unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("tsp_hpc.interface.server.secrets.token_urlsafe", broken_random)
        with pytest.raises(RuntimeError):
            pair.rearm(second)
    assert pair.authorized(f"Bearer {replacement}".encode())
    pair.clear()
    assert not pair.authorized(f"Bearer {replacement}".encode())
    with pytest.raises(ValueError):
        pair.rearm(first)
    with pytest.raises(InterfaceError):
        pair.exchange(third)


def test_owner_rotation_waits_for_admitted_operation_but_denies_later_old_grants():
    from concurrent.futures import ThreadPoolExecutor
    import threading

    first, second = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    pair = Pairing(first)
    token, _ = pair.exchange(first)
    header = f"Bearer {token}".encode()
    entered = threading.Event()

    def rotate():
        entered.set()
        pair.rearm(second)

    with ThreadPoolExecutor(max_workers=1) as worker:
        with pair.operation(header):
            rotation = worker.submit(rotate)
            assert entered.wait(5)
            assert not rotation.done()
        rotation.result(timeout=5)
    with pytest.raises(InterfaceError):
        with pair.operation(header):
            pytest.fail("revoked operation was admitted")


def test_repairing_preserves_catalog_and_running_ingestion(session, tmp_path, monkeypatch):
    import threading

    client, app, workspace, old_secret, old_token = session
    body = dataset_request(session, tmp_path)
    catalog = client.post("/api/artifacts/query", json=query(workspace)).json()
    identity = app.state.pairing._session_id
    started, release = threading.Event(), threading.Event()
    original = app.state.datasets._ingest

    def gated(entry):
        started.set()
        assert release.wait(10)
        original(entry)

    monkeypatch.setattr(app.state.datasets, "_ingest", gated)
    try:
        accepted = client.post("/api/datasets", json=body).json()
        assert started.wait(5)
        future = app.state.datasets._future
        fresh = secrets.token_urlsafe(32)
        app.state.pairing.rearm(fresh)
        assert client.get("/api/workspace").status_code == 403
        assert client.post("/api/session", json={"api_version": 1, "request_id": "old", "secret": old_secret}).status_code == 403
        fresh_body = {"api_version": 1, "request_id": "new", "secret": fresh}
        assert client.post("/api/session", json=fresh_body, headers={"Origin": "null"}).status_code == 403
        paired = client.post("/api/session", json=fresh_body)
        assert paired.status_code == 200
        assert paired.json()["session_token"] != old_token
        assert paired.json()["session_id"] != identity
        client.headers["Authorization"] = f"Bearer {paired.json()['session_token']}"
        assert client.get("/api/workspace").json()["workspace"] == workspace
        assert client.post("/api/artifacts/query", json=query(workspace)).json() == catalog
        poll = {"api_version": 1, "request_id": "poll", "workspace_id": workspace["workspace_id"],
                "dataset_id": accepted["dataset_id"]}
        assert client.post("/api/datasets/status", json=poll).json()["state"] == "loading"
        assert app.state.datasets._future is future and not future.done()
        for endpoint in ("/api/session/reset", "/api/session/rearm", "/api/jobs/submit", "/api/jobs/stop"):
            assert client.post(endpoint, json={}).status_code == 404
            assert client.post(endpoint, json={}, headers={"Authorization": ""}).status_code == 403
    finally:
        release.set()
    future.result(timeout=10)
    assert client.post("/api/datasets/status", json=poll).json()["state"] == "ready"
    assert (tmp_path / "empty.jsonl").read_bytes() == b""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["empty.jsonl", "one.json"]


def test_revocation_during_body_read_cannot_admit_a_stale_handler(session, monkeypatch):
    import asyncio

    _, app, workspace, _, token = session
    body = json.dumps({"api_version": 1, "request_id": "slow", "workspace_id": workspace["workspace_id"]}).encode()
    messages = []
    new_secret = secrets.token_urlsafe(32)
    reads = 0

    def forbidden_refresh():
        pytest.fail("a revoked request reached catalog mutation")

    monkeypatch.setattr(app.state.catalog, "refresh", forbidden_refresh)

    async def receive():
        nonlocal reads
        reads += 1
        if reads == 1:
            app.state.pairing.rearm(new_secret)
            return {"type": "http.request", "body": body[:5], "more_body": True}
        return {"type": "http.request", "body": body[5:], "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/api/artifacts/refresh", "raw_path": b"/api/artifacts/refresh",
             "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 1234),
             "headers": [(b"host", b"127.0.0.1:8000"), (b"authorization", f"Bearer {token}".encode()),
                         (b"content-type", b"application/json"), (b"x-request-id", b"slow")]}
    asyncio.run(app(scope, receive, send))
    assert reads == 2
    assert messages[0]["status"] == 403
    assert json.loads(messages[1]["body"])["code"] == "unauthorized"


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


def test_trial_query_http_boundaries_and_explicit_fields(session, tmp_path):
    client, app, workspace, _, _ = session
    accepted = client.post("/api/datasets", json=dataset_request(session, tmp_path)).json()
    app.state.datasets._future.result(timeout=10)
    context = {"api_version": 1, "request_id": "query", "workspace_id": workspace["workspace_id"],
               "dataset_id": accepted["dataset_id"]}
    ready = client.post("/api/datasets/status", json=context).json()
    body = {**context, "revision": ready["revision"], "filters": [], "sort": None, "limit": 100, "cursor": None}
    response = client.post("/api/trials/query", json=body)
    assert response.status_code == 200 and response.json()["items"] == []
    assert response.json()["next_cursor"] is None
    for field in ("revision", "filters", "sort", "limit", "cursor"):
        assert client.post("/api/trials/query", json={k: v for k, v in body.items() if k != field}).status_code == 400
    for changes in ({"filters": [{"field": "best_cost", "values": [True]}]}, {"limit": 501},
                    {"filters": [{"field": "algorithm", "values": []}] * 2}, {"path": "/private"}):
        response = client.post("/api/trials/query", json={**body, **changes})
        assert response.status_code == 400 and response.json()["code"] == "invalid_request"
        assert "/private" not in response.text
    for headers in ({"Authorization": "Bearer wrong"}, {"Origin": "null"}):
        assert client.post("/api/trials/query", json=body, headers=headers).status_code == 403
    assert client.post("/api/trials/query", json={**body, "workspace_id": "other"}).status_code == 403
    assert client.post("/api/trials/query", json={**body, "dataset_id": "other"}).status_code == 404
    app.state.datasets.invalidate()
    assert client.post("/api/trials/query", json=body).json()["code"] == "stale_revision"


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
