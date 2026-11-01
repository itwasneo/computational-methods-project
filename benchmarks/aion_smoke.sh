#!/usr/bin/env bash
set -euo pipefail

python="${1:?pass the prepared Python interpreter}"
output="${2:?pass a new output JSON path}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export PYTHONNOUSERSITE=1 MPLBACKEND=Agg

"$python" -B -m unittest discover -s tests -v
"$python" -B -m benchmarks.smart_suite --solver simulated_annealing:smart_sa \
    --params '{"initial_temperature":40.0,"cooling_rate":0.95,"moves_per_temperature":200,"max_moves":10000}' \
    --output "$output"
