"""A real offline wheel installation shared by installed science/backend gates."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import venv

import pytest


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    from tsp_hpc.reproducibility.build import file_digest
    root = Path(__file__).resolve().parents[2]
    work = tmp_path_factory.mktemp("verified-wheel")
    checkout = work / "checkout"
    checkout.mkdir()
    for name in ("pyproject.toml", "uv.lock", "README.md", ".gitignore"):
        shutil.copy2(root / name, checkout / name)
    for name in ("src", "tools"):
        shutil.copytree(root / name, checkout / name, ignore=shutil.ignore_patterns("__pycache__"))

    def command(*args, cwd=None):
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    command("git", "init", "--quiet", str(checkout))
    command("git", "-C", str(checkout), "add", ".")
    command("git", "-C", str(checkout), "-c", "user.name=Build Test", "-c", "user.email=test@example.invalid",
            "commit", "--quiet", "--no-gpg-sign", "-m", "test snapshot")
    revision = command("git", "-C", str(checkout), "rev-parse", "HEAD").stdout.strip()
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
    # Locked dependencies only; no source package, editable hooks or its metadata.
    for path in Path(sysconfig.get_path("purelib")).iterdir():
        if path.suffix == ".pth" or "tsp_hpc" in path.name or path.name == "__pycache__":
            continue
        (site / path.name).symlink_to(path, target_is_directory=path.is_dir())
    command("uv", "pip", "install", "--python", str(environment / "bin/python"), "--offline", "--no-deps", str(wheel))
    return checkout, wheel, receipt, environment


@pytest.fixture
def installed(built, tmp_path):
    environment = tmp_path / "env"
    shutil.copytree(built[3], environment, symlinks=True)
    site, = environment.glob("lib/python*/site-packages")
    return environment / "bin/python", site
