"""Owner-operated, loopback-only launcher. Credentials never appear in arguments/logs."""

import getpass
import os
import select
import socket
import sys
import threading

import uvicorn

from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.server import create_app
from tsp_hpc.interface.workspace import WorkspaceCatalog


def _terminal_line(fd: int, stop: threading.Event, limit: int):
    """Bound memory and remain stoppable even during a partial terminal line."""
    line = bytearray()
    while not stop.is_set():
        if not select.select([fd], [], [], 0.1)[0]:
            continue
        byte = os.read(fd, 1)
        if not byte:
            return None
        if byte == b"\n":
            return line.decode("ascii", errors="replace") if len(line) <= limit else ""
        if len(line) <= limit:
            line.extend(byte)
    return None


def _owner_controls(pairing, fd: int, stop: threading.Event):
    """Private terminal input only: no HTTP reset, command interpreter or socket."""
    import termios

    original = None
    try:
        original = termios.tcgetattr(fd)
        hidden = original.copy()
        hidden[3] &= ~(termios.ECHO | termios.ECHONL)
        # Hide even command input so an accidentally pasted secret is not echoed.
        termios.tcsetattr(fd, termios.TCSANOW, hidden)
        while not stop.is_set():
            print("Owner command (input hidden): pair + Enter to replace browser access; Ctrl-D disables terminal controls.",
                  file=sys.stderr, flush=True)
            command = _terminal_line(fd, stop, 4)
            if command is None:
                break
            if command != "pair":
                print("Unknown owner command. Only 'pair' is supported.", file=sys.stderr, flush=True)
                continue
            print("Fresh pairing secret (43–128 random URL-safe characters; empty line cancels): ",
                  end="", file=sys.stderr, flush=True)
            secret = _terminal_line(fd, stop, 128)
            print(file=sys.stderr, flush=True)
            if secret is None or stop.is_set():
                break
            if not secret:
                print("Pairing unchanged.", file=sys.stderr, flush=True)
                continue
            try:
                pairing.rearm(secret)
            except ValueError:
                print("Pairing unchanged. Supply a fresh valid secret, different from the current one.", file=sys.stderr, flush=True)
            else:
                print("Previous browser access revoked. Pair once with the new secret; backend work is unchanged.", file=sys.stderr, flush=True)
            finally:
                del secret
    except Exception:
        # Terminal failures must not leak input/tracebacks or terminate backend work.
        print("Owner terminal controls unavailable.", file=sys.stderr, flush=True)
    finally:
        if original is not None:
            try:
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)  # Do not leave a partial secret for the shell.
                finally:
                    termios.tcsetattr(fd, termios.TCSANOW, original)
            except (OSError, termios.error):
                print("Could not restore terminal input settings.", file=sys.stderr, flush=True)
        if not stop.is_set():
            print("Owner terminal controls disabled; backend continues. No remote reset is available.", file=sys.stderr, flush=True)


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
        stop = threading.Event()
        controls = None
        try:
            if not session_secret_stdin:
                controls = threading.Thread(target=_owner_controls,
                                            args=(app.state.pairing, sys.stdin.fileno(), stop),
                                            name="owner-terminal")
                controls.start()
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            if controls is not None and controls.ident is not None:
                controls.join()
        if not server.started:
            raise InterfaceError("workspace_unavailable", "Backend could not start; check the workspace and port.", 503)
    return 0
