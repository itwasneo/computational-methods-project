"""Build/file consistency, not authentication of an operator or running process.

Only the controlled wheel producer establishes the Git-to-payload relationship.
Verification checks installed files at campaign start, assuming a trusted builder,
installer, interpreter and caches, and no concurrent modification/monkeypatching.
"""

import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import sys

METADATA_FILE = "_build_provenance.json"
MAX_METADATA_BYTES = 1024 * 1024
METHOD = "package-sha256-v1"
IDENTITY_FIELDS = {"schema_version", "method", "source", "package_version",
                   "pyproject_sha256", "lock_sha256", "payload_sha256", "build_id"}
SHA256 = re.compile(r"[0-9a-f]{64}")


def _fields(value, keys):
    if type(value) is not dict or value.keys() != keys:
        raise ValueError("invalid provenance fields")


def validate_source(source):
    """Validate both legacy checkout observations and new build source claims."""
    _fields(source, {"revision", "dirty"})
    revision, dirty = source["revision"], source["dirty"]
    if revision is None:
        if dirty is not None:
            raise ValueError("unknown revision requires unknown dirty state")
    elif (type(revision) is not str or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is None
          or type(dirty) is not bool):
        raise ValueError("invalid source provenance")


def _digest(prefix, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(prefix + raw.encode("ascii")).hexdigest()


def payload_digest(files):
    return _digest(b"tsp-package-files-v1\0", files)


def build_id(header):
    return "build-v1-" + _digest(b"tsp-build-v1\0", header)


def validate_identity(identity):
    _fields(identity, IDENTITY_FIELDS)
    if type(identity["schema_version"]) is not int or identity["schema_version"] != 1 or identity["method"] != METHOD:
        raise ValueError("unsupported build identity")
    validate_source(identity["source"])
    if identity["source"]["revision"] is None:
        raise ValueError("build identity requires a source revision")
    if type(identity["package_version"]) is not str or not identity["package_version"].strip():
        raise ValueError("invalid build package version")
    for field in ("pyproject_sha256", "lock_sha256", "payload_sha256"):
        if type(identity[field]) is not str or SHA256.fullmatch(identity[field]) is None:
            raise ValueError("invalid build digest")
    if identity["build_id"] != build_id({k: v for k, v in identity.items() if k != "build_id"}):
        raise ValueError("build identity digest mismatch")


def observation(method="unknown", status="unavailable", identity=None):
    return {"schema_version": 1, "method": method, "status": status, "identity": identity}


def validate_observation(build, source, package_version):
    """Check recorded claims, not the producer's installation from a JSONL file."""
    _fields(build, {"schema_version", "method", "status", "identity"})
    validate_source(source)
    if type(build["schema_version"]) is not int or build["schema_version"] != 1:
        raise ValueError("unsupported build observation")
    method, status, identity = build["method"], build["status"], build["identity"]
    if method == METHOD and status == "verified":
        validate_identity(identity)
        if identity["source"] != source or identity["package_version"] != package_version:
            raise ValueError("conflicting build/source/version claims")
    elif method == "git-checkout" and status == "verified":
        if identity is not None or source["revision"] is None:
            raise ValueError("invalid checkout observation")
    elif (method, status) in (("unknown", "unavailable"), (METHOD, "invalid")):
        if identity is not None or source != {"revision": None, "dirty": None}:
            raise ValueError("unverified build cannot establish source identity")
    else:
        raise ValueError("invalid build observation")


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_inventory(root):
    """All regular package files except the manifest and trusted runtime caches."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("symlinked package")
    files = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError("symlinked package content")
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix in (".pyc", ".pyo"):
            raise ValueError("unsupported package content")
        if relative.as_posix() != METADATA_FILE:
            files["tsp_hpc/" + relative.as_posix()] = file_digest(path)
    return files


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate build metadata key")
        result[key] = value
    return result


def _verify_installed(root, version):
    manifest = root / METADATA_FILE
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("build metadata must be a regular file")
    with manifest.open("rb") as stream:
        raw = stream.read(MAX_METADATA_BYTES + 1)
    if len(raw) > MAX_METADATA_BYTES:
        raise ValueError("build metadata too large")
    document = json.loads(raw, object_pairs_hook=_unique)
    _fields(document, IDENTITY_FIELDS | {"files"})
    identity = {k: v for k, v in document.items() if k != "files"}
    validate_identity(identity)
    files = document["files"]
    if type(files) is not dict or not files or payload_digest(files) != identity["payload_sha256"]:
        raise ValueError("invalid package inventory")
    distributions = [d for d in metadata.distributions()
                     if re.sub(r"[-_.]+", "-", d.metadata.get("Name", "")).lower() == "tsp-hpc"]
    if len(distributions) != 1:
        raise ValueError("missing or ambiguous package distribution")
    dist = distributions[0]
    if (dist.metadata.get_all("Name") != ["tsp-hpc"] or dist.metadata.get_all("Version") != [version]
            or identity["package_version"] != version):
        raise ValueError("package version/name mismatch")
    registered = {str(path) for path in dist.files or ()}
    metadata_paths = [p for p in registered if len(PurePosixPath(p).parts) == 2 and p.endswith(".dist-info/METADATA")]
    if len(metadata_paths) != 1 or Path(dist.locate_file("tsp_hpc")).resolve() != root.resolve():
        raise ValueError("distribution does not own the executing package")
    dist_info = metadata_paths[0].split("/")[0]
    if not {metadata_paths[0], dist_info + "/WHEEL", "tsp_hpc/__init__.py"} <= files.keys():
        raise ValueError("incomplete package inventory")
    if not (set(files) | {"tsp_hpc/" + METADATA_FILE}) <= registered:
        raise ValueError("inventory is not registered to this distribution")
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (path.as_posix() != name or len(path.parts) < 2 or path.parts[0] not in ("tsp_hpc", dist_info)
                or any(part in ("..", ".", "__pycache__") for part in path.parts)
                or "\\" in name or name in ("tsp_hpc/" + METADATA_FILE, dist_info + "/RECORD")
                or type(digest) is not str or SHA256.fullmatch(digest) is None):
            raise ValueError("invalid inventory entry")
        actual = Path(dist.locate_file(name))
        if (not actual.is_file() or any(Path(dist.locate_file(PurePosixPath(*path.parts[:i]))).is_symlink()
                                       for i in range(1, len(path.parts) + 1))
                or file_digest(actual) != digest):
            raise ValueError("installed payload mismatch")
    if package_inventory(root) != {k: v for k, v in files.items() if k.startswith("tsp_hpc/")}:
        raise ValueError("extra or missing package files")
    # Refuse mixed package origins; disk checks cannot attest live monkeypatches.
    for name, module in tuple(sys.modules.items()):
        if name == "tsp_hpc" or name.startswith("tsp_hpc."):
            origin = getattr(module, "__file__", None)
            if origin is None or not Path(origin).resolve().is_relative_to(root.resolve()):
                raise ValueError("mixed package origins")
    return identity


def runtime_build(root, version, checkout_source):
    """Return conservative source + build observations; invalid evidence never falls back."""
    root = Path(root)
    manifest = root / METADATA_FILE
    unknown = {"revision": None, "dirty": None}
    try:
        if not manifest.exists() and not manifest.is_symlink():
            if checkout_source["revision"] is not None:
                return checkout_source, observation("git-checkout", "verified")
            return unknown, observation()
        if checkout_source["revision"] is not None:
            raise ValueError("embedded wheel metadata in a checkout")
        identity = _verify_installed(root, version)
        return identity["source"], observation(METHOD, "verified", identity)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return unknown, observation(METHOD, "invalid")
