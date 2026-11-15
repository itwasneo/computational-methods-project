"""The documented one-command launcher really pairs and shuts down both services."""
import json
import os
from pathlib import Path
import pty
import secrets
import select
import signal
import socket
import subprocess
import sys
import termios
import time

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]


def test_owner_launcher_private_prompt_and_graceful_shutdown(tmp_path):
    for port in (8000, 5173):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
    master, terminal = pty.openpty()
    original = termios.tcgetattr(terminal)
    secret = secrets.token_urlsafe(32)
    process = subprocess.Popen([sys.executable, "tools/local_app.py", "--workspace", str(tmp_path / "inputs"),
                                "--execution-root", str(tmp_path / "outputs")], cwd=ROOT,
                               stdin=terminal, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    transcript = bytearray()
    def until(marker):
        deadline = time.monotonic() + 25
        while marker not in transcript:
            assert process.poll() is None and time.monotonic() < deadline
            if select.select([process.stdout], [], [], .1)[0]:
                transcript.extend(os.read(process.stdout.fileno(), 4096))
    try:
        until(b"random URL-safe characters): ")
        assert not termios.tcgetattr(terminal)[3] & termios.ECHO
        os.write(master, (secret + "\n").encode())
        until(b"Local backend:")
        assert termios.tcgetattr(terminal) == original
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(); page.goto("http://127.0.0.1:5173")
            page.get_by_label("Pairing secret", exact=True).fill(secret)
            page.get_by_role("button", name="Connect", exact=True).click()
            expect(page.get_by_role("button", name="Generate instance", exact=True)).to_be_visible()
            browser.close()
        process.send_signal(signal.SIGINT); assert process.wait(timeout=15) == 0
        transcript.extend(process.stdout.read())
        assert secret.encode() not in transcript
        assert json.loads((tmp_path / "outputs/.tsp-local/queue.json").read_bytes())["held"] is True
        for port in (8000, 5173):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        process.stdout.close(); os.close(master); os.close(terminal)
