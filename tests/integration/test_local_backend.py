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
            assert json.load(response)["workspace"]["access"] == "read_only"
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        for private in (secret, token, str(tmp_path), "Traceback"):
            assert private not in stdout + stderr
        assert sorted(p.name for p in tmp_path.iterdir()) == ["example.json"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


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
