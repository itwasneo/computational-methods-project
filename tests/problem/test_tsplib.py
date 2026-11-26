"""Importer tests use no network, third-party TSPLIB reader, or heuristic optimum assumptions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tsp_hpc.cli import main
from tsp_hpc.problem.exact import exact_reference
from tsp_hpc.problem.instances import instance_from_json, load_instance
from tsp_hpc.problem.objective import edge_cost, tour_cost
from tsp_hpc.problem.tsplib import MAX_SOURCE_BYTES, load_tsplib, tsplib_reference

FIXTURE = Path(__file__).resolve().parents[2] / 'examples/instances/half-unit.tsp'


def test_cli_import_roundtrip_keeps_original_hash_and_per_edge_half_up_cost(tmp_path):
    output = tmp_path / 'artifact.json'
    assert main(['import-tsplib', str(FIXTURE), '--output', str(output)]) == 0
    record = load_instance(output)
    assert record.descriptor['distance_kind'] == 'tsplib_euc_2d'
    assert record.descriptor['instance_seed'] is None
    assert record.provenance['parameters'] == {'name': 'half-unit', 'n_cities': 3,
        'source_sha256': hashlib.sha256(FIXTURE.read_bytes()).hexdigest()}
    instance = record.instance
    assert instance.coordinates.tolist() == [[0., 0.], [.5, 0.], [1., 0.]]
    assert edge_cost(instance, 0, 1) == 1  # Not ties-to-even.
    assert tour_cost(instance, np.arange(3, dtype=np.int64)) == exact_reference(instance).cost == 3
    # Rounding the complete unrounded tour instead would give 2, a different objective.
    original = output.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(['import-tsplib', str(FIXTURE), '--output', str(output)])
    assert exc.value.code != 0 and output.read_bytes() == original


@pytest.mark.parametrize('mode', ['dense', 'on_demand'])
def test_import_is_never_silently_run_with_unrounded_native_distances(mode):
    from tsp_hpc.problem.distances import prepare_distances
    with pytest.raises(ValueError, match='unrounded'):
        prepare_distances(load_tsplib(FIXTURE).instance, mode=mode)


def test_formatting_and_coordinate_row_order_only_change_source_identity(tmp_path):
    altered = tmp_path / 'different-filename'
    text = FIXTURE.read_text().replace('3 1.0 0\n1 0 0\n2 0.5 0', '2 +5e-1 0\n1 0 0\n3 1.0 0')
    text = text.replace('NODE_COORD_SECTION', 'COMMENT : second\nEDGE_WEIGHT_FORMAT : FUNCTION\nNODE_COORD_TYPE : TWOD_COORDS\nDISPLAY_DATA_TYPE : COORD_DISPLAY\nNODE_COORD_SECTION')
    altered.write_bytes(text.replace('EOF\n', '').replace('\n', '\r\n').encode())
    a, b = load_tsplib(FIXTURE), load_tsplib(altered)
    assert a.descriptor == b.descriptor
    assert a.provenance['parameters']['source_sha256'] != b.provenance['parameters']['source_sha256']


@pytest.mark.parametrize('old,new', [
    ('TYPE: TSP','TYPE: ATSP'), ('TYPE: TSP','TYPE: TSP\nTYPE: TSP'),
    ('EUC_2D','ATT'), ('EUC_2D','EXPLICIT'), ('EUC_2D','GEO'), ('EUC_2D','EUC_3D'),
    ('DIMENSION: 3',''), ('DIMENSION: 3','DIMENSION: 4097'), ('DIMENSION: 3','DIMENSION: 1'),
    ('NODE_COORD_SECTION','EDGE_WEIGHT_SECTION'), ('NODE_COORD_SECTION','FIXED_EDGES_SECTION\nNODE_COORD_SECTION'),
    ('NODE_COORD_SECTION','EDGE_WEIGHT_FORMAT: FULL_MATRIX\nNODE_COORD_SECTION'),
    ('1 0 0',''), ('1 0 0','2 0 0'), ('1 0 0','0 0 0'), ('1 0 0','4 0 0'),
    ('1 0 0','1 NaN 0'), ('1 0 0','1 1e999 0'), ('1 0 0','1 0 0 3'),
    ('EOF','EOF\nCOMMENT: trailing data'), ('EOF','TOUR_SECTION'),
])
def test_malformed_or_unsupported_is_not_reinterpreted(tmp_path, old, new):
    path = tmp_path / 'source'
    path.write_text(FIXTURE.read_text().replace(old, new))
    with pytest.raises(ValueError):
        load_tsplib(path)


def test_input_size_and_encoding(tmp_path):
    path = tmp_path / 'source'
    for raw in (b' ' * (MAX_SOURCE_BYTES + 1), b'\xff'):
        path.write_bytes(raw)
        with pytest.raises(ValueError):
            load_tsplib(path)


def test_import_metadata_is_validated_on_artifact_load():
    artifact = load_tsplib(FIXTURE).to_dict()
    for changes in ({'source_sha256': 'x'}, {'name': ''}, {'n_cities': 2}):
        damaged = deepcopy(artifact)
        damaged['provenance']['parameters'].update(changes)
        with pytest.raises(ValueError):
            instance_from_json(json.dumps(damaged))
    from tsp_hpc.reproducibility.identity import make_instance_id
    artifact['instance']['distance_kind'] = 'euclidean_2d'
    artifact['instance']['instance_id'] = make_instance_id(np.array(artifact['coordinates']), 'euclidean_2d')
    with pytest.raises(ValueError, match='rounded'):
        instance_from_json(json.dumps(artifact))


def test_references_are_explicit_sourced_and_objective_bound():
    record = load_tsplib(FIXTURE)
    for kind in ('exact', 'best_known'):
        ref = tsplib_reference(record, cost=3, kind=kind, source='hand-checked fixture')
        assert ref == {'source': 'hand-checked fixture', 'cost': 3, 'kind': kind,
            **{k: record.descriptor[k] for k in ('instance_id', 'coordinate_checksum', 'distance_kind')}}
    for args in ({'cost': 3.5}, {'cost': 2**53+1}, {'cost': True}, {'cost': -1}, {'source': ''}, {'kind': 'estimate'}):
        with pytest.raises(ValueError):
            tsplib_reference(record, **({'cost': 3, 'source': 'test', 'kind': 'best_known'} | args))
