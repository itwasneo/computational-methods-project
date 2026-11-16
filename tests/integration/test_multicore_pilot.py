"""The bounded pilot is a real runner consumer, not an alternate scientific path."""
import json
from pathlib import Path
import subprocess
import sys


def test_pilot_validates_and_archives_without_overwriting(tmp_path):
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "pilot"
    command = [sys.executable, str(root / "tools/multicore_pilot.py"), str(output),
               "--cities", "4", "--starts", "4", "--threads", "1", "--repetitions", "3"]
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    summary = json.loads((output / "summary.json").read_text())
    assert len(summary["rows"]) == 4
    assert {row["backend"] for row in summary["rows"]} == {"serial", "multicore"}
    assert all(row["trials"] == 3 and row["search_time_s"] > 0 for row in summary["rows"])
    original = (output / "results-4.jsonl").read_bytes()
    assert (output / "serial-profile-4.txt").is_file()
    repeated = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=60)
    assert repeated.returncode != 0
    assert (output / "results-4.jsonl").read_bytes() == original
