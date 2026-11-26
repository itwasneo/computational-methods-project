"""Score a supplied TSPLIB tour and run a small repeatable NN/GA correctness campaign."""
import argparse
import json
from pathlib import Path

from tsp_hpc.analysis import read_shards, reference_gaps
from tsp_hpc.experiments.manifests import Manifest
from tsp_hpc.experiments.runner import run_manifest
from tsp_hpc.problem.distances import prepare_distances
from tsp_hpc.problem.instances import save_instance
from tsp_hpc.problem.objective import tour_cost
from tsp_hpc.problem.tsplib import load_tsplib, load_tsplib_tour, tsplib_reference
from tsp_hpc.solvers.resources import validate_threads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('instance', type=Path, help='local uncompressed .tsp')
    parser.add_argument('tour', type=Path, help='matching uncompressed TYPE: TOUR')
    parser.add_argument('output', type=Path, help='new output directory')
    parser.add_argument('--cost', required=True, type=int, help='published reference length')
    parser.add_argument('--kind', required=True, choices=['exact', 'best_known'])
    parser.add_argument('--source', required=True, help='citation for the published reference')
    parser.add_argument('--threads', type=int, default=1, help='threaded NN limit (default 1)')
    args = parser.parse_args()
    validate_threads(args.threads)
    record = load_tsplib(args.instance)
    instance = record.instance
    tour = load_tsplib_tour(args.tour, record)
    reference = tsplib_reference(record, cost=args.cost, kind=args.kind, source=args.source)
    modes = ('dense', 'on_demand')
    if tour_cost(instance, tour) != args.cost or any(
            prepare_distances(instance, mode=mode).tour_cost(tour) != args.cost for mode in modes):
        parser.error('supplied tour does not score to the declared reference length in all scorers')
    # Verification, data conversion and acquisition are outside the measured solver trials.
    args.output.mkdir(parents=True, exist_ok=False)
    artifact = args.output / 'instance.json'
    save_instance(record, artifact)
    def executions(labels):
        return [{'backend': backend, 'implementation': name, 'distance_mode': mode,
                 'resources': {'profile': 'local-smoke', 'workers': 1, 'threads_per_worker': threads}}
                for backend, name, threads in labels for mode in modes]
    common = {'instance_id': instance.instance_id, 'solver_seed': 42, 'repetitions': [0]}
    manifest = Manifest({'schema_version': 1, 'instances': [record.descriptor], 'experiments': [
        {**common, 'algorithm': 'nn', 'budget': {'start_cities': list(range(min(8, instance.n_cities)))},
         'executions': executions([('serial', 'visited-scan-v1', 1), ('serial', 'remaining-array-v1', 1),
                                   ('multicore', 'visited-scan-threaded-v1', args.threads)])},
        {**common, 'algorithm': 'ga', 'parameters': {'population_size': 32}, 'budget': {'max_generations': 20},
         'logical_search_ids': ['population-0', 'population-1'],
         'executions': executions([('serial', 'serial-generational-v1', 1)])}]})
    references = {'schema_version': 1, 'references': [reference]}
    for name, document in [('manifest', manifest.to_dict()), ('references', references)]:
        (args.output / f'{name}.json').write_text(json.dumps(document, indent=2) + '\n')
    shard = args.output / 'runs.jsonl'
    summary = run_manifest(manifest, instance_paths={instance.instance_id: artifact}, output_path=shard)
    data = read_shards([shard], expected_manifest=manifest)
    if summary['counts']['failed'] or data.diagnostics or not data.trials['usable'].all():
        raise RuntimeError(f'incomplete or invalid trials; inspect {shard}')
    gaps = reference_gaps(data, references)
    gaps.to_csv(args.output / 'gaps.csv', index=False)
    for algorithm in ('nn', 'ga'):
        rows = data.trials[data.trials.algorithm == algorithm]
        if any(rows[key].nunique() != 1 for key in ('tour_digest', 'best_cost', 'work_json')):
            raise ValueError(f'{algorithm} fixed-work results differ across execution modes')
        print(f'{algorithm}: {len(rows)} trials, cost {rows.best_cost.iloc[0]:g}; reference {args.cost}')
    print(f'Artifacts: {args.output} (correctness smoke, not a performance benchmark)')


if __name__ == '__main__':
    main()
