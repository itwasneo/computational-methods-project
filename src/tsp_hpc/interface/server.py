"""Optional local API foundation: session pairing and read-only candidate catalogs."""

from contextlib import ExitStack, asynccontextmanager, contextmanager
import hashlib
import hmac
import re
import secrets
import threading

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.workspace import WorkspaceCatalog
from tsp_hpc.interface.queries import TrialQueries
from tsp_hpc.interface.requests import (
    ArtifactRequest, DatasetRequest, DatasetStatusRequest, SessionRequest, TrialQueryRequest, WorkspaceRequest, RunRequest,
    InstanceLoadRequest, InstanceGenerateRequest, SnapshotRequest, ExecutionPreflightRequest,
    JobSubmitRequest, JobStatusRequest, JobsQueryRequest, ExecutionControlRequest, QueueContinueRequest,
)

BODY_LIMIT = 16 * 1024
SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class Pairing:
    """One browser grant, replaceable only by the trusted owner's terminal.

    Rotation and bounded API operations serialize at admission. An operation
    already admitted finishes first; slow bodies/queued handlers cannot use a
    revoked grant. Rotation never touches catalog, dataset or worker state.
    """

    def __init__(self, secret: str):
        self._lock = threading.Lock()
        self._closed = False
        self._digest = b""
        self.rearm(secret)

    def rearm(self, secret: str):
        """Owner-only in-process action, deliberately not an HTTP operation."""
        if not SECRET_PATTERN.fullmatch(secret):
            raise ValueError("Use a fresh random URL-safe secret of 43 to 128 characters.")
        digest = hashlib.sha256(secret.encode()).digest()
        with self._lock:
            if self._closed or hmac.compare_digest(digest, self._digest):
                raise ValueError("Pairing is closed or the secret was not replaced.")
            token, session_id = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            self._digest, self._token, self._session_id = digest, token, session_id
            self._attempts = 0
            self._authorization = None

    def exchange(self, secret: str):
        with self._lock:
            self._attempts = min(self._attempts + 1, 6)
            if self._authorization is not None or self._attempts > 5 or not hmac.compare_digest(hashlib.sha256(secret.encode()).digest(), self._digest):
                raise InterfaceError("unauthorized", "Session pairing denied; ask the owner to enable fresh pairing in the backend terminal.", 403)
            self._authorization = f"Bearer {self._token}".encode()
            return self._token, self._session_id

    def authorized(self, header: bytes):
        # A nonblocking preliminary check for the ASGI event loop. The immutable
        # grant is checked again under the lock in the synchronous handler.
        grant = self._authorization
        return grant is not None and hmac.compare_digest(header, grant)

    @contextmanager
    def operation(self, header: bytes):
        # Recheck at actual handler admission, not just before reading its body.
        # Never hold this lock across HTTP reads/sends or background work.
        with self._lock:
            if not self.authorized(header):
                raise InterfaceError("unauthorized", "Session authorization required.", 403)
            yield

    def clear(self):
        with self._lock:
            self._closed = True
            self._authorization = None
            self._attempts = 6
            self._digest = b""
            self._token = self._session_id = ""


def error_response(error: InterfaceError, request_id: str):
    return JSONResponse(status_code=error.status, content={
        "api_version": 1, "request_id": request_id, "code": error.code,
        "message": error.message, "retryable": error.retryable,
    })


class LocalBoundary:
    """ASGI guard: check authority before parsing; bound even chunked bodies."""

    def __init__(self, app, *, origin: str, pairing: Pairing):
        self.app, self.origin, self.pairing = app, origin.encode(), pairing
        self.host = origin.removeprefix("http://").encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {}
        duplicate = False
        for key, value in scope["headers"]:
            key = key.lower()
            if key in headers and key in (b"host", b"origin", b"authorization", b"content-length", b"content-type", b"sec-fetch-site", b"x-request-id"):
                duplicate = True
            headers[key] = value
        supplied_id = headers.get(b"x-request-id", b"").decode("ascii", errors="ignore")
        request_id = supplied_id if REQUEST_ID.fullmatch(supplied_id) else secrets.token_urlsafe(16)
        scope.setdefault("state", {})["request_id"] = request_id

        response_started = False
        response_finished = False

        async def protected_send(message):
            nonlocal response_started, response_finished
            if message["type"] == "http.response.start":
                response_started = True
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
                ]
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_finished = True
            await send(message)

        try:
            if duplicate or headers.get(b"host") != self.host:
                raise InterfaceError("unauthorized", "Local request authority rejected.", 403)
            if headers.get(b"origin", self.origin) != self.origin or headers.get(b"sec-fetch-site", b"none") not in (b"none", b"same-origin"):
                raise InterfaceError("unauthorized", "Request origin rejected.", 403)
            if scope.get("query_string"):
                raise InterfaceError("invalid_request", "Query-string parameters are not supported.")
            if scope["method"] not in ("GET", "POST"):
                raise InterfaceError("invalid_request", "Unsupported request method.", 405)
            if scope["path"] != "/api/session" and not self.pairing.authorized(headers.get(b"authorization", b"")):
                raise InterfaceError("unauthorized", "Session authorization required.", 403)
            length = headers.get(b"content-length")
            if length is not None and (len(length) > 10 or not length.isdigit() or int(length) > BODY_LIMIT):
                raise InterfaceError("request_too_large", "Request body exceeds its limit.", 413)
            if scope["method"] == "POST" and headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != b"application/json":
                raise InterfaceError("invalid_request", "JSON request body required.", 415)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > BODY_LIMIT:
                    raise InterfaceError("request_too_large", "Request body exceeds its limit.", 413)
                if not message.get("more_body", False):
                    break
            if length is not None and int(length) != len(body):
                raise InterfaceError("invalid_request", "Request body length mismatch.")
            if scope["method"] == "GET" and body:
                raise InterfaceError("invalid_request", "GET requests must not have a body.")
            consumed = False

            async def replay():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, replay, protected_send)
        except InterfaceError as error:
            await error_response(error, scope["state"]["request_id"])(scope, receive, protected_send)
        except Exception:
            # Do not let an unexpected exception leak paths/input through server tracebacks.
            if not response_started:
                await error_response(InterfaceError("internal_error", "Local service request failed.", 500), scope["state"]["request_id"])(scope, receive, protected_send)
            elif not response_finished:
                await send({"type": "http.response.body", "body": b"", "more_body": False})


def create_app(workspace, *, port: int, bootstrap_secret: str, enable_execution=False,
               execution_root=None, execution_limits=None):
    """Construct a session without opening its workspace until lifespan startup."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if type(enable_execution) is not bool or enable_execution != (execution_root is not None):
        raise ValueError("Execution requires both --enable-execution and --execution-root.")
    pairing = Pairing(bootstrap_secret)
    origin = f"http://127.0.0.1:{port}"

    @asynccontextmanager
    async def lifespan(app):
        from tsp_hpc.interface.ingestion import DatasetStore
        from tsp_hpc.interface.execution import ExecutionAuthority
        with ExitStack() as cleanup:
            cleanup.callback(pairing.clear)
            catalog = WorkspaceCatalog(workspace)
            cleanup.callback(catalog.close)
            catalog.refresh()
            app.state.catalog = catalog
            execution = ExecutionAuthority(workspace, enabled=enable_execution, output_root=execution_root, limits=execution_limits)
            cleanup.callback(execution.close)
            app.state.execution = execution
            app.state.snapshots = None
            if execution.enabled:
                from tsp_hpc.interface.storage import LocalStorage
                from tsp_hpc.interface.snapshots import SnapshotStore
                storage = LocalStorage(execution, catalog._root)
                cleanup.callback(storage.close)
                app.state.storage = storage
                app.state.snapshots = SnapshotStore(storage)
            from tsp_hpc.interface.planning import PreflightStore
            app.state.preflights = PreflightStore(execution, app.state.snapshots)
            app.state.jobs = None
            datasets = DatasetStore(catalog)
            cleanup.callback(datasets.close)
            app.state.datasets = datasets
            app.state.trial_queries = TrialQueries(datasets)
            if execution.enabled:
                from tsp_hpc.interface.jobs import JobQueue
                jobs = JobQueue(storage, app.state.snapshots, app.state.preflights)
                cleanup.callback(jobs.close)  # Hold/stop jobs before waiting for ingestion shutdown.
                app.state.jobs = jobs
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pairing = pairing  # Launcher authority, never exposed through a route.
    app.add_middleware(LocalBoundary, origin=origin, pairing=pairing)

    @app.exception_handler(InterfaceError)
    async def interface_error(request: Request, error: InterfaceError):
        return error_response(error, request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, _error):
        # FastAPI's default validation response echoes input, potentially including secrets/paths.
        return error_response(InterfaceError("invalid_request", "Request fields are invalid."), request.state.request_id)

    @app.exception_handler(HTTPException)
    async def unsupported_endpoint(request: Request, error: HTTPException):
        return error_response(InterfaceError("invalid_request", "Endpoint or method is unavailable.", error.status_code), request.state.request_id)

    def catalog_for(request: Request, body: WorkspaceRequest):
        request.state.request_id = body.request_id
        catalog = request.app.state.catalog
        if not hmac.compare_digest(body.workspace_id.encode(), catalog.workspace_id.encode()):
            raise InterfaceError("unauthorized", "Workspace is not authorized for this session.", 403)
        return catalog

    @app.post("/api/session")
    def session(body: SessionRequest, request: Request):
        request.state.request_id = body.request_id
        token, session_id = pairing.exchange(body.secret)
        return {"api_version": 1, "request_id": body.request_id, "token_type": "Bearer",
                "session_token": token, "session_id": session_id}

    def authorized_operation(request: Request):
        return pairing.operation(request.headers.get("authorization", "").encode("latin-1"))

    def run_context(request: Request, body: RunRequest):
        catalog_for(request, body)
        if not hmac.compare_digest(body.session_id, pairing._session_id):
            raise InterfaceError("unauthorized", "Session identity is not current.", 403)
        return {"api_version": 1, "request_id": body.request_id,
                "workspace_id": body.workspace_id, "session_id": body.session_id}

    @app.post("/api/execution")
    def execution_info(body: RunRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **request.app.state.execution.describe()}

    @app.post("/api/runs/capabilities")
    def run_capabilities(body: RunRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **request.app.state.preflights.capabilities()}

    @app.post("/api/runs/preflight")
    def run_preflight(body: ExecutionPreflightRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            plan = request.app.state.preflights.preflight(body.instance_snapshot_id, body.configuration,
                                                         body.capabilities_revision, body.session_id)
            return {**context, **plan}

    def jobs_for(request):
        request.app.state.execution.require()
        return request.app.state.jobs

    @app.post("/api/jobs/submit", status_code=202)
    def job_submit(body: JobSubmitRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            job, duplicate = jobs_for(request).submit(body.preflight_id, body.submission_key, body.session_id)
            return {**context, "job": job, "deduplicated": duplicate}

    @app.post("/api/jobs/status")
    def job_status(body: JobStatusRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, "job": jobs_for(request).status(body.job_id)}

    @app.post("/api/jobs/review")
    def job_review(body: JobStatusRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **jobs_for(request).review(body.job_id)}

    @app.post("/api/jobs/control")
    def job_control(body: ExecutionControlRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            job = jobs_for(request).control(body.job_id, body.expected_revision, body.action)
            return {**context, "job": job}

    @app.post("/api/queue/continue")
    def queue_continue(body: QueueContinueRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **jobs_for(request).continue_queue(body.expected_queue_revision)}

    @app.post("/api/jobs/query")
    def jobs_query(body: JobsQueryRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **jobs_for(request).page(limit=body.limit, cursor=body.cursor)}

    @app.post("/api/queue/status")
    def queue_status(body: RunRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **jobs_for(request).queue()}

    def snapshots_for(request):
        request.app.state.execution.require()
        return request.app.state.snapshots

    @app.post("/api/instances/load", status_code=201)
    def instance_load(body: InstanceLoadRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            snapshot = snapshots_for(request).load(request.app.state.catalog, body.artifact_handle, body.artifact_revision)
            return {**context, **snapshot}

    @app.post("/api/instances/generate", status_code=201)
    def instance_generate(body: InstanceGenerateRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **snapshots_for(request).generate(body.generation.model_dump())}

    @app.post("/api/instances/snapshot")
    def instance_snapshot(body: SnapshotRequest, request: Request):
        with authorized_operation(request):
            context = run_context(request, body)
            return {**context, **snapshots_for(request).describe(body.instance_snapshot_id)}

    @app.post("/api/instances/export")
    def instance_export(body: SnapshotRequest, request: Request):
        with authorized_operation(request):
            run_context(request, body)
            raw = snapshots_for(request).export(body.instance_snapshot_id)
            return Response(raw, media_type="application/octet-stream", headers={
                "Content-Disposition": 'attachment; filename="instance.json"', "X-Request-ID": body.request_id})

    @app.get("/api/workspace")
    def workspace_info(request: Request):
        with authorized_operation(request):
            return {"api_version": 1, "request_id": request.state.request_id,
                    "workspace": request.app.state.catalog.workspace()}

    @app.post("/api/artifacts/query")
    def artifacts(body: ArtifactRequest, request: Request):
        with authorized_operation(request):
            catalog = catalog_for(request, body)
            return {"api_version": 1, "request_id": body.request_id, "workspace_id": catalog.workspace_id,
                    **catalog.page(limit=body.limit, cursor=body.cursor)}

    @app.post("/api/artifacts/refresh")
    def refresh(body: WorkspaceRequest, request: Request):
        with authorized_operation(request):
            catalog = catalog_for(request, body)
            revision = catalog.refresh()
            request.app.state.datasets.invalidate()
            return {"api_version": 1, "request_id": body.request_id, "workspace_id": catalog.workspace_id,
                    "revision": revision}

    @app.post("/api/datasets", status_code=202)
    def ingest(body: DatasetRequest, request: Request):
        with authorized_operation(request):
            catalog_for(request, body)
            return request.app.state.datasets.submit(body.result_handles, body.expected_manifest_handle,
                                                     body.artifact_catalog_revision, body.request_id)

    @app.post("/api/datasets/status")
    def dataset_status(body: DatasetStatusRequest, request: Request):
        with authorized_operation(request):
            catalog_for(request, body)
            return request.app.state.datasets.status(body.dataset_id, body.request_id)

    @app.post("/api/trials/query")
    def trials(body: TrialQueryRequest, request: Request):
        with authorized_operation(request):
            catalog_for(request, body)
            return request.app.state.trial_queries.page(body.model_dump())

    return app
