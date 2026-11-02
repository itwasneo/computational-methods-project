"""Source-level container regressions; no image, verifier, or job is executed."""

import os
import re
import subprocess
from pathlib import Path

import pytest


def _definition() -> str:
    root = Path(__file__).resolve().parents[1]
    # The build ships the same definition alongside the installed application.
    path = root / "containers" / "cpu.def"
    if not path.is_file():
        path = root / "container.def"
    return path.read_text()


def test_base_reference_uses_digest_without_tag() -> None:
    reference = re.search(r"^From: (.+)$", _definition(), re.MULTILINE)
    assert reference is not None
    repository, digest = reference.group(1).split("@")
    assert ":" not in repository
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)


@pytest.mark.skipif(os.name != "posix", reason="Apptainer test script uses a POSIX shell")
@pytest.mark.parametrize("verifier_exit_code", [0, 9])
def test_image_tests_use_disposable_tmp_and_preserve_exit_status(
    tmp_path: Path, verifier_exit_code: int
) -> None:
    script = _definition().split("%test\n", 1)[1].split("\n%", 1)[0]
    subprocess.run(["sh", "-n"], input=script, text=True, check=True)

    scratch = "work=$(mktemp -d /tmp/tsp-image-test.XXXXXX)"
    verifier = '/opt/tsp/.venv/bin/python -I -B /opt/tsp/check_runtime.py --output "$work/report.json"'
    assert script.count(scratch) == 1
    assert script.count(verifier) == 1
    assert "mkdir -p /cache" not in script

    # Map writable /tmp into this test's directory and stub out all image execution.
    script = script.replace(
        scratch, 'work=$(mktemp -d "$TEST_TEMP_ROOT/tsp-image-test.XXXXXX")'
    ).replace(
        verifier,
        '''printf '%s\\n' "$work" > "$TEST_MARKER"
    test "$TMPDIR" = "$work/tmp"
    test "$XDG_CACHE_HOME" = "$work/xdg"
    test "$NUMBA_CACHE_DIR" = "$work/numba"
    test -d "$TMPDIR" && test -d "$XDG_CACHE_HOME" && test -d "$NUMBA_CACHE_DIR"
    printf 'cache probe\\n' > "$NUMBA_CACHE_DIR/probe"
    printf '{}\\n' > "$work/report.json"
    exit "$TEST_EXIT_CODE"''',
    )
    marker = tmp_path / "work-path.txt"
    unavailable_runtime_cache = tmp_path / "unmounted-cache"
    result = subprocess.run(
        ["sh", "-s"],
        input=script,
        text=True,
        capture_output=True,
        timeout=10,
        env={
            **os.environ,
            "TMPDIR": str(unavailable_runtime_cache / "tmp"),
            "XDG_CACHE_HOME": str(unavailable_runtime_cache / "xdg"),
            "NUMBA_CACHE_DIR": str(unavailable_runtime_cache / "numba"),
            "TEST_TEMP_ROOT": str(tmp_path),
            "TEST_MARKER": str(marker),
            "TEST_EXIT_CODE": str(verifier_exit_code),
        },
        check=False,
    )
    assert result.returncode == verifier_exit_code, result.stderr
    work = Path(marker.read_text().strip())
    assert work.parent == tmp_path
    assert not work.exists(), "Temporary caches/reports must be cleaned on success and failure"
    assert not unavailable_runtime_cache.exists(), "Do not write through the runtime cache defaults"
