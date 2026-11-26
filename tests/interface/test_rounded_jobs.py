"""Local artifact import -> reviewed native work -> validated results. No downloads."""
import json
from pathlib import Path

import pytest

from tsp_hpc.analysis import read_shards, reference_gaps
from tsp_hpc.problem.instances import save_instance
from tsp_hpc.problem.tsplib import load_tsplib, tsplib_reference


@pytest.mark.parametrize('algorithm,options', [
    ('nn', {'budget': {'start_cities': [2, 0]}, 'implementations': ['visited-scan-v1', 'remaining-array-v1']}),
    ('nn', {'budget': {'start_cities': [2, 0]}, 'implementations': ['visited-scan-threaded-v1'],
            'backend': 'multicore', 'threads_per_worker': 1, 'record_history': False}),
    ('ga', {'budget': {'max_generations': 3}, 'parameters': {'population_size': 5},
            'logical_search_ids': ['beta', 'alpha'], 'implementations': ['serial-generational-v1']}),
])
def test_review_run_and_analyze_rounded_instance(job_client, execution_session, algorithm, options):
    client, _, context, workspace, output = execution_session
    record = load_tsplib(Path(__file__).resolve().parents[2] / 'examples/instances/half-unit.tsp')
    save_instance(record, workspace / 'import.json')
    catalog = {k: v for k, v in context.items() if k != 'session_id'}
    assert client.post('/api/artifacts/refresh', json=catalog).is_success
    item, = client.post('/api/artifacts/query', json={**catalog, 'limit': 10, 'cursor': None}).json()['items']
    snapshot = job_client.post('/api/instances/load', artifact_handle=item['artifact_handle'], artifact_revision=item['revision'])
    plan = job_client.review(snapshot=snapshot['instance_snapshot_id'], algorithm=algorithm,
                             distance_modes=['dense', 'on_demand'], max_matrix_bytes=4096, **options)
    job = job_client.wait(job_client.submit(plan)['job']['job_id'])
    assert job['state'] == 'completed' and not job['failed_trials']
    source = output / '.tsp-local/jobs' / job['job_id'] / 'results.jsonl'
    trials = [json.loads(line) for line in source.read_text().splitlines()][:-1]
    assert len(trials) == 2 * len(options['implementations'])
    for trial in trials:
        assert trial['native_execution']['verified'] and trial['adapter']['distance_kind'] == 'tsplib_euc_2d'
        assert trial['result'] == trials[0]['result'] and trial['result']['best_cost'] == 3
        if algorithm == 'ga':
            assert [r['logical_id'] for r in trial['logical_results']] == ['beta', 'alpha']
            assert trial['result']['work']['full_tour_evaluations'] == 34
    data = read_shards([source]); assert not data.diagnostics
    ref = tsplib_reference(record, cost=3, kind='exact', source='three-city hand proof')
    gaps = reference_gaps(data, {'schema_version': 1, 'references': [ref]})
    assert len(gaps) == len(trials) and (gaps['absolute_gap'] == 0).all()
