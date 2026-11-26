"""Offline import of a deliberately narrow TSPLIB symmetric EUC_2D subset."""

import hashlib
import math
from pathlib import Path
import re

import numpy as np

from tsp_hpc.problem.instances import InstanceRecord, _record
from tsp_hpc.problem.objective import rounded_integer_cost

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_CITIES = 4096
_INTEGER = re.compile(r"[1-9][0-9]{0,3}\Z")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_REQUIRED = {"NAME", "TYPE", "DIMENSION", "EDGE_WEIGHT_TYPE"}
_OPTIONAL = {"NODE_COORD_TYPE": {"TWOD_COORDS"}, "EDGE_WEIGHT_FORMAT": {"FUNCTION"},
             "DISPLAY_DATA_TYPE": {"COORD_DISPLAY", "NO_DISPLAY"}}


def _read_section(path, section, required, optional):
    """Share byte bounds, header validation and section boundary for both local formats."""
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
        if line == section:
            break
        key, sep, value = line.partition(':')
        key, value = key.strip(), value.strip()
        if key == 'COMMENT' and sep:
            continue
        if not sep or (key not in required and key not in optional):
            raise ValueError(f"unsupported TSPLIB header or section at line {number}")
        if key in headers or not value:
            raise ValueError(f"duplicate or empty TSPLIB {key} header")
        if key in optional and value not in optional[key]:
            raise ValueError(f"unsupported TSPLIB {key}: {value}")
        headers[key] = value
    else:
        raise ValueError(f"missing TSPLIB {section}")
    if not required.issubset(headers):
        raise ValueError("missing required TSPLIB header(s): " + ', '.join(sorted(required - headers.keys())))
    return headers, lines, raw


def load_tsplib(path: str | Path) -> InstanceRecord:
    """Read a local ASCII .tsp. Source node k maps to city k-1 regardless of row order."""
    headers, lines, raw = _read_section(path, 'NODE_COORD_SECTION', _REQUIRED, _OPTIONAL)
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


def load_tsplib_tour(path: str | Path, record: InstanceRecord) -> np.ndarray:
    """Read a single matching TYPE: TOUR (.opt.tour); this does not prove optimality."""
    if not isinstance(record, InstanceRecord) or record.provenance['generator'] != 'tsplib-euc-2d-v1':
        raise ValueError('a TSPLIB EUC_2D import is required')
    headers, lines, _ = _read_section(path, 'TOUR_SECTION', {'TYPE', 'NAME', 'DIMENSION'}, {})
    n = record.instance.n_cities
    name = record.provenance['parameters']['name']
    if (headers['TYPE'] != 'TOUR' or headers['DIMENSION'] != str(n)
            or headers['NAME'] not in (name, name + '.tour', name + '.opt.tour')):
        raise ValueError('TSPLIB tour type/name/dimension does not match instance')
    cities, seen = [], set()
    terminated = eof = False
    for _, text in lines:
        for token in text.split():
            if terminated and not eof and token == 'EOF':
                eof = True
                continue
            if terminated:
                raise ValueError('unexpected content after TSPLIB tour terminator')
            if token == '-1':
                terminated = True
                continue
            if not _INTEGER.fullmatch(token) or not 1 <= int(token) <= n or int(token) in seen:
                raise ValueError('invalid, duplicate or out-of-range TSPLIB tour node')
            seen.add(int(token))
            cities.append(int(token) - 1)
    if not terminated or len(cities) != n:
        raise ValueError('expected one complete TSPLIB tour followed by -1')
    return np.array(cities, dtype=np.int64)


def tsplib_reference(record: InstanceRecord, *, cost: int, kind: str, source: str) -> dict:
    """Bind an explicitly sourced reference declaration, never promote a feasible tour."""
    if not isinstance(record, InstanceRecord) or record.provenance['generator'] != 'tsplib-euc-2d-v1':
        raise ValueError("a TSPLIB EUC_2D import is required")
    if kind not in ('exact', 'best_known') or not isinstance(source, str) or not source.strip():
        raise ValueError("declare exact or best_known and cite the reference source")
    descriptor = record.descriptor
    return {**{k: descriptor[k] for k in ('instance_id', 'coordinate_checksum', 'distance_kind')},
            'cost': rounded_integer_cost(cost), 'kind': kind, 'source': source}
