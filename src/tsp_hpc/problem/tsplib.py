"""Offline import of a deliberately narrow TSPLIB symmetric EUC_2D subset."""

import hashlib
import math
from pathlib import Path
import re

import numpy as np

from tsp_hpc.problem.instances import InstanceRecord, _record
from tsp_hpc.problem.objective import rounded_reference_cost

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_CITIES = 4096
_INTEGER = re.compile(r"[1-9][0-9]{0,3}\Z")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_REQUIRED = {"NAME", "TYPE", "DIMENSION", "EDGE_WEIGHT_TYPE"}
_OPTIONAL = {"NODE_COORD_TYPE": {"TWOD_COORDS"}, "EDGE_WEIGHT_FORMAT": {"FUNCTION"},
             "DISPLAY_DATA_TYPE": {"COORD_DISPLAY", "NO_DISPLAY"}}


def load_tsplib(path: str | Path) -> InstanceRecord:
    """Read a local ASCII .tsp, retaining original byte hash and node-ID ordering.

    No fetching, decompression, matrix construction, or inference of a reference cost.
    Source node ID k maps to canonical city index k-1 regardless of row order.
    """
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("TSPLIB file exceeds the byte limit")
    lines = iter(enumerate(raw.decode('ascii').splitlines(), 1))
    headers = {}
    for number, text in lines:
        line = text.strip()
        if not line:
            continue
        if line == 'NODE_COORD_SECTION':
            break
        key, sep, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if key == 'COMMENT' and sep:
            continue
        if not sep or (key not in _REQUIRED and key not in _OPTIONAL):
            raise ValueError(f"unsupported TSPLIB header or section at line {number}")
        if key in headers or not value:
            raise ValueError(f"duplicate or empty TSPLIB {key} header")
        if key in _OPTIONAL and value not in _OPTIONAL[key]:
            raise ValueError(f"unsupported TSPLIB {key}: {value}")
        headers[key] = value
    else:
        raise ValueError("missing TSPLIB NODE_COORD_SECTION")
    if not _REQUIRED.issubset(headers):
        raise ValueError("missing required TSPLIB header(s): " + ', '.join(sorted(_REQUIRED - headers.keys())))
    if headers['TYPE'] != 'TSP' or headers['EDGE_WEIGHT_TYPE'] != 'EUC_2D':
        raise ValueError("only symmetric TYPE: TSP with EDGE_WEIGHT_TYPE: EUC_2D is supported")
    dimension = headers['DIMENSION']
    if not _INTEGER.fullmatch(dimension) or not 2 <= int(dimension) <= MAX_CITIES:
        raise ValueError(f"TSPLIB DIMENSION must be in 2..{MAX_CITIES}")
    n = int(dimension)
    coordinates = np.empty((n, 2), dtype=np.float64)
    seen = set()
    ended = False
    for number, text in lines:
        fields = text.split()
        if not fields:
            continue
        if ended:
            raise ValueError(f"unexpected TSPLIB content after EOF at line {number}")
        if fields == ['EOF']:
            ended = True
            continue
        if len(fields) != 3 or not _INTEGER.fullmatch(fields[0]):
            raise ValueError(f"expected node ID and two coordinates at line {number}")
        node = int(fields[0])
        if not 1 <= node <= n or node in seen:
            raise ValueError(f"duplicate or out-of-range TSPLIB node ID {node}")
        for axis, token in enumerate(fields[1:]):
            if len(token) > 128 or not _DECIMAL.fullmatch(token):
                raise ValueError(f"invalid TSPLIB decimal coordinate at line {number}")
            value = float(token)
            if not math.isfinite(value):
                raise ValueError(f"non-finite TSPLIB coordinate at line {number}")
            coordinates[node - 1, axis] = value
        seen.add(node)
    if len(seen) != n:
        raise ValueError("missing TSPLIB coordinate rows")
    return _record(coordinates, 'tsplib_euc_2d', 'tsplib-euc-2d-v1', {
        'name': headers['NAME'], 'source_sha256': hashlib.sha256(raw).hexdigest(), 'n_cities': n}, None)


def tsplib_reference(record: InstanceRecord, *, cost: int, kind: str, source: str) -> dict:
    """Bind an explicitly sourced reference declaration, never promote a feasible tour."""
    if not isinstance(record, InstanceRecord) or record.provenance['generator'] != 'tsplib-euc-2d-v1':
        raise ValueError("a TSPLIB EUC_2D import is required")
    if kind not in ('exact', 'best_known') or not isinstance(source, str) or not source.strip():
        raise ValueError("declare exact or best_known and cite the reference source")
    descriptor = record.descriptor
    return {**{k: descriptor[k] for k in ('instance_id', 'coordinate_checksum', 'distance_kind')},
            'cost': rounded_reference_cost(cost), 'kind': kind, 'source': source}
