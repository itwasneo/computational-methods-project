"""Check installed package metadata and the minimal command-line boundary."""

import subprocess
import sys
from importlib.metadata import distribution, version
from pathlib import Path

import pytest

import tsp_hpc
from tsp_hpc.cli import main


def test_package_version_matches_installed_metadata() -> None:
    assert tsp_hpc.__version__ == version("tsp-hpc")
    assert tsp_hpc.__version__


def test_console_entry_point_is_registered() -> None:
    entry_points = [
        entry
        for entry in distribution("tsp-hpc").entry_points
        if entry.group == "console_scripts" and entry.name == "tsp-hpc"
    ]
    assert len(entry_points) == 1
    assert entry_points[0].load() is main


@pytest.mark.parametrize(
    ("arguments", "exit_code", "output"),
    [
        ([], 0, "solver and benchmark commands are not yet implemented"),
        (["--help"], 0, "usage: tsp-hpc"),
        (["--version"], 0, f"tsp-hpc {tsp_hpc.__version__}"),
        (["--unknown"], 2, "unrecognized arguments: --unknown"),
    ],
)
def test_module_cli_outside_checkout(
    tmp_path: Path, arguments: list[str], exit_code: int, output: str
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tsp_hpc", *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == exit_code
    stream = result.stdout if exit_code == 0 else result.stderr
    assert output in " ".join(stream.split())
