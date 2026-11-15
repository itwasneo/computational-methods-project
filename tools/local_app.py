"""Launch the checkout's browser app and owner-enabled local service together.

Run with uv run --locked --extra interface python tools/local_app.py.
Secrets stay at the backend's private interactive prompt, never command arguments.
"""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

from tsp_hpc.interface.errors import InterfaceError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("data/generated/app-inputs"))
    parser.add_argument("--execution-root", type=Path, default=Path("results/app-output"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pnpm = shutil.which("pnpm")
    if not pnpm or not (root / "frontend/node_modules").is_dir():
        parser.error("Install Node 24/pnpm, then run pnpm --dir frontend install --frozen-lockfile.")
    read, output = args.workspace.resolve(), args.execution_root.resolve()
    if read.is_relative_to(output) or output.is_relative_to(read):
        parser.error("Workspace and execution root must be disjoint.")
    # Explicit owner command authorizes creating these directories and enabling jobs.
    args.workspace.mkdir(parents=True, exist_ok=True)
    args.execution_root.mkdir(parents=True, exist_ok=True)
    from tsp_hpc.interface.execution import ExecutionAuthority
    authority = ExecutionAuthority(args.workspace, enabled=True, output_root=args.execution_root)
    authority.close()
    for port in (8000, 5173):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                parser.error(f"Local port {port} is occupied; no existing process was stopped.")
    frontend = subprocess.Popen([pnpm, "--dir", str(root / "frontend"), "run", "dev"],
                                stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        # Detect a missing toolchain/occupied strict port before requesting credentials.
        deadline = time.monotonic() + 15
        while True:
            if frontend.poll() is not None or time.monotonic() >= deadline:
                parser.error("Frontend could not start; inspect its output above.")
            try:
                with socket.create_connection(('127.0.0.1', 5173), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        print("Open http://127.0.0.1:5173 and pair with the secret you enter below.", flush=True)
        from tsp_hpc.interface.launcher import serve
        return serve(str(args.workspace), port=8000, enable_execution=True, execution_root=str(args.execution_root))
    finally:
        if frontend.poll() is None:
            os.killpg(frontend.pid, signal.SIGTERM)
            try:
                frontend.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(frontend.pid, signal.SIGKILL)
                frontend.wait()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (InterfaceError, OSError, ValueError) as error:
        print(f"Local app: {error}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
