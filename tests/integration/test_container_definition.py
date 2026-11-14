"""Definition and preflight regressions; no Apptainer or cluster commands."""

import hashlib
import json
import os
import re
import runpy
import shutil
import subprocess
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _definition() -> str:
    return (ROOT / "containers/cpu.def").read_text()


@pytest.fixture(scope="module")
def checker():
    return runpy.run_path(str(ROOT / "containers/check_runtime.py"))


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
    verifier = '/opt/tsp/.venv/bin/python -I -B /opt/tsp/check_runtime.py --output "$work/check"'
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
    test "$MPLCONFIGDIR" = "$work/matplotlib"
    test -d "$MPLCONFIGDIR"
    test "$NUMBA_CACHE_DIR" = "$work/numba"
    test -d "$TMPDIR" && test -d "$XDG_CACHE_HOME" && test -d "$NUMBA_CACHE_DIR"
    printf 'cache probe\\n' > "$NUMBA_CACHE_DIR/probe"
    mkdir "$work/check"
    printf '{}\\n' > "$work/check/report.json"
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


def test_installed_wheel_boundary_and_scientific_test_selection(checker):
    definition = _definition()
    files = definition.split("%files\n", 1)[1].split("\n%", 1)[0]
    assert "    src " not in files and ".image-build/wheels /opt/tsp/wheels" in files
    assert "--no-install-project" in definition and '--no-deps "$wheel"' in definition
    assert definition.index("--check-wheel") < definition.index("uv pip install") < definition.index("--record-build")
    for section in ("post", "environment", "runscript", "test"):
        script = definition.split(f"%{section}\n", 1)[1].split("\n%", 1)[0]
        subprocess.run(["sh", "-n"], input=script, text=True, check=True)
    selection = checker["PACKAGED_TESTS"]
    assert "tests/experiments" in selection and "tests/analysis" in selection
    assert "tests/integration/test_native_runtime.py" in selection
    assert "tests" not in selection and "tests/integration" not in selection
    assert all((ROOT / path).exists() for path in selection)
    copied_tests = {line.split()[0] for line in files.splitlines() if line.strip().startswith("tests")}
    assert copied_tests == set(selection)


@pytest.fixture
def staged(tmp_path, checker):
    (tmp_path / "wheels").mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        (tmp_path / name).write_text("staged " + name)
    (tmp_path / "source-revision").write_text("a" * 40)
    build = {"source": {"revision": "a" * 40, "dirty": False},
             "pyproject_sha256": checker["digest"](tmp_path / "pyproject.toml"),
             "lock_sha256": checker["digest"](tmp_path / "uv.lock")}
    wheel = tmp_path / "wheels/tsp_hpc-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("tsp_hpc/_build_provenance.json", json.dumps(build))
    receipt = {"schema_version": 1, "wheel_filename": wheel.name,
               "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "build": build}
    path = wheel.with_name(wheel.name + ".build.json")
    path.write_text(json.dumps(receipt))
    return tmp_path, wheel, path


@pytest.mark.parametrize("fault", [None, "wheel", "receipt", "revision", "lock", "pyproject", "multiple"])
def test_preinstall_rejects_mixed_staging_inputs(checker, staged, fault):
    root, wheel, receipt_path = staged
    if fault == "wheel": wheel.write_bytes(wheel.read_bytes() + b"altered")
    elif fault == "receipt":
        receipt = json.loads(receipt_path.read_text())
        receipt["build"]["source"]["dirty"] = True
        receipt_path.write_text(json.dumps(receipt))
    elif fault == "revision": (root / "source-revision").write_text("b" * 40)
    elif fault in ("lock", "pyproject"):
        (root / ("uv.lock" if fault == "lock" else "pyproject.toml")).write_text("different input")
    elif fault == "multiple": shutil.copy(wheel, wheel.with_name("second.whl"))
    if fault is None:
        assert checker["check_wheel"](root)[0] == wheel
    else:
        with pytest.raises(ValueError):
            checker["check_wheel"](root)


def test_unverified_execution_cannot_create_build_info(checker, staged, monkeypatch):
    from tsp_hpc.experiments import runner
    monkeypatch.setattr(runner, "runtime_provenance", lambda: {
        "source": {"revision": None, "dirty": None},
        "build": {"method": "unknown", "status": "unavailable", "identity": None},
    })
    with pytest.raises(ValueError, match="executing package"):
        checker["build_information"](staged[0])
    assert not (staged[0] / "build-info.json").exists()
