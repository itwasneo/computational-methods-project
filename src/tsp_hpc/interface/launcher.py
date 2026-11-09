"""Owner-operated, loopback-only launcher. Credentials never appear in arguments/logs."""

import getpass
import socket
import sys

import uvicorn

from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.server import create_app
from tsp_hpc.interface.workspace import WorkspaceCatalog


def serve(workspace: str, *, port: int = 8000, session_secret_stdin: bool = False) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    # Report invalid roots/scan limits before asking for credentials or starting a listener.
    catalog = WorkspaceCatalog(workspace)
    try:
        catalog.refresh()
    finally:
        catalog.close()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
        except OSError:
            raise InterfaceError("workspace_unavailable", "Cannot bind the local port; choose another --port.", 503) from None
        try:
            if session_secret_stdin:
                secret = sys.stdin.readline(130).removesuffix("\n").removesuffix("\r")
            else:
                if not sys.stdin.isatty():
                    raise ValueError("Interactive startup needs a terminal; use --session-secret-stdin for private automation.")
                secret = getpass.getpass("Session pairing secret (43–128 random URL-safe characters): ")
        except (EOFError, KeyboardInterrupt):
            raise ValueError("Session pairing cancelled.") from None
        app = create_app(workspace, port=port, bootstrap_secret=secret)
        del secret
        # Disable raw ASGI/Uvicorn tracebacks and access logs; API errors are sanitized.
        logging = {
            "version": 1, "disable_existing_loggers": False,
            "handlers": {"discard": {"class": "logging.NullHandler"}},
            "loggers": {name: {"handlers": ["discard"], "propagate": False}
                        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")},
        }
        config = uvicorn.Config(app, host="127.0.0.1", port=port, proxy_headers=False,
                                access_log=False, log_config=logging, timeout_keep_alive=5,
                                limit_concurrency=32, ws="none")
        server = uvicorn.Server(config)
        print(f"Local backend: http://127.0.0.1:{port} (API only; session pairing required)", flush=True)
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass
        if not server.started:
            raise InterfaceError("workspace_unavailable", "Backend could not start; check the workspace and port.", 503)
    return 0
