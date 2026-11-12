#!/usr/bin/env python3
"""Build a provenance-bearing wheel from this repository's clean HEAD.

Uses the existing uv backend, then adds an inventory and rewrites wheel RECORD.
No metadata is written into the checkout. Outputs are exclusive, never replaced.
"""

import argparse
import base64
import csv
from email.parser import BytesParser
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Load the shared stdlib-only format module without executing tsp_hpc.__init__,
# which needs installed distribution metadata that a clean builder does not have.
_spec = importlib.util.spec_from_file_location("_tsp_build_provenance", ROOT / "src/tsp_hpc/reproducibility/build.py")
provenance = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = provenance
_spec.loader.exec_module(provenance)


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.PIPE)


def build(output, *, offline=False):
    if git("rev-parse", "--show-toplevel").decode().strip() != str(ROOT):
        raise ValueError("build from the project checkout, not a containing repository")
    if git("status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("verified builds require a clean committed checkout")
    git("ls-files", "--error-unmatch", "tools/build_verified_wheel.py", "src/tsp_hpc/reproducibility/build.py")
    revision = git("rev-parse", "HEAD").decode().strip()
    with tempfile.TemporaryDirectory(prefix="tsp-wheel-") as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", revision))) as archive:
            if any(not (member.isfile() or member.isdir()) for member in archive.getmembers()):
                raise ValueError("source snapshots must contain only regular files/directories")
            archive.extractall(stage, filter="data")
        project = tomllib.loads((stage / "pyproject.toml").read_text())
        if project["project"]["name"] != "tsp-hpc" or (stage / "src/tsp_hpc" / provenance.METADATA_FILE).exists():
            raise ValueError("unexpected package or pre-existing build metadata")
        version = project["project"]["version"]
        wheels = Path(temporary) / "wheels"
        command = ["uv", "build", str(stage), "--wheel", "--no-sources", "--out-dir", str(wheels)]
        if offline:
            command.append("--offline")
        subprocess.run(command, check=True)
        wheel, = wheels.glob("*.whl")
        with zipfile.ZipFile(wheel) as archive:
            names = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
            if len(names) != len(set(names)):
                raise ValueError("unexpected wheel entries")
            contents = {name: archive.read(name) for name in names}
        metadata_path, = [p for p in contents if p.endswith(".dist-info/METADATA")]
        dist_info = metadata_path.split("/")[0]
        info = BytesParser().parsebytes(contents[metadata_path])
        if info.get_all("Name") != ["tsp-hpc"] or info.get_all("Version") != [version]:
            raise ValueError("wheel package identity differs from source")
        if any(len(Path(p).parts) < 2 or Path(p).parts[0] not in ("tsp_hpc", dist_info)
               or ".." in Path(p).parts or "\\" in p for p in contents):
            raise ValueError("only a pure package/dist-info wheel is supported")
        if {p: hashlib.sha256(raw).hexdigest() for p, raw in contents.items() if p.startswith("tsp_hpc/")} != provenance.package_inventory(stage / "src/tsp_hpc"):
            raise ValueError("wheel package differs from committed source snapshot")
        record_path = dist_info + "/RECORD"
        del contents[record_path]
        files = {p: hashlib.sha256(raw).hexdigest() for p, raw in contents.items()}
        header = {
            "schema_version": 1, "method": provenance.METHOD, "source": {"revision": revision, "dirty": False},
            "package_version": version, "pyproject_sha256": provenance.file_digest(stage / "pyproject.toml"),
            "lock_sha256": provenance.file_digest(stage / "uv.lock"), "payload_sha256": provenance.payload_digest(files),
        }
        identity = {**header, "build_id": provenance.build_id(header)}
        document = {**identity, "files": files}
        manifest = (json.dumps(document, sort_keys=True, indent=2) + "\n").encode()
        if len(manifest) > provenance.MAX_METADATA_BYTES:
            raise ValueError("build metadata exceeds the runtime verification limit")
        contents["tsp_hpc/" + provenance.METADATA_FILE] = manifest
        record = io.StringIO(newline="")
        writer = csv.writer(record)
        for name, raw in sorted(contents.items()):
            digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode("ascii")
            writer.writerow([name, "sha256=" + digest, len(raw)])
        writer.writerow([record_path, "", ""])
        contents[record_path] = record.getvalue().encode()
        stamped = Path(temporary) / wheel.name
        with zipfile.ZipFile(stamped, "w") as archive:
            for name, raw in sorted(contents.items()):
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16  # Readable after a root-owned image install.
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, raw)
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        target, receipt = output / wheel.name, output / (wheel.name + ".build.json")
        if target.exists() or receipt.exists():
            raise FileExistsError("wheel/receipt already exists; choose a new output directory")
        evidence = {"schema_version": 1, "wheel_filename": wheel.name,
                    "wheel_sha256": provenance.file_digest(stamped), "build": document}
        with target.open("xb") as stream:
            stream.write(stamped.read_bytes())
        with receipt.open("x") as stream:
            json.dump(evidence, stream, sort_keys=True, indent=2)
            stream.write("\n")
        return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="exclusive wheel/receipt destination")
    parser.add_argument("--offline", action="store_true", help="require cached build dependencies")
    options = parser.parse_args()
    try:
        print(build(options.output, offline=options.offline))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Verified build failed: {error}\n")
