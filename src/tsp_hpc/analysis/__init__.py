"""Public result-analysis API; static reporting is separate in .reports."""

from tsp_hpc.analysis.core import (
    Analysis,
    MAX_RECORD_BYTES,
    METRICS,
    PHASES,
    TRIAL_COLUMNS,
    TRIAL_FIELDS,
    load_references,
    paired_comparisons,
    read_shards,
    reference_gaps,
    summary_table,
)

__all__ = [
    "Analysis", "MAX_RECORD_BYTES", "METRICS", "PHASES", "TRIAL_COLUMNS", "TRIAL_FIELDS",
    "load_references", "paired_comparisons", "read_shards", "reference_gaps", "summary_table",
]
