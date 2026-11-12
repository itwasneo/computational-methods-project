"""Installed-wheel consistency and run→analysis; no network or benchmark claims.

Build from a disposable committed snapshot, not the developer's dirty checkout.
Use a real isolated wheel installation with links to already-locked dependencies.
"""

import base64
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import venv
import zipfile

import pytest

from tsp_hpc.reproducibility.build import METADATA_FILE, build_id, file_digest, payload_digest

ROOT = Path(__file__).resolve().parents[2]


def command(*args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=90)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def commit(root):
    command("git", "-C", str(root), "add", ".")
    command("git", "-C", str(root), "-c", "user.name=Build Test", "-c", "user.email=test@example.invalid",
            "commit", "--quiet", "--no-gpg-sign", "-m", "test snapshot")
    return command("git", "-C", str(root), "rev-parse", "HEAD").stdout.strip()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    work = tmp_path_factory.mktemp("verified-wheel")
    checkout = work / "checkout"
    checkout.mkdir()
    for name in ("pyproject.toml", "uv.lock", "README.md", ".gitignore"):
        shutil.copy2(ROOT / name, checkout / name)
    for name in ("src", "tools"):
        shutil.copytree(ROOT / name, checkout / name, ignore=shutil.ignore_patterns("__pycache__"))
    command("git", "init", "--quiet", str(checkout))
    revision = commit(checkout)
    result = command(sys.executable, str(checkout / "tools/build_verified_wheel.py"),
                     "--offline", "--output", str(work / "wheels"), cwd=work)
    wheel = Path(result.stdout.strip().splitlines()[-1])
    receipt = json.loads(wheel.with_name(wheel.name + ".build.json").read_text())
    assert receipt["wheel_sha256"] == file_digest(wheel)
    assert receipt["build"]["source"] == {"revision": revision, "dirty": False}
    assert command("git", "-C", str(checkout), "status", "--porcelain").stdout == ""
    environment = work / "env"
    venv.EnvBuilder(with_pip=False).create(environment)
    site = environment / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    # Share dependency files only: never source-package/editable hooks or metadata.
    for path in Path(sysconfig.get_path("purelib")).iterdir():
        if path.suffix == ".pth" or "tsp_hpc" in path.name or path.name == "__pycache__":
            continue
        (site / path.name).symlink_to(path, target_is_directory=path.is_dir())
    command("uv", "pip", "install", "--python", str(environment / "bin/python"),
            "--offline", "--no-deps", str(wheel))
    return checkout, wheel, receipt, environment


@pytest.fixture
def installed(built, tmp_path):
    environment = tmp_path / "env"
    shutil.copytree(built[3], environment, symlinks=True)
    site, = environment.glob("lib/python*/site-packages")
    return environment / "bin/python", site


def probe(python, cwd):
    return json.loads(command(str(python), "-I", "-c", """
import json
from pathlib import Path
import sys
import tsp_hpc
from tsp_hpc.reproducibility.build import runtime_build
source, build = runtime_build(Path(tsp_hpc.__file__).parent, tsp_hpc.__version__, {'revision': None, 'dirty': None})
assert not {'numpy', 'pandas', 'fastapi'} & sys.modules.keys()
print(json.dumps({'source': source, 'build': build}))
""", cwd=cwd).stdout)


def test_verified_wheel_record_and_exclusive_clean_build(built, tmp_path):
    checkout, wheel, receipt, _ = built
    with zipfile.ZipFile(wheel) as archive:
        assert all((entry.external_attr >> 16) & 0o777 == 0o644 for entry in archive.infolist())
        record, = [p for p in archive.namelist() if p.endswith(".dist-info/RECORD")]
        for name, digest, size in csv.reader(StringIO(archive.read(record).decode())):
            if name == record:
                assert digest == size == ""
            else:
                raw = archive.read(name)
                assert digest == "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
                assert len(raw) == int(size)
        inventory = receipt["build"]["files"]
        assert record not in inventory and "tsp_hpc/" + METADATA_FILE not in inventory
        assert receipt["build"]["payload_sha256"] == payload_digest(inventory)
    before = file_digest(wheel)
    result = command(sys.executable, str(checkout / "tools/build_verified_wheel.py"), "--offline",
                     "--output", str(wheel.parent), check=False)
    assert result.returncode == 1 and "already exists" in result.stderr
    assert file_digest(wheel) == before
    (checkout / "README.md").write_text("dirty tracked file")
    try:
        result = command(sys.executable, str(checkout / "tools/build_verified_wheel.py"),
                         "--output", str(tmp_path / "refused"), check=False)
        assert result.returncode == 1 and "clean committed checkout" in result.stderr
        assert not (tmp_path / "refused").exists()
    finally:
        command("git", "-C", str(checkout), "restore", "README.md")


@pytest.mark.parametrize("fault", [None, "missing", "malformed", "duplicate-key", "version", "identity",
                                   "code", "extra-code", "missing-code", "symlink", "distribution-metadata",
                                   "incomplete-inventory", "duplicate-distribution", "special-file", "oversized"])
def test_installed_payload_verification(built, installed, tmp_path, monkeypatch, fault):
    python, site = installed
    package = site / "tsp_hpc"
    manifest = package / METADATA_FILE
    document = json.loads(manifest.read_text())
    target = package / "analysis/reports.py"
    dist, = site.glob("tsp_hpc-*.dist-info")
    if fault == "missing": manifest.unlink()
    elif fault == "malformed": manifest.write_text("not json")
    elif fault == "duplicate-key": manifest.write_text('{"schema_version":1,' + manifest.read_text()[1:])
    elif fault == "version":
        document["package_version"] = "99.0"
        document["build_id"] = build_id({k: v for k, v in document.items() if k not in ("files", "build_id")})
        manifest.write_text(json.dumps(document))
    elif fault == "identity":
        document["source"]["revision"] = "f" * 40
        manifest.write_text(json.dumps(document))
    elif fault == "code": target.write_text(target.read_text() + "\n# altered\n")
    elif fault == "extra-code": (package / "extra.py").write_text("# unexpected\n")
    elif fault == "missing-code": target.unlink()
    elif fault == "symlink":
        external = tmp_path / "external.py"
        target.rename(external)
        target.symlink_to(external)
    elif fault == "distribution-metadata":
        target = dist / "METADATA"
        target.write_text(target.read_text() + "\naltered metadata\n")
    elif fault == "incomplete-inventory":
        del document["files"]["tsp_hpc/analysis/reports.py"]
        document["payload_sha256"] = payload_digest(document["files"])
        document["build_id"] = build_id({k: v for k, v in document.items() if k not in ("files", "build_id")})
        manifest.write_text(json.dumps(document))
    elif fault == "duplicate-distribution": shutil.copytree(dist, site / "duplicate.dist-info")
    elif fault == "special-file":
        target.unlink()
        os.mkfifo(target)
    elif fault == "oversized": manifest.write_bytes(b" " * (1024 * 1024 + 1))
    monkeypatch.setenv("TSP_SOURCE_REVISION", "e" * 40)  # Not a source of authority.
    result = probe(python, built[0])  # Caller is a real, unrelated source checkout.
    if fault is None:
        assert (package / "__init__.py").stat().st_mode & 0o444 == 0o444
        assert result["source"] == built[2]["build"]["source"]
        assert result["build"]["status"] == "verified"
        assert result["build"]["identity"]["build_id"] == built[2]["build"]["build_id"]
    else:
        assert result["source"] == {"revision": None, "dirty": None}
        assert result["build"]["status"] == ("unavailable" if fault == "missing" else "invalid")


def test_installed_cli_run_and_analysis_with_and_without_evidence(built, installed, tmp_path):
    python, site = installed
    manifest = ROOT / "examples/manifests/nn-fixed-work.json"
    artifact = ROOT / "examples/instances/square.json"
    instance_id = json.loads(manifest.read_text())["instances"][0]["instance_id"]
    for verified in (True, False):
        shard = tmp_path / f"{verified}.jsonl"
        command(str(python), "-I", "-m", "tsp_hpc", "run", str(manifest),
                "--instance", f"{instance_id}={artifact}", "--output", str(shard), cwd=built[0])
        rows = [json.loads(line) for line in shard.read_text().splitlines()]
        assert len(rows) == 5 and all(row["schema_version"] == 2 for row in rows)
        for row in rows[:-1]:
            assert row["result"]["best_cost"] == 4
            if verified:
                assert row["runtime"]["source"] == built[2]["build"]["source"]
                assert row["runtime"]["build"]["identity"]["build_id"] == built[2]["build"]["build_id"]
            else:
                assert row["runtime"]["source"] == {"revision": None, "dirty": None}
                assert row["runtime"]["build"]["status"] == "unavailable"
        report = tmp_path / f"report-{verified}"
        result = command(str(python), "-I", "-m", "tsp_hpc", "analyze", str(shard), "--output", str(report),
                         "--baseline", "visited-scan-v1", "--max-plot-groups", "1", cwd=built[0], check=False)
        assert result.returncode == (0 if verified else 1), result.stderr
        summary = json.loads((report / "report.json").read_text())
        assert summary["observed_trials"] == 4
        assert summary["comparison"]["blocked_pairs"] == (0 if verified else 2)
        if verified:
            # Native bytecode/JIT cache files do not invalidate the immutable payload.
            assert probe(python, built[0])["build"]["status"] == "verified"
            (site / "tsp_hpc" / METADATA_FILE).unlink()
