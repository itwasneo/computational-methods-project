"""Immutable canonical instances; only shared loaders/generators create science."""

import hashlib
import re
import secrets
import threading

from tsp_hpc.interface.errors import InterfaceError
from tsp_hpc.interface.storage import decode, encode


def wire_descriptor(descriptor):
    return {**descriptor, "instance_seed": None if descriptor["instance_seed"] is None else str(descriptor["instance_seed"])}


class SnapshotStore:
    def __init__(self, storage):
        self.storage, self.limits = storage, storage.authority.limits
        self._lock = threading.RLock()
        self._entries = {}
        with storage.directory("snapshots") as directory:
            names = directory.names(self.limits.max_snapshots)
            self._count = len(names)  # Incomplete creations still consume capacity/evidence.
            for identifier in names:
                if not re.fullmatch(r"[0-9a-f]{32}", identifier):
                    raise InterfaceError("execution_unavailable", "Invalid snapshot storage identity.", 503)
                with directory.directory(identifier) as snapshot:
                    try:
                        raw = snapshot.read("metadata.json", 8192)
                    except FileNotFoundError:
                        continue  # Crash before publication; never pretend this is a snapshot.
                metadata = decode(raw)
                if (type(metadata) is not dict or set(metadata) != {"schema_version", "snapshot_id", "sha256", "instance", "source"}
                        or type(metadata["schema_version"]) is not int or metadata["schema_version"] != 1
                        or metadata["snapshot_id"] != identifier or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])):
                    raise InterfaceError("execution_unavailable", "Snapshot metadata is invalid.", 503)
                self._entries[identifier] = raw

    def _capacity(self):
        self.storage.check()
        if self._count >= self.limits.max_snapshots:
            raise InterfaceError("resource_limit", "Snapshot capacity reached; use a new owner-selected output root.", 413)

    def _parse(self, raw):
        from tsp_hpc.problem.instances import instance_from_json
        if len(raw) > self.limits.max_instance_bytes:
            raise InterfaceError("artifact_too_large", "Instance exceeds its byte limit.", 413)
        try:
            return instance_from_json(raw.decode("utf-8"), max_cities=self.limits.max_cities)
        except (ValueError, TypeError, KeyError, UnicodeError, OverflowError, RecursionError):
            raise InterfaceError("invalid_artifact", "Instance failed canonical validation or city limits.", 422) from None

    def _publish(self, record, source):
        raw = encode(record.to_dict())
        if len(raw) > self.limits.max_instance_bytes:
            raise InterfaceError("artifact_too_large", "Canonical instance exceeds its byte limit.", 413)
        identifier = secrets.token_hex(16)
        metadata = {"schema_version": 1, "snapshot_id": identifier, "sha256": hashlib.sha256(raw).hexdigest(),
                    "instance": record.descriptor, "source": source}
        encoded = encode(metadata)
        with self.storage.directory("snapshots") as directory:
            with directory.directory(identifier, create=True) as snapshot:
                self._count += 1
                snapshot.write_new("instance.json", raw)
                snapshot.write_new("metadata.json", encoded)  # Publication comes last.
        self._entries[identifier] = encoded
        return self.describe(identifier)

    def load(self, catalog, handle, revision):
        with self._lock:
            self._capacity()
            source = catalog.select_instance_source(handle, revision, max_bytes=self.limits.max_instance_bytes)
            with source.open() as stream:
                raw = stream.read(self.limits.max_instance_bytes + 1)
            record = self._parse(raw)
            return self._publish(record, {"kind": "artifact", "sha256": hashlib.sha256(raw).hexdigest()})

    def generate(self, generation):
        with self._lock:
            self._capacity()
            parameters = dict(generation)
            if parameters["n_cities"] > self.limits.max_cities:
                raise InterfaceError("resource_limit", "Generation exceeds the city limit.", 413)
            # Conservative JSON-coordinate estimate before generation/allocation.
            if 64 * parameters["n_cities"] + 4096 > self.limits.max_instance_bytes:
                raise InterfaceError("resource_limit", "Generation exceeds the artifact byte budget.", 413)
            from tsp_hpc.problem.instances import uniform_instance, clustered_instance
            generator = parameters.pop("generator")
            parameters["seed"] = int(parameters["seed"])
            try:
                if generator == "uniform-v1":
                    record = uniform_instance(**parameters)
                elif generator == "clustered-v1":
                    record = clustered_instance(**parameters)
                else:
                    raise ValueError("unknown generator")
            except (ValueError, TypeError, OverflowError, FloatingPointError):
                raise InterfaceError("invalid_request", "Generation parameters failed scientific validation.") from None
            return self._publish(record, {"kind": "generation", "sha256": None})

    def read(self, identifier):
        """Return fresh owned data only after bytes AND canonical science agree."""
        with self._lock:
            self.storage.check()
            expected = self._entries.get(identifier)
            if expected is None:
                raise InterfaceError("snapshot_not_found", "Snapshot is unavailable in this execution root.", 404)
            metadata = decode(expected)
            try:
                with self.storage.directory("snapshots") as directory:
                    with directory.directory(identifier) as snapshot:
                        if snapshot.read("metadata.json", 8192) != expected:
                            raise ValueError("metadata changed")
                        raw = snapshot.read("instance.json", self.limits.max_instance_bytes)
                if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
                    raise ValueError("content changed")
                record = self._parse(raw)
                if record.descriptor != metadata["instance"]:
                    raise ValueError("descriptor changed")
            except (OSError, ValueError, TypeError, InterfaceError):
                raise InterfaceError("snapshot_stale", "Snapshot changed or became unavailable.", 409) from None
            return record, raw, metadata

    def describe(self, identifier):
        record, _, metadata = self.read(identifier)
        return {"instance_snapshot_id": identifier, "content_sha256": metadata["sha256"],
                "instance": wire_descriptor(record.descriptor), "source": metadata["source"]}

    def export(self, identifier):
        return self.read(identifier)[1]
