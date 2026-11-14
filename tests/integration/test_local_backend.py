"""Optional CLI boundary and a bounded real loopback lifecycle smoke test."""

import json
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


@pytest.mark.parametrize("arguments", [["serve", "--help"], ["serve", "--workspace", "."]])
def test_server_help_and_missing_extra_do_not_import_numerical_stack(arguments):
    script = r'''
import importlib.abc
import sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split('.')[0]
        if root in ('numpy', 'numba'):
            raise AssertionError('numerical import during optional CLI startup')
        if root in ('fastapi', 'pydantic', 'uvicorn'):
            raise ModuleNotFoundError('optional dependency unavailable')
sys.meta_path.insert(0, Block())
from tsp_hpc.cli import main
main(sys.argv[1:])
'''
    result = subprocess.run([sys.executable, "-c", script, *arguments], capture_output=True, text=True, timeout=15)
    assert result.returncode == (0 if "--help" in arguments else 2)
    if "--help" not in arguments:
        assert "Interface dependencies unavailable" in result.stderr
        assert "Traceback" not in result.stderr


def test_real_server_pairing_and_clean_shutdown(tmp_path):
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    (tmp_path / "example.json").write_text("{}")
    (tmp_path / "empty.jsonl").touch()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    secret = secrets.token_urlsafe(32)
    process = subprocess.Popen(
        [sys.executable, "-m", "tsp_hpc", "serve", "--workspace", str(tmp_path),
         "--port", str(port), "--session-secret-stdin"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    token = ""
    try:
        process.stdin.write(secret + "\n")
        process.stdin.close()
        process.stdin = None
        origin = f"http://127.0.0.1:{port}"
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            try:
                direct.open(origin + "/api/workspace", timeout=0.2)
            except urllib.error.HTTPError as error:
                assert error.code == 403
                break
            except (OSError, TimeoutError):
                assert process.poll() is None, "server exited during startup"
                time.sleep(0.05)
        else:
            pytest.fail("server did not start")
        request = urllib.request.Request(origin + "/api/session", data=json.dumps({
            "api_version": 1, "request_id": "smoke", "secret": secret,
        }).encode(), headers={"Content-Type": "application/json"})
        with direct.open(request, timeout=3) as response:
            token = json.load(response)["session_token"]
        request = urllib.request.Request(origin + "/api/workspace", headers={"Authorization": f"Bearer {token}"})
        with direct.open(request, timeout=3) as response:
            workspace = json.load(response)["workspace"]
            assert workspace["access"] == "read_only"

        def post(endpoint, body):
            request = urllib.request.Request(origin + endpoint, data=json.dumps(body).encode(), headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {token}",
            })
            with direct.open(request, timeout=3) as response:
                return response.status, json.load(response)

        context = {"api_version": 1, "request_id": "ingest-smoke", "workspace_id": workspace["workspace_id"]}
        _, catalog = post("/api/artifacts/query", {**context, "limit": 100, "cursor": None})
        handle = next(item["artifact_handle"] for item in catalog["items"] if item["kind"] == "result_shard")
        status, accepted = post("/api/datasets", {**context, "artifact_catalog_revision": catalog["revision"],
                                                  "result_handles": [handle], "expected_manifest_handle": None})
        assert status == 202 and accepted["state"] == "loading"
        dataset = accepted
        for _ in range(100):
            try:
                _, dataset = post("/api/datasets/status", {**context, "dataset_id": accepted["dataset_id"]})
            except urllib.error.HTTPError as error:
                try:
                    assert error.code == 409 and json.load(error)["code"] == "catalog_busy"
                finally:
                    error.close()
            else:
                if dataset["state"] != "loading":
                    break
            time.sleep(0.05)
        assert dataset["state"] == "ready" and dataset["diagnostics"][0]["code"] == "incomplete_campaign"
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        for private in (secret, token, str(tmp_path), "Traceback"):
            assert private not in stdout + stderr
        assert sorted(p.name for p in tmp_path.iterdir()) == ["empty.jsonl", "example.json"]
        assert (tmp_path / "empty.jsonl").read_bytes() == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("ending", ["partial_secret", "terminal_eof"])
def test_real_terminal_repairing_preserves_backend_and_restores_terminal(tmp_path, ending):
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    import os
    import pty
    import select
    import termios

    (tmp_path / "empty.jsonl").touch()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    master, terminal = pty.openpty()
    original = termios.tcgetattr(terminal)
    first, second = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    transcript, pending = bytearray(), bytearray()
    process = subprocess.Popen(
        [sys.executable, "-m", "tsp_hpc", "serve", "--workspace", str(tmp_path), "--port", str(port)],
        stdin=terminal, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def until(marker):
        marker = marker.encode()
        deadline = time.monotonic() + 10
        while marker not in pending:
            assert process.poll() is None, "interactive backend exited unexpectedly"
            assert time.monotonic() < deadline, "interactive backend prompt timed out"
            if select.select([process.stdout], [], [], 0.1)[0]:
                chunk = os.read(process.stdout.fileno(), 4096)
                assert chunk, "interactive backend output closed"
                transcript.extend(chunk)
                pending.extend(chunk)
        del pending[:pending.index(marker) + len(marker)]

    def request(path, token="", body=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        data = None
        if body is not None:
            data = json.dumps({"api_version": 1, "request_id": "terminal-test", **body}).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers)
        try:
            response = direct.open(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    try:
        until("Session pairing secret")
        os.write(master, (first + "\n").encode())
        until("Owner command")
        for _ in range(100):
            try:
                assert request("/api/workspace")[0] == 403
                break
            except (OSError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("interactive backend did not start")
        code, initial = request("/api/session", body={"secret": first})
        assert code == 200
        token = initial["session_token"]
        workspace = request("/api/workspace", token)[1]["workspace"]
        context = {"workspace_id": workspace["workspace_id"]}
        code, catalog = request("/api/artifacts/query", token, {**context, "limit": 100, "cursor": None})
        assert code == 200
        code, dataset = request("/api/datasets", token, {**context,
            "result_handles": [catalog["items"][0]["artifact_handle"]], "expected_manifest_handle": None,
            "artifact_catalog_revision": catalog["revision"]})
        assert code == 202
        poll = {**context, "dataset_id": dataset["dataset_id"]}
        for _ in range(100):
            code, dataset = request("/api/datasets/status", token, poll)
            if code == 200 and dataset["state"] == "ready":
                break
            time.sleep(0.05)
        else:
            pytest.fail("dataset did not become ready")
        os.write(master, b"pair\n")
        until("empty line cancels): ")
        assert request("/api/workspace", token)[0] == 200  # Prompt does not block or revoke early.
        os.write(master, (second + "\n").encode())
        until("Previous browser access revoked.")
        assert request("/api/workspace", token)[0] == 403
        assert request("/api/session", body={"secret": first})[0] == 403
        code, replacement = request("/api/session", body={"secret": second})
        assert code == 200 and replacement["session_id"] != initial["session_id"]
        new_token = replacement["session_token"]
        assert new_token != token
        assert request("/api/workspace", new_token)[1]["workspace"] == workspace
        assert request("/api/datasets/status", new_token, poll)[1] == dataset
        assert request("/api/session/reset", new_token, {})[0] == 404
        if ending == "terminal_eof":
            os.write(master, b"\x04")
            until("Owner terminal controls disabled")
            assert termios.tcgetattr(terminal) == original
            assert request("/api/workspace", new_token)[0] == 200
        else:
            os.write(master, b"pair\n")
            until("empty line cancels): ")
            os.write(master, b"partial-secret-never-submitted")
        process.send_signal(signal.SIGINT)
        remaining, _ = process.communicate(timeout=10)
        transcript.extend(remaining)
        assert process.returncode == 0
        assert termios.tcgetattr(terminal) == original
        # A partial hidden secret must not be delivered to the next terminal reader.
        os.write(master, b"\n")
        assert select.select([terminal], [], [], 3)[0]
        assert os.read(terminal, 1024) == b"\n"
        os.set_blocking(master, False)
        try:
            while True:
                echo = os.read(master, 4096)
                if not echo:
                    break
                transcript.extend(echo)  # Include any terminal echo, not just server output.
        except BlockingIOError:
            pass
        output = transcript.decode(errors="replace")
        for private in (first, second, token, new_token, str(tmp_path), "partial-secret-never-submitted", "Traceback"):
            assert private not in output
        assert (tmp_path / "empty.jsonl").read_bytes() == b""
        assert sorted(p.name for p in tmp_path.iterdir()) == ["empty.jsonl"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        os.close(master)
        os.close(terminal)


def test_owner_terminal_bounds_discard_whole_overlong_lines():
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    import os
    import threading
    from tsp_hpc.interface.launcher import _terminal_line

    reader, writer = os.pipe()
    try:
        os.write(writer, b"x" * 2000 + b"\npair\n" + b"s" * 128 + b"\n")
        os.close(writer)
        writer = -1
        stop = threading.Event()
        assert _terminal_line(reader, stop, 128) == ""  # Not the first 128 characters.
        assert _terminal_line(reader, stop, 4) == "pair"
        assert _terminal_line(reader, stop, 128) == "s" * 128
        assert _terminal_line(reader, stop, 128) is None
    finally:
        os.close(reader)
        if writer >= 0:
            os.close(writer)


def test_owner_terminal_failure_is_sanitized_and_restores_echo(monkeypatch, capsys):
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    import os
    import pty
    import termios
    import threading
    from tsp_hpc.interface import launcher
    from tsp_hpc.interface.server import Pairing

    master, terminal = pty.openpty()
    original = termios.tcgetattr(terminal)
    secret = secrets.token_urlsafe(32)
    pairing = Pairing(secret)
    token, _ = pairing.exchange(secret)

    def failed_read(*_args):
        raise OSError(f"private terminal failure {secret} {token}")

    monkeypatch.setattr(launcher, "_terminal_line", failed_read)
    try:
        launcher._owner_controls(pairing, terminal, threading.Event())
        assert termios.tcgetattr(terminal) == original
        assert pairing.authorized(f"Bearer {token}".encode())
        output = capsys.readouterr().err
        assert "Owner terminal controls unavailable" in output
        for private in (secret, token, "private terminal failure", "Traceback"):
            assert private not in output
    finally:
        os.close(master)
        os.close(terminal)


def test_cancelled_hidden_prompt_is_sanitized_and_releases_port(tmp_path, monkeypatch):
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    from tsp_hpc.interface import launcher
    from types import SimpleNamespace
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def cancelled(_prompt):
        raise EOFError

    monkeypatch.setattr(launcher.getpass, "getpass", cancelled)
    with pytest.raises(ValueError, match="pairing cancelled"):
        launcher.serve(str(tmp_path), port=port)
    with socket.socket() as released:
        released.bind(("127.0.0.1", port))


def test_occupied_port_and_invalid_workspace_fail_without_secret_or_traceback(tmp_path):
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        result = subprocess.run([sys.executable, "-m", "tsp_hpc", "serve", "--workspace", str(tmp_path),
                                 "--port", str(port), "--session-secret-stdin"],
                                input="", capture_output=True, text=True, timeout=10)
        assert result.returncode == 2
        assert "Cannot bind the local port" in result.stderr
    result = subprocess.run([sys.executable, "-m", "tsp_hpc", "serve", "--workspace", str(tmp_path / "missing"),
                             "--session-secret-stdin"], input="", capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "accessible directory" in result.stderr
    assert str(tmp_path) not in result.stderr and "Traceback" not in result.stderr
